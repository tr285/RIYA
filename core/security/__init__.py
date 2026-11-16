"""Security, permission, audit, and confirmation controls for RIYA."""

from core.security.audit import (
    PermissionAuditLogger,
    PermissionAuditRecord,
    sanitize_reason,
    sanitize_resource_label,
)
from core.security.confirmation import ConfirmationGrant, ConfirmationManager
from core.security.manager import SecurityDecision, SecurityManager
from core.security.permissions import (
    PERMISSION_SPECS,
    PermissionAuthorityError,
    PermissionConflictError,
    PermissionDecision,
    PermissionDeniedError,
    PermissionErrorBase,
    PermissionEvaluation,
    PermissionGrant,
    PermissionLifetime,
    PermissionManager,
    PermissionSpec,
    ToolPermissionRequirement,
    normalize_resource_scope,
    resolve_permissions_for_tool,
)

__all__ = [
    "ConfirmationGrant",
    "ConfirmationManager",
    "PERMISSION_SPECS",
    "PermissionAuditLogger",
    "PermissionAuditRecord",
    "PermissionAuthorityError",
    "PermissionConflictError",
    "PermissionDecision",
    "PermissionDeniedError",
    "PermissionErrorBase",
    "PermissionEvaluation",
    "PermissionGrant",
    "PermissionLifetime",
    "PermissionManager",
    "PermissionSpec",
    "SecurityDecision",
    "SecurityManager",
    "ToolPermissionRequirement",
    "normalize_resource_scope",
    "resolve_permissions_for_tool",
    "sanitize_reason",
    "sanitize_resource_label",
]
