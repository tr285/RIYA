"""Identity data models separating passive UserProfile from AuthenticatedIdentity."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserProfile:
    """Public/passive user profile metadata.

    Security invariant: a UserProfile is NEVER an authenticated identity and
    must never be accepted by IdentityManager.validate_session() or ToolExecutor.
    """

    user_id: str
    username: str
    display_name: str
    workspace_dir: str
    auth_method: str
    is_active: bool
    created_at: str
    last_login_at: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return False


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Validated authenticated user identity bound to an active local session."""

    user_id: str
    username: str
    display_name: str
    session_id: str
    session_token: str
    workspace_dir: str
    auth_method: str
    expires_at: str

    def __repr__(self) -> str:
        return (
            f"AuthenticatedIdentity(user_id={self.user_id!r}, "
            f"username={self.username!r}, session_id={self.session_id!r}, "
            f"auth_method={self.auth_method!r}, session_token='***')"
        )
