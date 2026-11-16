"""Automated tests for LocalTTS, personality chunking, and AudioOutputPlayer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core.voice.audio_output import AudioOutputPlayer
from core.voice.config import VoiceConfig
from core.voice.errors import TTSError
from core.voice.personality import prepare_text_for_speech, split_into_speech_chunks, style_rate_wpm
from core.voice.tts import LocalTTS


class _FakeOutputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.writes = []
        self.aborted = False
        self.stopped = False
        self.closed = False

    def start(self):
        pass

    def write(self, data):
        self.writes.append(data.copy())

    def abort(self):
        self.aborted = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class TestVoiceTTSAndAudioOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tts_success_failure_validation_and_honest_capabilities(self):
        with self.assertRaises(ValueError):
            LocalTTS(voice="-f /etc/passwd")
        with self.assertRaises(ValueError):
            LocalTTS(rate_wpm=999)
        with self.assertRaises(ValueError):
            LocalTTS(volume=2.0)

        tts = LocalTTS(voice="Samantha", rate_wpm=190)
        self.assertFalse(tts.supports_emotional_prosody)

        with patch("core.voice.tts.subprocess.run") as mock_run:
            tts.speak("Hello there!")
        self.assertEqual(mock_run.call_args.args[0], ["/usr/bin/say", "-v", "Samantha", "-r", "190", "-f", "-"])

        with patch("core.voice.tts.subprocess.run", side_effect=OSError("say failed")):
            with self.assertRaises(TTSError):
                tts.speak("Hello again")

    def test_personality_formatting_and_sentence_chunking_no_midword_splits(self):
        raw = "**Here** is your answer: First sentence works nicely! Second sentence checks `inline_code`. What next?"
        prepared = prepare_text_for_speech(raw, style="calm")
        self.assertNotIn("**", prepared)
        self.assertNotIn("`", prepared)

        chunks = split_into_speech_chunks(prepared, max_chunk_chars=60)
        self.assertGreaterEqual(len(chunks), 2)
        rejoined_words = " ".join(chunks).split()
        self.assertEqual(rejoined_words, prepared.split())

        # Confirmation prompts preserve exact security meaning.
        confirm = "Confirm write_file(path='/tmp/a.txt')? Proceed?"
        self.assertEqual(prepare_text_for_speech(confirm), confirm)
        self.assertLess(style_rate_wpm(180, "calm"), style_rate_wpm(180, "excited"))

    def test_pcm_chunked_playback_cancellation_and_temp_cleanup(self):
        class _FakeWavTTS:
            supports_wav_synthesis = True
            supports_emotional_prosody = False

            def synthesize_to_wav(self, text, output_path, *, rate_wpm=None):
                p = Path(output_path)
                sf.write(str(p), np.ones(4096, dtype=np.float32) * 0.25, 16000)
                return p

        work_dir = self.root / "work"
        created_streams: list[_FakeOutputStream] = []

        def stream_factory(**kwargs):
            s = _FakeOutputStream(**kwargs)
            created_streams.append(s)
            return s

        cfg = VoiceConfig(speech_chunk_chars=40, volume=0.5)
        player = AudioOutputPlayer(cfg, tts=_FakeWavTTS(), output_stream_factory=stream_factory)

        # Interrupt immediately after chunk 0 finishes so chunk 1 and 2 must never play.
        completed = player.play_text_chunks(
            ["First sentence.", "Second sentence.", "Third sentence."],
            temp_dir=work_dir,
            on_chunk_done=lambda idx, _c: player.stop() if idx == 0 else None,
        )
        self.assertFalse(completed)
        self.assertTrue(player.cancelled)
        self.assertEqual(player.spoken_chunks, ["First sentence."])
        self.assertFalse(player.is_playing)
        # Chunk WAV files cleaned up immediately.
        self.assertEqual(list(work_dir.glob("*.wav")), [])
        self.assertEqual(len(created_streams), 1)
        self.assertTrue(created_streams[0].stopped)


if __name__ == "__main__":
    unittest.main()
