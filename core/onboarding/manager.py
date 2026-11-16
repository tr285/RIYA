"""Unified Onboarding & Session Coordinator connecting identity, permissions, AI, voice, and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from core.agent.assistant import RIYAAssistant
from core.config.settings import APP_NAME, APP_VERSION, OFFLINE_FIRST, PROJECT_ROOT
from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity, UserProfile
from core.memory.database import DATABASE_PATH, MemoryDatabase
from core.onboarding.ai_setup import (
    AIEngineStatus,
    LocalAISetupService,
    ModelVerificationResult,
)
from core.onboarding.diagnostics import (
    ReadinessDiagnostics,
    ReadinessReport,
)
from core.onboarding.state import (
    ONBOARDING_STEPS,
    InstallState,
    OnboardingDetectionResult,
    OnboardingStateStore,
    default_checkpoint_path,
    detect_install_state,
)
from core.onboarding.voice_setup import (
    VoiceSetupService,
    VoiceSubsystemStatus,
    VoiceTestResult,
    load_user_voice_config_safely,
)
from core.security.permissions import PermissionManager
from core.voice.config import VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession


PRIVACY_NOTICE_SUMMARY = (
    f"{APP_NAME} — Your Personal AI Assistant\n"
    "\"Your assistant. Your data. Your control.\"\n\n"
    "How RIYA protects your privacy:\n"
    "• Works offline for supported core features (local SQLite DB, local files, local voice, local GGUF inference).\n"
    "• Keeps personal profiles, memories, voice settings, and audit logs isolated per local user account.\n"
    "• Requires explicit user approval or confirmation for protected files, automation, microphone, and speaker actions.\n"
    "• Uses your device's embedded llama.cpp runtime — does not require Ollama or silent cloud fallbacks.\n"
    "• Honest limitations: Security guarantees depend on OS file permissions and your configured policies; "
    "untested voice languages (hi/mr) are explicitly labeled untested."
)

PERMISSION_ONBOARDING_GUIDE = (
    "RIYA Permission & Trust Model (User-Authoritative)\n\n"
    "• Decisions:\n"
    "  - ALLOW: Action runs automatically without prompting.\n"
    "  - CONFIRM: RIYA explains the exact action and asks 'Proceed?' before running.\n"
    "  - DENY: Permanently blocks the capability until you explicitly revoke the DENY rule.\n"
    "  - NOT CONFIGURED: Protected actions (system apps, screenshot, volume, mic, speaker, directory rules) fail closed.\n\n"
    "• Permission Lifetimes:\n"
    "  - one_time: Consumed once immediately before execution (never silently promoted).\n"
    "  - session: Active only until you sign out or close RIYA.\n"
    "  - time_limited: Expires automatically after the selected duration.\n"
    "  - persistent: Saved across restarts ONLY with your explicit opt-in confirmation.\n\n"
    "• File Access & Memory Privacy:\n"
    "  - Each user's files and personal memory rows are strictly scoped by authenticated user_id.\n"
    "  - The AI model / planner cannot grant permissions, override DENY rules, or switch users.\n"
    "  - Every permission decision and grant change is recorded in the append-only audit log for your review."
)


@dataclass(frozen=True)
class OnboardingWelcomeInfo:
    title: str
    tagline: str
    offline_first: bool
    summary: str
    privacy_details: str
    detection: OnboardingDetectionResult


class OnboardingCoordinator:
    """Framework-independent controller for RIYA's first-time setup, login, diagnostics, and launch."""

    def __init__(
        self,
        *,
        database_path: str | Path = DATABASE_PATH,
        users_root: str | Path | None = None,
        checkpoint_path: str | Path | None = None,
        ai_service: LocalAISetupService | None = None,
        voice_service: VoiceSetupService | None = None,
        diagnostics: ReadinessDiagnostics | None = None,
        strict_permissions: bool = False,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.users_root = Path(users_root).expanduser().resolve() if users_root is not None else None
        self.checkpoint_path = (
            Path(checkpoint_path).expanduser().resolve()
            if checkpoint_path is not None
            else default_checkpoint_path(self.database_path)
        )
        self.ai_service = ai_service or LocalAISetupService(project_root=PROJECT_ROOT)
        self.voice_service = voice_service or VoiceSetupService()
        self.diagnostics = diagnostics or ReadinessDiagnostics(
            ai_service=self.ai_service,
            voice_service=self.voice_service,
            project_root=self.ai_service.project_root,
        )
        self.strict_permissions = bool(strict_permissions)

        self._database: MemoryDatabase | None = None
        self._identity_manager: IdentityManager | None = None
        self._permission_manager: PermissionManager | None = None
        self._state_store: OnboardingStateStore | None = None

        self.active_identity: AuthenticatedIdentity | None = None
        self.selected_model_path: str | None = None
        self.selected_backend: str = "auto"
        self.ai_inference_verified: bool = False
        self.last_model_verification: ModelVerificationResult | None = None

    def detect_initial_state(self) -> OnboardingDetectionResult:
        """Detect installation state cleanly BEFORE touching/migrating DB files."""
        return detect_install_state(
            self.database_path,
            checkpoint_path=self.checkpoint_path,
        )

    def welcome_info(self) -> OnboardingWelcomeInfo:
        det = self.detect_initial_state()
        return OnboardingWelcomeInfo(
            title=f"{APP_NAME} v{APP_VERSION}",
            tagline="Your assistant. Your data. Your control.",
            offline_first=OFFLINE_FIRST,
            summary=det.message,
            privacy_details=PRIVACY_NOTICE_SUMMARY,
            detection=det,
        )

    def initialize_services(self) -> tuple[MemoryDatabase, IdentityManager, PermissionManager, OnboardingStateStore]:
        """Safely initialize SQLite, migrations, IdentityManager, and PermissionManager."""
        if (
            self._database is not None
            and self._identity_manager is not None
            and self._permission_manager is not None
            and self._state_store is not None
        ):
            return (
                self._database,
                self._identity_manager,
                self._permission_manager,
                self._state_store,
            )

        db = MemoryDatabase(self.database_path)
        id_mgr = IdentityManager(database=db, users_root=self.users_root)
        pm = PermissionManager(id_mgr, strict_default_deny=self.strict_permissions)
        store = OnboardingStateStore(db, checkpoint_path=self.checkpoint_path)

        self._database = db
        self._identity_manager = id_mgr
        self._permission_manager = pm
        self._state_store = store

        saved_model, saved_backend = store.get_effective_model_config()
        if saved_model and not self.selected_model_path:
            self.selected_model_path = saved_model
        if saved_backend:
            self.selected_backend = saved_backend

        return db, id_mgr, pm, store

    def recover_corrupted_checkpoint(self) -> Path | None:
        """Preserve corrupted checkpoint as backup so onboarding can proceed safely."""
        return OnboardingStateStore.repair_corrupted_checkpoint(self.checkpoint_path)

    def recover_corrupted_voice_config(self, user_id: str) -> Path | None:
        """Preserve corrupted per-user voice_settings.json before restoring safe defaults."""
        _, id_mgr, _, _ = self.initialize_services()
        cfg_path = VoiceConfig.path_for_user(id_mgr.users_root, user_id)
        if not cfg_path.exists():
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = cfg_path.with_name(f"{cfg_path.name}.corrupt.{ts}")
        os.replace(cfg_path, backup)
        return backup

    def start_or_resume_setup(self, *, step: str = "welcome") -> OnboardingDetectionResult:
        _, _, _, store = self.initialize_services()
        return store.begin_or_resume_setup(
            step=step,
            active_user_id=self.active_identity.user_id if self.active_identity else None,
        )

    def list_existing_profiles(self) -> list[UserProfile]:
        _, id_mgr, _, _ = self.initialize_services()
        return id_mgr.list_users(active_only=True)

    # ---- Step 4: Account setup --------------------------------------------
    def create_local_account(
        self,
        *,
        username: str,
        display_name: str | None = None,
        auth_method: str = "local_password",
        secret: str | None = None,
        confirm_secret: str | None = None,
    ) -> AuthenticatedIdentity:
        """Create and authenticate a local profile without ever storing plaintext secrets."""
        if auth_method in {"local_password", "local_pin"}:
            if not secret:
                raise ValueError("Please enter a password or PIN.")
            if confirm_secret is not None and secret != confirm_secret:
                raise ValueError("Password/PIN confirmation did not match.")
        _, id_mgr, _, store = self.initialize_services()
        profile = id_mgr.create_user(
            username,
            password=secret if auth_method != "local_unlock" else None,
            display_name=display_name or username,
            auth_method=auth_method,
        )
        identity = id_mgr.authenticate(
            profile.username,
            password=secret if auth_method != "local_unlock" else None,
            auth_method=auth_method,
        )
        self.active_identity = identity
        store.record_step_progress(
            "account",
            next_step="ai_engine",
            identity=identity,
        )
        return identity

    def sign_in(
        self,
        username: str,
        *,
        secret: str | None = None,
    ) -> AuthenticatedIdentity:
        _, id_mgr, _, store = self.initialize_services()
        if self.active_identity is not None:
            try:
                id_mgr.logout(self.active_identity)
            except Exception:
                pass
            self.active_identity = None

        profile = id_mgr.get_profile_by_username(username)
        identity = id_mgr.authenticate(
            profile.username,
            password=secret if profile.auth_method != "local_unlock" else None,
            auth_method=profile.auth_method,
        )
        self.active_identity = identity
        saved_model, saved_backend = store.get_effective_model_config(identity.user_id)
        if saved_model:
            self.selected_model_path = saved_model
        if saved_backend:
            self.selected_backend = saved_backend
        return identity

    def logout_current_user(self) -> None:
        if self._identity_manager is not None and self.active_identity is not None:
            try:
                self._identity_manager.logout(self.active_identity)
            except Exception:
                pass
        self.active_identity = None

    # ---- Step 5: AI engine setup ------------------------------------------
    def inspect_ai(self, *, assistant: Any | None = None) -> AIEngineStatus:
        return self.ai_service.inspect_environment(
            selected_model_path=self.selected_model_path,
            assistant=assistant,
            inference_verified=self.ai_inference_verified,
        )

    def configure_and_verify_ai(
        self,
        *,
        model_path: str | Path | None,
        backend: str = "auto",
        assistant: Any | None = None,
    ) -> ModelVerificationResult:
        _, _, _, store = self.initialize_services()
        if not model_path:
            self.selected_model_path = None
            self.selected_backend = "unloaded"
            self.ai_inference_verified = False
            self.last_model_verification = ModelVerificationResult(
                success=False,
                model_path=None,
                backend_used="unloaded",
                output_preview="",
                error_message="No local model selected; continuing in Limited Mode.",
            )
            store.record_step_progress(
                "ai_engine",
                next_step="voice",
                identity=self.active_identity,
                selected_model_path=None,
                selected_backend="unloaded",
            )
            return self.last_model_verification

        res = self.ai_service.verify_model_with_inference(
            model_path,
            backend=backend,
            assistant=assistant,
        )
        self.last_model_verification = res
        self.selected_model_path = res.model_path
        self.selected_backend = backend
        self.ai_inference_verified = res.success
        store.record_step_progress(
            "ai_engine",
            next_step="voice" if res.success else "ai_engine",
            identity=self.active_identity,
            selected_model_path=res.model_path,
            selected_backend=backend,
        )
        return res

    def continue_without_ai(self) -> None:
        _, _, _, store = self.initialize_services()
        self.selected_model_path = None
        self.selected_backend = "unloaded"
        self.ai_inference_verified = False
        store.record_step_progress(
            "ai_engine",
            next_step="voice",
            identity=self.active_identity,
            selected_model_path=None,
            selected_backend="unloaded",
        )

    # ---- Step 6: Voice setup ----------------------------------------------
    def load_current_voice_config(self) -> tuple[VoiceConfig, str | None]:
        _, id_mgr, _, _ = self.initialize_services()
        uid = self.active_identity.user_id if self.active_identity else None
        return load_user_voice_config_safely(id_mgr.users_root, uid)

    def inspect_voice(self, *, config: VoiceConfig | None = None) -> VoiceSubsystemStatus:
        _, id_mgr, pm, _ = self.initialize_services()
        if config is None:
            config, _ = self.load_current_voice_config()
        return self.voice_service.inspect_voice(
            self.active_identity,
            config=config,
            permission_manager=pm,
        )

    def test_microphone(
        self,
        *,
        config: VoiceConfig | None = None,
        duration_seconds: float = 0.25,
    ) -> VoiceTestResult:
        _, _, pm, _ = self.initialize_services()
        if config is None:
            config, _ = self.load_current_voice_config()
        return self.voice_service.run_microphone_test(
            self.active_identity,
            config=config,
            permission_manager=pm,
            duration_seconds=duration_seconds,
        )

    def test_speaker(
        self,
        *,
        config: VoiceConfig | None = None,
    ) -> VoiceTestResult:
        _, _, pm, _ = self.initialize_services()
        if config is None:
            config, _ = self.load_current_voice_config()
        return self.voice_service.run_speaker_test(
            self.active_identity,
            config=config,
            permission_manager=pm,
        )

    def save_voice_setup(
        self,
        new_config: VoiceConfig,
    ) -> VoiceConfig:
        _, id_mgr, pm, store = self.initialize_services()
        if self.active_identity is None:
            raise PermissionError("Sign in before saving per-user voice configuration.")
        old_cfg, _ = self.load_current_voice_config()
        saved = self.voice_service.save_voice_preferences(
            self.active_identity,
            identity_manager=id_mgr,
            permission_manager=pm,
            previous_config=old_cfg,
            new_config=new_config,
        )
        store.record_step_progress(
            "voice",
            next_step="permissions",
            identity=self.active_identity,
            voice_configured=True,
        )
        return saved

    def skip_voice_setup(self) -> None:
        _, _, _, store = self.initialize_services()
        store.record_step_progress(
            "voice",
            next_step="permissions",
            identity=self.active_identity,
            voice_configured=False,
        )

    # ---- Step 7: Permission onboarding (NO automatic grants!) --------------
    def permission_guide_text(self) -> str:
        return PERMISSION_ONBOARDING_GUIDE

    def acknowledge_permissions_reviewed(self) -> None:
        """Record only that the user reviewed the permission guide; never grants permissions."""
        _, _, _, store = self.initialize_services()
        store.record_step_progress(
            "permissions",
            next_step="diagnostics",
            identity=self.active_identity,
            permissions_reviewed=True,
        )

    # ---- Step 8: Readiness diagnostics & completion -----------------------
    def run_diagnostics(self, *, assistant: Any | None = None) -> ReadinessReport:
        _, id_mgr, pm, _ = self.initialize_services()
        return self.diagnostics.run_all(
            identity_manager=id_mgr,
            permission_manager=pm,
            identity=self.active_identity,
            detection=self.detect_initial_state(),
            assistant=assistant,
            selected_model_path=self.selected_model_path,
            ai_verified=self.ai_inference_verified,
        )

    def complete_onboarding(self, *, assistant: Any | None = None) -> ReadinessReport:
        """Validate readiness checks before marking onboarding completed."""
        _, _, _, store = self.initialize_services()
        report = self.run_diagnostics(assistant=assistant)
        if not report.mandatory_passed or self.active_identity is None:
            failed = [
                c.name
                for c in report.checks
                if c.category == "mandatory" and c.status.value in {"failed", "not_configured"}
            ]
            raise RuntimeError(
                f"Cannot complete setup while mandatory checks are unresolved: {', '.join(failed)}"
            )
        store.record_step_progress(
            "diagnostics",
            next_step="complete",
            identity=self.active_identity,
            health_checked=True,
        )
        store.mark_setup_completed(
            self.active_identity,
            selected_model_path=self.selected_model_path,
            selected_backend=self.selected_backend,
        )
        return report

    # ---- Step 9: Build live runtime for authenticated user ----------------
    def build_runtime_session(
        self,
        identity: AuthenticatedIdentity | None = None,
    ) -> tuple[RIYAAssistant, VoiceOrchestrator]:
        _, id_mgr, pm, store = self.initialize_services()
        active = identity or self.active_identity
        if active is None:
            raise PermissionError("Authenticated user session is required before opening RIYA.")
        validated = id_mgr.validate_session(active)
        self.active_identity = validated

        eff_model, eff_backend = store.get_effective_model_config(validated.user_id)
        model_to_use = self.selected_model_path or eff_model
        backend_to_use = self.selected_backend or eff_backend or "auto"

        assistant = RIYAAssistant(
            identity_manager=id_mgr,
            default_identity=validated,
            require_identity=True,
            permission_manager=pm,
            strict_permissions=self.strict_permissions,
            model_path=model_to_use,
            ai_backend=backend_to_use,
            allow_degraded_ai=True,
        )
        cfg, _ = load_user_voice_config_safely(id_mgr.users_root, validated.user_id)
        voice_session = VoiceSession(
            cfg,
            user_id=validated.user_id,
            auth_session_id=validated.session_id,
        )
        orchestrator = VoiceOrchestrator(
            assistant,
            config=cfg,
            session=voice_session,
            identity_manager=id_mgr,
            permission_manager=pm,
            identity=validated,
        )
        return assistant, orchestrator
