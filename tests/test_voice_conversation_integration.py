"""Integration tests for Mode A (text), Mode B (voice), Mode C (switching), and UI."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from core.agent.context import ConversationContext
from core.agent.intent import IntentDetector
from core.memory.database import MemoryDatabase
from core.voice.audio_input import MicrophoneStream
from core.voice.config import VERIFIED_LANGUAGES, VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession
from core.voice.state import VoiceState
from core.voice.stt import TranscriptionResult
from core.voice.ui import VoiceUIController


class _FakeMicStream:
    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _make_assistant(tmp_dir: Path, ai_responses: list[str]):
    with patch.object(MemoryDatabase, "_initialize"):
        from core.agent.agent import RiyaAgent
        from core.agent.assistant import RIYAAssistant
        from core.agent.executor import CommandExecutor
        from core.agent.planner import AgentPlanner
        from core.memory.manager import MemoryManager
        from core.memory.service import MemoryService

        assistant = RIYAAssistant.__new__(RIYAAssistant)
        assistant.agent = RiyaAgent()

    db = MemoryDatabase(tmp_dir / "integ_memory.db")
    assistant.memory = MemoryService()
    assistant.memory.manager = MemoryManager(db)
    assistant.semantic = None
    assistant.semantic_unavailable_reason = "disabled"
    assistant.ai = Mock()
    assistant.ai.generate.side_effect = ai_responses
    assistant.intent_detector = IntentDetector()
    assistant.command_executor = CommandExecutor(assistant.agent)
    assistant.planner = AgentPlanner(assistant.ai, assistant.agent.tools, semantic_engine=None)
    assistant.context = ConversationContext(max_messages=12)
    assistant.pending_action = None
    assistant.pending_plan = None
    return assistant


class TestVoiceConversationIntegration(unittest.TestCase):
    def setUp(self):
        VoiceSession._active_session_id = None
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        VoiceSession._active_session_id = None
        self.tmp.cleanup()

    def test_seamless_text_voice_text_continuity_and_no_duplicates(self):
        # Turn 1 (text): plan=[] -> chat reply
        # Turn 2 (voice): plan=[] -> chat reply
        # Turn 3 (text again): plan=[] -> chat reply referencing voice turn
        assistant = _make_assistant(
            self.root,
            [
                '{"steps": []}',
                "Nice to meet you, Tukaram!",
                '{"steps": []}',
                "Your favorite color is blue, as you just told me by voice.",
                '{"steps": []}',
                "Continuing from our voice and text chat seamlessly.",
            ],
        )
        spoken: list[str] = []
        fake_stt = SimpleNamespace(
            transcribe_detailed=lambda audio, language=None: TranscriptionResult(
                "My favorite color is blue.", "en", 0.94, 0.01, 1.2
            )
        )
        fake_tts = SimpleNamespace(
            supports_wav_synthesis=False,
            supports_emotional_prosody=False,
            speak=lambda chunk, **kw: spoken.append(chunk),
        )
        cfg = VoiceConfig(vad_engine="energy", recordings_dir=str(self.root / "rec"))
        orch = VoiceOrchestrator(
            assistant,
            config=cfg,
            stt=fake_stt,
            tts=fake_tts,
            mic_factory=lambda _c: MicrophoneStream(stream_factory=lambda **kw: _FakeMicStream()),
        )
        ui = VoiceUIController(orch)

        # Mode A: Text conversation.
        self.assertEqual(orch.mode, "text")
        r1 = ui.submit_text("Hi RIYA, my name is Tukaram.")
        self.assertEqual(r1, "Nice to meet you, Tukaram!")
        self.assertEqual(spoken, [])

        # Mode B / C: Switch to voice seamlessly; same ConversationContext preserved!
        orch.switch_mode("voice", background_workers=False)
        self.assertEqual(orch.mode, "voice")
        self.assertEqual(orch.state, VoiceState.LISTENING)
        self.assertTrue(ui.snapshot().microphone_active)

        r2 = orch.handle_utterance_audio(np.ones(1600, dtype=np.float32) * 0.05)
        self.assertIn("blue", r2)
        self.assertEqual(spoken, ["Your favorite color is blue, as you just told me by voice."])

        # Verify shared ConversationContext did NOT duplicate turns.
        msgs = assistant.context.messages()
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[0].content, "Hi RIYA, my name is Tukaram.")
        self.assertEqual(msgs[2].content, "My favorite color is blue.")

        # Mode C: Switch back to text and continue same conversation.
        orch.switch_mode("text")
        self.assertEqual(orch.mode, "text")
        self.assertEqual(orch.state, VoiceState.IDLE)
        self.assertFalse(ui.snapshot().microphone_active)

        r3 = ui.submit_text("Let's keep going.")
        self.assertIn("seamlessly", r3)
        self.assertEqual(len(assistant.context.messages()), 6)
        self.assertEqual(len(ui.snapshot().transcript), 6)

    def test_ui_controller_settings_persistence_and_honest_languages(self):
        assistant = _make_assistant(self.root, [])
        orch = VoiceOrchestrator(
            assistant,
            config=VoiceConfig(recordings_dir=str(self.root / "rec")),
        )
        ui = VoiceUIController(orch)

        langs = dict(ui.language_options())
        self.assertEqual(VERIFIED_LANGUAGES, frozenset({"en"}))
        self.assertIn("untested", langs["hi"])
        self.assertIn("untested", langs["mr"])
        self.assertNotIn("untested", langs["en"])

        settings_file = self.root / "voice_settings.json"
        new_cfg = orch.config.updated(voice="Daniel", speech_rate_wpm=200, response_style="calm")
        new_cfg.save(settings_file)
        mode_bits = os.stat(settings_file).st_mode & 0o777
        self.assertEqual(mode_bits, 0o600)

        reloaded = VoiceConfig.load(settings_file)
        self.assertEqual(reloaded.voice, "Daniel")
        self.assertEqual(reloaded.speech_rate_wpm, 200)
        self.assertEqual(reloaded.response_style, "calm")

        snap = ui.snapshot()
        self.assertEqual(len(snap.waveform_bars), VoiceUIController.BAR_COUNT)
        self.assertEqual(snap.voice_state, VoiceState.IDLE)


if __name__ == "__main__":
    unittest.main()
