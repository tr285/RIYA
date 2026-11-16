"""Offline local Speech-to-Text engine built on faster-whisper (CTranslate2)."""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from faster_whisper import WhisperModel

from core.ai.safety import MAX_USER_INPUT_CHARS, strip_unsafe_control_chars
from core.voice.config import PIPELINE_SAMPLE_RATE, STT_COMPUTE_TYPES, STT_MODELS
from core.voice.errors import STTModelUnavailableError, TranscriptionError

logger = logging.getLogger("riya.voice.stt")

STT_MODEL_APPROX_SIZES = {
    "tiny": "~75 MB",
    "tiny.en": "~75 MB",
    "base": "~148 MB",
    "base.en": "~148 MB",
    "small": "~486 MB",
    "small.en": "~486 MB",
    "medium": "~1.5 GB",
    "medium.en": "~1.5 GB",
    "distil-small.en": "~350 MB",
    "large-v3-turbo": "~1.6 GB",
}

_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[Any, ...], Any] = {}


def clear_stt_model_cache() -> None:
    """Clear cached local WhisperModel instances (primarily for tests/cleanup)."""

    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()


@dataclass(frozen=True)
class TranscriptionResult:
    """Structured local speech-to-text output."""

    text: str
    language: str
    confidence: float
    no_speech_prob: float
    duration_seconds: float


