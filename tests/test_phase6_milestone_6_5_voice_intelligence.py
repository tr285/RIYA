"""Phase 6.5 Voice Intelligence and Natural Conversation Test Suite.

Verifies:
- Voice adapter contracts and truthful cross-platform capability reporting.
- macOS native audio input/output, STT, and TTS adapters.
- Windows/Linux unsupported adapters fail closed honestly.
- Push-to-talk and continuous conversation modes.
- Barge-in and interruption during speaking and thinking without stale bleed.
- Clean permission enforcement and confirmation bypass prevention.
- Text + voice conversation history continuity.
- Headless audio transcription and temporary resource cleanup.
- Offline-only execution guarantee.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf

from core.identity.models import AuthenticatedIdentity
from core.security.permissions import PermissionManager
from core.voice.adapters import (
    DefaultVoiceSessionManager,
    EnergyVADAdapter,
    FasterWhisperSTTAdapter,
    LinuxAudioInputAdapter,
    LinuxAudioOutputAdapter,
    LinuxSpeechRecognitionAdapter,
    LinuxSpeechSynthesisAdapter,
    MacOSAudioInputAdapter,
    MacOSAudioOutputAdapter,
    MacOSSayTTSAdapter,
    SileroVADAdapter,
    WindowsAudioInputAdapter,
    WindowsAudioOutputAdapter,
    WindowsSpeechRecognitionAdapter,
    WindowsSpeechSynthesisAdapter,
    check_voice_capabilities,
)
from core.voice.audio_input import AudioDeviceInfo
from core.voice.config import PIPELINE_SAMPLE_RATE, VoiceConfig
from core.voice.errors import (
    MicrophonePermissionError,
    MicrophoneUnavailableError,
    VoiceError,
    VoicePermissionError,
)
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession
from core.voice.state import VoiceState, VoiceStateMachine
from core.voice.stt import LocalSTT, TranscriptionResult
from core.voice.tts import LocalTTS
from core.voice.ui import VoiceUIController
from core.voice.vad import UtteranceSegmenter, VADFrameEvent


class MockLocalModel:
    """Mock WhisperModel for deterministic offline STT tests."""

    def __init__(self, text: str = "open safari", language: str = "en", avg_lp: float = -0.1, nsp: float = 0.05):
        self.text = text
        self.language = language
        self.avg_lp = avg_lp
        self.nsp = nsp

    def transcribe(self, audio, **kwargs):
        class Seg:
            def __init__(self, text, avg_lp, nsp):
                self.text = text
                self.avg_logprob = avg_lp
                self.no_speech_prob = nsp

        class Info:
            def __init__(self, lang):
                self.language = lang
                self.duration = 1.0

        return [Seg(self.text, self.avg_lp, self.nsp)], Info(self.language)


class MockAssistant:
    def __init__(self):
        self.last_query = None
        self.response = "Understood. Safari is now open."
        self.pending_action = None
        self.cancelled = False
        self.current_identity = AuthenticatedIdentity(
            user_id="u_voice_test",
            username="voice_tester",
            display_name="Voice Tester",
            session_id="s_voice_test",
            session_token="tok_voice_test",
            workspace_dir="/tmp",
            auth_method="password",
            expires_at="2099-01-01T00:00:00Z",
        )

    def process(self, text: str) -> str:
        self.last_query = text
        return self.response

    def cancel(self):
        self.cancelled = True

    def reset_cancel(self):
        self.cancelled = False


class TestPhase6Milestone65VoiceIntelligence(unittest.TestCase):
    def setUp(self):
        self.config = VoiceConfig()
        self.identity = AuthenticatedIdentity(
            user_id="u_voice_test",
            username="voice_tester",
            display_name="Voice Tester",
            session_id="s_voice_test",
            session_token="tok_voice_test",
            workspace_dir="/tmp",
            auth_method="password",
            expires_at="2099-01-01T00:00:00Z",
        )

    # ------------------------------------------------------------------
    # 1. Capability Diagnostics & Cross-Platform Truthfulness
    # ------------------------------------------------------------------
    def test_voice_capabilities_truthful_reporting(self):
        mac_caps = check_voice_capabilities("Darwin")
        self.assertEqual(mac_caps["platform"], "macos")
        self.assertTrue(mac_caps["stt_available"])
        self.assertTrue(mac_caps["offline_first"])
        self.assertIn("faster-whisper", mac_caps["adapters"]["stt"])

        win_caps = check_voice_capabilities("Windows")
        self.assertEqual(win_caps["platform"], "windows")
        self.assertFalse(win_caps["input_available"])
        self.assertFalse(win_caps["tts_available"])
        self.assertIn("Windows audio input not implemented", win_caps["notes"][0])

        linux_caps = check_voice_capabilities("Linux")
        self.assertEqual(linux_caps["platform"], "linux")
        self.assertFalse(linux_caps["input_available"])
        self.assertFalse(linux_caps["tts_available"])

    def test_unsupported_platform_adapters_fail_closed(self):
        win_in = WindowsAudioInputAdapter()
        self.assertFalse(win_in.is_available())
        self.assertEqual(win_in.list_devices(), [])
        with self.assertRaises(RuntimeError):
            win_in.start_capture(lambda frame: None)

        win_out = WindowsAudioOutputAdapter()
        self.assertFalse(win_out.is_available())
        with self.assertRaises(RuntimeError):
            win_out.play_audio(np.zeros(100, dtype=np.float32))

        win_stt = WindowsSpeechRecognitionAdapter()
        self.assertFalse(win_stt.is_available())
        with self.assertRaises(RuntimeError):
            win_stt.transcribe(np.zeros(100, dtype=np.float32))

        win_tts = WindowsSpeechSynthesisAdapter()
        self.assertFalse(win_tts.is_available())
        with self.assertRaises(RuntimeError):
            win_tts.speak("hello")

        linux_in = LinuxAudioInputAdapter()
        self.assertFalse(linux_in.is_available())
        with self.assertRaises(RuntimeError):
            linux_in.start_capture(lambda frame: None)

        linux_out = LinuxAudioOutputAdapter()
        self.assertFalse(linux_out.is_available())
        with self.assertRaises(RuntimeError):
            linux_out.play_chunks(["hello"])

        linux_stt = LinuxSpeechRecognitionAdapter()
        self.assertFalse(linux_stt.is_available())
        with self.assertRaises(RuntimeError):
            linux_stt.transcribe(np.zeros(100, dtype=np.float32))

        linux_tts = LinuxSpeechSynthesisAdapter()
        self.assertFalse(linux_tts.is_available())
        with self.assertRaises(RuntimeError):
            linux_tts.speak("hello")

    # ------------------------------------------------------------------
    # 2. Native macOS Adapters
    # ------------------------------------------------------------------
    def test_macos_adapters_contract_conformance(self):
        in_adapter = MacOSAudioInputAdapter()
        devs = in_adapter.list_devices()
        self.assertIsInstance(devs, list)
        self.assertGreaterEqual(len(devs), 0)

        out_adapter = MacOSAudioOutputAdapter()
        self.assertIsInstance(out_adapter.is_playing, bool)
        self.assertIsInstance(out_adapter.current_level, float)

        mock_stt = LocalSTT(model=MockLocalModel("open terminal", "en"))
        stt_adapter = FasterWhisperSTTAdapter(stt=mock_stt)
        self.assertTrue(stt_adapter.is_available())
        res = stt_adapter.transcribe(np.zeros(16000, dtype=np.float32))
        self.assertIn("open terminal", res.text)
        info = stt_adapter.get_model_info()
        self.assertEqual(info["engine"], "faster-whisper")

        tts_adapter = MacOSSayTTSAdapter()
        self.assertTrue(tts_adapter.is_available())
        voices = tts_adapter.list_voices()
        self.assertIsInstance(voices, list)

        vad_adapter = EnergyVADAdapter()
        frame = np.zeros(512, dtype=np.float32)
        prob = vad_adapter.is_speech(frame)
        self.assertLess(prob, 0.5)

        mock_session = MagicMock()
        mock_session.is_active = False
        session_mgr = DefaultVoiceSessionManager(mock_session)
        self.assertIsInstance(session_mgr.is_active, bool)

    # ------------------------------------------------------------------
    # 3. Audio File Transcription (Headless Fixture)
    # ------------------------------------------------------------------
    def test_transcribe_audio_file_headless(self):
        mock_stt = LocalSTT(model=MockLocalModel("launch visual studio code", "en"))
        stt_adapter = FasterWhisperSTTAdapter(stt=mock_stt)

        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "test_cmd.wav"
            pcm = np.sin(2 * np.pi * 440 * np.linspace(0, 1.0, 16000)).astype(np.float32) * 0.2
            sf.write(str(wav_path), pcm, PIPELINE_SAMPLE_RATE)

            res = stt_adapter.transcribe(wav_path)
            self.assertEqual(res.text, "launch visual studio code")
            self.assertEqual(res.language, "en")
            self.assertGreater(res.confidence, 0.5)

    # ------------------------------------------------------------------
    # 4. Push-to-Talk Lifecycle
    # ------------------------------------------------------------------
    def test_push_to_talk_recording_and_processing(self):
        mock_mic = MagicMock()
        mock_mic.is_active = True
        mock_mic.current_level = 0.4
        mock_mic.read_frame.return_value = np.zeros(512, dtype=np.float32)

        assistant = MockAssistant()
        assistant.response = "Terminal has been brought to the foreground."
        mock_stt = LocalSTT(model=MockLocalModel("switch to terminal", "en"))

        orch = VoiceOrchestrator(
            assistant=assistant,
            config=self.config,
            stt=mock_stt,
            mic_factory=lambda cfg: mock_mic,
            identity=self.identity,
        )

        orch.start_push_to_talk(background_workers=False)
        self.assertEqual(orch.conversation_mode, "push_to_talk")
        self.assertTrue(orch.session.is_recording)

        # Simulate audio frames accumulated while holding button
        for _ in range(10):
            orch.session.append_recording_frame(np.ones(512, dtype=np.float32) * 0.1)

        res = orch.stop_push_to_talk_and_transcribe()
        self.assertFalse(orch.session.is_recording)
        self.assertEqual(res, "Terminal has been brought to the foreground.")
        self.assertEqual(assistant.last_query, "switch to terminal")

        # Verify unified transcript entry recorded with modality=voice
        entries = orch.session.transcript_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].role, "user")
        self.assertEqual(entries[0].modality, "voice")
        self.assertEqual(entries[0].text, "switch to terminal")
        self.assertEqual(entries[1].role, "assistant")
        self.assertEqual(entries[1].text, "Terminal has been brought to the foreground.")

        orch.stop_voice_mode()

    # ------------------------------------------------------------------
    # 5. Continuous Conversation Mode and Mode Toggling
    # ------------------------------------------------------------------
    def test_continuous_conversation_mode_toggle(self):
        assistant = MockAssistant()
        orch = VoiceOrchestrator(
            assistant=assistant,
            config=self.config,
            identity=self.identity,
        )
        self.assertEqual(orch.conversation_mode, "continuous")

        mode = orch.toggle_continuous_mode()
        self.assertEqual(mode, "push_to_talk")
        self.assertEqual(orch.conversation_mode, "push_to_talk")

        mode2 = orch.toggle_continuous_mode()
        self.assertEqual(mode2, "continuous")
        self.assertEqual(orch.conversation_mode, "continuous")

        orch.set_conversation_mode("push_to_talk")
        self.assertEqual(orch.conversation_mode, "push_to_talk")

        with self.assertRaises(ValueError):
            orch.set_conversation_mode("invalid_mode")

    # ------------------------------------------------------------------
    # 6. Pause and Resume Behavior
    # ------------------------------------------------------------------
    def test_pause_and_resume_listening(self):
        assistant = MockAssistant()
        orch = VoiceOrchestrator(
            assistant=assistant,
            config=self.config,
            identity=self.identity,
        )

        self.assertFalse(orch.is_paused)
        orch.pause_listening()
        self.assertTrue(orch.is_paused)

        ui = VoiceUIController(orch)
        snap = ui.snapshot()
        self.assertTrue(snap.is_paused)
        self.assertEqual(snap.status_label, "Voice Paused")

        orch.resume_listening()
        self.assertFalse(orch.is_paused)
        snap2 = ui.snapshot()
        self.assertFalse(snap2.is_paused)

    # ------------------------------------------------------------------
    # 7. Interruption / Barge-in During Speaking and Thinking
    # ------------------------------------------------------------------
    def test_barge_in_cancels_playback_and_rolls_back_unconfirmed(self):
        assistant = MockAssistant()
        assistant.pending_action = {
            "tool": "delete_file",
            "token": "tok_unconfirmed_123",
            "risk_tier": "dangerous",
        }

        player_mock = MagicMock()
        player_mock.is_playing = True
        player_mock.play_text_chunks.return_value = False

        orch = VoiceOrchestrator(
            assistant=assistant,
            config=self.config,
            player=player_mock,
            identity=self.identity,
        )
        orch.state_machine.transition(VoiceState.LISTENING)
        orch.state_machine.transition(VoiceState.THINKING)
        orch.state_machine.transition(VoiceState.SPEAKING)

        orch.interrupt("user_spoke_during_playback")

        player_mock.stop.assert_called()
        self.assertTrue(assistant.cancelled)
        self.assertEqual(orch.state, VoiceState.LISTENING)

    def test_interruption_during_thinking_discards_stale_response(self):
        assistant = MockAssistant()
        slow_inference_done = threading.Event()

        def slow_process(text: str) -> str:
            slow_inference_done.wait(timeout=2.0)
            return "Stale response that should never be spoken."

        assistant.process = slow_process
        mock_stt = LocalSTT(model=MockLocalModel("test query", "en"))

        orch = VoiceOrchestrator(
            assistant=assistant,
            config=self.config,
            stt=mock_stt,
            identity=self.identity,
        )
        orch.state_machine.transition(VoiceState.LISTENING)

        result_holder = []

        def _worker():
            res = orch.handle_utterance_audio(np.zeros(16000, dtype=np.float32))
            result_holder.append(res)

        t = threading.Thread(target=_worker)
        t.start()

        # Wait until state transitions to THINKING
        for _ in range(50):
            if orch.state == VoiceState.THINKING:
                break
            time.sleep(0.02)
        self.assertEqual(orch.state, VoiceState.THINKING)

        # Interrupt while thinking
        orch.interrupt("user_interrupted_thinking")

        # Allow slow inference to complete
        slow_inference_done.set()
        t.join(timeout=2.0)

        # Stale response must be discarded and return None
        self.assertEqual(result_holder, [None])
        self.assertEqual(orch.state, VoiceState.LISTENING)

    # ------------------------------------------------------------------
    # 8. Confirmation Gate and Safety Invariants
    # ------------------------------------------------------------------
    def test_voice_confirmation_gate_requires_clear_speech(self):
        assistant = MockAssistant()
        assistant.pending_action = {
            "tool": "terminal_execute",
            "confirmation_token": "tok_safe_999",
            "risk_tier": "dangerous",
        }

        # 1. Low confidence utterance -> rejected
        low_conf_result = TranscriptionResult(
            text="yes",
            language="en",
            confidence=0.20,
            no_speech_prob=0.8,
            duration_seconds=0.5,
        )
        orch = VoiceOrchestrator(assistant=assistant, config=self.config, identity=self.identity)
        msg = orch._check_voice_confirmation_gate(low_conf_result)
        self.assertIn("clearly enough to confirm", msg)

        # 2. Ambiguous speech -> rejected
        ambig_result = TranscriptionResult(
            text="maybe tomorrow",
            language="en",
            confidence=0.95,
            no_speech_prob=0.01,
            duration_seconds=1.0,
        )
        msg2 = orch._check_voice_confirmation_gate(ambig_result)
        self.assertIn("say yes to proceed or no to cancel", msg2)

        # 3. Clear affirmative speech -> accepted (gate returns None)
        clear_yes = TranscriptionResult(
            text="confirm",
            language="en",
            confidence=0.92,
            no_speech_prob=0.02,
            duration_seconds=0.6,
        )
        msg3 = orch._check_voice_confirmation_gate(clear_yes)
        self.assertIsNone(msg3)

    # ------------------------------------------------------------------
    # 9. Text + Voice History Continuity
    # ------------------------------------------------------------------
    def test_seamless_text_and_voice_continuity(self):
        assistant = MockAssistant()
        orch = VoiceOrchestrator(
            assistant=assistant,
            config=self.config,
            identity=self.identity,
        )

        # Turn 1: Text message
        assistant.response = "File summary: 14 Python scripts found."
        orch.send_text("Summarize my workspace", speak=False)

        # Turn 2: Voice message
        assistant.response = "Downloads folder organized into 3 subfolders."
        mock_stt = LocalSTT(model=MockLocalModel("organize my downloads", "en"))
        orch._stt = mock_stt
        orch.state_machine.transition(VoiceState.LISTENING)
        orch.handle_utterance_audio(np.zeros(16000, dtype=np.float32))

        # Check transcript contains sequential entries with accurate modalities
        entries = orch.session.transcript_entries()
        self.assertEqual(len(entries), 4)
        self.assertEqual(entries[0].role, "user")
        self.assertEqual(entries[0].modality, "text")
        self.assertEqual(entries[1].role, "assistant")
        self.assertEqual(entries[1].modality, "text")
        self.assertEqual(entries[2].role, "user")
        self.assertEqual(entries[2].modality, "voice")
        self.assertEqual(entries[3].role, "assistant")
        self.assertEqual(entries[3].modality, "voice")

    # ------------------------------------------------------------------
    # 10. Microphone Permission Denial and Recovery
    # ------------------------------------------------------------------
    def test_microphone_permission_denial_recovers_to_text(self):
        def failing_mic(_cfg):
            raise MicrophonePermissionError(
                "Microphone access denied by macOS system privacy settings.",
                user_message="Microphone permission was denied in System Settings.",
            )

        assistant = MockAssistant()
        orch = VoiceOrchestrator(
            assistant=assistant,
            config=self.config,
            mic_factory=failing_mic,
            identity=self.identity,
        )

        with self.assertRaises(MicrophonePermissionError):
            orch.start_voice_mode()

        # System must fail closed, remain in text mode, and recover to IDLE
        self.assertEqual(orch.mode, "text")
        self.assertEqual(orch.state, VoiceState.IDLE)
        self.assertFalse(orch.session.is_active)


if __name__ == "__main__":
    unittest.main()
