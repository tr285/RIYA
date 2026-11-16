"""Validated, persistable voice configuration."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from core.ai.safety import MAX_USER_INPUT_CHARS
from core.config.settings import DATA_DIR, PROJECT_ROOT, RECORDINGS_DIR


# Language codes understood by faster-whisper. Labels are display text only.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
    "auto": "Auto-detect",
}

# Languages whose STT *and* TTS paths have been exercised on real audio in
# this project. Only update after an actual end-to-end test. Non-verified
# languages are shown as "(untested)" in the UI.
VERIFIED_LANGUAGES = frozenset({"en"})

RESPONSE_STYLES: tuple[str, ...] = (
    "friendly",
    "calm",
    "excited",
    "empathetic",
    "professional",
)

TTS_ENGINES: tuple[str, ...] = ("macos_say",)
VAD_ENGINES: tuple[str, ...] = ("silero", "energy")
CONVERSATION_MODES: tuple[str, ...] = ("continuous", "push_to_talk")

# Allow-list of faster-whisper model identifiers (no arbitrary repo IDs / URLs).
STT_MODELS: tuple[str, ...] = (
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "distil-small.en",
    "large-v3-turbo",
)
STT_COMPUTE_TYPES: tuple[str, ...] = ("int8", "int8_float32", "float32")

# The VAD and STT pipeline operates on 16 kHz mono float32 audio. Silero VAD
# (v6, bundled with faster-whisper) expects 512-sample windows at 16 kHz.
PIPELINE_SAMPLE_RATE = 16000
VAD_FRAME_SAMPLES = 512

DEFAULT_SETTINGS_PATH = DATA_DIR / "voice_settings.json"
DEFAULT_RECORDINGS_DIR = RECORDINGS_DIR

_VOICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,63}$")
_DEVICE_NAME_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")


def _check_range(name: str, value: Any, low: float, high: float, kind: type) -> None:
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer.")
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number.")
    if not (low <= value <= high):
        raise ValueError(f"{name} must be between {low} and {high}.")


def _check_device(name: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a device index, name, or null.")
    if isinstance(value, int):
        if not (0 <= value <= 1024):
            raise ValueError(f"{name} index out of range.")
        return
    if isinstance(value, str) and _DEVICE_NAME_PATTERN.fullmatch(value):
        return
    raise ValueError(f"{name} must be a device index, name, or null.")


@dataclass(frozen=True)
class VoiceConfig:
    # ---- audio devices ---------------------------------------------------
    input_device: int | str | None = None
    output_device: int | str | None = None
    sample_rate: int = PIPELINE_SAMPLE_RATE

    # ---- voice activity detection ---------------------------------------
    vad_engine: str = "silero"
    vad_threshold: float = 0.5
    vad_neg_threshold: float = 0.35
    min_speech_ms: int = 250
    min_silence_ms: int = 700
    speech_pad_ms: int = 300
    max_utterance_seconds: float = 30.0
    permission_probe_seconds: float = 1.5

    # ---- barge-in --------------------------------------------------------
    barge_in_enabled: bool = True
    barge_in_threshold: float = 0.8
    barge_in_min_speech_ms: int = 400
    echo_guard_ms: int = 250

    # ---- speech recognition ---------------------------------------------
    stt_model: str = "base"
    stt_compute_type: str = "int8"
    stt_beam_size: int = 1
    stt_cpu_threads: int = 4
    stt_model_dir: str | None = None
    language: str = "en"
    max_transcript_chars: int = MAX_USER_INPUT_CHARS

    # ---- speech synthesis -----------------------------------------------
    tts_engine: str = "macos_say"
    voice: str = "Samantha"
    speech_rate_wpm: int = 180
    volume: float = 0.9
    response_style: str = "friendly"
    voice_responses_enabled: bool = True
    speech_chunk_chars: int = 220
    max_spoken_chars: int = 1500

    # ---- confirmation safety --------------------------------------------
    voice_confirmation_enabled: bool = True
    min_confirmation_confidence: float = 0.55

    # ---- conversation mode -----------------------------------------------
    conversation_mode: str = "continuous"

    # ---- privacy ---------------------------------------------------------
    retain_recordings: bool = False
    recordings_dir: str = field(default_factory=lambda: str(DEFAULT_RECORDINGS_DIR))

    def __post_init__(self) -> None:
        self.validate()

    # ------------------------------------------------------------------
    def validate(self) -> None:
        _check_device("input_device", self.input_device)
        _check_device("output_device", self.output_device)
        if self.sample_rate != PIPELINE_SAMPLE_RATE:
            raise ValueError(
                f"sample_rate must be {PIPELINE_SAMPLE_RATE} Hz (device audio is resampled)."
            )
        if self.vad_engine not in VAD_ENGINES:
            raise ValueError(f"vad_engine must be one of {VAD_ENGINES}.")
        _check_range("vad_threshold", self.vad_threshold, 0.05, 0.99, float)
        _check_range("vad_neg_threshold", self.vad_neg_threshold, 0.01, 0.98, float)
        if self.vad_neg_threshold >= self.vad_threshold:
            raise ValueError("vad_neg_threshold must be lower than vad_threshold.")
        _check_range("min_speech_ms", self.min_speech_ms, 32, 5000, int)
        _check_range("min_silence_ms", self.min_silence_ms, 64, 10000, int)
        _check_range("speech_pad_ms", self.speech_pad_ms, 0, 2000, int)
        _check_range("max_utterance_seconds", self.max_utterance_seconds, 1.0, 120.0, float)
        _check_range("permission_probe_seconds", self.permission_probe_seconds, 0.2, 10.0, float)
        for flag in (
            "barge_in_enabled",
            "voice_responses_enabled",
            "voice_confirmation_enabled",
            "retain_recordings",
        ):
            if not isinstance(getattr(self, flag), bool):
                raise ValueError(f"{flag} must be true or false.")
        _check_range("barge_in_threshold", self.barge_in_threshold, 0.05, 0.99, float)
        _check_range("barge_in_min_speech_ms", self.barge_in_min_speech_ms, 32, 5000, int)
        _check_range("echo_guard_ms", self.echo_guard_ms, 0, 3000, int)
        if self.stt_model not in STT_MODELS:
            raise ValueError(f"stt_model must be one of {STT_MODELS}.")
        if self.stt_compute_type not in STT_COMPUTE_TYPES:
            raise ValueError(f"stt_compute_type must be one of {STT_COMPUTE_TYPES}.")
        _check_range("stt_beam_size", self.stt_beam_size, 1, 10, int)
        _check_range("stt_cpu_threads", self.stt_cpu_threads, 0, 16, int)
        if self.stt_model_dir is not None:
            if not isinstance(self.stt_model_dir, str) or "\x00" in self.stt_model_dir:
                raise ValueError("stt_model_dir must be a path string or null.")
        if self.language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"language must be one of {tuple(SUPPORTED_LANGUAGES)}.")
        _check_range("max_transcript_chars", self.max_transcript_chars, 16, MAX_USER_INPUT_CHARS, int)
        if self.tts_engine not in TTS_ENGINES:
            raise ValueError(f"tts_engine must be one of {TTS_ENGINES}.")
        if not isinstance(self.voice, str) or not _VOICE_NAME_PATTERN.fullmatch(self.voice):
            raise ValueError("voice name is invalid.")
        _check_range("speech_rate_wpm", self.speech_rate_wpm, 90, 360, int)
        _check_range("volume", self.volume, 0.0, 1.0, float)
        if self.response_style not in RESPONSE_STYLES:
            raise ValueError(f"response_style must be one of {RESPONSE_STYLES}.")
        _check_range("speech_chunk_chars", self.speech_chunk_chars, 40, 1000, int)
        _check_range("max_spoken_chars", self.max_spoken_chars, 100, 10000, int)
        _check_range("min_confirmation_confidence", self.min_confirmation_confidence, 0.0, 1.0, float)
        if self.conversation_mode not in CONVERSATION_MODES:
            raise ValueError(f"conversation_mode must be one of {CONVERSATION_MODES}.")
        if not isinstance(self.recordings_dir, str) or "\x00" in self.recordings_dir or not self.recordings_dir:
            raise ValueError("recordings_dir must be a path string.")

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoiceConfig":
        if not isinstance(data, dict):
            raise ValueError("Voice settings must be a JSON object.")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown voice settings: {sorted(unknown)}")
        return cls(**data)

    def updated(self, **changes: Any) -> "VoiceConfig":
        return replace(self, **changes)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path = DEFAULT_SETTINGS_PATH) -> "VoiceConfig":
        """Load settings; missing file -> defaults. Invalid file -> ValueError."""

        settings_path = Path(path)
        if not settings_path.exists():
            return cls()
        if settings_path.stat().st_size > 64 * 1024:
            raise ValueError("Voice settings file is too large.")
        with settings_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data)

    def save(self, path: str | Path = DEFAULT_SETTINGS_PATH) -> Path:
        """Atomically write settings with owner-only permissions."""

        settings_path = Path(path)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".voice_settings.", suffix=".tmp", dir=str(settings_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, settings_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
        return settings_path

    @staticmethod
    def _validate_user_dir(users_root: str | Path, user_id: str) -> Path:
        if not isinstance(user_id, str) or not user_id.startswith("usr_") or "/" in user_id or "\\" in user_id or ".." in user_id:
            raise ValueError("Invalid user_id for voice configuration.")
        voice_dir = Path(users_root).expanduser().resolve() / user_id / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(voice_dir, 0o700)
        except OSError:
            pass
        return voice_dir

    @classmethod
    def path_for_user(cls, users_root: str | Path, user_id: str) -> Path:
        return cls._validate_user_dir(users_root, user_id) / "voice_settings.json"

    @classmethod
    def recordings_dir_for_user(cls, users_root: str | Path, user_id: str) -> Path:
        recordings = cls._validate_user_dir(users_root, user_id) / "recordings"
        recordings.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(recordings, 0o700)
        except OSError:
            pass
        return recordings

    @classmethod
    def load_for_user(cls, users_root: str | Path, user_id: str) -> "VoiceConfig":
        settings_path = cls.path_for_user(users_root, user_id)
        recordings_dir = cls.recordings_dir_for_user(users_root, user_id)
        cfg = cls.load(settings_path)
        if cfg.recordings_dir == str(DEFAULT_RECORDINGS_DIR):
            return replace(cfg, recordings_dir=str(recordings_dir))
        return cfg

    def save_for_user(self, users_root: str | Path, user_id: str) -> Path:
        settings_path = self.path_for_user(users_root, user_id)
        return self.save(settings_path)

    # ------------------------------------------------------------------
    @property
    def frame_ms(self) -> float:
        return VAD_FRAME_SAMPLES * 1000.0 / PIPELINE_SAMPLE_RATE

    @property
    def stt_language(self) -> str | None:
        return None if self.language == "auto" else self.language