class LocalSTT:
    """Local offline Speech-to-Text engine for RIYA.

    By default, ``local_files_only=True`` prevents unexpected network model
    downloads at runtime and reuses cached ``WhisperModel`` instances across
    turns to avoid duplicate memory usage on Apple Silicon.
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        *,
        cpu_threads: int = 4,
        beam_size: int = 1,
        download_root: str | None = None,
        local_files_only: bool = True,
        max_transcript_chars: int = MAX_USER_INPUT_CHARS,
        model: Any | None = None,
    ):
        if model is None:
            if not isinstance(model_size, str) or not model_size.strip():
                raise ValueError("model_size must be a non-empty string.")
            cleaned_model = model_size.strip()
            # Allow either known short identifiers or an existing local directory path.
            is_local_dir = Path(cleaned_model).exists()
            if cleaned_model not in STT_MODELS and not is_local_dir:
                raise ValueError(
                    f"Unsupported STT model '{cleaned_model}'. Allowed: {STT_MODELS}."
                )
        else:
            cleaned_model = str(model_size)

        if compute_type not in STT_COMPUTE_TYPES and compute_type != "default":
            raise ValueError(f"Unsupported compute_type '{compute_type}'.")
        if isinstance(beam_size, bool) or not isinstance(beam_size, int) or not (1 <= beam_size <= 10):
            raise ValueError("beam_size must be between 1 and 10.")
        if isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int) or not (0 <= cpu_threads <= 16):
            raise ValueError("cpu_threads must be between 0 and 16.")
        if (
            isinstance(max_transcript_chars, bool)
            or not isinstance(max_transcript_chars, int)
            or not (16 <= max_transcript_chars <= MAX_USER_INPUT_CHARS)
        ):
            raise ValueError("max_transcript_chars is out of range.")

        self.model_size = cleaned_model
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size
        self.download_root = download_root
        self.local_files_only = bool(local_files_only)
        self.max_transcript_chars = max_transcript_chars

        if model is not None:
            self.model = model
        else:
            self.model = self._load_or_get_cached()

    def _load_or_get_cached(self) -> Any:
        cache_key = (
            id(WhisperModel),
            self.model_size,
            self.device,
            self.compute_type,
            self.cpu_threads,
            self.download_root,
            self.local_files_only,
        )
        with _MODEL_CACHE_LOCK:
            cached = _MODEL_CACHE.get(cache_key)
            if cached is not None:
                return cached

            try:
                loaded = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    cpu_threads=self.cpu_threads,
                    download_root=self.download_root,
                    local_files_only=self.local_files_only,
                )
            except Exception as exc:
                if self._looks_like_missing_model_error(exc):
                    size_note = STT_MODEL_APPROX_SIZES.get(self.model_size, "local files")
                    raise STTModelUnavailableError(
                        f"Local Whisper model '{self.model_size}' ({size_note}) is not cached offline: {exc}",
                        user_message=(
                            f"Speech recognition model '{self.model_size}' ({size_note}) is not "
                            "installed locally. RIYA does not download models automatically."
                        ),
                    ) from exc
                raise TranscriptionError(f"Failed to load local STT model: {exc}") from exc

            _MODEL_CACHE[cache_key] = loaded
            return loaded

    @staticmethod
    def _looks_like_missing_model_error(exc: BaseException) -> bool:
        msg = f"{type(exc).__name__}: {exc}".lower()
        return any(
            token in msg
            for token in (
                "localentrynotfound",
                "filenotfound",
                "no such file",
                "cannot find the requested files",
                "local_files_only",
                "unable to open file",
                "snapshot",
                "offline",
            )
        )

    def transcribe_detailed(
        self,
        audio: str | Path | np.ndarray,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe a WAV file path or 16 kHz mono float32 numpy array."""

        duration_seconds = 0.0
        audio_input: Any
        if isinstance(audio, (str, Path)):
            audio_path = Path(audio)
            if not audio_path.is_file():
                raise TranscriptionError("Audio file for transcription was not found.")
            audio_input = str(audio_path)
        elif isinstance(audio, np.ndarray):
            arr = np.asarray(audio, dtype=np.float32).reshape(-1)
            if arr.size == 0:
                return TranscriptionResult(
                    text="",
                    language=language or "en",
                    confidence=0.0,
                    no_speech_prob=1.0,
                    duration_seconds=0.0,
                )
            duration_seconds = float(arr.size) / float(PIPELINE_SAMPLE_RATE)
            audio_input = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        else:
            raise ValueError("audio must be a file path or a numpy ndarray.")

        lang_arg = None if (language is None or language == "auto") else str(language)

        try:
            segments_iter, info = self.model.transcribe(
                audio_input,
                language=lang_arg,
                beam_size=self.beam_size,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            segments = list(segments_iter)
        except Exception as exc:
            raise TranscriptionError(f"Local STT transcription failed: {exc}") from exc

        kept_texts: list[str] = []
        logprobs: list[float] = []
        no_speech_probs: list[float] = []

        for seg in segments:
            seg_text = getattr(seg, "text", "") or ""
            seg_text = strip_unsafe_control_chars(seg_text, allow_newlines=False).strip()
            if not seg_text:
                continue
            avg_lp = getattr(seg, "avg_logprob", -0.5)
            nsp = getattr(seg, "no_speech_prob", 0.0)
            avg_lp = -0.5 if avg_lp is None else float(avg_lp)
            nsp = 0.0 if nsp is None else float(nsp)

            # Filter common Whisper silence hallucinations while retaining valid short answers.
            if nsp > 0.85 and avg_lp < -1.0:
                continue

            kept_texts.append(seg_text)
            logprobs.append(avg_lp)
            no_speech_probs.append(nsp)

        combined = " ".join(kept_texts).strip()
        if len(combined) > self.max_transcript_chars:
            combined = combined[: self.max_transcript_chars].rstrip()

        detected_lang = str(getattr(info, "language", None) or lang_arg or "en")
        if info is not None and getattr(info, "duration", None):
            try:
                duration_seconds = float(info.duration)
            except (TypeError, ValueError):
                pass

        if logprobs:
            mean_lp = sum(logprobs) / len(logprobs)
            raw_conf = max(0.0, min(1.0, math.exp(max(-10.0, min(0.0, mean_lp)))))
            mean_nsp = sum(no_speech_probs) / len(no_speech_probs)
            # Whisper segment avg_logprob includes special/EOS tokens, which deflates
            # short 1-word utterances ("yes"/"no"). Calibrate moderately only when
            # token logprob is already plausible and no_speech_prob is clearly low.
            if raw_conf >= 0.40 and mean_nsp <= 0.25:
                speech_presence = max(0.0, min(1.0, 1.0 - mean_nsp))
                confidence = max(raw_conf, min(1.0, 0.65 * raw_conf + 0.35 * speech_presence))
            else:
                confidence = raw_conf
        else:
            confidence = 0.0
            mean_nsp = 1.0

        return TranscriptionResult(
            text=combined,
            language=detected_lang,
            confidence=confidence,
            no_speech_prob=mean_nsp,
            duration_seconds=duration_seconds,
        )

    def transcribe_file(
        self,
        audio_path: str | Path,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file and return structured TranscriptionResult."""
        return self.transcribe_detailed(audio_path, language=language)

    def transcribe(
        self,
        audio_path: str | Path | np.ndarray,
        language: str | None = None,
    ) -> str:
        """Convert local audio file or numpy waveform into sanitized text."""

        return self.transcribe_detailed(audio_path, language=language).text
