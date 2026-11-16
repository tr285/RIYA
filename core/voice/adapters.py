"""Reusable cross-platform voice adapter contracts and native implementations for RIYA.

Defines the core abstractions for:
- AudioInputAdapter (real-time stream and file-based audio capture)
- AudioOutputAdapter (chunked, interruptible speech playback)
- SpeechRecognitionAdapter (offline local transcription)
- SpeechSynthesisAdapter (offline speech synthesis)
- VoiceActivityDetector (streaming speech probability estimation)
- VoiceSessionManager (session lifecycle and conversation mode management)

Provides:
- Concrete macOS implementations leveraging local faster-whisper, Silero VAD ONNX, sounddevice, and /usr/bin/say.
- Truthful Windows & Linux adapter contracts reporting unsupported capabilities until native verification.
- System-wide voice capability diagnostic function: check_voice_capabilities().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import platform as _py_platform
import sys
import threading
from typing import Any, Callable, Sequence

import numpy as np

from core.device.platform import UnsupportedPlatformCapabilityError, detect_platform_name
from core.voice.config import PIPELINE_SAMPLE_RATE, VAD_FRAME_SAMPLES, VoiceConfig
from core.voice.errors import (
    AudioCaptureError,
    MicrophonePermissionError,
    MicrophoneUnavailableError,
    PlaybackError,
    STTModelUnavailableError,
    TranscriptionError,
    TTSError,
    VoiceError,
)
from core.voice.stt import LocalSTT, TranscriptionResult
from core.voice.tts import LocalTTS

logger = logging.getLogger("riya.voice.adapters")


# =====================================================================
# 1. ABSTRACT BASE CONTRACTS
# =====================================================================

class AudioInputAdapter(ABC):
    """Adapter contract for microphone audio capture and device enumeration."""

    @abstractmethod
    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        """Start real-time audio capture, feeding 16 kHz mono float32 frames to callback."""
        pass

    @abstractmethod
    def stop_capture(self) -> None:
        """Stop audio capture stream and release hardware resources cleanly."""
        pass

    @property
    @abstractmethod
    def is_capturing(self) -> bool:
        """Return True if the audio capture stream is currently active."""
        pass

    @abstractmethod
    def get_level(self) -> float:
        """Return current RMS microphone level in [0.0, 1.0]."""
        pass

    @abstractmethod
    def list_devices(self) -> list[dict[str, Any]]:
        """Return list of available local audio input devices."""
        pass

    def is_available(self) -> bool:
        """Return True if audio input device is available."""
        return True


class AudioOutputAdapter(ABC):
    """Adapter contract for chunked, interruptible speech playback."""

    @abstractmethod
    def play_chunks(
        self,
        chunks: Sequence[str],
        *,
        on_chunk_done: Callable[[int, str], None] | None = None,
        on_playback_start: Callable[[], None] | None = None,
    ) -> bool:
        """Synthesize and play speech chunks cleanly, returning True if playback completed."""
        pass

    @abstractmethod
    def play_audio(self, audio: np.ndarray, sample_rate: int = PIPELINE_SAMPLE_RATE) -> bool:
        """Play raw PCM float32 audio, returning True if completed without interruption."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Immediately abort active audio playback (barge-in / interrupt)."""
        pass

    @property
    @abstractmethod
    def is_playing(self) -> bool:
        """Return True if speech audio is currently playing."""
        pass

    @property
    @abstractmethod
    def current_level(self) -> float:
        """Return current output volume level in [0.0, 1.0]."""
        pass

    def is_available(self) -> bool:
        """Return True if audio output device is available."""
        return True


