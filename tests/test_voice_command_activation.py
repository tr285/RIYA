"""Tests for RIYA Voice Command Activation and CLI Command Dispatcher.

Verifies:
- /voice ptt reaches the PTT handler and executes real/mocked audio capture & transcription.
- /voice continuous reaches the continuous conversation handler.
- /pause and /resume invoke the correct orchestrator methods.
- /interrupt cancels active processing and playback.
- /text stops voice mode safely and switches to text mode.
- Unknown commands return useful errors and never reach Qwen3.
- Slash commands never enter the AI message pipeline.
- Microphone permission denial is handled gracefully.
- Existing text conversations still work normally.
- Case-insensitivity and argument validation across all commands.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from core.command_dispatcher import CLICommandDispatcher, DispatchResult
from core.identity.models import AuthenticatedIdentity
from core.security.permissions import (
    PermissionDecision,
    PermissionEvaluation,
    PermissionManager,
)
from core.voice.config import VoiceConfig
from core.voice.errors import MicrophonePermissionError, VoicePermissionError
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession
from core.voice.state import VoiceState
from core.voice.stt import LocalSTT, TranscriptionResult


class MockLocalModel:
    def __init__(self, text: str = "open terminal", language: str = "en"):
        self.text = text
        self.language = language

    def transcribe(self, audio, **kwargs):
        class Seg:
            def __init__(self, t):
                self.text = t
                self.avg_logprob = -0.1

        class Info:
            language = "en"
            language_probability = 0.98

        return [Seg(self.text)], Info()


class MockAssistant:
    def __init__(self, response: str = "Command executed successfully."):
        self.response = response
        self.last_query: str | None = None
        self.process_call_count = 0
        self.cancelled = False
        self.current_identity = AuthenticatedIdentity(
            user_id="test_uid",
            username="testuser",
            display_name="Test User",
            session_id="sess_voice_test",
            session_token="tok_voice_test",
            workspace_dir="/tmp/riya_test_workspace",
            auth_method="local_password",
            expires_at="2099-01-01T00:00:00Z",
        )
        self.permission_manager = MagicMock(spec=PermissionManager)
        # Default: voice.microphone is allowed
        self.permission_manager.evaluate.return_value = PermissionEvaluation(
            permission_key="voice.microphone",
            category="voice",
            decision=PermissionDecision.ALLOW,
            allowed=True,
            blocked=False,
            requires_confirmation=False,
            reason="Authorized by test policy.",
        )
        self.permission_manager.consume_if_authorized.return_value = PermissionEvaluation(
            permission_key="voice.microphone",
            category="voice",
            decision=PermissionDecision.ALLOW,
            allowed=True,
            blocked=False,
            requires_confirmation=False,
            reason="Authorized by test policy.",
        )

    def process(self, text: str) -> str:
        self.last_query = text
        self.process_call_count += 1
        return self.response

    def cancel(self) -> None:
        self.cancelled = True

    def ai_status(self) -> dict:
        return {"loaded": True, "backend": "metal", "model": "Qwen3-4B"}


class TestVoiceCommandActivation(unittest.TestCase):
    def setUp(self):
        self.config = VoiceConfig(
            sample_rate=16000,
            voice_responses_enabled=False,
            recordings_dir="/tmp/riya_test_recordings",
        )
        self.assistant = MockAssistant()
        self.outputs: list[str] = []
        self.output_fn = lambda msg: self.outputs.append(str(msg))

        # Setup mock microphone and mock STT
        self.mock_mic = MagicMock()
        self.mock_mic.is_active = True
        self.mock_mic.current_level = 0.5
        self.mock_mic.read_frame.return_value = np.zeros(512, dtype=np.float32)

        self.mock_stt = LocalSTT(model=MockLocalModel("open terminal", "en"))
        self.session = VoiceSession(
            self.config,
            mic_factory=lambda cfg: self.mock_mic,
            user_id="test_uid",
            auth_session_id="sess_voice_test",
        )
        self.orchestrator = VoiceOrchestrator(
            assistant=self.assistant,
            config=self.config,
            session=self.session,
            stt=self.mock_stt,
            mic_factory=lambda cfg: self.mock_mic,
            identity=self.assistant.current_identity,
            permission_manager=self.assistant.permission_manager,
        )

        self.dispatcher = CLICommandDispatcher(
            assistant=self.assistant,
            get_orchestrator=lambda: self.orchestrator,
            output_fn=self.output_fn,
            input_fn=lambda: "",  # Presses enter immediately
        )

    def tearDown(self):
        if self.orchestrator is not None:
            self.orchestrator.close()

    # -----------------------------------------------------------------
    # 1. /voice ptt reaches the PTT handler and executes capture & STT
    # -----------------------------------------------------------------
    def test_voice_ptt_reaches_ptt_handler(self):
        # Provide simulated audio frames into session before stopping
        def _input_and_feed():
            # Feed audio into recording buffer
            self.orchestrator.session.append_recording_frame(np.ones(1024, dtype=np.float32) * 0.1)
            return ""

        self.dispatcher.input_fn = _input_and_feed
        result = self.dispatcher.dispatch("/voice ptt")

        self.assertTrue(result.handled)
        self.assertTrue(result.is_command)
        self.assertEqual(self.orchestrator.conversation_mode, "push_to_talk")
        # Transcript was transcribed through mock STT ("open terminal") and processed
        self.assertEqual(self.assistant.last_query, "open terminal")
        # RIYA responded
        self.assertTrue(any("Push-to-Talk active" in o for o in self.outputs))
        self.assertTrue(any("open terminal" in o for o in self.outputs))
        self.assertTrue(any("Command executed successfully" in o for o in self.outputs))

    def test_voice_ptt_case_insensitive(self):
        def _input_and_feed():
            self.orchestrator.session.append_recording_frame(np.ones(1024, dtype=np.float32) * 0.1)
            return ""

        self.dispatcher.input_fn = _input_and_feed
        result = self.dispatcher.dispatch("/VOICE PTT")
        self.assertTrue(result.handled)
        self.assertTrue(result.is_command)
        self.assertEqual(self.orchestrator.conversation_mode, "push_to_talk")

    # -----------------------------------------------------------------
    # 2. /voice continuous reaches the continuous handler
    # -----------------------------------------------------------------
    def test_voice_continuous_reaches_continuous_handler(self):
        result = self.dispatcher.dispatch("/voice continuous")

        self.assertTrue(result.handled)
        self.assertTrue(result.is_command)
        self.assertEqual(self.orchestrator.conversation_mode, "continuous")
        self.assertEqual(self.orchestrator.mode, "voice")
        self.assertTrue(self.orchestrator.session.microphone_active)
        self.assertTrue(any("Voice mode ON (continuous)" in o for o in self.outputs))

    def test_voice_cont_alias(self):
        result = self.dispatcher.dispatch("/voice cont")
        self.assertTrue(result.handled)
        self.assertEqual(self.orchestrator.conversation_mode, "continuous")

    # -----------------------------------------------------------------
    # 3. /pause and /resume invoke the correct methods
    # -----------------------------------------------------------------
    def test_pause_and_resume_invoke_correct_methods(self):
        # Start continuous voice mode first
        self.dispatcher.dispatch("/voice continuous")
        self.assertFalse(self.orchestrator.is_paused)

        # /pause
        res_pause = self.dispatcher.dispatch("/pause")
        self.assertTrue(res_pause.handled)
        self.assertTrue(res_pause.is_command)
        self.assertTrue(self.orchestrator.is_paused)
        self.assertTrue(any("Voice paused - Listening paused" in o for o in self.outputs))

        # /resume
        res_resume = self.dispatcher.dispatch("/resume")
        self.assertTrue(res_resume.handled)
        self.assertTrue(res_resume.is_command)
        self.assertFalse(self.orchestrator.is_paused)
        self.assertTrue(any("Voice resumed - Listening active" in o for o in self.outputs))

    def test_pause_when_voice_inactive_reports_cleanly(self):
        self.orchestrator.switch_mode("text")
        self.dispatcher.dispatch("/pause")
        self.assertTrue(any("Voice is not active" in o for o in self.outputs))

    def test_resume_when_voice_inactive_reports_cleanly(self):
        self.orchestrator.switch_mode("text")
        self.dispatcher.dispatch("/resume")
        self.assertTrue(any("Voice is not active" in o for o in self.outputs))

    # -----------------------------------------------------------------
    # 4. /interrupt cancels active processing
    # -----------------------------------------------------------------
    def test_interrupt_cancels_active_processing(self):
        self.dispatcher.dispatch("/voice continuous")
        res = self.dispatcher.dispatch("/interrupt")
        self.assertTrue(res.handled)
        self.assertTrue(res.is_command)
        self.assertTrue(self.assistant.cancelled)
        self.assertTrue(any("Playback and processing interrupted" in o for o in self.outputs))

    # -----------------------------------------------------------------
    # 5. /text stops voice mode safely
    # -----------------------------------------------------------------
    def test_text_stops_voice_mode_safely(self):
        self.dispatcher.dispatch("/voice continuous")
        self.assertEqual(self.orchestrator.mode, "voice")

        res = self.dispatcher.dispatch("/text")
        self.assertTrue(res.handled)
        self.assertTrue(res.is_command)
        self.assertEqual(self.orchestrator.mode, "text")
        self.assertFalse(self.orchestrator.session.microphone_active)
        self.assertTrue(any("Text mode ON - Microphone off" in o for o in self.outputs))

    # -----------------------------------------------------------------
    # 6. /status reports actual session and model status
    # -----------------------------------------------------------------
    def test_status_reports_actual_session_and_model_status(self):
        res = self.dispatcher.dispatch("/status")
        self.assertTrue(res.handled)
        self.assertTrue(res.is_command)
        self.assertTrue(any("user=@testuser" in o for o in self.outputs))
        self.assertTrue(any("ready (metal)" in o for o in self.outputs))
        self.assertTrue(any("mic=" in o for o in self.outputs))

    # -----------------------------------------------------------------
    # 7. Unknown commands return useful errors
    # -----------------------------------------------------------------
    def test_unknown_commands_return_useful_errors(self):
        res = self.dispatcher.dispatch("/unknown_command_xyz")
        self.assertTrue(res.handled)
        self.assertTrue(res.is_command)
        self.assertIsNotNone(res.error)
        self.assertTrue(any("Unknown command: '/unknown_command_xyz'" in o for o in self.outputs))
        self.assertTrue(any("Commands: /voice" in o for o in self.outputs))
        # Verify AI inference was NOT invoked
        self.assertEqual(self.assistant.process_call_count, 0)

    def test_voice_invalid_arguments_returns_error(self):
        res = self.dispatcher.dispatch("/voice invalid_mode")
        self.assertTrue(res.handled)
        self.assertTrue(res.is_command)
        self.assertTrue(any("Invalid voice mode: 'invalid_mode'" in o for o in self.outputs))
        self.assertEqual(self.assistant.process_call_count, 0)

    def test_voice_missing_arguments_returns_usage(self):
        res = self.dispatcher.dispatch("/voice")
        self.assertTrue(res.handled)
        self.assertTrue(res.is_command)
        self.assertTrue(any("Usage: /voice [ptt|continuous]" in o for o in self.outputs))
        self.assertEqual(self.assistant.process_call_count, 0)

    # -----------------------------------------------------------------
    # 8. Slash commands NEVER enter the AI message pipeline
    # -----------------------------------------------------------------
    def test_slash_commands_never_enter_ai_message_pipeline(self):
        slash_commands = [
            "/voice ptt",
            "/voice continuous",
            "/pause",
            "/resume",
            "/interrupt",
            "/text",
            "/status",
            "/help",
            "/foobar",
            "/voice invalid",
            "/permissions list",
        ]
        for cmd in slash_commands:
            initial_count = self.assistant.process_call_count
            self.dispatcher.dispatch(cmd)
            # In push-to-talk, assistant is only called with the TRANSCRIBED audio, never the slash command string
            if cmd == "/voice ptt":
                self.assertNotEqual(self.assistant.last_query, "/voice ptt")
            else:
                self.assertEqual(
                    self.assistant.process_call_count,
                    initial_count,
                    f"Slash command '{cmd}' erroneously reached assistant.process()!",
                )

    def test_orchestrator_send_text_rejects_slash_commands(self):
        res = self.orchestrator.send_text("/voice ptt")
        self.assertTrue("Voice mode ON" in res or "Push-to-talk active" in res)
        # Verify assistant.process was not called with the slash command
        self.assertNotEqual(self.assistant.last_query, "/voice ptt")

        res_unknown = self.orchestrator.send_text("/bogus_slash")
        self.assertTrue("Unknown command" in res_unknown)
        self.assertNotEqual(self.assistant.last_query, "/bogus_slash")

    def test_assistant_process_defense_in_depth_rejects_slash_commands(self):
        # Real RIYAAssistant defense-in-depth check
        from core.agent.assistant import RIYAAssistant

        real_assistant = object.__new__(RIYAAssistant)
        real_assistant.pending_action = None
        real_assistant.pending_plan = None
        real_assistant.max_input_chars = 4000
        real_assistant._resolve_turn_identity = lambda ctx: None

        res = RIYAAssistant.process(real_assistant, "/voice ptt")
        self.assertTrue("Slash commands are handled by the RIYA command dispatcher" in res)

    # -----------------------------------------------------------------
    # 9. Microphone permission denial is handled gracefully
    # -----------------------------------------------------------------
    def test_microphone_permission_denial_handled_gracefully(self):
        # Deny voice.microphone in permission manager
        self.assistant.permission_manager.evaluate.return_value = PermissionEvaluation(
            permission_key="voice.microphone",
            category="voice",
            decision=PermissionDecision.DENY,
            allowed=False,
            blocked=True,
            requires_confirmation=False,
            reason="Denied by user security policy.",
        )
        self.assistant.permission_manager.consume_if_authorized.return_value = PermissionEvaluation(
            permission_key="voice.microphone",
            category="voice",
            decision=PermissionDecision.DENY,
            allowed=False,
            blocked=True,
            requires_confirmation=False,
            reason="Denied by user security policy.",
        )

        res_ptt = self.dispatcher.dispatch("/voice ptt")
        self.assertTrue(res_ptt.handled)
        self.assertTrue(any("blocked by default-deny" in o or "Denied" in o for o in self.outputs))
        self.assertFalse(self.orchestrator.session.microphone_active)

        res_cont = self.dispatcher.dispatch("/voice continuous")
        self.assertTrue(res_cont.handled)
        self.assertTrue(any("blocked by default-deny" in o or "Denied" in o for o in self.outputs))
        self.assertFalse(self.orchestrator.session.microphone_active)

    # -----------------------------------------------------------------
    # 10. Existing text conversations still work
    # -----------------------------------------------------------------
    def test_existing_text_conversations_still_work(self):
        user_msg = "Hello RIYA, can you help me summarize this project?"
        res = self.dispatcher.dispatch(user_msg)

        self.assertTrue(res.handled)
        self.assertFalse(res.is_command)
        self.assertEqual(self.assistant.last_query, user_msg)
        self.assertEqual(res.output, "Command executed successfully.")
        self.assertTrue(any("RIYA: Command executed successfully." in o for o in self.outputs))

    def test_natural_language_activate_and_deactivate_voice(self):
        res_activate = self.dispatcher.dispatch("activate voice")
        self.assertTrue(res_activate.handled)
        self.assertTrue(res_activate.is_command)
        self.assertEqual(self.orchestrator.mode, "voice")

        res_deactivate = self.dispatcher.dispatch("deactivate voice")
        self.assertTrue(res_deactivate.handled)
        self.assertTrue(res_deactivate.is_command)
        self.assertEqual(self.orchestrator.mode, "text")


if __name__ == "__main__":
    unittest.main()
