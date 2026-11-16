"""Exceptions for RIYA's multi-user identity and isolation layer."""


class IdentityError(PermissionError):
    """Base error for identity, authentication, and authorization failures."""


class MissingIdentityError(IdentityError):
    """Raised when an operation requires an authenticated user but none was supplied."""


class InvalidIdentityError(IdentityError):
    """Raised when a supplied identity or session token is invalid, revoked, or expired."""


class AuthenticationError(IdentityError):
    """Raised when user credential verification fails."""


class UserAlreadyExistsError(ValueError):
    """Raised when attempting to register a duplicate username."""


class UserNotFoundError(LookupError):
    """Raised when a requested user account does not exist."""
