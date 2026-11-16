"""System readiness and health diagnostics for RIYA v0.5 onboarding and operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from core.config.settings import PROJECT_ROOT
from core.database.migrations import CURRENT_SCHEMA_VERSION, get_schema_version
from core.identity.errors import IdentityError
from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity
from core.onboarding.ai_setup import (
    AIEngineStatus,
    LocalAISetupService,
    get_free_disk_bytes,
)
from core.onboarding.state import InstallState, OnboardingDetectionResult
from core.onboarding.voice_setup import (
    VoiceSetupService,
    VoiceSubsystemStatus,
    load_user_voice_config_safely,
)
from core.security.permissions import PermissionDecision, PermissionManager


class CheckSeverity(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class DiagnosticCheck:
    check_id: str
    name: str
    category: str  # "mandatory" | "optional"
    status: CheckSeverity
    summary: str
    detail: str
    recovery_action: str | None = None


@dataclass(frozen=True)
class ReadinessReport:
    checks: tuple[DiagnosticCheck, ...]
    mandatory_passed: bool
    can_enter_app: bool
    degraded_capabilities: tuple[str, ...]
    checked_at: str

    def by_id(self, check_id: str) -> DiagnosticCheck | None:
        for check in self.checks:
            if check.check_id == check_id:
                return check
        return None


_MIN_DB_FREE_BYTES = 50 * 1024 * 1024  # 50 MB minimum for safe local operations
_LOW_DISK_WARNING_BYTES = 500 * 1024 * 1024  # 500 MB warning threshold


class ReadinessDiagnostics:
    """Runs honest, real checks across runtime, storage, auth, permissions, AI, and voice."""

    def __init__(
        self,
        *,
        ai_service: LocalAISetupService | None = None,
        voice_service: VoiceSetupService | None = None,
        project_root: Path = PROJECT_ROOT,
    ):
        self.project_root = Path(project_root).resolve()
        self.ai_service = ai_service or LocalAISetupService(project_root=self.project_root)
        self.voice_service = voice_service or VoiceSetupService()

    def run_all(
        self,
        *,
        identity_manager: IdentityManager,
        permission_manager: PermissionManager | None,
        identity: AuthenticatedIdentity | None,
        detection: OnboardingDetectionResult | None = None,
        assistant: Any | None = None,
        selected_model_path: str | Path | None = None,
        ai_verified: bool = False,
        ai_status: AIEngineStatus | None = None,
        voice_status: VoiceSubsystemStatus | None = None,
    ) -> ReadinessReport:
        checks: list[DiagnosticCheck] = []
        degraded: list[str] = []

        # 1. Python & runtime
        py_ok = sys.version_info >= (3, 10)
        checks.append(
            DiagnosticCheck(
                check_id="python_runtime",
                name="Python & Runtime",
                category="mandatory",
                status=CheckSeverity.PASSED if py_ok else CheckSeverity.FAILED,
                summary=f"Python {sys.version.split()[0]} / SQLite {sqlite3.sqlite_version}",
                detail="Offline local Python runtime and built-in SQLite3 engine are ready.",
                recovery_action=None if py_ok else "Upgrade Python to >= 3.10.",
            )
        )

        # 2. Directories & Disk Space
        db_dir = identity_manager.database.database_path.parent
        users_dir = identity_manager.users_root
        dirs_ok = (
            db_dir.is_dir()
            and os.access(db_dir, os.W_OK)
            and users_dir.is_dir()
            and os.access(users_dir, os.W_OK)
        )
        free_bytes = get_free_disk_bytes(db_dir)
        free_mb = free_bytes / float(1024 * 1024)
        free_gb = free_bytes / float(1024**3)
        if not dirs_ok:
            checks.append(
                DiagnosticCheck(
                    check_id="directories_disk",
                    name="Application Directories & Disk Space",
                    category="mandatory",
                    status=CheckSeverity.FAILED,
                    summary="Application data directory is not writable.",
                    detail=f"Cannot write to {db_dir} or {users_dir}.",
                    recovery_action="Verify folder ownership and write permissions for data/ and users/.",
                )
            )
        elif free_bytes > 0 and free_bytes < _MIN_DB_FREE_BYTES:
            checks.append(
                DiagnosticCheck(
                    check_id="directories_disk",
                    name="Application Directories & Disk Space",
                    category="mandatory",
                    status=CheckSeverity.FAILED,
                    summary=f"Critically low free disk space ({free_mb:.1f} MB free).",
                    detail="Insufficient disk space to safely persist database writes and backups.",
                    recovery_action="Free at least 50 MB (or ~3+ GB for local LLM models) before continuing.",
                )
            )
        elif free_bytes > 0 and free_bytes < _LOW_DISK_WARNING_BYTES:
            checks.append(
                DiagnosticCheck(
                    check_id="directories_disk",
                    name="Application Directories & Disk Space",
                    category="mandatory",
                    status=CheckSeverity.WARNING,
                    summary=f"Writable directories ready; low disk space ({free_mb:.0f} MB free).",
                    detail="Core database storage works, but space is limited for extra models or logs.",
                    recovery_action="Free disk space if planning to install local GGUF models (~2.5+ GB).",
                )
            )
        else:
            checks.append(
                DiagnosticCheck(
                    check_id="directories_disk",
                    name="Application Directories & Disk Space",
                    category="mandatory",
                    status=CheckSeverity.PASSED,
                    summary=f"Writable storage ready ({free_gb:.1f} GB free).",
                    detail=f"Database dir: {db_dir}; User storage root: {users_dir}.",
                )
            )

        # 3. Database & Schema Migrations
        db_check = self._check_database(identity_manager, detection)
        checks.append(db_check)

        # 4. User Profile, Workspace & Active Session
        validated_identity: AuthenticatedIdentity | None = None
        if identity is None:
            checks.append(
                DiagnosticCheck(
                    check_id="identity_session",
                    name="User Account & Session",
                    category="mandatory",
                    status=CheckSeverity.NOT_CONFIGURED,
                    summary="No active signed-in user session.",
                    detail="Create a local account or sign in to continue.",
                    recovery_action="Go to the Account step to create or unlock a local profile.",
                )
            )
        else:
            try:
                validated_identity = identity_manager.validate_session(identity)
                ws = Path(validated_identity.workspace_dir).expanduser().resolve()
                ws_ok = ws.is_dir() and os.access(ws, os.R_OK | os.W_OK)
                if not ws_ok:
                    checks.append(
                        DiagnosticCheck(
                            check_id="identity_session",
                            name="User Account & Session",
                            category="mandatory",
                            status=CheckSeverity.FAILED,
                            summary="User workspace folder is missing or unreadable.",
                            detail=f"Workspace path invalid: {ws}",
                            recovery_action="Recreate missing user workspace directory without deleting database records.",
                        )
                    )
                else:
                    checks.append(
                        DiagnosticCheck(
                            check_id="identity_session",
                            name="User Account & Session",
                            category="mandatory",
                            status=CheckSeverity.PASSED,
                            summary=f"Signed in as {validated_identity.display_name} (@{validated_identity.username})",
                            detail=f"Isolated workspace: {ws}",
                        )
                    )
            except IdentityError as exc:
                checks.append(
                    DiagnosticCheck(
                        check_id="identity_session",
                        name="User Account & Session",
                        category="mandatory",
                        status=CheckSeverity.FAILED,
                        summary="User session validation failed.",
                        detail=str(exc),
                        recovery_action="Sign in again with your local account credentials.",
                    )
                )

        # 5. Permission & Audit Manager
        checks.append(self._check_permissions(permission_manager, validated_identity))

        # 6. Configuration Persistence
        cfg_check = self._check_user_config(
            identity_manager,
            validated_identity,
            detection,
        )
        checks.append(cfg_check)

        # 7. Native Inference & Local AI Model (optional for degraded startup)
        ai_stat = ai_status or self.ai_service.inspect_environment(
            selected_model_path=selected_model_path,
            assistant=assistant,
            inference_verified=ai_verified,
        )
        ai_check = self._check_ai_engine(ai_stat, assistant=assistant)
        checks.append(ai_check)
        if ai_check.status != CheckSeverity.PASSED:
            degraded.append("Local LLM conversation")

        # 8 & 9. Voice Input & Output (optional capabilities)
        loaded_cfg, _cfg_err = (
            load_user_voice_config_safely(
                identity_manager.users_root,
                validated_identity.user_id if validated_identity else None,
            )
        )
        v_stat = voice_status or self.voice_service.inspect_voice(
            validated_identity,
            config=loaded_cfg,
            permission_manager=permission_manager,
        )
        vin_check, vout_check = self._check_voice(v_stat)
        checks.append(vin_check)
        checks.append(vout_check)
        if vin_check.status not in {CheckSeverity.PASSED, CheckSeverity.WARNING}:
            degraded.append("Voice microphone input")
        if vout_check.status not in {CheckSeverity.PASSED, CheckSeverity.WARNING}:
            degraded.append("Voice speaker output")

        mandatory_failed = any(
            c.category == "mandatory" and c.status in {CheckSeverity.FAILED, CheckSeverity.NOT_CONFIGURED}
            for c in checks
        )
        can_enter = not mandatory_failed
        return ReadinessReport(
            checks=tuple(checks),
            mandatory_passed=not mandatory_failed,
            can_enter_app=can_enter,
            degraded_capabilities=tuple(degraded),
            checked_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _check_database(
        identity_manager: IdentityManager,
        detection: OnboardingDetectionResult | None,
    ) -> DiagnosticCheck:
        if detection is not None and detection.install_state == InstallState.NEEDS_RECOVERY and not detection.database_accessible:
            return DiagnosticCheck(
                check_id="database_schema",
                name="Database & Migrations",
                category="mandatory",
                status=CheckSeverity.FAILED,
                summary="Database accessibility/corruption issue detected.",
                detail=detection.database_error or detection.message,
                recovery_action="Inspect/restore backup database; RIYA never deletes user databases automatically.",
            )
        try:
            conn = identity_manager.database.connect()
            try:
                quick = conn.execute("PRAGMA quick_check").fetchone()
                if quick is None or str(quick[0]).lower() != "ok":
                    raise sqlite3.DatabaseError(f"quick_check returned {quick}")
                ver = get_schema_version(conn)
                if ver != CURRENT_SCHEMA_VERSION:
                    return DiagnosticCheck(
                        check_id="database_schema",
                        name="Database & Migrations",
                        category="mandatory",
                        status=CheckSeverity.FAILED,
                        summary=f"Unexpected schema version {ver} (expected {CURRENT_SCHEMA_VERSION}).",
                        detail="Pending or interrupted migration detected.",
                        recovery_action="Run database initialization/migration check or restore backup.",
                    )
                return DiagnosticCheck(
                    check_id="database_schema",
                    name="Database & Migrations",
                    category="mandatory",
                    status=CheckSeverity.PASSED,
                    summary=f"SQLite schema v{ver} verified cleanly.",
                    detail=f"Database: {identity_manager.database.database_path}",
                )
            finally:
                conn.close()
        except Exception as exc:
            return DiagnosticCheck(
                check_id="database_schema",
                name="Database & Migrations",
                category="mandatory",
                status=CheckSeverity.FAILED,
                summary="Failed to verify local SQLite database.",
                detail=str(exc),
                recovery_action="Verify file permissions on data/riya.db or restore a backup.",
            )

    @staticmethod
    def _check_permissions(
        permission_manager: PermissionManager | None,
        identity: AuthenticatedIdentity | None,
    ) -> DiagnosticCheck:
        if permission_manager is None:
            return DiagnosticCheck(
                check_id="permission_manager",
                name="Permission & Trust Engine",
                category="mandatory",
                status=CheckSeverity.FAILED,
                summary="PermissionManager is not initialized.",
                detail="Protected actions cannot be authorized safely without PermissionManager.",
                recovery_action="Restart RIYA or reinitialize security subsystem.",
            )
        try:
            if identity is not None:
                grants = permission_manager.list_permissions(identity)
                events = permission_manager.audit_logger.list_audit_events(identity, limit=1)
                detail = (
                    f"Active rules for @{identity.username}: {len(grants)}; "
                    f"audit history accessible ({len(events)} latest event checked)."
                )
            else:
                detail = "Permission catalog initialized cleanly."
            return DiagnosticCheck(
                check_id="permission_manager",
                name="Permission & Trust Engine",
                category="mandatory",
                status=CheckSeverity.PASSED,
                summary="User-authoritative permissions & immutable audit log active.",
                detail=detail,
            )
        except Exception as exc:
            return DiagnosticCheck(
                check_id="permission_manager",
                name="Permission & Trust Engine",
                category="mandatory",
                status=CheckSeverity.FAILED,
                summary="Permission manager health check failed.",
                detail=str(exc),
                recovery_action="Inspect database permission_grants and permission_audit_log tables.",
            )

    @staticmethod
    def _check_user_config(
        identity_manager: IdentityManager,
        identity: AuthenticatedIdentity | None,
        detection: OnboardingDetectionResult | None,
    ) -> DiagnosticCheck:
        if detection is not None and detection.checkpoint_corrupted:
            return DiagnosticCheck(
                check_id="user_config",
                name="Configuration Persistence",
                category="mandatory",
                status=CheckSeverity.FAILED,
                summary="Onboarding checkpoint JSON is corrupted.",
                detail=detection.checkpoint_error or "Invalid JSON checkpoint file.",
                recovery_action="Back up and reset the corrupt checkpoint file cleanly (keeps all user data).",
            )
        if identity is None:
            return DiagnosticCheck(
                check_id="user_config",
                name="Configuration Persistence",
                category="mandatory",
                status=CheckSeverity.NOT_CONFIGURED,
                summary="Waiting for signed-in user to verify per-user configuration.",
                detail="Per-user voice and AI config is stored per local profile.",
                recovery_action="Sign in or create an account.",
            )
        _cfg, cfg_err = load_user_voice_config_safely(
            identity_manager.users_root,
            identity.user_id,
        )
        if cfg_err:
            return DiagnosticCheck(
                check_id="user_config",
                name="Configuration Persistence",
                category="mandatory",
                status=CheckSeverity.WARNING,
                summary="Per-user voice settings file needs repair; using safe defaults.",
                detail=cfg_err,
                recovery_action="Save settings cleanly in Voice Setup to overwrite or keep a backup copy.",
            )
        return DiagnosticCheck(
            check_id="user_config",
            name="Configuration Persistence",
            category="mandatory",
            status=CheckSeverity.PASSED,
            summary="Per-user settings storage verified.",
            detail=f"Settings dir: {identity_manager.user_root(identity.user_id)}",
        )

    @staticmethod
    def _check_ai_engine(
        ai_status: AIEngineStatus,
        *,
        assistant: Any | None = None,
    ) -> DiagnosticCheck:
        if not ai_status.native_library_loadable:
            return DiagnosticCheck(
                check_id="ai_engine",
                name="Local AI Inference Engine",
                category="optional",
                status=CheckSeverity.FAILED,
                summary="Native llama.cpp inference library could not be loaded.",
                detail=ai_status.native_library_error or "libriya_llama.dylib unavailable.",
                recovery_action="Continue in Limited Mode (deterministic tools/memory work) or rebuild libriya_llama.dylib.",
            )

        if not ai_status.selected_model_path:
            return DiagnosticCheck(
                check_id="ai_engine",
                name="Local AI Inference Engine",
                category="optional",
                status=CheckSeverity.NOT_CONFIGURED,
                summary="No local GGUF model installed or selected.",
                detail=ai_status.error_message or "Place a compatible .gguf model in models/ or select one in AI Setup.",
                recovery_action="Select a local .gguf file in AI Setup or continue in Limited Mode.",
            )

        if assistant is not None and ai_status.model_loaded and ai_status.inference_verified:
            return DiagnosticCheck(
                check_id="ai_engine",
                name="Local AI Inference Engine",
                category="optional",
                status=CheckSeverity.PASSED,
                summary=f"Model loaded & inference verified ({ai_status.active_backend})",
                detail=f"Model: {Path(ai_status.selected_model_path).name}; Hardware: {ai_status.hardware_acceleration}",
            )

        if assistant is None and ai_status.inference_verified:
            return DiagnosticCheck(
                check_id="ai_engine",
                name="Local AI Inference Engine",
                category="optional",
                status=CheckSeverity.PASSED,
                summary="Local GGUF model verified with real offline inference probe",
                detail=(
                    f"Model: {Path(ai_status.selected_model_path).name}; "
                    f"Hardware: {ai_status.hardware_acceleration} (runtime loads when opening chat)."
                ),
            )

        if assistant is not None and ai_status.model_loaded:
            return DiagnosticCheck(
                check_id="ai_engine",
                name="Local AI Inference Engine",
                category="optional",
                status=CheckSeverity.WARNING,
                summary=f"Model loaded ({ai_status.active_backend}), awaiting inference check.",
                detail=f"Model: {Path(ai_status.selected_model_path).name}. Run 'Load & Verify Model' to test generation.",
                recovery_action="Run the verification probe in AI Setup before relying on LLM chat.",
            )

        if ai_status.error_message:
            return DiagnosticCheck(
                check_id="ai_engine",
                name="Local AI Inference Engine",
                category="optional",
                status=CheckSeverity.FAILED,
                summary="Local AI model is not loaded.",
                detail=ai_status.error_message,
                recovery_action="Select a valid compatible GGUF model or continue in Limited Mode.",
            )

        return DiagnosticCheck(
            check_id="ai_engine",
            name="Local AI Inference Engine",
            category="optional",
            status=CheckSeverity.NOT_CONFIGURED,
            summary="Model detected on disk but not loaded or verified yet.",
            detail=f"Selected: {Path(ai_status.selected_model_path).name}",
            recovery_action="Run 'Load & Verify Model' in AI Setup, or continue in Limited Mode.",
        )

    @staticmethod
    def _check_voice(
        voice_status: VoiceSubsystemStatus,
    ) -> tuple[DiagnosticCheck, DiagnosticCheck]:
        # Voice input check
        if not voice_status.input_devices:
            vin = DiagnosticCheck(
                check_id="voice_input",
                name="Voice Input (Microphone & STT)",
                category="optional",
                status=CheckSeverity.FAILED,
                summary="No microphone input device detected.",
                detail="Text conversation remains completely available without a microphone.",
                recovery_action="Connect a microphone or continue using Text Mode.",
            )
        elif not voice_status.stt_cached_locally:
            vin = DiagnosticCheck(
                check_id="voice_input",
                name="Voice Input (Microphone & STT)",
                category="optional",
                status=CheckSeverity.WARNING,
                summary="Microphone detected, but offline STT model is not cached locally.",
                detail=voice_status.stt_status_detail,
                recovery_action="Cache faster-whisper model offline or use Text Mode.",
            )
        elif voice_status.mic_permission_decision == PermissionDecision.DENY.value:
            vin = DiagnosticCheck(
                check_id="voice_input",
                name="Voice Input (Microphone & STT)",
                category="optional",
                status=CheckSeverity.WARNING,
                summary="Microphone permission is set to DENY by user policy.",
                detail="Voice mode will stay disabled until you revoke the DENY rule in Permissions.",
                recovery_action="Keep disabled for privacy or manage voice.microphone in Permissions & Trust.",
            )
        elif voice_status.mic_permission_decision == PermissionDecision.NOT_CONFIGURED.value:
            vin = DiagnosticCheck(
                check_id="voice_input",
                name="Voice Input (Microphone & STT)",
                category="optional",
                status=CheckSeverity.NOT_CONFIGURED,
                summary="Microphone & STT available; permission not configured yet.",
                detail=f"Input devices: {len(voice_status.input_devices)}; VAD: {voice_status.vad_backend}; STT cached: {voice_status.stt_model}.",
                recovery_action="You can grant voice.microphone when activating Voice Mode or keep it unconfigured.",
            )
        else:
            vin = DiagnosticCheck(
                check_id="voice_input",
                name="Voice Input (Microphone & STT)",
                category="optional",
                status=CheckSeverity.PASSED,
                summary=f"Ready ({len(voice_status.input_devices)} mic(s), STT={voice_status.stt_model}, VAD={voice_status.vad_backend})",
                detail=f"RIYA voice.microphone policy: {voice_status.mic_permission_decision}. English verified; hi/mr untested.",
            )

        # Voice output check
        if not voice_status.output_devices:
            vout = DiagnosticCheck(
                check_id="voice_output",
                name="Voice Output (Speaker & TTS)",
                category="optional",
                status=CheckSeverity.FAILED,
                summary="No speaker output device detected.",
                detail="Text responses remain visible in the conversation transcript.",
                recovery_action="Connect an audio output device or use Text Mode.",
            )
        elif not voice_status.tts_available:
            vout = DiagnosticCheck(
                check_id="voice_output",
                name="Voice Output (Speaker & TTS)",
                category="optional",
                status=CheckSeverity.FAILED,
                summary="Local TTS engine (/usr/bin/say) is unavailable.",
                detail="Spoken replies are unavailable; text replies are unaffected.",
                recovery_action="Use Text Mode.",
            )
        elif voice_status.speaker_permission_decision == PermissionDecision.DENY.value:
            vout = DiagnosticCheck(
                check_id="voice_output",
                name="Voice Output (Speaker & TTS)",
                category="optional",
                status=CheckSeverity.WARNING,
                summary="Speaker output permission is set to DENY by user policy.",
                detail="Spoken TTS playback stays disabled unless you revoke the DENY rule.",
                recovery_action="Manage voice.speaker in Permissions & Trust.",
            )
        elif voice_status.speaker_permission_decision == PermissionDecision.NOT_CONFIGURED.value:
            vout = DiagnosticCheck(
                check_id="voice_output",
                name="Voice Output (Speaker & TTS)",
                category="optional",
                status=CheckSeverity.NOT_CONFIGURED,
                summary="Speakers & local TTS available; permission not configured yet.",
                detail=f"Output devices: {len(voice_status.output_devices)}; Voices available: {len(voice_status.tts_voices)}.",
                recovery_action="You can grant voice.speaker in Voice Setup/Permissions or leave muted.",
            )
        else:
            vout = DiagnosticCheck(
                check_id="voice_output",
                name="Voice Output (Speaker & TTS)",
                category="optional",
                status=CheckSeverity.PASSED,
                summary=f"Ready ({len(voice_status.output_devices)} output device(s), TTS ready)",
                detail=f"RIYA voice.speaker policy: {voice_status.speaker_permission_decision}.",
            )

        return vin, vout
