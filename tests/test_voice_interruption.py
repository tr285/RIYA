"""Automated tests for real-time user interruption (barge-in) and stale audio suppression."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from core.agent.context import ConversationContext
from core.agent.intent import IntentDetector
from core.voice.audio_input import MicrophoneStream
from core.voice.audio_output import AudioOutputPlayer
from core.voice.config import VAD_FRAME_SAMPLES, VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession
from core.voice.state import VoiceState
from core.voice.stt import TranscriptionResult
from core.voice.vad import EnergyVAD


class _FakeMicInnerStream:
    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _make_stub_assistant():
    assistant = Mock()
    assistant.context = ConversationContext(max_messages=12)
    assistant.intent_detector = IntentDetector()
    assistant.pending_action = None
    return assistant


class TestVoiceInterruption(unittest.TestCase):
    def setUp(self):
        VoiceSession._active_session_id = None
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        VoiceSession._active_session_id = None
        self.tmp.cleanup()

    def test_barge_in_during_speaking_stops_playback_and_processes_new_speech(self):
        assistant = _make_stub_assistant()
        speaking_entered = threading.Event()
        turn_calls: list[str] = []

        def fake_process(text: str) -> str:
            turn_calls.append(text)
            if "weather" in text.lower():
                return (
                    "First very long paragraph that will be interrupted before finishing. "
                    "Second paragraph should never be spoken aloud."
                )
            return "Here is the current time instead."

        assistant.process.side_effect = fake_process

        spoken_log: list[str] = []

        class _SlowInterruptibleTTS:
            supports_wav_synthesis = False
            supports_emotional_prosody = False

            def speak(self, chunk: str, *, rate_wpm=None):
                spoken_log.append(chunk)
                speaking_entered.set()
                # Simulate realistic ~250ms per chunk without blocking fast cancellation tests forever.
                time.sleep(0.22)

        # Alternate STT transcripts for turn 1 and barge-in turn 2.
        stt_transcripts = iter(
            [
                TranscriptionResult("Tell me the weather", "en", 0.95, 0.01, 1.0),
                TranscriptionResult("Stop and tell me what time it is", "en", 0.95, 0.01, 1.0),
            ]
        )
        fake_stt = SimpleNamespace(
            transcribe_detailed=lambda audio, language=None: next(stt_transcripts)
        )

        cfg = VoiceConfig(
            vad_engine="energy",
            vad_threshold=0.5,
            vad_neg_threshold=0.35,
            min_speech_ms=64,          # 2 frames
            min_silence_ms=128,        # 4 frames
            speech_pad_ms=32,          # 1 frame
            barge_in_threshold=0.7,
            barge_in_min_speech_ms=64, # 2 frames
            echo_guard_ms=32,          # 1 frame
            speech_chunk_chars=68,
            recordings_dir=str(self.root / "rec"),
        )
        mic = MicrophoneStream(stream_factory=lambda **kw: _FakeMicInnerStream())
        orch = VoiceOrchestrator(
            assistant,
            config=cfg,
            stt=fake_stt,
            tts=_SlowInterruptibleTTS(),
            mic_factory=lambda _c: mic,
            vad_backend=EnergyVAD(noise_floor=0.005, speech_rms=0.04),
        )

        orch.start_voice_mode(background_workers=True)
        try:
            t = np.arange(VAD_FRAME_SAMPLES, dtype=np.float32) / 16000.0
            speech = 0.09 * np.sin(2.0 * np.pi * 240.0 * t).astype(np.float32)
            silence = np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32)

            # Turn 1: user speaks -> ends -> RIYA enters SPEAKING.
            mic.push_test_audio(np.tile(speech, 4))
            mic.push_test_audio(np.tile(silence, 6))
            self.assertTrue(speaking_entered.wait(timeout=2.0))

            # Wait past echo_guard_ms (1 frame = 32ms), then barge in with user speech!
            mic.push_test_audio(silence)
            mic.push_test_audio(np.tile(speech, 4))
            mic.push_test_audio(np.tile(silence, 6))

            deadline = time.monotonic() + 3.0
            while len(turn_calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)

            # Wait briefly for second response to speak.
            deadline = time.monotonic() + 2.0
            while "Here is the current time instead." not in spoken_log and time.monotonic() < deadline:
                time.sleep(0.02)

            self.assertEqual(len(turn_calls), 2)
            # Crucially, chunk 2 of the first interrupted response MUST NEVER have been spoken!
            self.assertFalse(any("Second paragraph" in s for s in spoken_log))
            self.assertIn("Here is the current time instead.", spoken_log)
        finally:
            orch.stop_voice_mode()

    def test_stale_thinking_response_discarded_after_interrupt(self):
        assistant = _make_stub_assistant()
        thinking_started = threading.Event()
        allow_finish = threading.Event()

        def slow_process(text: str) -> str:
            thinking_started.set()
            allow_finish.wait(timeout=2.0)
            return "STALE RESPONSE SHOULD BE DISCARDED"

        assistant.process.side_effect = slow_process
        spoken: list[str] = []
        fake_stt = SimpleNamespace(
            transcribe_detailed=lambda audio, language=None: TranscriptionResult(
                "slow question", "en", 0.9, 0.01, 1.0
            )
        )
        fake_tts = SimpleNamespace(
            supports_wav_synthesis=False,
            speak=lambda chunk, **kw: spoken.append(chunk),
        )
        cfg = VoiceConfig(vad_engine="energy", recordings_dir=str(self.root / "rec"))
        orch = VoiceOrchestrator(
            assistant,
            config=cfg,
            stt=fake_stt,
            tts=fake_tts,
            mic_factory=lambda _c: MicrophoneStream(stream_factory=lambda **kw: _FakeMicInnerStream()),
            vad_backend=EnergyVAD(),
        )
        orch.start_voice_mode(background_workers=False)
        try:
            err_holder: list[Exception] = []

            def run_turn():
                try:
                    orch.handle_utterance_audio(np.ones(1600, dtype=np.float32) * 0.05)
                except Exception as exc:
                    err_holder.append(exc)

            th = threading.Thread(target=run_turn)
            th.start()
            self.assertTrue(thinking_started.wait(timeout=1.5))

            orch.interrupt("user_cancelled_thinking")
            self.assertEqual(orch.state, VoiceState.LISTENING)
            allow_finish.set()
            th.join(timeout=1.5)

            self.assertEqual(err_holder, [])
            self.assertEqual(spoken, [])
            self.assertFalse(any("STALE RESPONSE" in e.text for e in orch.session.transcript_entries()))
        finally:
            orch.stop_voice_mode()


if __name__ == "__main__":
    unittest.main()
