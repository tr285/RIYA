"""Automated tests for local offline Speech-to-Text (LocalSTT)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from core.voice.errors import STTModelUnavailableError, TranscriptionError
from core.voice.stt import LocalSTT, clear_stt_model_cache


class TestLocalSTT(unittest.TestCase):
    def setUp(self):
        clear_stt_model_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        clear_stt_model_cache()
        self.tmp.cleanup()

    def test_transcription_success_from_array_and_file_without_network(self):
        fake_segments = [
            SimpleNamespace(text=" Hello RIYA,\x00 ", avg_logprob=-0.2, no_speech_prob=0.01),
            SimpleNamespace(text="   uh   ", avg_logprob=-1.5, no_speech_prob=0.92),  # hallucination filtered
            SimpleNamespace(text="what time is it?", avg_logprob=-0.25, no_speech_prob=0.02),
        ]
        fake_info = SimpleNamespace(language="en", duration=2.4)

        class _FakeModel:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

            def transcribe(self, audio, **kwargs):
                return iter(fake_segments), fake_info

        with patch("core.voice.stt.WhisperModel", _FakeModel):
            stt = LocalSTT(model_size="base", device="cpu", compute_type="int8")
            self.assertTrue(stt.model.kwargs["local_files_only"])

            res = stt.transcribe_detailed(np.ones(16000, dtype=np.float32) * 0.05)
            self.assertEqual(res.text, "Hello RIYA, what time is it?")
            self.assertEqual(res.language, "en")
            self.assertGreater(res.confidence, 0.7)

            wav_file = self.root / "sample.wav"
            wav_file.write_bytes(b"fake")
            self.assertEqual(stt.transcribe(str(wav_file)), "Hello RIYA, what time is it?")

    def test_model_caching_reuses_instance_and_missing_model_fails_closed(self):
        init_calls = []

        class _CountingModel:
            def __init__(self, *args, **kwargs):
                init_calls.append(args)

        with patch("core.voice.stt.WhisperModel", _CountingModel):
            a = LocalSTT(model_size="base")
            b = LocalSTT(model_size="base")
            self.assertIs(a.model, b.model)
            self.assertEqual(len(init_calls), 1)

        class _MissingModel:
            def __init__(self, *args, **kwargs):
                raise FileNotFoundError("Cannot find the requested files in snapshot cache")

        with patch("core.voice.stt.WhisperModel", _MissingModel):
            with self.assertRaises(STTModelUnavailableError) as ctx:
                LocalSTT(model_size="medium", local_files_only=True)
            self.assertIn("medium", ctx.exception.user_message)

    def test_transcription_failure_and_empty_audio(self):
        class _BrokenModel:
            def transcribe(self, audio, **kwargs):
                raise RuntimeError("decoder error")

        stt = LocalSTT(model_size="base", model=_BrokenModel())
        with self.assertRaises(TranscriptionError):
            stt.transcribe(np.ones(512, dtype=np.float32))
        with self.assertRaises(TranscriptionError):
            stt.transcribe(str(self.root / "nonexistent.wav"))

        # Empty numpy waveform returns empty string gracefully without invoking model.
        self.assertEqual(stt.transcribe(np.zeros(0, dtype=np.float32)), "")


if __name__ == "__main__":
    unittest.main()
