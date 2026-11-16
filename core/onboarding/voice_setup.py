"""Permission-gated local voice setup, device checks, and per-user configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable

from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity
from core.security.permissions import PermissionDecision, PermissionManager
from core.voice.audio_input import (
    AudioDeviceInfo,
    MicrophoneStream,
    list_input_devices,
    list_output_devices,
)
from core.voice.audio_output import AudioOutputPlayer
from core.voice.config import (
    RESPONSE_STYLES,
    SUPPORTED_LANGUAGES,
    VERIFIED_LANGUAGES,
    VoiceConfig,
)
from core.voice.errors import (
    MicrophonePermissionError,
    MicrophoneUnavailableError,
    PlaybackError,
    TTSError,
    VoiceError,
)
from core.voice.tts import LocalTTS, _SAY_BIN
from core.voice.vad import _default_silero_onnx_path


@dataclass(frozen=True)
class VoiceSubsystemStatus:
    input_devices: tuple[AudioDeviceInfo, ...]
    output_devices: tuple[AudioDeviceInfo, ...]
    selected_input_device: int | str | None
    selected_output_device: int | str | None
    vad_available: bool
    vad_backend: str
    stt_available: bool
    stt_model: str
    stt_cached_locally: bool
    stt_status_detail: str
    tts_available: bool
    tts_voices: tuple[dict[str, str], ...]
    mic_permission_decision: str
    speaker_permission_decision: str
    voice_config_decision: str
    voice_device_decision: str
    language_options: tuple[tuple[str, str], ...]
    privacy_summary: str


@dataclass(frozen=True)
class VoiceTestResult:
    test_type: str  # "microphone" | "speaker"
    passed: bool
    blocked_by_permission: bool
    permission_decision: str
    measured_level: float
    message: str
    error_kind: str | None = None


def check_stt_local_availability(
    stt_model: str = "base",
    *,
    stt_model_dir: str | None = None,
) -> tuple[bool, bool, str]:
    """Inspect whether faster-whisper + local model weights exist offline without downloading."""
    try:
        import faster_whisper  # noqa: F401
    except Exception as exc:
        return False, False, f"faster-whisper package is unavailable: {exc}"

    if stt_model_dir:
        custom_dir = Path(stt_model_dir).expanduser()
        if (custom_dir / "model.bin").is_file():
            return True, True, f"Offline STT weights found at {custom_dir}."

    model_as_path = Path(stt_model).expanduser()
    if model_as_path.is_dir() and (model_as_path / "model.bin").is_file():
        return True, True, f"Offline STT weights found at {model_as_path}."

    try:
        from huggingface_hub import try_to_load_from_cache

        repo_id = f"Systran/faster-whisper-{stt_model}"
        cached = try_to_load_from_cache(
            repo_id=repo_id,
            filename="model.bin",
            cache_dir=stt_model_dir,
        )
        if isinstance(cached, (str, Path)) and Path(cached).is_file():
            return (
                True,
                True,
                f"Offline faster-whisper '{stt_model}' weights are cached locally.",
            )
    except Exception:
        pass

    return (
        True,
        False,
        f"Speech recognition model '{stt_model}' is not cached locally offline. "
        "RIYA will not download STT models automatically.",
    )


def honest_language_options() -> tuple[tuple[str, str], ...]:
    """Return (code, honest_display_label) keeping unverified languages marked untested."""
    options: list[tuple[str, str]] = []
    for code, label in SUPPORTED_LANGUAGES.items():
        if code in VERIFIED_LANGUAGES:
            options.append((code, f"{label} ({code} - verified)"))
        else:
            options.append((code, f"{label} ({code} - accuracy untested)"))
    return tuple(options)


def load_user_voice_config_safely(
    users_root: str | Path,
    user_id: str | None,
) -> tuple[VoiceConfig, str | None]:
    """Load per-user VoiceConfig without discarding corrupted settings files."""
    if not user_id:
        return VoiceConfig(), None
    cfg_path = VoiceConfig.path_for_user(users_root, user_id)
    if not cfg_path.exists():
        return VoiceConfig.load_for_user(users_root, user_id), None
    try:
        return VoiceConfig.load_for_user(users_root, user_id), None
    except Exception as exc:
        return VoiceConfig(), f"Saved voice settings file is corrupted ({cfg_path.name}): {exc}"


class VoiceSetupService:
    """Inspects local audio hardware and performs permission-gated mic/speaker checks."""

    def __init__(
        self,
        *,
        mic_factory: Callable[[VoiceConfig], Any] | None = None,
        player_factory: Callable[[VoiceConfig], Any] | None = None,
        input_lister: Callable[[], list[AudioDeviceInfo]] = list_input_devices,
        output_lister: Callable[[], list[AudioDeviceInfo]] = list_output_devices,
        tts_voice_lister: Callable[[], list[dict[str, str]]] = LocalTTS.list_voices,
    ):
        self._mic_factory = mic_factory or (
            lambda cfg: MicrophoneStream(
                sample_rate=cfg.sample_rate,
                device=cfg.input_device,
                check_permission_silence=False,
                permission_probe_seconds=cfg.permission_probe_seconds,
            )
        )
        self._player_factory = player_factory or (lambda cfg: AudioOutputPlayer(cfg))
        self._list_inputs = input_lister
        self._list_outputs = output_lister
        self._list_tts_voices = tts_voice_lister

    @staticmethod
    def _eval_permission_label(
        permission_manager: PermissionManager | None,
        identity: AuthenticatedIdentity | None,
        permission_key: str,
        resource: str,
    ) -> str:
        if permission_manager is None:
            return "unavailable"
        if identity is None:
            return PermissionDecision.NOT_CONFIGURED.value
        res = permission_manager.evaluate(
            identity,
            permission_key,
            resource_or_target=resource,
            actor="voice_setup",
        )
        return res.decision.value

    def inspect_voice(
        self,
        identity: AuthenticatedIdentity | None,
        *,
        config: VoiceConfig | None = None,
        permission_manager: PermissionManager | None = None,
    ) -> VoiceSubsystemStatus:
        cfg = config or VoiceConfig()
        in_devs = tuple(self._list_inputs())
        out_devs = tuple(self._list_outputs())

        silero_path = _default_silero_onnx_path()
        vad_backend = "energy"
        if silero_path is not None:
            try:
                import onnxruntime  # noqa: F401

                vad_backend = "silero"
            except Exception:
                vad_backend = "energy"

        stt_pkg_ok, stt_cached, stt_detail = check_stt_local_availability(
            cfg.stt_model,
            stt_model_dir=cfg.stt_model_dir,
        )

        tts_voices = tuple(self._list_tts_voices())
        tts_available = os.path.isfile(_SAY_BIN) or bool(tts_voices)

        mic_dec = self._eval_permission_label(
            permission_manager, identity, "voice.microphone", "voice:microphone"
        )
        spk_dec = self._eval_permission_label(
            permission_manager, identity, "voice.speaker", "voice:speaker"
        )
        cfg_dec = self._eval_permission_label(
            permission_manager, identity, "voice.config", "voice:config"
        )
        dev_dec = self._eval_permission_label(
            permission_manager, identity, "voice.device", "voice:device"
        )

        return VoiceSubsystemStatus(
            input_devices=in_devs,
            output_devices=out_devs,
            selected_input_device=cfg.input_device,
            selected_output_device=cfg.output_device,
            vad_available=True,
            vad_backend=vad_backend,
            stt_available=stt_pkg_ok and stt_cached,
            stt_model=cfg.stt_model,
            stt_cached_locally=stt_cached,
            stt_status_detail=stt_detail,
            tts_available=tts_available,
            tts_voices=tts_voices,
            mic_permission_decision=mic_dec,
            speaker_permission_decision=spk_dec,
            voice_config_decision=cfg_dec,
            voice_device_decision=dev_dec,
            language_options=honest_language_options(),
            privacy_summary=(
                "Voice audio is processed locally on your device. "
                "The microphone stays OFF unless you explicitly activate Voice Mode or run a mic test, "
                "and voice recordings are never saved unless you explicitly opt in."
            ),
        )

    def run_microphone_test(
        self,
        identity: AuthenticatedIdentity | None,
        *,
        config: VoiceConfig,
        permission_manager: PermissionManager | None,
        duration_seconds: float = 0.25,
    ) -> VoiceTestResult:
        """Probe microphone ONLY when authorized via PermissionManager."""
        if permission_manager is not None:
            if identity is None:
                return VoiceTestResult(
                    test_type="microphone",
                    passed=False,
                    blocked_by_permission=True,
                    permission_decision=PermissionDecision.NOT_CONFIGURED.value,
                    measured_level=0.0,
                    message="Sign in before testing the microphone.",
                    error_kind="unauthenticated",
                )
            eval_res = permission_manager.consume_if_authorized(
                identity,
                "voice.microphone",
                resource_or_target="voice:microphone_test",
                actor="voice_setup",
            )
            if eval_res.blocked:
                return VoiceTestResult(
                    test_type="microphone",
                    passed=False,
                    blocked_by_permission=True,
                    permission_decision=eval_res.decision.value,
                    measured_level=0.0,
                    message=eval_res.reason,
                    error_kind="permission_blocked",
                )
            decision_label = eval_res.decision.value
        else:
            decision_label = PermissionDecision.ALLOW.value

        if not self._list_inputs():
            return VoiceTestResult(
                test_type="microphone",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message="No microphone input device was detected on this system.",
                error_kind="device_unavailable",
            )

        mic = self._mic_factory(config)
        try:
            level = float(mic.probe_permission_and_read_level(duration=duration_seconds))
            return VoiceTestResult(
                test_type="microphone",
                passed=True,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=level,
                message=(
                    f"Microphone opened and responded cleanly (measured RMS level: {level:.4f}). "
                    "Microphone is now closed again."
                ),
            )
        except MicrophonePermissionError as exc:
            return VoiceTestResult(
                test_type="microphone",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message=exc.user_message,
                error_kind="os_permission_denied",
            )
        except MicrophoneUnavailableError as exc:
            return VoiceTestResult(
                test_type="microphone",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message=exc.user_message,
                error_kind="device_unavailable",
            )
        except VoiceError as exc:
            return VoiceTestResult(
                test_type="microphone",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message=exc.user_message,
                error_kind="capture_error",
            )
        except Exception as exc:
            return VoiceTestResult(
                test_type="microphone",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message=f"Microphone test failed: {exc}",
                error_kind="capture_error",
            )
        finally:
            try:
                mic.stop()
            except Exception:
                pass

    def run_speaker_test(
        self,
        identity: AuthenticatedIdentity | None,
        *,
        config: VoiceConfig,
        permission_manager: PermissionManager | None,
        test_phrase: str = "RIYA speaker check complete.",
    ) -> VoiceTestResult:
        """Play a short test phrase ONLY when authorized via PermissionManager."""
        if permission_manager is not None:
            if identity is None:
                return VoiceTestResult(
                    test_type="speaker",
                    passed=False,
                    blocked_by_permission=True,
                    permission_decision=PermissionDecision.NOT_CONFIGURED.value,
                    measured_level=0.0,
                    message="Sign in before testing speaker playback.",
                    error_kind="unauthenticated",
                )
            eval_res = permission_manager.consume_if_authorized(
                identity,
                "voice.speaker",
                resource_or_target="voice:speaker_test",
                actor="voice_setup",
            )
            if eval_res.blocked:
                return VoiceTestResult(
                    test_type="speaker",
                    passed=False,
                    blocked_by_permission=True,
                    permission_decision=eval_res.decision.value,
                    measured_level=0.0,
                    message=eval_res.reason,
                    error_kind="permission_blocked",
                )
            decision_label = eval_res.decision.value
        else:
            decision_label = PermissionDecision.ALLOW.value

        if not self._list_outputs():
            return VoiceTestResult(
                test_type="speaker",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message="No speaker/audio output device was detected on this system.",
                error_kind="device_unavailable",
            )

        player = self._player_factory(config)
        try:
            completed = bool(player.play_text(test_phrase))
            if not completed:
                return VoiceTestResult(
                    test_type="speaker",
                    passed=False,
                    blocked_by_permission=False,
                    permission_decision=decision_label,
                    measured_level=0.0,
                    message="Speaker playback was cancelled or did not complete.",
                    error_kind="playback_error",
                )
            return VoiceTestResult(
                test_type="speaker",
                passed=True,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=float(config.volume),
                message="Speaker playback completed successfully.",
            )
        except (TTSError, PlaybackError) as exc:
            return VoiceTestResult(
                test_type="speaker",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message=exc.user_message,
                error_kind="playback_error",
            )
        except Exception as exc:
            return VoiceTestResult(
                test_type="speaker",
                passed=False,
                blocked_by_permission=False,
                permission_decision=decision_label,
                measured_level=0.0,
                message=f"Speaker test failed: {exc}",
                error_kind="playback_error",
            )
        finally:
            try:
                player.stop()
            except Exception:
                pass

    def save_voice_preferences(
        self,
        identity: AuthenticatedIdentity,
        *,
        identity_manager: IdentityManager,
        permission_manager: PermissionManager | None,
        previous_config: VoiceConfig | None,
        new_config: VoiceConfig,
    ) -> VoiceConfig:
        """Validate, permission-check, and save per-user voice settings cleanly."""
        new_config.validate()
        validated = identity_manager.validate_session(identity)

        if permission_manager is not None:
            res = permission_manager.evaluate(
                validated,
                "voice.config",
                resource_or_target="voice:config",
                actor="voice_setup",
            )
            if res.blocked:
                raise PermissionError(res.reason)
            old = previous_config or VoiceConfig()
            if (
                new_config.input_device != old.input_device
                or new_config.output_device != old.output_device
            ):
                if new_config.input_device != old.input_device:
                    dev_res = permission_manager.evaluate(
                        validated,
                        "voice.device",
                        resource_or_target=f"device:{new_config.input_device or 'default_input'}",
                        actor="voice_setup",
                    )
                    if dev_res.blocked:
                        raise PermissionError(dev_res.reason)
                if new_config.output_device != old.output_device:
                    dev_res = permission_manager.evaluate(
                        validated,
                        "voice.device",
                        resource_or_target=f"device:{new_config.output_device or 'default_output'}",
                        actor="voice_setup",
                    )
                    if dev_res.blocked:
                        raise PermissionError(dev_res.reason)

        new_config.save_for_user(identity_manager.users_root, validated.user_id)
        return new_config
