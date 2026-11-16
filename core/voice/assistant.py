"""Backward-compatible single-turn voice facade integrated with RIYAAssistant."""

from __future__ import annotations

import os
import tempfile
import warnings
from pathlib import Path
from typing import Any

from core.ai.local_runtime import LocalLlamaRuntime
from core.voice.config import VoiceConfig
from core.voice.microphone import Microphone
from core.voice.stt import LocalSTT
from core.voice.tts import LocalTTS


class VoiceAssistant:
    """Offline single-turn voice facade (legacy Phase 5 compatibility adapter).

    .. deprecated:: 0.5.0
        Use :class:`core.voice.orchestrator.VoiceOrchestrator` for real-time
        continuous or push-to-talk voice conversation.
    """

    def __init__(
        self,
        audio_path: str | None = None,
        *,
        assistant: Any | None = None,
        config: VoiceConfig | None = None,
    ):
        warnings.warn(
            "VoiceAssistant is a legacy Phase 5 single-turn adapter. Use "
            "core.voice.orchestrator.VoiceOrchestrator for real-time voice sessions.",
            category=DeprecationWarning,
            stacklevel=2,
        )
        self.config = config or VoiceConfig()
        self.audio_path = audio_path
        self.assistant = assistant

        self.microphone = Microphone(
            sample_rate=self.config.sample_rate,
            device=self.config.input_device,
        )
        self.stt = LocalSTT(
            model_size=self.config.stt_model,
            compute_type=self.config.stt_compute_type,
            cpu_threads=self.config.stt_cpu_threads,
            beam_size=self.config.stt_beam_size,
            download_root=self.config.stt_model_dir,
            local_files_only=True,
        )
        self.tts = LocalTTS(
            voice=self.config.voice,
            rate_wpm=self.config.speech_rate_wpm,
            volume=self.config.volume,
        )
        self.runtime = LocalLlamaRuntime() if assistant is None else assistant.ai

    def listen_and_respond(self, duration: float = 5) -> str:
        """Listen, transcribe, process via RIYAAssistant/runtime, and speak response."""

        own_temp = self.audio_path is None
        if own_temp:
            fd, temp_name = tempfile.mkstemp(prefix="riya_turn_", suffix=".wav")
            os.close(fd)
            os.chmod(temp_name, 0o600)
            wav_path = Path(temp_name)
        else:
            wav_path = Path(self.audio_path)

        try:
            self.microphone.record(duration=duration, output_path=str(wav_path))
            text = self.stt.transcribe(str(wav_path), language=self.config.stt_language)

            if not text:
                response = "I couldn't hear anything clearly."
                if self.config.voice_responses_enabled:
                    self.tts.speak(response)
                return response

            if self.assistant is not None:
                response = self.assistant.process(text)
            else:
                self.runtime.load_model()
                try:
                    response = self.runtime.generate(text)
                finally:
                    self.runtime.unload_model()

            if self.config.voice_responses_enabled:
                self.tts.speak(response)
            return response
        finally:
            if not self.config.retain_recordings and wav_path.exists():
                try:
                    wav_path.unlink()
                except OSError:
                    pass
