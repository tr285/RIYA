"""Offline-first local user identity, authentication, session, and scoped storage management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Callable
from uuid import uuid4

from core.identity.context import ExecutionContext
from core.identity.errors import (
    AuthenticationError,
    IdentityError,
    InvalidIdentityError,
    MissingIdentityError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from core.identity.models import AuthenticatedIdentity, UserProfile
from core.memory.database import MemoryDatabase


USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,31}$")
USER_ID_PATTERN = re.compile(r"^usr_[0-9a-f]{32}$")
DEFAULT_PBKDF2_ITERATIONS = 310_000
MIN_SECRET_LENGTH = 4
MIN_SESSION_TTL_SECONDS = 1.0
MAX_SESSION_TTL_SECONDS = 30 * 24 * 3600.0
VALID_TOOL_POLICIES = frozenset({"allow", "confirm", "deny"})
VALID_PREFERENCE_NAMESPACES = frozenset(
    {"preferences", "app_settings", "automation", "voice"}
)

AuthVerifier = Callable[[sqlite3.Row, str | None], bool]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_secret(
    secret: str,
    *,
    salt_hex: str | None = None,
    iterations: int = DEFAULT_PBKDF2_ITERATIONS,
) -> tuple[str, str, int]:
    if not isinstance(secret, str) or len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(
            f"Credential must be at least {MIN_SECRET_LENGTH} characters."
        )
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        iterations,
    )
    return digest.hex(), salt.hex(), iterations


def _verify_pbkdf2(row: sqlite3.Row, secret: str | None) -> bool:
    if not isinstance(secret, str) or not secret:
        return False
    stored_hash = row["credential_hash"]
    stored_salt = row["credential_salt"]
    iterations = int(row["hash_iterations"] or DEFAULT_PBKDF2_ITERATIONS)
    if not stored_hash or not stored_salt:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        bytes.fromhex(stored_salt),
        iterations,
    ).hex()
    return hmac.compare_digest(candidate, stored_hash)


def _verify_local_unlock(row: sqlite3.Row, _secret: str | None) -> bool:
    return (
        row["auth_method"] == "local_unlock"
        and row["hash_algorithm"] == "local_unlock"
        and int(row["is_active"]) == 1
    )


def _mkdir_private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


class IdentityManager:
    """Manages local user accounts, PBKDF2 credentials, hashed sessions, and isolated paths."""

    def __init__(
        self,
        database: MemoryDatabase | None = None,
        *,
        users_root: str | Path | None = None,
        session_ttl_seconds: float = 86_400.0,
    ):
        self.database = database or MemoryDatabase()
        if users_root is not None:
            self.users_root = Path(users_root).expanduser().resolve()
        else:
            self.users_root = (
                self.database.database_path.parent / "users"
            ).resolve()
        _mkdir_private(self.users_root)

        if (
            not isinstance(session_ttl_seconds, (int, float))
            or session_ttl_seconds < MIN_SESSION_TTL_SECONDS
            or session_ttl_seconds > MAX_SESSION_TTL_SECONDS
        ):
            raise ValueError("Invalid session_ttl_seconds configuration.")
        self.session_ttl_seconds = float(session_ttl_seconds)

        self._verifiers: dict[str, AuthVerifier] = {
            "local_password": _verify_pbkdf2,
            "local_pin": _verify_pbkdf2,
            "local_unlock": _verify_local_unlock,
        }

    def register_auth_verifier(
        self,
        auth_method: str,
        verifier: AuthVerifier,
    ) -> None:
        """Register an offline/local authentication verifier extension."""
        if not isinstance(auth_method, str) or not auth_method.strip():
            raise ValueError("auth_method must be a non-empty string.")
        if not callable(verifier):
            raise TypeError("verifier must be callable.")
        self._verifiers[auth_method.strip().lower()] = verifier

    @staticmethod
    def normalize_username(username: str) -> str:
        if not isinstance(username, str):
            raise ValueError("Username must be a non-empty string.")
        cleaned = username.strip()
        if not USERNAME_PATTERN.match(cleaned):
            raise ValueError(
                "Username must be 2-32 chars using letters, digits, '_', '-', or '.'."
            )
        return cleaned.lower()

    @staticmethod
    def validate_display_name(display_name: str) -> str:
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("Display name must be a non-empty string.")
        cleaned = display_name.strip()
        if len(cleaned) > 64 or any(ord(ch) < 32 for ch in cleaned):
            raise ValueError("Display name contains invalid characters or length.")
        return cleaned

    def user_root(self, user_id: str) -> Path:
        if not isinstance(user_id, str) or not USER_ID_PATTERN.match(user_id):
            raise InvalidIdentityError("Invalid user_id format.")
        return _mkdir_private(self.users_root / user_id)

    def user_workspace(self, user_id: str) -> Path:
        return _mkdir_private(self.user_root(user_id) / "workspace")

    def user_voice_dir(self, user_id: str) -> Path:
        voice_dir = _mkdir_private(self.user_root(user_id) / "voice")
        _mkdir_private(voice_dir / "recordings")
        return voice_dir

    def user_voice_settings_path(self, user_id: str) -> Path:
        return self.user_voice_dir(user_id) / "voice_settings.json"

    def create_user(
        self,
        username: str,
        *,
        password: str | None = None,
        display_name: str | None = None,
        auth_method: str = "local_password",
    ) -> UserProfile:
        """Register a new local user with immutable user_id and isolated directories."""
        normalized_username = self.normalize_username(username)
        resolved_display = self.validate_display_name(display_name or normalized_username)
        method = auth_method.strip().lower() if isinstance(auth_method, str) else ""
        if method not in self._verifiers:
            raise ValueError(f"Unsupported auth_method: {auth_method!r}")

        if method in {"local_password", "local_pin"}:
            if not isinstance(password, str) or not password:
                raise ValueError(
                    f"Credential secret is required for auth_method={method!r}."
                )
            cred_hash, salt_hex, iterations = _hash_secret(password)
            hash_alg = "pbkdf2_sha256"
        elif method == "local_unlock":
            if password is not None and password != "":
                raise ValueError(
                    "Do not supply a password when using auth_method='local_unlock'."
                )
            salt_hex = secrets.token_bytes(16).hex()
            cred_hash = "local_unlock"
            iterations = 0
            hash_alg = "local_unlock"
        else:
            if not isinstance(password, str) or len(password) < MIN_SECRET_LENGTH:
                raise ValueError(
                    f"Credential must be at least {MIN_SECRET_LENGTH} characters."
                )
            cred_hash, salt_hex, iterations = _hash_secret(password)
            hash_alg = "pbkdf2_sha256"

        user_id = f"usr_{uuid4().hex}"
        workspace_dir = str(self.user_workspace(user_id))
        self.user_voice_dir(user_id)
        now_iso = _utc_now().isoformat()

        conn = self.database.connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO users (
                        user_id,
                        username,
                        display_name,
                        auth_method,
                        credential_hash,
                        credential_salt,
                        hash_algorithm,
                        hash_iterations,
                        workspace_dir,
                        is_active,
                        created_at,
                        updated_at,
                        last_login_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL)
                    """,
                    (
                        user_id,
                        normalized_username,
                        resolved_display,
                        method,
                        cred_hash,
                        salt_hex,
                        hash_alg,
                        iterations,
                        workspace_dir,
                        now_iso,
                        now_iso,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise UserAlreadyExistsError(
                f"User '{normalized_username}' already exists."
            ) from exc
        finally:
            conn.close()

        return self.get_profile(user_id)

    def _row_to_profile(self, row: sqlite3.Row) -> UserProfile:
        return UserProfile(
            user_id=str(row["user_id"]),
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            workspace_dir=str(row["workspace_dir"]),
            auth_method=str(row["auth_method"]),
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"]),
            last_login_at=(
                str(row["last_login_at"])
                if row["last_login_at"] is not None
                else None
            ),
        )

    def get_profile(self, user_id: str) -> UserProfile:
        if not isinstance(user_id, str) or not USER_ID_PATTERN.match(user_id):
            raise UserNotFoundError("Invalid or unknown user_id.")
        conn = self.database.connect()
        try:
            row = conn.execute(
                """
                SELECT user_id, username, display_name, workspace_dir,
                       auth_method, is_active, created_at, last_login_at
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                raise UserNotFoundError(f"Unknown user_id: {user_id}")
            return self._row_to_profile(row)
        finally:
            conn.close()

    def get_profile_by_username(self, username: str) -> UserProfile:
        normalized = self.normalize_username(username)
        conn = self.database.connect()
        try:
            row = conn.execute(
                """
                SELECT user_id, username, display_name, workspace_dir,
                       auth_method, is_active, created_at, last_login_at
                FROM users
                WHERE username = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None:
                raise UserNotFoundError(f"Unknown username: {normalized}")
            return self._row_to_profile(row)
        finally:
            conn.close()

    def list_users(self, *, active_only: bool = True) -> list[UserProfile]:
        conn = self.database.connect()
        try:
            if active_only:
                rows = conn.execute(
                    """
                    SELECT user_id, username, display_name, workspace_dir,
                           auth_method, is_active, created_at, last_login_at
                    FROM users
                    WHERE is_active = 1
                    ORDER BY created_at ASC, username ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT user_id, username, display_name, workspace_dir,
                           auth_method, is_active, created_at, last_login_at
                    FROM users
                    ORDER BY created_at ASC, username ASC
                    """
                ).fetchall()
            return [self._row_to_profile(row) for row in rows]
        finally:
            conn.close()

    def authenticate(
        self,
        username: str,
        *,
        password: str | None = None,
        auth_method: str | None = None,
        ttl_seconds: float | None = None,
    ) -> AuthenticatedIdentity:
        """Verify local user credentials and issue a DB-backed opaque session."""
        normalized = self.normalize_username(username)
        conn = self.database.connect()
        try:
            row = conn.execute(
                """
                SELECT *
                FROM users
                WHERE username = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None or not bool(row["is_active"]):
                raise AuthenticationError("Invalid credentials or inactive account.")

            expected_method = str(row["auth_method"])
            if auth_method is not None and auth_method.strip().lower() != expected_method:
                raise AuthenticationError("Authentication method mismatch.")

            verifier = self._verifiers.get(expected_method)
            if verifier is None or not verifier(row, password):
                raise AuthenticationError("Invalid credentials.")

            effective_ttl = (
                self.session_ttl_seconds
                if ttl_seconds is None
                else float(ttl_seconds)
            )
            if (
                effective_ttl < MIN_SESSION_TTL_SECONDS
                or effective_ttl > MAX_SESSION_TTL_SECONDS
            ):
                raise ValueError("Invalid session ttl_seconds.")

            now = _utc_now()
            now_iso = now.isoformat()
            expires_iso = (now + timedelta(seconds=effective_ttl)).isoformat()
            session_id = f"ses_{uuid4().hex}"
            raw_token = secrets.token_urlsafe(32)
            token_hash = _hash_session_token(raw_token)
            user_id = str(row["user_id"])
            workspace_dir = str(self.user_workspace(user_id))
            self.user_voice_dir(user_id)

            with conn:
                conn.execute(
                    """
                    INSERT INTO user_sessions (
                        session_id,
                        user_id,
                        token_hash,
                        auth_method,
                        created_at,
                        expires_at,
                        revoked_at,
                        last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        session_id,
                        user_id,
                        token_hash,
                        expected_method,
                        now_iso,
                        expires_iso,
                        now_iso,
                    ),
                )
                conn.execute(
                    """
                    UPDATE users
                    SET last_login_at = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (now_iso, now_iso, user_id),
                )

            return AuthenticatedIdentity(
                user_id=user_id,
                username=str(row["username"]),
                display_name=str(row["display_name"]),
                session_id=session_id,
                session_token=raw_token,
                workspace_dir=workspace_dir,
                auth_method=expected_method,
                expires_at=expires_iso,
            )
        finally:
            conn.close()

    def validate_session(
        self,
        identity: Any,
    ) -> AuthenticatedIdentity:
        """Fail closed unless identity holds a live, unrevoked, unexpired DB session."""
        if identity is None:
            raise MissingIdentityError("Authenticated user identity is required.")
        if isinstance(identity, UserProfile):
            raise InvalidIdentityError(
                "A UserProfile is not an authenticated identity."
            )
        if not isinstance(identity, AuthenticatedIdentity):
            raise InvalidIdentityError(
                "Invalid identity object; expected AuthenticatedIdentity."
            )
        if (
            not isinstance(identity.user_id, str)
            or not isinstance(identity.session_id, str)
            or not isinstance(identity.session_token, str)
            or not identity.session_token
        ):
            raise InvalidIdentityError("Malformed authenticated identity.")

        conn = self.database.connect()
        try:
            row = conn.execute(
                """
                SELECT
                    s.session_id,
                    s.user_id,
                    s.token_hash,
                    s.auth_method,
                    s.expires_at,
                    s.revoked_at,
                    u.username,
                    u.display_name,
                    u.workspace_dir,
                    u.is_active
                FROM user_sessions AS s
                JOIN users AS u ON u.user_id = s.user_id
                WHERE s.session_id = ? AND s.user_id = ?
                """,
                (identity.session_id, identity.user_id),
            ).fetchone()
            if row is None:
                raise InvalidIdentityError("Unknown or invalid user session.")
            if not bool(row["is_active"]):
                raise InvalidIdentityError("User account is inactive.")
            if row["revoked_at"] is not None:
                raise InvalidIdentityError("User session has been revoked.")

            expected_hash = str(row["token_hash"])
            candidate_hash = _hash_session_token(identity.session_token)
            if not hmac.compare_digest(candidate_hash, expected_hash):
                raise InvalidIdentityError("Session token verification failed.")

            expires_at = datetime.fromisoformat(str(row["expires_at"]))
            now = _utc_now()
            if expires_at <= now:
                raise InvalidIdentityError("User session has expired.")

            with conn:
                conn.execute(
                    """
                    UPDATE user_sessions
                    SET last_seen_at = ?
                    WHERE session_id = ?
                    """,
                    (now.isoformat(), identity.session_id),
                )

            return AuthenticatedIdentity(
                user_id=str(row["user_id"]),
                username=str(row["username"]),
                display_name=str(row["display_name"]),
                session_id=str(row["session_id"]),
                session_token=identity.session_token,
                workspace_dir=str(self.user_workspace(str(row["user_id"]))),
                auth_method=str(row["auth_method"]),
                expires_at=str(row["expires_at"]),
            )
        finally:
            conn.close()

    def authenticate_token(self, session_token: str) -> AuthenticatedIdentity:
        """Resolve and validate an opaque bearer session token without trusting user hints."""
        if not isinstance(session_token, str) or not session_token.strip():
            raise MissingIdentityError("Session token is required.")

        token_hash = _hash_session_token(session_token)
        conn = self.database.connect()
        try:
            row = conn.execute(
                """
                SELECT
                    s.session_id,
                    s.user_id,
                    s.auth_method,
                    s.expires_at,
                    s.revoked_at,
                    u.username,
                    u.display_name,
                    u.workspace_dir,
                    u.is_active
                FROM user_sessions AS s
                JOIN users AS u ON u.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None or not bool(row["is_active"]) or row["revoked_at"] is not None:
                raise InvalidIdentityError("Invalid or revoked session token.")

            expires_at = datetime.fromisoformat(str(row["expires_at"]))
            if expires_at <= _utc_now():
                raise InvalidIdentityError("User session has expired.")

            return AuthenticatedIdentity(
                user_id=str(row["user_id"]),
                username=str(row["username"]),
                display_name=str(row["display_name"]),
                session_id=str(row["session_id"]),
                session_token=session_token,
                workspace_dir=str(self.user_workspace(str(row["user_id"]))),
                auth_method=str(row["auth_method"]),
                expires_at=str(row["expires_at"]),
            )
        finally:
            conn.close()

    def logout(self, identity: AuthenticatedIdentity) -> bool:
        """Revoke the active session token immediately."""
        if not isinstance(identity, AuthenticatedIdentity):
            raise InvalidIdentityError("Expected AuthenticatedIdentity.")
        token_hash = _hash_session_token(identity.session_token)
        now_iso = _utc_now().isoformat()
        conn = self.database.connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE user_sessions
                    SET revoked_at = ?
                    WHERE session_id = ?
                      AND user_id = ?
                      AND token_hash = ?
                      AND revoked_at IS NULL
                    """,
                    (now_iso, identity.session_id, identity.user_id, token_hash),
                )
            return cursor.rowcount > 0
        finally:
            conn.close()

    def revoke_all_user_sessions(self, identity: AuthenticatedIdentity) -> int:
        validated = self.validate_session(identity)
        now_iso = _utc_now().isoformat()
        conn = self.database.connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE user_sessions
                    SET revoked_at = ?
                    WHERE user_id = ? AND revoked_at IS NULL
                    """,
                    (now_iso, validated.user_id),
                )
            return int(cursor.rowcount)
        finally:
            conn.close()

    def update_profile(
        self,
        identity: AuthenticatedIdentity,
        *,
        display_name: str | None = None,
        **forbidden_fields: Any,
    ) -> UserProfile:
        """Update mutable profile metadata while enforcing immutable user_id/username."""
        if forbidden_fields:
            raise IdentityError(
                f"Immutable or unsupported profile fields cannot be modified: "
                f"{sorted(forbidden_fields)}"
            )
        validated = self.validate_session(identity)
        if display_name is None:
            return self.get_profile(validated.user_id)

        cleaned = self.validate_display_name(display_name)
        now_iso = _utc_now().isoformat()
        conn = self.database.connect()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE users
                    SET display_name = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (cleaned, now_iso, validated.user_id),
                )
        finally:
            conn.close()
        return self.get_profile(validated.user_id)

    def change_password(
        self,
        identity: AuthenticatedIdentity,
        *,
        current_password: str | None,
        new_password: str,
    ) -> None:
        validated = self.validate_session(identity)
        conn = self.database.connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (validated.user_id,),
            ).fetchone()
            if row is None:
                raise UserNotFoundError("User not found.")
            method = str(row["auth_method"])
            if method in {"local_password", "local_pin"}:
                if not _verify_pbkdf2(row, current_password):
                    raise AuthenticationError("Current credential verification failed.")
            cred_hash, salt_hex, iterations = _hash_secret(new_password)
            now_iso = _utc_now().isoformat()
            with conn:
                conn.execute(
                    """
                    UPDATE users
                    SET auth_method = 'local_password',
                        credential_hash = ?,
                        credential_salt = ?,
                        hash_algorithm = 'pbkdf2_sha256',
                        hash_iterations = ?,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (cred_hash, salt_hex, iterations, now_iso, validated.user_id),
                )
        finally:
            conn.close()

    def build_execution_context(
        self,
        identity: AuthenticatedIdentity,
        *,
        memory_manager: Any | None = None,
    ) -> ExecutionContext:
        validated = self.validate_session(identity)
        workspace = self.user_workspace(validated.user_id)
        voice_dir = self.user_voice_dir(validated.user_id)
        return ExecutionContext(
            user_id=validated.user_id,
            session_id=validated.session_id,
            workspace_dir=workspace,
            allowed_roots=(workspace,),
            voice_dir=voice_dir,
            identity=validated,
            memory_manager=memory_manager,
        )


class UserPreferencesService:
    """User-isolated storage for personal preferences, app settings, automation, and voice config."""

    def __init__(
        self,
        identity_manager: IdentityManager,
        *,
        permission_manager: Any = None,
    ):
        self.identity_manager = identity_manager
        self.database = identity_manager.database
        self.permission_manager = permission_manager

    def _enforce_permission(
        self,
        validated: AuthenticatedIdentity,
        resource: str,
    ) -> None:
        if self.permission_manager is not None:
            res = self.permission_manager.evaluate(
                validated,
                "personal.preferences",
                resource_or_target=resource,
                actor="preferences_service",
            )
            if res.blocked:
                raise PermissionError(res.reason)

    @staticmethod
    def _validate_namespace(namespace: str) -> str:
        if namespace not in VALID_PREFERENCE_NAMESPACES:
            raise ValueError(
                f"Invalid preference namespace {namespace!r}; "
                f"expected one of {sorted(VALID_PREFERENCE_NAMESPACES)}"
            )
        return namespace

    @staticmethod
    def _validate_key(pref_key: str) -> str:
        if not isinstance(pref_key, str) or not pref_key.strip():
            raise ValueError("Preference key must be a non-empty string.")
        cleaned = pref_key.strip()
        if len(cleaned) > 128 or any(ord(ch) < 32 for ch in cleaned):
            raise ValueError("Invalid preference key.")
        return cleaned

    def set_preference(
        self,
        identity: AuthenticatedIdentity,
        namespace: str,
        pref_key: str,
        value: Any,
    ) -> None:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"preferences:{namespace}:{pref_key}")
        ns = self._validate_namespace(namespace)
        key = self._validate_key(pref_key)
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        now_iso = _utc_now().isoformat()

        conn = self.database.connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO user_preferences (
                        user_id, namespace, pref_key, pref_value, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, namespace, pref_key)
                    DO UPDATE SET
                        pref_value = excluded.pref_value,
                        updated_at = excluded.updated_at
                    """,
                    (validated.user_id, ns, key, serialized, now_iso),
                )
        finally:
            conn.close()

    def get_preference(
        self,
        identity: AuthenticatedIdentity,
        namespace: str,
        pref_key: str,
        default: Any = None,
    ) -> Any:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"preferences:{namespace}:{pref_key}")
        ns = self._validate_namespace(namespace)
        key = self._validate_key(pref_key)

        conn = self.database.connect()
        try:
            row = conn.execute(
                """
                SELECT pref_value
                FROM user_preferences
                WHERE user_id = ? AND namespace = ? AND pref_key = ?
                """,
                (validated.user_id, ns, key),
            ).fetchone()
            if row is None:
                return default
            return json.loads(str(row["pref_value"]))
        finally:
            conn.close()

    def list_preferences(
        self,
        identity: AuthenticatedIdentity,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"preferences:{namespace or 'all'}")
        conn = self.database.connect()
        try:
            if namespace is not None:
                ns = self._validate_namespace(namespace)
                rows = conn.execute(
                    """
                    SELECT pref_key, pref_value
                    FROM user_preferences
                    WHERE user_id = ? AND namespace = ?
                    ORDER BY pref_key ASC
                    """,
                    (validated.user_id, ns),
                ).fetchall()
                return {
                    str(row["pref_key"]): json.loads(str(row["pref_value"]))
                    for row in rows
                }

            rows = conn.execute(
                """
                SELECT namespace, pref_key, pref_value
                FROM user_preferences
                WHERE user_id = ?
                ORDER BY namespace ASC, pref_key ASC
                """,
                (validated.user_id,),
            ).fetchall()
            grouped: dict[str, dict[str, Any]] = {}
            for row in rows:
                ns = str(row["namespace"])
                grouped.setdefault(ns, {})[str(row["pref_key"])] = json.loads(
                    str(row["pref_value"])
                )
            return grouped
        finally:
            conn.close()

    def delete_preference(
        self,
        identity: AuthenticatedIdentity,
        namespace: str,
        pref_key: str,
    ) -> bool:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"preferences:{namespace}:{pref_key}")
        ns = self._validate_namespace(namespace)
        key = self._validate_key(pref_key)
        conn = self.database.connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    DELETE FROM user_preferences
                    WHERE user_id = ? AND namespace = ? AND pref_key = ?
                    """,
                    (validated.user_id, ns, key),
                )
            return cursor.rowcount > 0
        finally:
            conn.close()


class UserPermissionStore:
    """Per-user tool execution authorization policies scoped strictly by user_id."""

    def __init__(self, identity_manager: IdentityManager):
        self.identity_manager = identity_manager
        self.database = identity_manager.database

    def set_tool_policy(
        self,
        identity: AuthenticatedIdentity,
        tool_name: str,
        policy: str,
        *,
        granted_by: str | None = None,
    ) -> None:
        validated = self.identity_manager.validate_session(identity)
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be a non-empty string.")
        cleaned_tool = tool_name.strip()
        if policy not in VALID_TOOL_POLICIES:
            raise ValueError(
                f"Invalid tool policy {policy!r}; expected one of {sorted(VALID_TOOL_POLICIES)}"
            )
        now_iso = _utc_now().isoformat()

        conn = self.database.connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO user_permissions (
                        user_id, tool_name, policy, granted_by, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, tool_name)
                    DO UPDATE SET
                        policy = excluded.policy,
                        granted_by = excluded.granted_by,
                        updated_at = excluded.updated_at
                    """,
                    (
                        validated.user_id,
                        cleaned_tool,
                        policy,
                        granted_by or validated.user_id,
                        now_iso,
                    ),
                )
        finally:
            conn.close()

    def get_tool_policy(
        self,
        identity: AuthenticatedIdentity,
        tool_name: str,
    ) -> str | None:
        validated = self.identity_manager.validate_session(identity)
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None
        conn = self.database.connect()
        try:
            row = conn.execute(
                """
                SELECT policy
                FROM user_permissions
                WHERE user_id = ? AND tool_name = ?
                """,
                (validated.user_id, tool_name.strip()),
            ).fetchone()
            return str(row["policy"]) if row is not None else None
        finally:
            conn.close()

    def list_tool_policies(
        self,
        identity: AuthenticatedIdentity,
    ) -> dict[str, str]:
        validated = self.identity_manager.validate_session(identity)
        conn = self.database.connect()
        try:
            rows = conn.execute(
                """
                SELECT tool_name, policy
                FROM user_permissions
                WHERE user_id = ?
                ORDER BY tool_name ASC
                """,
                (validated.user_id,),
            ).fetchall()
            return {str(row["tool_name"]): str(row["policy"]) for row in rows}
        finally:
            conn.close()