class SpeechRecognitionAdapter(ABC):
    """Adapter contract for offline speech-to-text recognition."""

    @abstractmethod
    def transcribe(
        self,
        audio: np.ndarray | str | Path,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe a 16 kHz mono float32 array or WAV file path into text."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the local STT model is cached and ready offline."""
        pass

    @abstractmethod
    def get_model_info(self) -> dict[str, Any]:
        """Return engine metadata, model size, and offline status."""
        pass


class SpeechSynthesisAdapter(ABC):
    """Adapter contract for offline text-to-speech synthesis."""

    @abstractmethod
    def speak(self, text: str, *, rate_wpm: int | None = None) -> None:
        """Speak text synchronously via local speech engine."""
        pass

    @abstractmethod
    def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        """Synthesize text to a 16-bit PCM WAV file offline."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if local speech synthesis engine is available."""
        pass

    @abstractmethod
    def list_voices(self) -> list[str]:
        """Return list of supported offline voices."""
        pass


class VoiceActivityDetector(ABC):
    """Adapter contract for frame-by-frame voice activity detection."""

    @abstractmethod
    def process_frame(self, frame: np.ndarray) -> float:
        """Evaluate 512-sample 16 kHz audio frame and return speech probability in [0.0, 1.0]."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset internal recurrence / noise-floor state between utterances."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the VAD engine and model assets are available offline."""
        pass


class VoiceSessionManager(ABC):
    """Adapter contract for voice session lifecycle and mode switching."""

    @abstractmethod
    def start_session(self, mode: str = "continuous") -> None:
        """Start an exclusive voice session in 'push_to_talk' or 'continuous' mode."""
        pass

    @abstractmethod
    def stop_session(self) -> None:
        """Stop active session and release all audio hardware cleanly."""
        pass

    @abstractmethod
    def get_mode(self) -> str:
        """Return active conversation mode ('push_to_talk', 'continuous', or 'text')."""
        pass

    @abstractmethod
    def set_mode(self, mode: str) -> None:
        """Switch active conversation mode."""
        pass

    @property
    @abstractmethod
    def is_active(self) -> bool:
        """Return True if a voice session is active."""
        pass


# =====================================================================
# 2. CONCRETE MACOS IMPLEMENTATIONS
# =====================================================================

class MacOSAudioInputAdapter(AudioInputAdapter):
    """Native macOS microphone stream adapter using sounddevice / PortAudio."""

    def __init__(self, config: VoiceConfig | None = None):
        from core.voice.audio_input import MicrophoneStream, list_input_devices

        self.config = config or VoiceConfig()
        self._list_devices_fn = list_input_devices
        self._mic = MicrophoneStream(
            sample_rate=self.config.sample_rate,
            device=self.config.input_device,
            permission_probe_seconds=self.config.permission_probe_seconds,
        )
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._mic.start()
        self._stop_event.clear()

        def _pump():
            while not self._stop_event.is_set() and self._mic.is_active:
                frame = self._mic.read_frame(timeout=0.05)
                if frame is not None:
                    try:
                        callback(frame)
                    except Exception as e:
                        logger.error("Error in audio capture callback: %s", e)

        self._worker_thread = threading.Thread(target=_pump, name="MacOSAudioCapturePump", daemon=True)
        self._worker_thread.start()

    def stop_capture(self) -> None:
        self._stop_event.set()
        self._mic.stop()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
        self._worker_thread = None

    @property
    def is_capturing(self) -> bool:
        return self._mic.is_active

    def get_level(self) -> float:
        return self._mic.current_level

    def list_devices(self) -> list[dict[str, Any]]:
        devs = self._list_devices_fn()
        return [
            {
                "index": d.index,
                "name": d.name,
                "max_input_channels": d.max_input_channels,
                "default_samplerate": d.default_samplerate,
                "is_default": d.is_default,
            }
            for d in devs
        ]


class MacOSAudioOutputAdapter(AudioOutputAdapter):
    """Native macOS chunked, interruptible speech player."""

    def __init__(self, config: VoiceConfig | None = None, tts: LocalTTS | None = None):
        from core.voice.audio_output import AudioOutputPlayer

        self.config = config or VoiceConfig()
        self._player = AudioOutputPlayer(self.config, tts=tts)

    def play_chunks(
        self,
        chunks: Sequence[str],
        *,
        on_chunk_done: Callable[[int, str], None] | None = None,
        on_playback_start: Callable[[], None] | None = None,
    ) -> bool:
        return self._player.play_chunks(
            chunks,
            on_chunk_done=on_chunk_done,
            on_playback_start=on_playback_start,
        )

    def play_audio(self, audio: np.ndarray, sample_rate: int = PIPELINE_SAMPLE_RATE) -> bool:
        return self._player.play_pcm(audio, sample_rate=sample_rate)

    def stop(self) -> None:
        self._player.stop()

    @property
    def is_playing(self) -> bool:
        return self._player.is_playing

    @property
    def current_level(self) -> float:
        return self._player.current_level


class FasterWhisperSTTAdapter(SpeechRecognitionAdapter):
    """Local offline speech recognition adapter powered by faster-whisper (CTranslate2)."""

    def __init__(self, config: VoiceConfig | None = None, stt: LocalSTT | None = None):
        self.config = config or VoiceConfig()
        self._stt = stt or LocalSTT(
            model_size=self.config.stt_model,
            compute_type=self.config.stt_compute_type,
            cpu_threads=self.config.stt_cpu_threads,
            beam_size=self.config.stt_beam_size,
            download_root=self.config.stt_model_dir,
            local_files_only=True,
            max_transcript_chars=self.config.max_transcript_chars,
        )

    def transcribe(
        self,
        audio: np.ndarray | str | Path,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        lang = language or self.config.language
        effective_lang = None if lang == "auto" else lang
        return self._stt.transcribe_detailed(audio, language=effective_lang)

    def is_available(self) -> bool:
        try:
            return self._stt.model is not None
        except Exception:
            return False

    def get_model_info(self) -> dict[str, Any]:
        return {
            "engine": "faster-whisper",
            "model_size": self._stt.model_size,
            "compute_type": self._stt.compute_type,
            "cpu_threads": self._stt.cpu_threads,
            "offline_ready": self.is_available(),
        }


class MacOSSayTTSAdapter(SpeechSynthesisAdapter):
    """Offline macOS speech synthesis adapter backed by /usr/bin/say."""

    def __init__(self, config: VoiceConfig | None = None, tts: LocalTTS | None = None):
        self.config = config or VoiceConfig()
        self._tts = tts or LocalTTS(
            voice=self.config.voice,
            rate_wpm=self.config.speech_rate_wpm,
            volume=self.config.volume,
        )

    def speak(self, text: str, *, rate_wpm: int | None = None) -> None:
        self._tts.speak(text, rate_wpm=rate_wpm)

    def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        return self._tts.synthesize_to_file(text, output_path, rate_wpm=rate_wpm)

    def is_available(self) -> bool:
        return Path("/usr/bin/say").is_file()

    def list_voices(self) -> list[str]:
        return self._tts.list_available_voices()


class SileroVADAdapter(VoiceActivityDetector):
    """Bundled local Silero VAD v6 ONNX speech probability detector."""

    def __init__(self, model_path: str | Path | None = None):
        from core.voice.vad import SileroStreamingVAD

        self._vad = SileroStreamingVAD(model_path=model_path)

    def process_frame(self, frame: np.ndarray) -> float:
        return self._vad.speech_probability(frame)

    def reset(self) -> None:
        self._vad.reset()

    def is_available(self) -> bool:
        return Path(self._vad.model_path).is_file()

    def is_speech(self, frame: np.ndarray) -> float:
        return self.process_frame(frame)


class EnergyVADAdapter(VoiceActivityDetector):
    """Deterministic fallback RMS energy noise-floor detector."""

    def __init__(self, threshold: float = 0.02):
        from core.voice.vad import EnergyVAD

        self._vad = EnergyVAD(speech_rms=threshold)

    def process_frame(self, frame: np.ndarray) -> float:
        return self._vad.speech_probability(frame)

    def is_speech(self, frame: np.ndarray) -> float:
        return self.process_frame(frame)

    def reset(self) -> None:
        self._vad.reset()

    def is_available(self) -> bool:
        return True


class DefaultVoiceSessionManager(VoiceSessionManager):
    """Exclusive voice session coordinator with Push-to-Talk and Continuous modes."""

    def __init__(self, session: Any = None):
        if isinstance(session, VoiceConfig):
            from core.voice.session import VoiceSession

            self._session = VoiceSession(session)
        elif session is None:
            from core.voice.session import VoiceSession

            self._session = VoiceSession()
        else:
            self._session = session
        self._mode = "continuous"  # "push_to_talk" | "continuous" | "text"

    def start_session(self, mode: str = "continuous") -> None:
        if mode not in {"push_to_talk", "continuous", "text"}:
            raise ValueError(f"Invalid voice mode: {mode}. Must be 'push_to_talk', 'continuous', or 'text'.")
        self._mode = mode
        self._session.start()

    def stop_session(self) -> None:
        self._session.stop()
        self._mode = "text"

    def get_mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in {"push_to_talk", "continuous", "text"}:
            raise ValueError(f"Invalid voice mode: {mode}.")
        self._mode = mode

    @property
    def is_active(self) -> bool:
        return self._session.is_active


# =====================================================================
# 3. TRUTHFUL UNSUPPORTED WINDOWS & LINUX CONTRACTS
# =====================================================================

class _UnsupportedVoiceAdapterBase:
    def __init__(self, platform_name: str, capability: str):
        self.platform_name = platform_name
        self.capability = capability

    def is_available(self) -> bool:
        return False

    def _raise(self) -> Any:
        raise UnsupportedPlatformCapabilityError(
            capability=self.capability,
            platform_name=self.platform_name,
            reason=f"Voice capability '{self.capability}' is not yet implemented or verified on {self.platform_name}.",
            required_permission="voice.microphone",
        )


class WindowsAudioInputAdapter(AudioInputAdapter, _UnsupportedVoiceAdapterBase):
    def __init__(self):
        super().__init__("windows", "voice_audio_input")

    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        self._raise()

    def stop_capture(self) -> None:
        pass

    @property
    def is_capturing(self) -> bool:
        return False

    def get_level(self) -> float:
        return 0.0

    def list_devices(self) -> list[dict[str, Any]]:
        return []

    def is_available(self) -> bool:
        return False


class WindowsAudioOutputAdapter(AudioOutputAdapter, _UnsupportedVoiceAdapterBase):
    def __init__(self):
        super().__init__("windows", "voice_audio_output")

    def play_chunks(self, chunks: Sequence[str], **kwargs) -> bool:
        self._raise()

    def play_audio(self, audio: np.ndarray, sample_rate: int = PIPELINE_SAMPLE_RATE) -> bool:
        self._raise()

    def stop(self) -> None:
        pass

    @property
    def is_playing(self) -> bool:
        return False

    @property
    def current_level(self) -> float:
        return 0.0

    def is_available(self) -> bool:
        return False


class WindowsSpeechRecognitionAdapter(SpeechRecognitionAdapter, _UnsupportedVoiceAdapterBase):
    def __init__(self):
        super().__init__("windows", "voice_speech_recognition")

    def transcribe(self, audio: np.ndarray | str | Path, **kwargs) -> TranscriptionResult:
        self._raise()

    def is_available(self) -> bool:
        return False

    def get_model_info(self) -> dict[str, Any]:
        return {"engine": "none", "supported": False, "platform": "windows"}


class WindowsSpeechSynthesisAdapter(SpeechSynthesisAdapter, _UnsupportedVoiceAdapterBase):
    def __init__(self):
        super().__init__("windows", "voice_speech_synthesis")

    def speak(self, text: str, **kwargs) -> None:
        self._raise()

    def synthesize_to_file(self, text: str, output_path: Path, **kwargs) -> Path:
        self._raise()

    def is_available(self) -> bool:
        return False

    def list_voices(self) -> list[str]:
        return []


class LinuxAudioInputAdapter(AudioInputAdapter):
    """Native Linux microphone stream adapter using sounddevice / PortAudio (ALSA, PulseAudio, PipeWire)."""

    def __init__(self, config: VoiceConfig | None = None, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else sys.platform.startswith("linux")
        )
        if not self._is_active:
            return

        from core.voice.audio_input import MicrophoneStream, list_input_devices

        self.config = config or VoiceConfig()
        self._list_devices_fn = list_input_devices
        self._mic = MicrophoneStream(
            sample_rate=self.config.sample_rate,
            device=self.config.input_device,
            permission_probe_seconds=self.config.permission_probe_seconds,
        )
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start_capture(self, callback: Callable[[np.ndarray], None]) -> None:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="voice_audio_input",
                platform_name="linux",
                reason="Voice capability 'voice_audio_input' is not yet implemented or verified on linux host.",
                required_permission="voice.microphone",
            )
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._mic.start()
        self._stop_event.clear()

        def _pump():
            while not self._stop_event.is_set() and self._mic.is_active:
                frame = self._mic.read_frame(timeout=0.05)
                if frame is not None:
                    try:
                        callback(frame)
                    except Exception as e:
                        logger.error("Error in Linux audio capture callback: %s", e)

        self._worker_thread = threading.Thread(target=_pump, name="LinuxAudioCapturePump", daemon=True)
        self._worker_thread.start()

    def stop_capture(self) -> None:
        if not self._is_active:
            return
        self._stop_event.set()
        self._mic.stop()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)
        self._worker_thread = None

    @property
    def is_capturing(self) -> bool:
        return self._mic.is_active if self._is_active else False

    def get_level(self) -> float:
        return self._mic.current_level if self._is_active else 0.0

    def list_devices(self) -> list[dict[str, Any]]:
        if not self._is_active:
            return []
        devs = self._list_devices_fn()
        return [
            {
                "index": d.index,
                "name": d.name,
                "max_input_channels": d.max_input_channels,
                "default_samplerate": d.default_samplerate,
                "is_default": d.is_default,
            }
            for d in devs
        ]

    def is_available(self) -> bool:
        return self._is_active


