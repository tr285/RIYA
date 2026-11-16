"""RIYA first-time setup, onboarding, AI/voice readiness, and error recovery package."""

from core.onboarding.ai_setup import (
    DEFAULT_MODEL_FILENAME,
    AIEngineStatus,
    LocalAISetupService,
    ModelCandidate,
    ModelVerificationResult,
    discover_local_models,
    get_free_disk_bytes,
    get_system_ram_bytes,
    inspect_native_library,
    validate_gguf_file,
)
from core.onboarding.diagnostics import (
    CheckSeverity,
    DiagnosticCheck,
    ReadinessDiagnostics,
    ReadinessReport,
)
from core.onboarding.manager import (
    PERMISSION_ONBOARDING_GUIDE,
    PRIVACY_NOTICE_SUMMARY,
    OnboardingCoordinator,
    OnboardingWelcomeInfo,
)
from core.onboarding.state import (
    ONBOARDING_STEPS,
    InstallState,
    OnboardingDetectionResult,
    OnboardingStateStore,
    UserOnboardingRecord,
    default_checkpoint_path,
    detect_install_state,
)
from core.onboarding.voice_setup import (
    VoiceSetupService,
    VoiceSubsystemStatus,
    VoiceTestResult,
    check_stt_local_availability,
    honest_language_options,
    load_user_voice_config_safely,
)

__all__ = [
    "AIEngineStatus",
    "CheckSeverity",
    "DEFAULT_MODEL_FILENAME",
    "DiagnosticCheck",
    "InstallState",
    "LocalAISetupService",
    "ModelCandidate",
    "ModelVerificationResult",
    "ONBOARDING_STEPS",
    "OnboardingCoordinator",
    "OnboardingDetectionResult",
    "OnboardingStateStore",
    "OnboardingWelcomeInfo",
    "PERMISSION_ONBOARDING_GUIDE",
    "PRIVACY_NOTICE_SUMMARY",
    "ReadinessDiagnostics",
    "ReadinessReport",
    "UserOnboardingRecord",
    "VoiceSetupService",
    "VoiceSubsystemStatus",
    "VoiceTestResult",
    "check_stt_local_availability",
    "default_checkpoint_path",
    "detect_install_state",
    "discover_local_models",
    "get_free_disk_bytes",
    "get_system_ram_bytes",
    "honest_language_options",
    "inspect_native_library",
    "load_user_voice_config_safely",
    "validate_gguf_file",
]
