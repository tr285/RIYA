"""Automated tests for VoiceSession, MicrophoneStream, and VAD turn detection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from core.voice.audio_input import MicrophoneStream
from core.voice.config import VAD_FRAME_SAMPLES, VoiceConfig
from core.voice.errors import (
    AudioCaptureError,
    MicrophonePermissionError,
    MicrophoneUnavailableError,
    VoiceSessionBusyError,
)
from core.voice.session import VoiceSession
from core.voice.state import VoiceState
from core.voice.vad import EnergyVAD, SileroStreamingVAD, UtteranceSegmenter


class _FakeStream:
    def __init__(self, *, callback=None, fail_on_start: Exception | None = None, **kwargs):
        self.callback = callback
        self.fail_on_start = fail_on_start
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        if self.fail_on_start is not None:
            raise self.fail_on_start
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class TestVoiceSessionAndVAD(unittest.TestCase):
    def setUp(self):
        VoiceSession._active_session_id = None
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        VoiceSession._active_session_id = None
        self.tmp.cleanup()

    def test_session_init_explicit_mic_activation_and_temp_cleanup(self):
        cfg = VoiceConfig(recordings_dir=str(self.root / "rec"))
        fake_stream = _FakeStream()
        session = VoiceSession(
            cfg,
            mic_factory=lambda c: MicrophoneStream(
                sample_rate=c.sample_rate,
                stream_factory=lambda **kw: fake_stream,
            ),
        )
        # Never opens microphone prior to explicit start().
        self.assertEqual(session.state, VoiceState.IDLE)
        self.assertFalse(session.microphone_active)
        self.assertFalse(fake_stream.started)

        mic = session.start()
        self.assertEqual(session.state, VoiceState.LISTENING)
        self.assertTrue(session.microphone_active)
        self.assertTrue(fake_stream.started)

        temp_dir = session.temp_dir
        self.assertIsNotNone(temp_dir)
        self.assertTrue(temp_dir.is_dir())
        temp_wav = session.create_temp_wav_path("probe")
        temp_wav.write_bytes(b"RIFF")

        # retain_recordings=False must never save permanent files.
        saved = session.maybe_save_recording(np.zeros(512, dtype=np.float32))
        self.assertIsNone(saved)

        mic.stop()
        session.stop()
        self.assertEqual(session.state, VoiceState.IDLE)
        self.assertFalse(session.microphone_active)
        self.assertTrue(fake_stream.stopped)
        self.assertTrue(fake_stream.closed)
        self.assertFalse(temp_dir.exists())

    def test_microphone_unavailable_error(self):
        cfg = VoiceConfig(recordings_dir=str(self.root / "rec"))
        with patch("core.voice.audio_input._query_sounddevice_devices", return_value=([], None, None)):
            session = VoiceSession(cfg)
            with self.assertRaises(MicrophoneUnavailableError):
                session.start()
        self.assertEqual(session.state, VoiceState.IDLE)
        self.assertFalse(session.microphone_active)

    def test_permission_denied_via_os_error_and_zero_probe(self):
        cfg = VoiceConfig(recordings_dir=str(self.root / "rec"))
        # 1) PortAudio/macOS TCC permission denied error.
        session1 = VoiceSession(
            cfg,
            mic_factory=lambda c: MicrophoneStream(
                stream_factory=lambda **kw: _FakeStream(
                    fail_on_start=RuntimeError("Operation not permitted: microphone permission denied")
                )
            ),
        )
        with self.assertRaises(MicrophonePermissionError):
            session1.start()
        self.assertEqual(session1.state, VoiceState.IDLE)

        # 2) macOS TCC silent exact-zero frames across probe window.
        mic = MicrophoneStream(
            check_permission_silence=True,
            permission_probe_seconds=0.35,
            stream_factory=lambda **kw: _FakeStream(**kw),
        )
        mic.start()
        mic.push_test_audio(np.zeros(VAD_FRAME_SAMPLES * 12, dtype=np.float32))
        with self.assertRaises(MicrophonePermissionError):
            mic.read_frame(timeout=0.05)
        mic.stop()

    def test_audio_capture_failure_and_concurrent_session_prevention(self):
        cfg = VoiceConfig(recordings_dir=str(self.root / "rec"))
        bad_stream = _FakeStream()

        def failing_factory(**kw):
            bad_stream.callback = kw["callback"]
            return bad_stream

        mic = MicrophoneStream(stream_factory=failing_factory)
        session_a = VoiceSession(cfg, mic_factory=lambda c: mic)
        session_a.start()

        # Second concurrent session must fail cleanly.
        session_b = VoiceSession(cfg, mic_factory=lambda c: MicrophoneStream(stream_factory=lambda **kw: _FakeStream()))
        with self.assertRaises(VoiceSessionBusyError):
            session_b.start()

        # Runtime capture failure inside audio callback surfaces as AudioCaptureError.
        bad_stream.callback(object(), 512, None, None)
        with self.assertRaises(AudioCaptureError):
            mic.read_frame(timeout=0.05)

        session_a.stop(to_error=True)
        self.assertEqual(session_a.state, VoiceState.ERROR)
        session_a.recover_from_error()
        self.assertEqual(session_a.state, VoiceState.IDLE)

        # Now session B can start cleanly after cleanup.
        session_b.start()
        self.assertEqual(session_b.state, VoiceState.LISTENING)
        session_b.stop()

    def test_vad_speech_start_end_pause_and_silence_handling(self):
        cfg = VoiceConfig(
            vad_engine="energy",
            vad_threshold=0.5,
            vad_neg_threshold=0.35,
            min_speech_ms=96,   # 3 frames
            min_silence_ms=160,  # 5 frames
            speech_pad_ms=64,    # 2 frames
            max_utterance_seconds=2.0,
        )
        seg = UtteranceSegmenter(cfg, backend=EnergyVAD(noise_floor=0.005, speech_rms=0.04))

        # Silence only never triggers speech.
        silence = np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32)
        for _ in range(10):
            ev = seg.process_frame(silence)
            self.assertFalse(ev.speech_started)
            self.assertFalse(ev.speech_ended)

        # Continuous speech tone -> triggers speech_started.
        t = np.arange(VAD_FRAME_SAMPLES, dtype=np.float32) / 16000.0
        tone = 0.08 * np.sin(2.0 * np.pi * 220.0 * t).astype(np.float32)
        started = False
        for _ in range(5):
            ev = seg.process_frame(tone)
            if ev.speech_started:
                started = True
        self.assertTrue(started)
        self.assertTrue(seg.in_speech)

        # Brief natural pause (2 frames < 5 min_silence_frames) must NOT cut off user!
        for _ in range(2):
            ev = seg.process_frame(silence)
            self.assertFalse(ev.speech_ended)
        self.assertTrue(seg.in_speech)

        # More speech, then sustained trailing silence -> speech_ended with utterance.
        seg.process_frame(tone)
        ended_utterance = None
        for _ in range(8):
            ev = seg.process_frame(silence)
            if ev.speech_ended:
                ended_utterance = ev.utterance
                break
        self.assertIsNotNone(ended_utterance)
        self.assertFalse(seg.in_speech)
        self.assertGreater(ended_utterance.size, VAD_FRAME_SAMPLES * 5)

    def test_real_bundled_silero_onnx_vad_offline(self):
        # Exercises the real local bundled silero_vad_v6.onnx without network.
        vad = SileroStreamingVAD()
        silence = np.zeros(VAD_FRAME_SAMPLES, dtype=np.float32)
        probs = [vad.speech_probability(silence) for _ in range(5)]
        self.assertTrue(all(0.0 <= p <= 1.0 for p in probs))
        self.assertLess(max(probs), 0.3)

    def test_retain_recordings_opt_in_only(self):
        rec_dir = self.root / "retained"
        cfg = VoiceConfig(retain_recordings=True, recordings_dir=str(rec_dir))
        session = VoiceSession(
            cfg,
            mic_factory=lambda c: MicrophoneStream(stream_factory=lambda **kw: _FakeStream()),
        )
        session.start()
        path = session.maybe_save_recording(np.ones(VAD_FRAME_SAMPLES, dtype=np.float32) * 0.02)
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        session.stop()


if __name__ == "__main__":
    unittest.main()