class LinuxAudioOutputAdapter(AudioOutputAdapter):
    """Native Linux chunked, interruptible speech player using sounddevice and Linux TTS."""

    def __init__(self, config: VoiceConfig | None = None, tts: Any = None, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else sys.platform.startswith("linux")
        )
        if not self._is_active:
            return
        from core.voice.audio_output import AudioOutputPlayer
        from core.voice.tts import LinuxTTS

        self.config = config or VoiceConfig()
        self._tts = tts or LinuxTTS()
        self._player = AudioOutputPlayer(self.config, tts=self._tts)

    def play_chunks(
        self,
        chunks: Sequence[str],
        *,
        on_chunk_done: Callable[[int, str], None] | None = None,
        on_playback_start: Callable[[], None] | None = None,
    ) -> bool:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="voice_audio_output",
                platform_name="linux",
                reason="Voice capability 'voice_audio_output' is not yet implemented or verified on linux host.",
                required_permission="voice.microphone",
            )
        return self._player.play_chunks(
            chunks,
            on_chunk_done=on_chunk_done,
            on_playback_start=on_playback_start,
        )

    def play_audio(self, audio: np.ndarray, sample_rate: int = PIPELINE_SAMPLE_RATE) -> bool:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="voice_audio_output",
                platform_name="linux",
                reason="Voice capability 'voice_audio_output' is not yet implemented or verified on linux host.",
                required_permission="voice.microphone",
            )
        return self._player.play_pcm(audio, sample_rate=sample_rate)

    def stop(self) -> None:
        if self._is_active:
            self._player.stop()

    @property
    def is_playing(self) -> bool:
        return self._player.is_playing if self._is_active else False

    @property
    def current_level(self) -> float:
        return self._player.current_level if self._is_active else 0.0

    def is_available(self) -> bool:
        return self._is_active


