"""One-time, action-bound confirmations for privileged RIYA tools."""

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import secrets
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class ConfirmationGrant:
    """An unforgeable, short-lived approval bound to one action and user session."""

    fingerprint: str
    expires_at: float
    user_id: str | None = None
    session_id: str | None = None
    tool_name: str = ""
    permission_key: str = ""


class ConfirmationManager:
    """Issue and consume one-time confirmations without persisting them."""

    def __init__(
        self,
        ttl_seconds: float = 120,
        clock: Callable[[], float] = time.monotonic,
        max_grants: int = 256,
    ):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive.")
        if max_grants <= 0:
            raise ValueError("max_grants must be positive.")

        self.ttl_seconds = ttl_seconds
        self.max_grants = max_grants
        self._clock = clock
        self._lock = threading.Lock()
        self._grants: dict[str, ConfirmationGrant] = {}

    @property
    def pending_count(self) -> int:
        """Return the number of currently stored confirmation grants."""

        with self._lock:
            return len(self._grants)

    def _purge_expired(self, now: float) -> None:
        """Remove all expired grants. Caller must hold self._lock."""

        expired = [
            token
            for token, grant in self._grants.items()
            if now >= grant.expires_at
        ]
        for token in expired:
            self._grants.pop(token, None)

    def issue(
        self,
        tool_name: str,
        parameters: dict,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        permission_key: str = "",
    ) -> str:
        """Create an approval bound to an exact tool name, parameters, and user session."""

        fingerprint = self._fingerprint(
            tool_name,
            parameters,
            user_id=user_id,
            session_id=session_id,
        )
        token = secrets.token_urlsafe(32)

        with self._lock:
            now = self._clock()
            self._purge_expired(now)

            while len(self._grants) >= self.max_grants:
                oldest = next(iter(self._grants))
                self._grants.pop(oldest, None)

            self._grants[token] = ConfirmationGrant(
                fingerprint=fingerprint,
                expires_at=now + self.ttl_seconds,
                user_id=user_id,
                session_id=session_id,
                tool_name=str(tool_name or ""),
                permission_key=str(permission_key or ""),
            )

        return token

    def consume(
        self,
        token: str | None,
        tool_name: str,
        parameters: dict,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[bool, str]:
        """Consume a token once, accepting it only for its original action and user session."""

        if not isinstance(token, str) or not token:
            return False, "A valid confirmation token is required."

        with self._lock:
            now = self._clock()
            # Pop before validation so failed mismatch attempts cannot be replayed.
            grant = self._grants.pop(token, None)
            self._purge_expired(now)

        if grant is None:
            return False, "Confirmation token is invalid, expired, or already used."

        if now >= grant.expires_at:
            return False, "Confirmation token has expired."

        if grant.user_id != user_id or grant.session_id != session_id:
            return False, "Confirmation token does not belong to the active user session."

        fingerprint = self._fingerprint(
            tool_name,
            parameters,
            user_id=user_id,
            session_id=session_id,
        )

        if not hmac.compare_digest(grant.fingerprint, fingerprint):
            return False, "Confirmation token does not authorize this action."

        return True, "Confirmation accepted."

    def cancel(self, token: str | None) -> None:
        """Invalidate a pending approval without executing its action."""

        if isinstance(token, str):
            with self._lock:
                self._grants.pop(token, None)
                self._purge_expired(self._clock())

    def cancel_for_session(
        self,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Invalidate all pending approvals bound to a user or session."""

        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            targets = [
                token
                for token, grant in self._grants.items()
                if (user_id is None or grant.user_id == user_id)
                and (session_id is None or grant.session_id == session_id)
            ]
            for token in targets:
                self._grants.pop(token, None)
            return len(targets)

    def cancel_matching(
        self,
        *,
        user_id: str | None = None,
        tool_names: set[str] | frozenset[str] | tuple[str, ...] | None = None,
        permission_key: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """Invalidate pending confirmations matching user, session, permission, or tools."""

        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            targets = []
            for token, grant in self._grants.items():
                if user_id is not None and grant.user_id != user_id:
                    continue
                if session_id is not None and grant.session_id != session_id:
                    continue
                if tool_names is not None or permission_key is not None:
                    matches_tool = (
                        tool_names is not None and grant.tool_name in tool_names
                    )
                    matches_key = (
                        permission_key is not None
                        and grant.permission_key == permission_key
                    )
                    if not (matches_tool or matches_key):
                        continue
                targets.append(token)
            for token in targets:
                self._grants.pop(token, None)
            return len(targets)

    @staticmethod
    def _fingerprint(
        tool_name: str,
        parameters: dict,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        payload = json.dumps(
            {
                "tool": tool_name,
                "parameters": parameters,
                "user_id": user_id,
                "session_id": session_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return sha256(payload.encode("utf-8")).hexdigest()
