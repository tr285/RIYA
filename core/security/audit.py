"""Privacy-conscious append-only SQLite audit logging for RIYA permissions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import threading
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.identity.manager import AuthenticatedIdentity, IdentityManager
    from core.memory.database import MemoryDatabase


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]+")
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(token|password|passwd|secret|api[_-]?key|bearer|authorization)\s*[:=]\s*\S+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]+")
_TOKEN_PREFIX_RE = re.compile(r"\b(?:ses_|riya_|sk-)[A-Za-z0-9._\-]{8,}")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_sensitive_text(text: str) -> str:
    cleaned = _CONTROL_CHAR_RE.sub(" ", str(text)).strip()
    cleaned = _BEARER_RE.sub("Bearer [REDACTED]", cleaned)
    cleaned = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", cleaned)
    cleaned = _TOKEN_PREFIX_RE.sub("[REDACTED_TOKEN]", cleaned)
    return cleaned


def sanitize_resource_label(resource_or_tool: str, *, max_len: int = 120) -> str:
    """Return a bounded, non-secret resource or tool label for audit storage."""
    if not isinstance(resource_or_tool, str) or not resource_or_tool.strip():
        return "unknown"
    text = _redact_sensitive_text(resource_or_tool)

    # Collapse explicit filesystem paths to safe prefix + basename to avoid leaking
    # user-sensitive directory structures or filenames carrying embedded text.
    if text.startswith("path:"):
        raw_path = text[5:].strip()
        name = Path(raw_path).name or raw_path
        text = f"path:{name}"
    elif text.startswith("dir:"):
        raw_dir = text[4:].strip()
        name = Path(raw_dir).name or raw_dir
        text = f"dir:{name}"

    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def sanitize_reason(reason: str, *, max_len: int = 200) -> str:
    """Normalize and redact audit reason strings before persistence."""
    if not isinstance(reason, str) or not reason.strip():
        return "unspecified"
    cleaned = _redact_sensitive_text(reason)
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3] + "..."
    return cleaned


@dataclass(frozen=True)
class PermissionAuditRecord:
    audit_id: int
    user_id: str | None
    session_id: str | None
    permission_category: str
    permission_key: str
    resource_or_tool: str
    decision: str
    reason: str
    actor: str
    created_at: str


class PermissionAuditLogger:
    """Append-only permission audit writer & user-scoped reader."""

    def __init__(
        self,
        database: "MemoryDatabase | IdentityManager",
        *,
        identity_manager: "IdentityManager | None" = None,
    ):
        if hasattr(database, "database") and hasattr(database, "validate_session"):
            # Caller passed an IdentityManager directly.
            self.identity_manager: "IdentityManager | None" = database  # type: ignore[assignment]
            self.database: "MemoryDatabase" = database.database  # type: ignore[assignment]
        else:
            self.identity_manager = identity_manager
            self.database = database  # type: ignore[assignment]
        self._lock = threading.RLock()

    def log_event(
        self,
        *,
        user_id: str | None,
        session_id: str | None = None,
        permission_category: str,
        permission_key: str,
        resource_or_tool: str,
        decision: str,
        reason: str,
        actor: str = "system",
    ) -> int:
        """Append a sanitized permission audit record to SQLite."""
        category = sanitize_resource_label(permission_category, max_len=48)
        perm_key = sanitize_resource_label(permission_key, max_len=64)
        resource_label = sanitize_resource_label(resource_or_tool, max_len=120)
        safe_decision = sanitize_resource_label(decision, max_len=32)
        safe_reason = sanitize_reason(reason, max_len=200)
        safe_actor = sanitize_resource_label(actor, max_len=48)
        now_iso = _utc_now_iso()

        with self._lock:
            conn = self.database.connect()
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO permission_audit_log (
                            user_id,
                            session_id,
                            permission_category,
                            permission_key,
                            resource_or_tool,
                            decision,
                            reason,
                            actor,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id,
                            session_id,
                            category,
                            perm_key,
                            resource_label,
                            safe_decision,
                            safe_reason,
                            safe_actor,
                            now_iso,
                        ),
                    )
                    return int(cursor.lastrowid or 0)
            finally:
                conn.close()

    def list_audit_events(
        self,
        identity: "AuthenticatedIdentity",
        *,
        limit: int = 50,
        permission_key: str | None = None,
        category: str | None = None,
    ) -> list[PermissionAuditRecord]:
        """Return recent audit records strictly scoped to the validated user."""
        if self.identity_manager is None:
            raise RuntimeError(
                "PermissionAuditLogger requires identity_manager to read user audit logs."
            )
        validated = self.identity_manager.validate_session(identity)
        bounded_limit = max(1, min(int(limit), 500))

        clauses = ["user_id = ?"]
        params: list[Any] = [validated.user_id]
        if permission_key is not None and permission_key.strip():
            clauses.append("permission_key = ?")
            params.append(permission_key.strip())
        if category is not None and category.strip():
            clauses.append("permission_category = ?")
            params.append(category.strip())

        where_sql = " AND ".join(clauses)
        params.append(bounded_limit)

        with self._lock:
            conn = self.database.connect()
            try:
                rows = conn.execute(
                    f"""
                    SELECT
                        audit_id,
                        user_id,
                        session_id,
                        permission_category,
                        permission_key,
                        resource_or_tool,
                        decision,
                        reason,
                        actor,
                        created_at
                    FROM permission_audit_log
                    WHERE {where_sql}
                    ORDER BY audit_id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
            finally:
                conn.close()

        return [
            PermissionAuditRecord(
                audit_id=int(row["audit_id"]),
                user_id=str(row["user_id"]) if row["user_id"] is not None else None,
                session_id=(
                    str(row["session_id"]) if row["session_id"] is not None else None
                ),
                permission_category=str(row["permission_category"]),
                permission_key=str(row["permission_key"]),
                resource_or_tool=str(row["resource_or_tool"]),
                decision=str(row["decision"]),
                reason=str(row["reason"]),
                actor=str(row["actor"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]