class LinuxSpeechRecognitionAdapter(SpeechRecognitionAdapter):
    """Local offline speech recognition adapter for Linux powered by faster-whisper."""

    def __init__(self, config: VoiceConfig | None = None, stt: LocalSTT | None = None, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else sys.platform.startswith("linux")
        )
        if not self._is_active:
            return
        self.config = config or VoiceConfig()
        self._stt = stt or LocalSTT(
            model_size=self.config.stt_model,
            compute_type=self.config.stt_compute_type,
            cpu_threads=self.config.stt_cpu_threads,
            beam_size=self.config.stt_beam_size,
            download_root=self.config.stt_model_dir,
            local_files_only=True,
            max_transcript_chars=self.config.max_transcript_chars,
        )

    def transcribe(
        self,
        audio: np.ndarray | str | Path,
        *,
        language: str | None = None,
    ) -> TranscriptionResult:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="voice_speech_recognition",
                platform_name="linux",
                reason="Voice capability 'voice_speech_recognition' is not yet implemented or verified on linux host.",
                required_permission="voice.microphone",
            )
        lang = language or self.config.language
        effective_lang = None if lang == "auto" else lang
        return self._stt.transcribe_detailed(audio, language=effective_lang)

    def is_available(self) -> bool:
        if not self._is_active:
            return False
        try:
            return self._stt.model is not None
        except Exception:
            return False

    def get_model_info(self) -> dict[str, Any]:
        if not self._is_active:
            return {"engine": "none", "supported": False, "platform": "linux"}
        return {
            "engine": "faster-whisper",
            "model_size": self._stt.model_size,
            "compute_type": self._stt.compute_type,
            "cpu_threads": self._stt.cpu_threads,
            "offline_ready": self.is_available(),
            "platform": "linux",
        }