class ConversationHistoryStore:
    """SQLite-backed conversation history strictly isolated per (user_id, session_id)."""

    def __init__(
        self,
        identity_manager: IdentityManager,
        *,
        permission_manager: Any = None,
    ):
        self.identity_manager = identity_manager
        self.database = identity_manager.database
        self.permission_manager = permission_manager

    def _enforce_permission(
        self,
        validated: AuthenticatedIdentity,
        resource: str,
    ) -> None:
        if self.permission_manager is not None:
            res = self.permission_manager.evaluate(
                validated,
                "personal.history",
                resource_or_target=resource,
                actor="history_store",
            )
            if res.blocked:
                raise PermissionError(res.reason)

    def append_message(
        self,
        identity: AuthenticatedIdentity,
        role: str,
        content: str,
        *,
        session_id: str | None = None,
    ) -> int:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"history:append:{role}")
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"Unsupported conversation role: {role!r}")
        if not isinstance(content, str):
            raise ValueError("Conversation content must be a string.")
        effective_session = session_id or validated.session_id
        now_iso = _utc_now().isoformat()

        conn = self.database.connect()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO conversation_history (
                        user_id, session_id, role, content, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (validated.user_id, effective_session, role, content, now_iso),
                )
                return int(cursor.lastrowid)
        finally:
            conn.close()

    def get_history(
        self,
        identity: AuthenticatedIdentity,
        *,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, str]]:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "history:read")
        safe_limit = max(1, min(int(limit), 500))
        conn = self.database.connect()
        try:
            if session_id is not None:
                rows = conn.execute(
                    """
                    SELECT role, content, session_id, created_at
                    FROM conversation_history
                    WHERE user_id = ? AND session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (validated.user_id, session_id, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT role, content, session_id, created_at
                    FROM conversation_history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (validated.user_id, safe_limit),
                ).fetchall()
            return [
                {
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                    "session_id": str(row["session_id"]),
                    "created_at": str(row["created_at"]),
                }
                for row in reversed(rows)
            ]
        finally:
            conn.close()

    def clear_history(
        self,
        identity: AuthenticatedIdentity,
        *,
        session_id: str | None = None,
    ) -> int:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "history:clear")
        conn = self.database.connect()
        try:
            with conn:
                if session_id is not None:
                    cursor = conn.execute(
                        """
                        DELETE FROM conversation_history
                        WHERE user_id = ? AND session_id = ?
                        """,
                        (validated.user_id, session_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        DELETE FROM conversation_history
                        WHERE user_id = ?
                        """,
                        (validated.user_id,),
                    )
            return int(cursor.rowcount)
        finally:
            conn.close()
