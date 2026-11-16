"""Automated test suite for RIYA Linux Voice & Audio Integration (Milestone 5.2).

Covers:
- LinuxAudioInputAdapter (microphone capture, device enumeration, PortAudio stream).
- LinuxAudioOutputAdapter (PCM playback, chunked synthesis, barge-in interruption).
- LinuxSpeechRecognitionAdapter (faster-whisper CTranslate2 offline transcription).
- LinuxSpeechSynthesisAdapter & LinuxTTS (piper, espeak-ng, spd-say backends, validation, WAV synthesis).
- TTSFactory multi-platform routing.
- check_voice_capabilities("linux") truthful diagnostic evaluation.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from core.voice.adapters import (
    LinuxAudioInputAdapter,
    LinuxAudioOutputAdapter,
    LinuxSpeechRecognitionAdapter,
    LinuxSpeechSynthesisAdapter,
    check_voice_capabilities,
)
from core.voice.config import VoiceConfig
from core.voice.errors import TTSError
from core.voice.tts import LinuxTTS, LocalTTS, TTSFactory


# =============================================================================
# 1. Linux Audio Input Adapter Tests
# =============================================================================

def test_linux_audio_input_adapter_device_listing():
    """Verify Linux microphone enumeration returns structured PortAudio devices."""
    adapter = LinuxAudioInputAdapter(force_active=True)
    devs = adapter.list_devices()
    assert isinstance(devs, list)
    assert adapter.is_available() is True


def test_linux_audio_input_adapter_capture_lifecycle():
    """Verify clean start, frame delivery to callback, and resource release."""
    adapter = LinuxAudioInputAdapter(force_active=True)
    frames_received = []

    # Mock underlying mic stream
    mock_frame = np.zeros(512, dtype=np.float32)
    adapter._mic = MagicMock()
    adapter._mic.is_active = True
    adapter._mic.read_frame.side_effect = [mock_frame, None]
    adapter._mic.current_level = 0.42

    def callback(frame):
        frames_received.append(frame)
        adapter._stop_event.set()

    adapter.start_capture(callback)
    # Wait for thread to process
    if adapter._worker_thread:
        adapter._worker_thread.join(timeout=1.0)
    adapter.stop_capture()

    assert len(frames_received) >= 1
    assert adapter.get_level() == 0.42
    assert adapter._worker_thread is None


# =============================================================================
# 2. Linux Audio Output Adapter Tests
# =============================================================================

def test_linux_audio_output_adapter_playback():
    """Test PCM audio playback and barge-in stop on Linux."""
    adapter = LinuxAudioOutputAdapter(force_active=True)
    adapter._player = MagicMock()
    adapter._player.is_playing = False
    adapter._player.current_level = 0.5
    adapter._player.play_pcm.return_value = True

    pcm_data = np.zeros(16000, dtype=np.float32)
    res = adapter.play_audio(pcm_data)
    assert res is True
    assert adapter._player.play_pcm.called

    adapter.stop()
    assert adapter._player.stop.called
    assert adapter.current_level == 0.5


# =============================================================================
# 3. Linux Speech Recognition Adapter Tests
# =============================================================================

def test_linux_speech_recognition_adapter_metadata():
    """Test faster-whisper adapter reporting for Linux."""
    mock_stt = MagicMock()
    mock_stt.model_size = "base"
    mock_stt.compute_type = "int8"
    mock_stt.cpu_threads = 4
    mock_stt.model = MagicMock()

    adapter = LinuxSpeechRecognitionAdapter(stt=mock_stt, force_active=True)
    assert adapter.is_available() is True

    info = adapter.get_model_info()
    assert info["engine"] == "faster-whisper"
    assert info["platform"] == "linux"
    assert info["offline_ready"] is True



# =============================================================================
# 4. Linux Speech Synthesis & LinuxTTS Tests
# =============================================================================

def test_linux_tts_backend_detection():
    """Test auto-detection of Linux TTS backends."""
    # When piper is present
    with patch("shutil.which", side_effect=lambda x: "/usr/bin/piper" if x == "piper" else None):
        tts = LinuxTTS()
        assert tts._selected_backend == "piper"

    # When only espeak-ng is present
    with patch("shutil.which", side_effect=lambda x: "/usr/bin/espeak-ng" if x == "espeak-ng" else None):
        tts = LinuxTTS()
        assert tts._selected_backend == "espeak-ng"

    # When only spd-say is present
    with patch("shutil.which", side_effect=lambda x: "/usr/bin/spd-say" if x == "spd-say" else None):
        tts = LinuxTTS()
        assert tts._selected_backend == "spd-say"

    # When no TTS backend is installed
    with patch("shutil.which", return_value=None):
        tts = LinuxTTS()
        assert tts._selected_backend == "none"
        assert tts.is_available() is False
        with pytest.raises(TTSError):
            tts.speak("Hello")


def test_linux_tts_speak_via_espeak():
    """Test espeak-ng parameter array execution and rate control."""
    with patch("shutil.which", return_value="/usr/bin/espeak-ng"):
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(returncode=0)
            tts = LinuxTTS(backend="espeak-ng", rate_wpm=180, voice="en-us")
            tts.speak("Hello from Linux assistant")

            assert mock_sub.called
            cmd = mock_sub.call_args.args[0]
            assert cmd == ["/usr/bin/espeak-ng", "-s", "180", "-v", "en-us", "Hello from Linux assistant"]
            assert mock_sub.call_args.kwargs.get("shell") is False


def test_linux_tts_synthesize_to_wav(tmp_path):
    """Test WAV file generation via espeak-ng."""
    out_wav = tmp_path / "speech.wav"

    def fake_run(cmd, **kwargs):
        # Create output file
        out_wav.write_bytes(b"RIFF" + b"\x00" * 100)
        return MagicMock(returncode=0)

    with patch("shutil.which", return_value="/usr/bin/espeak-ng"):
        with patch("subprocess.run", side_effect=fake_run) as mock_sub:
            tts = LinuxTTS(backend="espeak-ng")
            target = tts.synthesize_to_wav("Testing synthesis", out_wav)
            assert target == out_wav
            assert target.is_file()
            cmd = mock_sub.call_args.args[0]
            assert cmd[:4] == ["/usr/bin/espeak-ng", "-w", str(out_wav), "-s"]


def test_linux_speech_synthesis_adapter():
    """Test LinuxSpeechSynthesisAdapter interface."""
    mock_tts = MagicMock()
    mock_tts.is_available.return_value = True
    mock_tts.list_available_voices.return_value = ["default", "en-us"]

    adapter = LinuxSpeechSynthesisAdapter(tts=mock_tts, force_active=True)
    assert adapter.is_available() is True
    assert adapter.list_voices() == ["default", "en-us"]

    adapter.speak("Test voice")
    assert mock_tts.speak.called


# =============================================================================
# 5. Multi-Platform TTSFactory Tests
# =============================================================================

def test_tts_factory_creates_proper_engine():
    """Verify TTSFactory instantiates LinuxTTS for Linux and LocalTTS for macOS."""
    linux_engine = TTSFactory.create_tts_engine(platform_name="linux")
    assert isinstance(linux_engine, LinuxTTS)

    macos_engine = TTSFactory.create_tts_engine(platform_name="macos")
    assert isinstance(macos_engine, LocalTTS)


# =============================================================================
# 6. check_voice_capabilities Diagnostics Tests
# =============================================================================

def test_check_voice_capabilities_linux():
    """Verify check_voice_capabilities truthfully reports Linux voice diagnostic state."""
    with patch("sys.platform", "linux"):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/espeak-ng" if x == "espeak-ng" else None):
            with patch("core.voice.vad._default_silero_onnx_path", return_value=Path("/tmp/silero.onnx")):
                with patch("pathlib.Path.is_file", return_value=True):
                    diag = check_voice_capabilities(platform_name="linux")
                    assert diag["platform"] == "linux"
                    assert diag["supported"] is True
                    assert diag["tts"]["engine"] == "espeak-ng"
                    assert diag["tts"]["available"] is True
                    assert "LinuxAudioInputAdapter" in diag["adapters"]["input"]