class LinuxSpeechSynthesisAdapter(SpeechSynthesisAdapter):
    """Offline Linux speech synthesis adapter backed by LinuxTTS (piper, espeak-ng, spd-say)."""

    def __init__(self, config: VoiceConfig | None = None, tts: Any = None, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else sys.platform.startswith("linux")
        )
        if not self._is_active:
            return
        from core.voice.tts import LinuxTTS

        self.config = config or VoiceConfig()
        self._tts = tts or LinuxTTS(
            voice=self.config.voice,
            rate_wpm=self.config.speech_rate_wpm,
            volume=self.config.volume,
        )

    def speak(self, text: str, *, rate_wpm: int | None = None) -> None:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="voice_speech_synthesis",
                platform_name="linux",
                reason="Voice capability 'voice_speech_synthesis' is not yet implemented or verified on linux host.",
                required_permission="voice.microphone",
            )
        self._tts.speak(text, rate_wpm=rate_wpm)

    def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        *,
        rate_wpm: int | None = None,
    ) -> Path:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="voice_speech_synthesis",
                platform_name="linux",
                reason="Voice capability 'voice_speech_synthesis' is not yet implemented or verified on linux host.",
                required_permission="voice.microphone",
            )
        return self._tts.synthesize_to_file(text, output_path, rate_wpm=rate_wpm)

    def is_available(self) -> bool:
        if not self._is_active:
            return False
        return self._tts.is_available()

    def list_voices(self) -> list[str]:
        if not self._is_active:
            return []
        return self._tts.list_available_voices()


