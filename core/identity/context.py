"""Thread/async-safe execution context binding active user identity, workspace, and memory."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.identity.models import AuthenticatedIdentity


@dataclass(frozen=True)
class ExecutionContext:
    """Trusted runtime scope injected by IdentityManager / ToolExecutor / RIYAAssistant."""

    user_id: str
    session_id: str
    workspace_dir: Path
    allowed_roots: tuple[Path, ...]
    voice_dir: Path
    identity: AuthenticatedIdentity | None = None
    memory_manager: Any | None = None


_CURRENT_EXECUTION_CONTEXT: ContextVar[ExecutionContext | None] = ContextVar(
    "riya_execution_context",
    default=None,
)


def get_current_execution_context() -> ExecutionContext | None:
    """Return the active trusted user execution context, if one is bound."""
    return _CURRENT_EXECUTION_CONTEXT.get()


class ExecutionContextScope:
    """Context manager that binds an isolated ExecutionContext for one execution."""

    def __init__(self, context: ExecutionContext):
        self.context = context
        self._token: Token[ExecutionContext | None] | None = None

    def __enter__(self) -> ExecutionContext:
        self._token = _CURRENT_EXECUTION_CONTEXT.set(self.context)
        return self.context

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._token is not None:
            _CURRENT_EXECUTION_CONTEXT.reset(self._token)
            self._token = None
