"""SQLite-backed long-term memory manager with multi-user isolation, semantic scoring, TTL, and conflict protection."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Iterator

from core.ai.safety import strip_unsafe_control_chars
from core.identity.context import get_current_execution_context
from core.identity.errors import InvalidIdentityError, MissingIdentityError
from core.identity.models import AuthenticatedIdentity, UserProfile
from core.memory.database import MemoryDatabase
from core.memory.semantic_memory import MemorySemanticScorer


MAX_MEMORY_CATEGORY_CHARS = 64
MAX_MEMORY_KEY_CHARS = 128
MAX_MEMORY_VALUE_CHARS = 2000
MAX_MEMORY_SEARCH_RESULTS = 50
MAX_MEMORY_LIST_RESULTS = 100

MEMORY_CATEGORIES: tuple[str, ...] = (
    "user_preferences",
    "personal_instructions",
    "important_facts",
    "projects",
    "conversation_summaries",
    "frequently_used",
)

_CANONICAL_ALIASES: dict[str, str] = {
    "user_preferences": "user_preferences",
    "preferences": "user_preferences",
    "preference": "user_preferences",
    "prefs": "user_preferences",
    "personal_instructions": "personal_instructions",
    "instructions": "personal_instructions",
    "instruction": "personal_instructions",
    "rules": "personal_instructions",
    "important_facts": "important_facts",
    "facts": "important_facts",
    "fact": "important_facts",
    "personal": "important_facts",
    "general": "important_facts",
    "work": "important_facts",
    "projects": "projects",
    "project": "projects",
    "conversation_summaries": "conversation_summaries",
    "summaries": "conversation_summaries",
    "summary": "conversation_summaries",
    "frequently_used": "frequently_used",
    "frequent": "frequently_used",
    "shortcuts": "frequently_used",
}

_CANONICAL_TO_LEGACY_GROUP: dict[str, tuple[str, ...]] = {
    "user_preferences": ("user_preferences", "preferences", "preference", "prefs"),
    "personal_instructions": ("personal_instructions", "instructions", "instruction", "rules"),
    "important_facts": ("important_facts", "facts", "fact", "personal", "general", "work"),
    "projects": ("projects", "project"),
    "conversation_summaries": ("conversation_summaries", "summaries", "summary"),
    "frequently_used": ("frequently_used", "frequent", "shortcuts"),
}

_UNSET = object()


class MemoryConflictError(ValueError):
    """Raised when an operation attempts to silently overwrite an existing memory."""

    def __init__(self, message: str, *, existing_record: dict[str, Any] | None = None):
        super().__init__(message)
        self.existing_record = existing_record


@dataclass(frozen=True)
class DuplicateCheckResult:
    """Result of checking a candidate memory against existing user memories."""

    status: str  # "none" | "exact_duplicate" | "key_conflict" | "near_duplicate"
    existing_record: dict[str, Any] | None = None
    similarity: float = 0.0
    message: str = ""


def resolve_canonical_category(category: str) -> str:
    """Map a stored/legacy category string onto one of the 6 canonical Phase 4 categories."""
    cleaned = str(category or "").strip().lower()
    return _CANONICAL_ALIASES.get(cleaned, cleaned or "important_facts")


def expand_canonical_categories(category_or_canonical: str) -> tuple[str, ...]:
    """Return all category names matching a canonical category (including legacy aliases)."""
    cleaned = str(category_or_canonical or "").strip().lower()
    if cleaned in _CANONICAL_TO_LEGACY_GROUP:
        return _CANONICAL_TO_LEGACY_GROUP[cleaned]
    canonical = _CANONICAL_ALIASES.get(cleaned)
    if canonical and canonical in _CANONICAL_TO_LEGACY_GROUP:
        return _CANONICAL_TO_LEGACY_GROUP[canonical]
    return (cleaned,)


_CONFIRMED_MEMORY_OVERWRITE: ContextVar[bool] = ContextVar(
    "riya_confirmed_memory_overwrite",
    default=False,
)


@contextmanager
def confirmed_memory_overwrite_scope() -> Iterator[None]:
    """Allow overwriting an existing memory key within a verified tool-confirmation execution."""
    token = _CONFIRMED_MEMORY_OVERWRITE.set(True)
    try:
        yield
    finally:
        _CONFIRMED_MEMORY_OVERWRITE.reset(token)


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_expires_at(
    *,
    ttl_seconds: float | None = None,
    expires_at: str | None = None,
) -> str | None:
    if ttl_seconds is not None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive number.")
        dt = datetime.now(timezone.utc) + timedelta(seconds=float(ttl_seconds))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    if expires_at is not None:
        raw = str(expires_at).strip()
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    return None


class MemoryManager:
    """SQLite-backed long-term memory manager with fail-closed multi-user isolation."""

    def __init__(
        self,
        database=None,
        *,
        user_id: str | None | object = _UNSET,
        identity: Any = _UNSET,
        identity_manager: Any = None,
        permission_manager: Any = None,
        require_user: bool = False,
        prevent_silent_overwrite: bool = False,
        semantic_scorer: MemorySemanticScorer | None = None,
    ):
        self.database = database or MemoryDatabase()
        self.identity_manager = identity_manager
        self.permission_manager = permission_manager
        self.require_user = bool(require_user)
        self.prevent_silent_overwrite = bool(prevent_silent_overwrite)
        self.semantic_scorer = semantic_scorer or MemorySemanticScorer()
        self._explicit_user_id_provided = user_id is not _UNSET
        self._explicit_identity_provided = identity is not _UNSET
        self._user_id: str | None = None if user_id is _UNSET else user_id  # type: ignore[assignment]
        self._identity: Any = None if identity is _UNSET else identity

        if self._explicit_identity_provided:
            self._validate_explicit_identity_object(self._identity)
        if self._explicit_user_id_provided:
            self._validate_raw_user_id(self._user_id)

    @staticmethod
    def _validate_explicit_identity_object(identity: Any) -> AuthenticatedIdentity:
        if identity is None:
            raise MissingIdentityError("Authenticated user identity is required.")
        if isinstance(identity, UserProfile):
            raise InvalidIdentityError(
                "A UserProfile cannot be used as an authenticated memory identity."
            )
        if not isinstance(identity, AuthenticatedIdentity):
            raise InvalidIdentityError(
                "Invalid identity object; expected AuthenticatedIdentity."
            )
        return identity

    @staticmethod
    def _validate_raw_user_id(user_id: Any) -> str:
        if user_id is None or (isinstance(user_id, str) and not user_id.strip()):
            raise MissingIdentityError("Non-empty user_id is required.")
        if not isinstance(user_id, str):
            raise InvalidIdentityError("user_id must be a valid string.")
        return user_id.strip()

    def for_identity(
        self,
        identity: AuthenticatedIdentity,
        *,
        identity_manager: Any | None = None,
        prevent_silent_overwrite: bool | None = None,
    ) -> "MemoryManager":
        """Create a MemoryManager strictly bound to a validated user session."""
        mgr = identity_manager if identity_manager is not None else self.identity_manager
        validated = self._validate_explicit_identity_object(identity)
        if mgr is not None:
            validated = mgr.validate_session(validated)
        return MemoryManager(
            self.database,
            identity=validated,
            identity_manager=mgr,
            permission_manager=self.permission_manager,
            require_user=True,
            prevent_silent_overwrite=(
                self.prevent_silent_overwrite
                if prevent_silent_overwrite is None
                else prevent_silent_overwrite
            ),
            semantic_scorer=self.semantic_scorer,
        )

    def _enforce_permission(
        self,
        permission_key: str,
        *,
        resource: str = "*",
    ) -> None:
        if self.permission_manager is None:
            return
        ctx = get_current_execution_context()
        identity = None
        if self._explicit_identity_provided:
            identity = self._identity
        elif ctx is not None and ctx.identity is not None:
            identity = ctx.identity
        if not isinstance(identity, AuthenticatedIdentity):
            return
        eval_res = self.permission_manager.evaluate(
            identity,
            permission_key,
            resource_or_target=resource,
            actor="memory_manager",
        )
        if eval_res.blocked:
            raise PermissionError(eval_res.reason)

    def _assert_active_user_exists(self, user_id: str) -> None:
        conn = self.database.connect()
        try:
            row = conn.execute(
                "SELECT is_active FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None or not bool(row["is_active"]):
                raise InvalidIdentityError(
                    f"User '{user_id}' does not exist or is inactive."
                )
        finally:
            conn.close()

    def _resolve_user_id(self) -> str | None:
        """Resolve the authoritative user_id or fail closed in multi-user mode."""
        ctx = get_current_execution_context()

        if self._explicit_identity_provided:
            validated = self._validate_explicit_identity_object(self._identity)
            if self.identity_manager is not None:
                validated = self.identity_manager.validate_session(validated)
            else:
                if datetime.fromisoformat(validated.expires_at) <= datetime.now(timezone.utc):
                    raise InvalidIdentityError("Authenticated identity has expired.")
                self._assert_active_user_exists(validated.user_id)
            if ctx is not None and ctx.user_id != validated.user_id:
                raise InvalidIdentityError(
                    "Cross-user memory access blocked by execution context."
                )
            return validated.user_id

        if self._explicit_user_id_provided:
            if self.identity_manager is not None:
                raise MissingIdentityError(
                    "AuthenticatedIdentity is required when identity_manager is configured."
                )
            scoped = self._validate_raw_user_id(self._user_id)
            if ctx is not None and ctx.user_id != scoped:
                raise InvalidIdentityError(
                    "Cross-user memory access blocked by execution context."
                )
            self._assert_active_user_exists(scoped)
            return scoped

        if ctx is not None:
            if self.identity_manager is not None and ctx.identity is not None:
                validated = self.identity_manager.validate_session(ctx.identity)
                return validated.user_id
            if not isinstance(ctx.user_id, str) or not ctx.user_id.strip():
                raise MissingIdentityError("Invalid execution context user_id.")
            return ctx.user_id

        if (
            self.require_user
            or self.identity_manager is not None
            or self.database.has_registered_users()
        ):
            raise MissingIdentityError(
                "Authenticated user context is required for memory access."
            )

        # Single-userless v0.4 compatibility mode only when 0 users are registered.
        return None

    @staticmethod
    def _validate_identifier(
        value: str,
        field_name: str,
        max_chars: int,
    ) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Memory {field_name} must be a string.")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError(
                f"Memory {field_name} contains unsafe control characters."
            )
        cleaned = strip_unsafe_control_chars(value, allow_newlines=False).strip()
        if not cleaned:
            raise ValueError(f"Memory {field_name} cannot be empty.")
        if len(cleaned) > max_chars:
            raise ValueError(
                f"Memory {field_name} exceeds maximum length ({max_chars})."
            )
        return cleaned

    @staticmethod
    def _validate_value(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Memory value must be a string.")
        cleaned = strip_unsafe_control_chars(value, allow_newlines=True).strip()
        if not cleaned:
            raise ValueError("Memory value cannot be empty.")
        if len(cleaned) > MAX_MEMORY_VALUE_CHARS:
            raise ValueError(
                f"Memory value exceeds maximum length ({MAX_MEMORY_VALUE_CHARS})."
            )
        return cleaned

    @staticmethod
    def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> str:
        if not tags:
            return "[]"
        cleaned: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            t = strip_unsafe_control_chars(str(tag), allow_newlines=False).strip().lower()
            if t and t not in seen:
                seen.add(t)
                cleaned.append(t[:40])
            if len(cleaned) >= 12:
                break
        return json.dumps(cleaned)

    @staticmethod
    def _memory_columns(connection) -> set[str]:
        rows = connection.execute("PRAGMA table_info(memories)").fetchall()
        return {row[1] for row in rows}

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        item = dict(row)
        raw_tags = item.get("tags")
        if isinstance(raw_tags, str):
            try:
                parsed = json.loads(raw_tags)
                item["tags"] = parsed if isinstance(parsed, list) else []
            except Exception:
                item["tags"] = []
        elif not isinstance(raw_tags, list):
            item["tags"] = []
        item.setdefault("importance", 0.5)
        item.setdefault("access_count", 0)
        item.setdefault("last_accessed_at", None)
        item.setdefault("expires_at", None)
        item.setdefault("source", "explicit")
        item["canonical_category"] = resolve_canonical_category(str(item.get("category", "")))
        return item

    def _select_columns_sql(self, cols: set[str]) -> str:
        base = ["id", "category", "key", "value", "created_at", "updated_at"]
        for extra in ("importance", "access_count", "last_accessed_at", "expires_at", "tags", "source"):
            if extra in cols:
                base.append(extra)
        return ", ".join(base)

    def _fetch_exact_row(
        self,
        connection,
        user_id: str | None,
        category: str,
        key: str,
        *,
        include_expired: bool = False,
    ) -> dict[str, Any] | None:
        cols = self._memory_columns(connection)
        select_cols = self._select_columns_sql(cols)
        now_ts = _utc_now_str()
        exp_clause = ""
        params_extra: tuple[Any, ...] = ()
        if not include_expired and "expires_at" in cols:
            exp_clause = " AND (expires_at IS NULL OR expires_at > ?)"
            params_extra = (now_ts,)

        if user_id is None:
            row = connection.execute(
                f"""
                SELECT {select_cols}
                FROM memories
                WHERE user_id IS NULL
                  AND legacy_unclaimed = 0
                  AND category = ?
                  AND key = ?
                  {exp_clause}
                """,
                (category, key, *params_extra),
            ).fetchone()
        else:
            row = connection.execute(
                f"""
                SELECT {select_cols}
                FROM memories
                WHERE user_id = ?
                  AND category = ?
                  AND key = ?
                  {exp_clause}
                """,
                (user_id, category, key, *params_extra),
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def check_duplicate(
        self,
        category: str,
        key: str,
        value: str,
        *,
        near_threshold: float = 0.85,
    ) -> DuplicateCheckResult:
        """Detect exact conflicts on (category, key) or near-duplicates across user memories."""
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.read", resource="memory:read")
        category = self._validate_identifier(category, "category", MAX_MEMORY_CATEGORY_CHARS)
        key = self._validate_identifier(key, "key", MAX_MEMORY_KEY_CHARS)
        value = self._validate_value(value)

        with self.database.connect() as connection:
            exact = self._fetch_exact_row(connection, user_id, category, key)

        if exact is not None:
            old_val = str(exact["value"])
            if old_val.strip().lower() == value.strip().lower():
                return DuplicateCheckResult(
                    status="exact_duplicate",
                    existing_record=exact,
                    similarity=1.0,
                    message=f"Memory '{key}' under '{category}' is already saved as '{old_val}'.",
                )
            return DuplicateCheckResult(
                status="key_conflict",
                existing_record=exact,
                similarity=1.0,
                message=(
                    f"Existing memory '{key}' under '{category}' is saved as '{old_val}' "
                    f"(conflicts with new value '{value}')."
                ),
            )

        existing_all = self.list_memories(limit=MAX_MEMORY_LIST_RESULTS)
        candidate_text = f"{key.replace('_', ' ')}: {value}"
        best_sim = 0.0
        best_mem: dict[str, Any] | None = None
        for item in existing_all:
            item_val = str(item.get("value", ""))
            item_text = f"{str(item.get('key', '')).replace('_', ' ')}: {item_val}"
            sim = max(
                self.semantic_scorer.pair_similarity(value, item_val),
                self.semantic_scorer.pair_similarity(candidate_text, item_text),
            )
            if sim > best_sim:
                best_sim = sim
                best_mem = item

        if best_mem is not None and best_sim >= near_threshold:
            return DuplicateCheckResult(
                status="near_duplicate",
                existing_record=best_mem,
                similarity=round(best_sim, 4),
                message=(
                    f"Similar memory already exists: [{best_mem['category']}] "
                    f"{best_mem['key']} = '{best_mem['value']}'."
                ),
            )

        return DuplicateCheckResult(status="none")

    def store_memory(
        self,
        category: str,
        key: str,
        value: str,
        *,
        overwrite: bool = False,
        ttl_seconds: float | None = None,
        expires_at: str | None = None,
        importance: float = 0.5,
        tags: list[str] | tuple[str, ...] | None = None,
        source: str = "explicit",
    ) -> str:
        """Store a memory safely, refusing silent overwrites unless overwrite=True."""
        return self.remember(
            category,
            key,
            value,
            allow_overwrite=overwrite,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
            importance=importance,
            tags=tags,
            source=source,
        )

    def remember(
        self,
        category: str,
        key: str,
        value: str,
        *,
        allow_overwrite: bool | None = None,
        ttl_seconds: float | None = None,
        expires_at: str | None = None,
        importance: float = 0.5,
        tags: list[str] | tuple[str, ...] | None = None,
        source: str = "explicit",
    ) -> str:
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.write", resource="memory:write")
        category = self._validate_identifier(
            category,
            "category",
            MAX_MEMORY_CATEGORY_CHARS,
        )
        key = self._validate_identifier(
            key,
            "key",
            MAX_MEMORY_KEY_CHARS,
        )
        value = self._validate_value(value)

        safe_importance = max(0.0, min(1.0, float(importance)))
        safe_tags = self._normalize_tags(tags)
        safe_source = self._validate_identifier(source or "explicit", "source", 32)
        exp_ts = _normalize_expires_at(ttl_seconds=ttl_seconds, expires_at=expires_at)
        now_ts = _utc_now_str()

        effective_allow_overwrite = (
            (True if _CONFIRMED_MEMORY_OVERWRITE.get() else not self.prevent_silent_overwrite)
            if allow_overwrite is None
            else bool(allow_overwrite)
        )

        with self.database.connect() as connection:
            cols = self._memory_columns(connection)
            existing = self._fetch_exact_row(
                connection,
                user_id,
                category,
                key,
                include_expired=False,
            )
            if existing is not None and not effective_allow_overwrite:
                if str(existing["value"]).strip() == value.strip():
                    return f"Memory '{key}' under '{category}' is already up to date."
                raise MemoryConflictError(
                    f"Memory '{key}' under '{category}' already exists with value "
                    f"'{existing['value']}'. Refusing silent overwrite.",
                    existing_record=existing,
                )

            has_p4 = {"importance", "expires_at", "tags", "source"} <= cols
            if user_id is None:
                if has_p4:
                    connection.execute(
                        """
                        INSERT INTO memories (
                            user_id,
                            legacy_unclaimed,
                            category,
                            key,
                            value,
                            importance,
                            expires_at,
                            tags,
                            source,
                            created_at,
                            updated_at
                        )
                        VALUES (NULL, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(category, key) WHERE user_id IS NULL
                        DO UPDATE SET
                            value = excluded.value,
                            importance = excluded.importance,
                            expires_at = excluded.expires_at,
                            tags = excluded.tags,
                            source = excluded.source,
                            updated_at = excluded.updated_at
                        """,
                        (
                            category,
                            key,
                            value,
                            safe_importance,
                            exp_ts,
                            safe_tags,
                            safe_source,
                            now_ts,
                            now_ts,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO memories (
                            user_id,
                            legacy_unclaimed,
                            category,
                            key,
                            value,
                            created_at,
                            updated_at
                        )
                        VALUES (NULL, 0, ?, ?, ?, ?, ?)
                        ON CONFLICT(category, key) WHERE user_id IS NULL
                        DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (category, key, value, now_ts, now_ts),
                    )
            else:
                if has_p4:
                    connection.execute(
                        """
                        INSERT INTO memories (
                            user_id,
                            legacy_unclaimed,
                            category,
                            key,
                            value,
                            importance,
                            expires_at,
                            tags,
                            source,
                            created_at,
                            updated_at
                        )
                        VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, category, key)
                        DO UPDATE SET
                            value = excluded.value,
                            legacy_unclaimed = 0,
                            importance = excluded.importance,
                            expires_at = excluded.expires_at,
                            tags = excluded.tags,
                            source = excluded.source,
                            updated_at = excluded.updated_at
                        """,
                        (
                            user_id,
                            category,
                            key,
                            value,
                            safe_importance,
                            exp_ts,
                            safe_tags,
                            safe_source,
                            now_ts,
                            now_ts,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO memories (
                            user_id,
                            legacy_unclaimed,
                            category,
                            key,
                            value,
                            created_at,
                            updated_at
                        )
                        VALUES (?, 0, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id, category, key)
                        DO UPDATE SET
                            value = excluded.value,
                            legacy_unclaimed = 0,
                            updated_at = excluded.updated_at
                        """,
                        (user_id, category, key, value, now_ts, now_ts),
                    )
            connection.commit()

        return f"Remembered '{key}' under '{category}'."

    def update_memory(
        self,
        category: str,
        key: str,
        value: str,
        *,
        importance: float | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        ttl_seconds: float | None = None,
    ) -> bool:
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.write", resource="memory:write")
        category = self._validate_identifier(
            category,
            "category",
            MAX_MEMORY_CATEGORY_CHARS,
        )
        key = self._validate_identifier(
            key,
            "key",
            MAX_MEMORY_KEY_CHARS,
        )
        value = self._validate_value(value)
        now_ts = _utc_now_str()

        with self.database.connect() as connection:
            cols = self._memory_columns(connection)
            set_clauses = ["value = ?", "updated_at = ?"]
            set_params: list[Any] = [value, now_ts]
            if importance is not None and "importance" in cols:
                set_clauses.append("importance = ?")
                set_params.append(max(0.0, min(1.0, float(importance))))
            if tags is not None and "tags" in cols:
                set_clauses.append("tags = ?")
                set_params.append(self._normalize_tags(tags))
            if ttl_seconds is not None and "expires_at" in cols:
                set_clauses.append("expires_at = ?")
                set_params.append(_normalize_expires_at(ttl_seconds=ttl_seconds))

            if user_id is None:
                cursor = connection.execute(
                    f"""
                    UPDATE memories
                    SET {", ".join(set_clauses)}
                    WHERE user_id IS NULL
                      AND legacy_unclaimed = 0
                      AND category = ?
                      AND key = ?
                    """,
                    (*set_params, category, key),
                )
            else:
                cursor = connection.execute(
                    f"""
                    UPDATE memories
                    SET {", ".join(set_clauses)}
                    WHERE user_id = ?
                      AND category = ?
                      AND key = ?
                    """,
                    (*set_params, user_id, category, key),
                )
            connection.commit()

        return cursor.rowcount > 0

    def edit_memory(
        self,
        category: str,
        key: str,
        new_value: str,
        *,
        importance: float | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        ttl_seconds: float | None = None,
    ) -> bool:
        """User-controlled explicit edit alias for update_memory."""
        return self.update_memory(
            category,
            key,
            new_value,
            importance=importance,
            tags=tags,
            ttl_seconds=ttl_seconds,
        )

    def recall(
        self,
        category: str,
        key: str,
        *,
        record_access: bool = True,
    ) -> str | None:
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.read", resource="memory:read")
        category = self._validate_identifier(
            category,
            "category",
            MAX_MEMORY_CATEGORY_CHARS,
        )
        key = self._validate_identifier(
            key,
            "key",
            MAX_MEMORY_KEY_CHARS,
        )
        now_ts = _utc_now_str()

        with self.database.connect() as connection:
            cols = self._memory_columns(connection)
            row = self._fetch_exact_row(
                connection,
                user_id,
                category,
                key,
                include_expired=False,
            )
            if row is not None and record_access and "access_count" in cols:
                connection.execute(
                    """
                    UPDATE memories
                    SET access_count = COALESCE(access_count, 0) + 1,
                        last_accessed_at = ?
                    WHERE id = ?
                    """,
                    (now_ts, row["id"]),
                )
                connection.commit()

        if row is None:
            return None

        return str(row["value"])

    def _load_non_expired_rows(
        self,
        *,
        user_id: str | None,
        category_filter: str | None = None,
        canonical_category: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        now_ts = _utc_now_str()
        categories: tuple[str, ...] | None = None
        if canonical_category is not None:
            categories = expand_canonical_categories(canonical_category)
        elif category_filter is not None:
            categories = (category_filter,)

        with self.database.connect() as connection:
            cols = self._memory_columns(connection)
            select_cols = self._select_columns_sql(cols)
            where_parts: list[str] = []
            params: list[Any] = []

            if user_id is None:
                where_parts.append("user_id IS NULL AND legacy_unclaimed = 0")
            else:
                where_parts.append("user_id = ?")
                params.append(user_id)

            if not include_expired and "expires_at" in cols:
                where_parts.append("(expires_at IS NULL OR expires_at > ?)")
                params.append(now_ts)

            if categories:
                placeholders = ", ".join("?" for _ in categories)
                where_parts.append(f"category IN ({placeholders})")
                params.extend(categories)

            rows = connection.execute(
                f"""
                SELECT {select_cols}
                FROM memories
                WHERE {" AND ".join(where_parts)}
                ORDER BY updated_at DESC, id DESC
                """,
                tuple(params),
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = MAX_MEMORY_SEARCH_RESULTS,
        *,
        use_semantic: bool = True,
        include_scores: bool = False,
        record_access: bool = False,
    ) -> list[dict[str, Any]]:
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.read", resource="memory:read")
        if not isinstance(query, str):
            raise ValueError("Memory search query must be a string.")

        query = strip_unsafe_control_chars(query, allow_newlines=False).strip()
        if not query:
            return []

        if len(query) > MAX_MEMORY_KEY_CHARS * 2:
            query = query[: MAX_MEMORY_KEY_CHARS * 2]

        limit = max(1, min(int(limit), MAX_MEMORY_SEARCH_RESULTS))

        category_value = (
            self._validate_identifier(
                category,
                "category",
                MAX_MEMORY_CATEGORY_CHARS,
            )
            if category is not None
            else None
        )

        canonical_filter = (
            category_value if category_value in _CANONICAL_TO_LEGACY_GROUP else None
        )
        memories = self._load_non_expired_rows(
            user_id=user_id,
            category_filter=None if canonical_filter else category_value,
            canonical_category=canonical_filter,
            include_expired=False,
        )

        ranked = self.semantic_scorer.rank_memories(
            query,
            memories,
            use_semantic=use_semantic,
        )[:limit]

        if record_access and ranked:
            now_ts = _utc_now_str()
            with self.database.connect() as connection:
                cols = self._memory_columns(connection)
                if "access_count" in cols:
                    for item in ranked:
                        connection.execute(
                            """
                            UPDATE memories
                            SET access_count = COALESCE(access_count, 0) + 1,
                                last_accessed_at = ?
                            WHERE id = ?
                            """,
                            (now_ts, item["id"]),
                        )
                    connection.commit()

        if not include_scores:
            for item in ranked:
                item.pop("relevance_score", None)
                item.pop("semantic_score", None)

        return ranked

    def list_memories(
        self,
        category: str | None = None,
        limit: int = MAX_MEMORY_LIST_RESULTS,
        *,
        canonical_category: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.read", resource="memory:read")
        if category is not None:
            category = self._validate_identifier(
                category,
                "category",
                MAX_MEMORY_CATEGORY_CHARS,
            )
        if canonical_category is not None:
            canonical_category = self._validate_identifier(
                canonical_category,
                "canonical_category",
                MAX_MEMORY_CATEGORY_CHARS,
            )

        limit = max(1, min(int(limit), MAX_MEMORY_LIST_RESULTS))
        rows = self._load_non_expired_rows(
            user_id=user_id,
            category_filter=category,
            canonical_category=canonical_category,
            include_expired=include_expired,
        )
        rows.sort(key=lambda r: (str(r.get("category", "")), str(r.get("key", ""))))
        return rows[:limit]

    def frequently_used(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return most frequently accessed non-expired memories for the user."""
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.read", resource="memory:read")
        limit = max(1, min(int(limit), MAX_MEMORY_LIST_RESULTS))
        rows = self._load_non_expired_rows(user_id=user_id, include_expired=False)
        rows.sort(
            key=lambda r: (int(r.get("access_count", 0) or 0), float(r.get("importance", 0.5) or 0.5), str(r.get("updated_at", ""))),
            reverse=True,
        )
        return rows[:limit]

    def prune_expired(self) -> int:
        """Delete expired memories strictly for the active user scope."""
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.delete", resource="memory:prune")
        now_ts = _utc_now_str()
        with self.database.connect() as connection:
            cols = self._memory_columns(connection)
            if "expires_at" not in cols:
                return 0
            if user_id is None:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE user_id IS NULL
                      AND legacy_unclaimed = 0
                      AND expires_at IS NOT NULL
                      AND expires_at <= ?
                    """,
                    (now_ts,),
                )
            else:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE user_id = ?
                      AND expires_at IS NOT NULL
                      AND expires_at <= ?
                    """,
                    (user_id, now_ts),
                )
            connection.commit()
        return int(cursor.rowcount)

    def export_memories(self, category: str | None = None) -> dict[str, Any]:
        """Export non-expired user memories as a user-isolated JSON-serializable structure."""
        rows = self.list_memories(category=category, limit=MAX_MEMORY_LIST_RESULTS)
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(rows),
            "memories": rows,
        }

    def forget(
        self,
        category: str,
        key: str,
    ) -> bool:
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.delete", resource="memory:delete")
        category = self._validate_identifier(
            category,
            "category",
            MAX_MEMORY_CATEGORY_CHARS,
        )
        key = self._validate_identifier(
            key,
            "key",
            MAX_MEMORY_KEY_CHARS,
        )

        with self.database.connect() as connection:
            if user_id is None:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE user_id IS NULL
                      AND legacy_unclaimed = 0
                      AND category = ?
                      AND key = ?
                    """,
                    (category, key),
                )
            else:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE user_id = ?
                      AND category = ?
                      AND key = ?
                    """,
                    (user_id, category, key),
                )
            connection.commit()

        return cursor.rowcount > 0

    def clear_category(
        self,
        category: str,
    ) -> int:
        user_id = self._resolve_user_id()
        self._enforce_permission("memory.delete", resource="memory:delete")
        category = self._validate_identifier(
            category,
            "category",
            MAX_MEMORY_CATEGORY_CHARS,
        )

        with self.database.connect() as connection:
            if user_id is None:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE user_id IS NULL
                      AND legacy_unclaimed = 0
                      AND category = ?
                    """,
                    (category,),
                )
            else:
                cursor = connection.execute(
                    """
                    DELETE FROM memories
                    WHERE user_id = ? AND category = ?
                    """,
                    (user_id, category),
                )
            connection.commit()

        return cursor.rowcount

    def claim_legacy_memories(
        self,
        identity: AuthenticatedIdentity,
        *,
        identity_manager: Any | None = None,
        overwrite: bool = False,
    ) -> int:
        """Explicitly claim legacy v0.4 unclaimed memories into an authenticated user's scope."""
        validated = self._validate_explicit_identity_object(identity)
        mgr = identity_manager if identity_manager is not None else self.identity_manager
        if mgr is not None:
            validated = mgr.validate_session(validated)
        else:
            self._assert_active_user_exists(validated.user_id)

        claimed = 0
        with self.database.connect() as connection:
            legacy_rows = connection.execute(
                """
                SELECT id, category, key
                FROM memories
                WHERE user_id IS NULL AND legacy_unclaimed = 1
                ORDER BY id ASC
                """
            ).fetchall()
            for row in legacy_rows:
                existing = connection.execute(
                    """
                    SELECT id
                    FROM memories
                    WHERE user_id = ? AND category = ? AND key = ?
                    """,
                    (validated.user_id, row["category"], row["key"]),
                ).fetchone()
                if existing is not None:
                    if overwrite:
                        connection.execute(
                            "DELETE FROM memories WHERE id = ?",
                            (existing["id"],),
                        )
                    else:
                        continue
                connection.execute(
                    """
                    UPDATE memories
                    SET user_id = ?, legacy_unclaimed = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND user_id IS NULL AND legacy_unclaimed = 1
                    """,
                    (validated.user_id, row["id"]),
                )
                claimed += 1
            connection.commit()
        return claimed