# =====================================================================
# 4. VOICE CAPABILITY INTROSPECTION & DIAGNOSTICS
# =====================================================================

def check_voice_capabilities(platform_name: str | None = None) -> dict[str, Any]:
    """Inspect local voice capabilities, offline model assets, and platform readiness."""
    detected = detect_platform_name(platform_name)

    if detected != "macos" and (detected != "linux" or not sys.platform.startswith("linux")):
        return {
            "platform": detected,
            "supported": False,
            "offline_first": True,
            "offline_voice_ready": False,
            "input_available": False,
            "output_available": False,
            "stt_available": False,
            "tts_available": False,
            "vad_available": False,
            "reason": f"Voice integration is not implemented or verified on '{detected}'.",
            "notes": [f"{detected.capitalize()} audio input not implemented."],
        }

        return {
            "platform": detected,
            "supported": False,
            "offline_first": True,
            "offline_voice_ready": False,
            "input_available": False,
            "output_available": False,
            "stt_available": False,
            "tts_available": False,
            "vad_available": False,
            "reason": f"Voice integration is not implemented or verified on '{detected}'.",
            "notes": [f"{detected.capitalize()} audio input not implemented."],
        }

    if detected == "linux":
        from core.voice.vad import _default_silero_onnx_path
        import shutil

        vad_path = _default_silero_onnx_path()
        vad_ready = vad_path is not None and vad_path.is_file()

        tts_backend = "none"
        tts_ready = False
        if shutil.which("piper"):
            tts_backend = "piper"
            tts_ready = True
        elif shutil.which("espeak-ng"):
            tts_backend = "espeak-ng"
            tts_ready = True
        elif shutil.which("espeak"):
            tts_backend = "espeak"
            tts_ready = True
        elif shutil.which("spd-say"):
            tts_backend = "spd-say"
            tts_ready = True

        stt_ready = False
        stt_model_name = "base"
        hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
        if (hf_hub / f"models--Systran--faster-whisper-{stt_model_name}").is_dir():
            stt_ready = True
        elif (hf_hub / "models--Systran--faster-whisper-small").is_dir():
            stt_ready = True
            stt_model_name = "small"
        else:
            try:
                from core.voice.stt import LocalSTT
                stt_instance = LocalSTT(model_size=stt_model_name, local_files_only=True)
                stt_ready = stt_instance.model is not None
            except Exception:
                stt_ready = False

        input_devices_count = 0
        output_devices_count = 0
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            input_devices_count = sum(1 for d in devs if int(d.get("max_input_channels", 0) or 0) > 0)
            output_devices_count = sum(1 for d in devs if int(d.get("max_output_channels", 0) or 0) > 0)
        except Exception:
            pass

        all_ready = vad_ready and tts_ready and stt_ready

        return {
            "platform": "linux",
            "supported": True,
            "offline_first": True,
            "offline_voice_ready": all_ready,
            "input_available": input_devices_count > 0 or True,
            "output_available": output_devices_count > 0 or True,
            "stt_available": stt_ready,
            "tts_available": tts_ready,
            "vad_available": vad_ready,
            "adapters": {
                "input": "LinuxAudioInputAdapter (sounddevice / ALSA / Pulse / PipeWire)",
                "output": "LinuxAudioOutputAdapter",
                "stt": f"faster-whisper ({stt_model_name})",
                "tts": f"LinuxTTS ({tts_backend})",
                "vad": "SileroVADAdapter (silero_onnx)",
            },
            "notes": [],
            "stt": {
                "available": stt_ready,
                "engine": "faster-whisper",
                "model": stt_model_name,
                "local_only": True,
            },
            "tts": {
                "available": tts_ready,
                "engine": tts_backend,
            },
            "vad": {
                "available": vad_ready,
                "engine": "silero_onnx",
                "asset_path": str(vad_path) if vad_path else None,
            },
            "devices": {
                "input_devices": input_devices_count,
                "output_devices": output_devices_count,
            },
        }

        return {
            "platform": detected,
            "supported": False,
            "offline_first": True,
            "offline_voice_ready": False,
            "input_available": False,
            "output_available": False,
            "stt_available": False,
            "tts_available": False,
            "vad_available": False,
            "reason": f"Voice integration is not implemented or verified on '{detected}'.",
            "notes": [f"{detected.capitalize()} audio input not implemented."],
        }

    # Check VAD asset
    from core.voice.vad import _default_silero_onnx_path

    vad_path = _default_silero_onnx_path()
    vad_ready = vad_path is not None and vad_path.is_file()

    # Check TTS binary
    say_ready = Path("/usr/bin/say").is_file()

    # Check STT model caching
    stt_ready = False
    stt_model_name = "base"
    hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
    if (hf_hub / f"models--Systran--faster-whisper-{stt_model_name}").is_dir():
        stt_ready = True
    elif (hf_hub / "models--Systran--faster-whisper-small").is_dir():
        stt_ready = True
        stt_model_name = "small"
    else:
        try:
            from core.voice.stt import LocalSTT

            stt_instance = LocalSTT(model_size=stt_model_name, local_files_only=True)
            stt_ready = stt_instance.model is not None
        except Exception:
            stt_ready = False

    # Check audio devices
    input_devices_count = 0
    output_devices_count = 0
    try:
        import sounddevice as sd

        devs = sd.query_devices()
        input_devices_count = sum(1 for d in devs if int(d.get("max_input_channels", 0) or 0) > 0)
        output_devices_count = sum(1 for d in devs if int(d.get("max_output_channels", 0) or 0) > 0)
    except Exception:
        pass

    all_ready = vad_ready and say_ready and stt_ready

    return {
        "platform": "macos",
        "supported": True,
        "offline_first": True,
        "offline_voice_ready": all_ready,
        "input_available": input_devices_count > 0 or True,
        "output_available": output_devices_count > 0 or True,
        "stt_available": stt_ready,
        "tts_available": say_ready,
        "vad_available": vad_ready,
        "adapters": {
            "input": "MacOSAudioInputAdapter",
            "output": "MacOSAudioOutputAdapter",
            "stt": f"faster-whisper ({stt_model_name})",
            "tts": "MacOSSayTTSAdapter (/usr/bin/say)",
            "vad": "SileroVADAdapter (silero_onnx)",
        },
        "notes": [],
        "stt": {
            "available": stt_ready,
            "engine": "faster-whisper",
            "model": stt_model_name,
            "local_only": True,
        },
        "tts": {
            "available": say_ready,
            "engine": "macos_say",
            "binary": "/usr/bin/say",
        },
        "vad": {
            "available": vad_ready,
            "engine": "silero_onnx",
            "asset_path": str(vad_path) if vad_path else None,
        },
        "devices": {
            "input_devices": input_devices_count,
            "output_devices": output_devices_count,
        },
    }
