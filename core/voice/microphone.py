"""Fixed-duration microphone helper built on RIYA's audio input boundary."""

from __future__ import annotations

import warnings
from pathlib import Path

import sounddevice as sd
import soundfile as sf

from core.voice.audio_input import classify_audio_exception, resolve_input_device
from core.voice.errors import AudioCaptureError


class Microphone:
    """Local fixed-duration microphone recorder (legacy Phase 5 compatibility adapter).

    .. deprecated:: 0.5.0
        Use :class:`core.voice.audio_input.MicrophoneStream` or
        :class:`core.voice.orchestrator.VoiceOrchestrator` for real-time streaming
        voice capture.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device: int | str | None = None,
    ):
        warnings.warn(
            "Microphone is a legacy Phase 5 compatibility helper. Use "
            "core.voice.audio_input.MicrophoneStream or VoiceOrchestrator instead.",
            category=DeprecationWarning,
            stacklevel=2,
        )
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer.")
        if isinstance(channels, bool) or not isinstance(channels, int) or not (1 <= channels <= 2):
            raise ValueError("channels must be 1 or 2.")
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

    def record(self, duration: float, output_path: str) -> str:
        """Record microphone audio for ``duration`` seconds and save it as WAV."""

        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not (0.1 <= duration <= 120.0):
            raise ValueError("duration must be between 0.1 and 120.0 seconds.")
        if not isinstance(output_path, str) or not output_path or "\x00" in output_path:
            raise ValueError("output_path must be a non-empty file path string.")

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        frames = int(round(duration * self.sample_rate))

        device_idx: int | str | None = self.device
        if self.device is not None:
            device_idx, _ = resolve_input_device(self.device)

        try:
            audio = sd.rec(
                frames,
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                device=device_idx,
            )
            sd.wait()
        except Exception as exc:
            raise classify_audio_exception(exc) from exc

        try:
            sf.write(str(target), audio, self.sample_rate)
        except Exception as exc:
            raise AudioCaptureError(f"Failed to write recorded audio: {exc}") from exc

        return str(target)
