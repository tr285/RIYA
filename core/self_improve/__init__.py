"""Controlled, user-authorized self-improvement pipeline for RIYA."""

from core.self_improve.analyzer import (
    PROTECTED_RELATIVE_PREFIXES,
    StaticSecurityReport,
    analyze_proposed_code,
    resolve_safe_repo_target,
)
from core.self_improve.manager import SelfImprovementManager, SelfImprovementProposal
from core.self_improve.verifier import SandboxVerificationResult, run_isolated_verification

__all__ = [
    "PROTECTED_RELATIVE_PREFIXES",
    "SandboxVerificationResult",
    "SelfImprovementManager",
    "SelfImprovementProposal",
    "StaticSecurityReport",
    "analyze_proposed_code",
    "resolve_safe_repo_target",
    "run_isolated_verification",
]
