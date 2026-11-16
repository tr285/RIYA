"""Multi-user local identity, session validation, and execution-scope isolation."""

from core.identity.context import (
    ExecutionContext,
    ExecutionContextScope,
    get_current_execution_context,
)
from core.identity.errors import (
    AuthenticationError,
    IdentityError,
    InvalidIdentityError,
    MissingIdentityError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from core.identity.manager import (
    ConversationHistoryStore,
    IdentityManager,
    UserPermissionStore,
    UserPreferencesService,
)
from core.identity.models import AuthenticatedIdentity, UserProfile

__all__ = [
    "AuthenticationError",
    "AuthenticatedIdentity",
    "ConversationHistoryStore",
    "ExecutionContext",
    "ExecutionContextScope",
    "IdentityError",
    "IdentityManager",
    "InvalidIdentityError",
    "MissingIdentityError",
    "UserAlreadyExistsError",
    "UserNotFoundError",
    "UserPermissionStore",
    "UserPreferencesService",
    "UserProfile",
    "get_current_execution_context",
]
