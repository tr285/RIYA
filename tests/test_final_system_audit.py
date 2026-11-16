"""Regression tests for RIYA v0.4 Final System Audit."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from core.agent.agent import RiyaAgent
from core.agent.assistant import RIYAAssistant
from core.agent.context import ConversationContext
from core.agent.executor import CommandExecutor
from core.agent.intent import IntentDetector
from core.agent.planner import AgentPlanner, PlanStep
from core.ai.safety import InferenceCancelledError
from core.config import settings
from core.memory.database import MemoryDatabase
from core.memory.manager import MemoryManager
from core.memory.service import MemoryService
from core.tools import memory as memory_tool_mod
from core.voice.config import PIPELINE_SAMPLE_RATE, VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.personality import (
    apply_response_style_wording,
    prepare_text_for_speech,
    style_prompt_instruction,
)
from core.voice.session import VoiceSession
from core.voice.state import VoiceState
from core.voice.stt import TranscriptionResult
from core.voice.tts import LocalTTS


def _tr(text: str, confidence: float = 0.95) -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        language="en",
        confidence=confidence,
        no_speech_prob=0.02,
        duration_seconds=0.5,
    )


class _FakeMicStream:
    is_active = True

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class _StubPlayer:
    def __init__(self):
        self.stop_calls = 0

    def update_config(self, config):
        self.config = config

    def play_text_chunks(self, chunks, **_kwargs):
        return True

    def stop(self):
        self.stop_calls += 1


def _build_isolated_assistant(tmp_dir: Path, ai_mock: Mock) -> tuple[RIYAAssistant, MemoryDatabase]:
    with patch.object(MemoryDatabase, "_initialize"):
        assistant = RIYAAssistant.__new__(RIYAAssistant)
        assistant.agent = RiyaAgent()

    db = MemoryDatabase(tmp_dir / "audit_memory.db")
    assistant.memory = MemoryService()
    assistant.memory.manager = MemoryManager(db)
    assistant.semantic = None
    assistant.semantic_unavailable_reason = "disabled in test"
    assistant.ai = ai_mock
    assistant.intent_detector = IntentDetector()
    assistant.command_executor = CommandExecutor(assistant.agent)
    assistant.planner = AgentPlanner(assistant.ai, assistant.agent.tools, semantic_engine=None)
    assistant.context = ConversationContext(max_messages=12)
    assistant.pending_action = None
    assistant.pending_plan = None
    assistant._cancel_event = threading.Event()
    return assistant, db


class TestFinalSystemAudit(unittest.TestCase):
    def setUp(self):
        VoiceSession._active_session_id = None

    def tearDown(self):
        VoiceSession._active_session_id = None

    def test_planner_does_not_hallucinate_remember_on_conversational_question(self):
        agent = RiyaAgent()
        prompts_seen: list[str] = []

        class _MockAI:
            def generate(self, prompt: str, **_kwargs) -> str:
                prompts_seen.append(prompt)
                return (
                    '{"steps":[{"action":"remember","parameters":'
                    '{"category":"user_query","key":"creator","value":"Tularam"},'
                    '"description":"Store conversational query"}]}'
                )

        planner = AgentPlanner(_MockAI(), agent.tools, semantic_engine=None)
        plan = planner.create_plan("In one short sentence, who created you and do you work offline?")

        self.assertEqual(plan.steps, [])
        self.assertEqual(len(prompts_seen), 1)
        self.assertNotIn("- remember(", prompts_seen[0])
        self.assertNotIn("- forget_memory(", prompts_seen[0])

        explicit_plan = planner.create_plan("Remember that my project codename is Project Riya")
        self.assertEqual(len(explicit_plan.steps), 1)
        self.assertEqual(explicit_plan.steps[0].action, "remember")
        self.assertIn("- remember(", prompts_seen[1])

    def test_interruption_mid_plan_execution_stops_subsequent_steps_and_clears_stale_pending_action(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            blocked_target = tmp_dir / "should_not_exist.txt"

            orig_roots = settings.FILESYSTEM_ALLOWED_ROOTS
            settings.FILESYSTEM_ALLOWED_ROOTS = (tmp_dir,)
            try:
                assistant, _ = _build_isolated_assistant(tmp_dir, Mock())
                real_execute = assistant.agent.execute
                calls: list[str] = []

                def _interrupt_after_first(tool_name, confirmation_token=None, **kwargs):
                    calls.append(tool_name)
                    result = real_execute(tool_name, confirmation_token=confirmation_token, **kwargs)
                    if tool_name == "current_time":
                        assistant.cancel()
                    return result

                assistant.agent.execute = _interrupt_after_first
                steps = [
                    PlanStep(action="current_time", parameters={}, description="Get time"),
                    PlanStep(
                        action="write_file",
                        parameters={"path": str(blocked_target), "content": "unauthorized"},
                        description="Write file",
                    ),
                ]
                with self.assertRaises(InferenceCancelledError):
                    assistant._execute_plan_steps(
                        steps,
                        start_index=0,
                        results=[],
                        user_input="check time and write file",
                    )

                self.assertEqual(calls, ["current_time"])
                self.assertFalse(blocked_target.exists())

                # Part B: Interrupt during handle_utterance_audio after pending_action was staged.
                assistant.reset_cancel()
                assistant.agent.execute = real_execute
                assistant.planner.create_plan = lambda _q: Mock(
                    steps=[
                        PlanStep(
                            action="write_file",
                            parameters={"path": str(blocked_target), "content": "stale"},
                            description="Stage write",
                        )
                    ]
                )
                stt = Mock()
                stt.transcribe_detailed.return_value = _tr("write stale file")
                cfg = VoiceConfig(voice_responses_enabled=False, recordings_dir=str(tmp_dir / "rec"))
                orch = VoiceOrchestrator(
                    assistant,
                    config=cfg,
                    stt=stt,
                    player=_StubPlayer(),
                    mic_factory=lambda _c: _FakeMicStream(),
                    vad_backend=lambda _f: 0.0,
                )
                staged_tokens: list[str] = []
                real_process = assistant.process

                def _process_then_interrupt(text: str) -> str:
                    out = real_process(text)
                    if assistant.pending_action:
                        staged_tokens.append(assistant.pending_action["confirmation_token"])
                    orch.interrupt("user_barge_in")
                    return out

                assistant.process = _process_then_interrupt
                orch.start_voice_mode(background_workers=False)
                audio = np.zeros(PIPELINE_SAMPLE_RATE // 2, dtype=np.float32)
                result = orch.handle_utterance_audio(audio)
                orch.close()

                self.assertIsNone(result)
                self.assertEqual(len(staged_tokens), 1)
                self.assertIsNone(assistant.pending_action)
                self.assertIsNone(assistant.pending_plan)

                replay = real_execute(
                    "write_file",
                    confirmation_token=staged_tokens[0],
                    path=str(blocked_target),
                    content="stale",
                )
                self.assertNotEqual(replay.get("status"), "success")
                self.assertFalse(blocked_target.exists())
            finally:
                settings.FILESYSTEM_ALLOWED_ROOTS = orig_roots

    def test_voice_to_agent_to_tool_full_security_and_confirmation_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            read_target = tmp_dir / "audit_read.txt"
            read_target.write_text("offline_audit_ok", encoding="utf-8")
            write_target = tmp_dir / "audit_write.txt"
            tampered_target = tmp_dir / "tampered.txt"

            orig_roots = settings.FILESYSTEM_ALLOWED_ROOTS
            settings.FILESYSTEM_ALLOWED_ROOTS = (tmp_dir,)
            try:
                assistant, _ = _build_isolated_assistant(tmp_dir, Mock())

                def _scripted_plan(text: str):
                    if "read audit" in text.lower():
                        return Mock(
                            steps=[PlanStep("read_file", {"path": str(read_target)}, "Read file")]
                        )
                    return Mock(
                        steps=[
                            PlanStep(
                                "write_file",
                                {"path": str(write_target), "content": "confirmed_payload"},
                                "Write file",
                            )
                        ]
                    )

                assistant.planner.create_plan = _scripted_plan
                stt = Mock()
                cfg = VoiceConfig(voice_responses_enabled=False, recordings_dir=str(tmp_dir / "rec"))
                orch = VoiceOrchestrator(
                    assistant,
                    config=cfg,
                    stt=stt,
                    player=_StubPlayer(),
                    mic_factory=lambda _c: _FakeMicStream(),
                    vad_backend=lambda _f: 0.0,
                )
                orch.start_voice_mode(background_workers=False)
                audio = np.zeros(PIPELINE_SAMPLE_RATE // 2, dtype=np.float32)

                # Safe read tool without confirmation prompt
                stt.transcribe_detailed.return_value = _tr("read audit")
                read_resp = orch.handle_utterance_audio(audio)
                self.assertIn("offline_audit_ok", read_resp)

                # Protected write staged -> rejected with spoken "No"
                stt.transcribe_detailed.return_value = _tr("write payload")
                stage_resp = orch.handle_utterance_audio(audio)
                self.assertIn("Proceed?", stage_resp)
                self.assertIsNotNone(assistant.pending_action)

                # Verify token cannot be abused on tampered parameters even while pending.
                token = assistant.pending_action["confirmation_token"]
                tamper_res = assistant.agent.execute(
                    "write_file",
                    confirmation_token=token,
                    path=str(tampered_target),
                    content="tampered",
                )
                self.assertNotEqual(tamper_res.get("status"), "success")
                self.assertFalse(tampered_target.exists())

                stt.transcribe_detailed.return_value = _tr("No")
                cancel_resp = orch.handle_utterance_audio(audio)
                self.assertIn("cancelled", cancel_resp.lower())
                self.assertFalse(write_target.exists())

                # Re-stage and confirm with spoken "Yes"
                stt.transcribe_detailed.return_value = _tr("write payload")
                stage_resp2 = orch.handle_utterance_audio(audio)
                self.assertIn("Proceed?", stage_resp2)

                stt.transcribe_detailed.return_value = _tr("Yes")
                exec_resp = orch.handle_utterance_audio(audio)
                self.assertEqual(write_target.read_text(encoding="utf-8"), "confirmed_payload")
                self.assertIsNone(assistant.pending_action)
                self.assertTrue(exec_resp)
                orch.close()
            finally:
                settings.FILESYSTEM_ALLOWED_ROOTS = orig_roots

    def test_offline_restart_and_recovery_preserves_voice_config_and_memory(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            cfg_path = tmp_dir / "voice_settings.json"
            db_path = tmp_dir / "restart_memory.db"
            rec_dir = str(tmp_dir / "rec")

            orig_mem_tool = memory_tool_mod._memory
            try:
                mgr1 = MemoryManager(MemoryDatabase(db_path))
                memory_tool_mod._memory = mgr1
                mgr1.remember("general", "codename", "Project Riya")

                cfg1 = VoiceConfig(
                    voice="Samantha",
                    speech_rate_wpm=192,
                    response_style="calm",
                    voice_responses_enabled=False,
                    recordings_dir=rec_dir,
                )
                cfg1.save(cfg_path)

                assistant1, _ = _build_isolated_assistant(tmp_dir, Mock())
                assistant1.memory.manager = mgr1
                orch1 = VoiceOrchestrator(
                    assistant1,
                    config=cfg1,
                    stt=Mock(),
                    player=_StubPlayer(),
                    mic_factory=lambda _c: _FakeMicStream(),
                    vad_backend=lambda _f: 0.0,
                )
                orch1.start_voice_mode(background_workers=False)
                self.assertEqual(orch1.state, VoiceState.LISTENING)
                orch1.close()
                self.assertIsNone(VoiceSession._active_session_id)

                # Simulated cold offline restart
                loaded_cfg = VoiceConfig.load(cfg_path)
                self.assertEqual(loaded_cfg.speech_rate_wpm, 192)
                self.assertEqual(loaded_cfg.response_style, "calm")

                mgr2 = MemoryManager(MemoryDatabase(db_path))
                memory_tool_mod._memory = mgr2
                assistant2, _ = _build_isolated_assistant(tmp_dir, Mock())
                assistant2.memory.manager = mgr2
                orch2 = VoiceOrchestrator(
                    assistant2,
                    config=loaded_cfg,
                    stt=Mock(),
                    player=_StubPlayer(),
                    mic_factory=lambda _c: _FakeMicStream(),
                    vad_backend=lambda _f: 0.0,
                )
                orch2.start_voice_mode(background_workers=False)
                recalled = assistant2.process("recall codename")
                self.assertIn("Project Riya", recalled)
                self.assertEqual(orch2.config.speech_rate_wpm, 192)
                orch2.close()
            finally:
                memory_tool_mod._memory = orig_mem_tool

    def test_expressive_response_wording_vs_honest_tts_prosody_capability(self):
        self.assertFalse(LocalTTS.supports_emotional_prosody)
        tts = LocalTTS(voice="Samantha", rate_wpm=180)
        self.assertFalse(tts.supports_emotional_prosody)

        instructions = {s: style_prompt_instruction(s) for s in ("friendly", "calm", "excited", "empathetic", "professional")}
        self.assertEqual(len(set(instructions.values())), 5)

        prof = prepare_text_for_speech("Hey there! Done! Check settings.", style="professional")
        calm = prepare_text_for_speech("Awesome! Done! Check settings.", style="calm")
        exc = prepare_text_for_speech("Done. Ready.", style="excited")
        emp = apply_response_style_wording("Cannot access that file.", style="empathetic")

        self.assertEqual(prof, "Done. Check settings.")
        self.assertEqual(calm, "Awesome. Done. Check settings.")
        self.assertEqual(exc, "Done! Ready.")
        self.assertEqual(emp, "I'm sorry, I cannot access that file.")

        prompt_text = "This action requires confirmation. Action: write_file(path='a.txt'). Proceed?"
        for style in ("friendly", "calm", "excited", "empathetic", "professional"):
            self.assertEqual(prepare_text_for_speech(prompt_text, style=style), prompt_text)

    def test_short_utterance_stt_confidence_calibration_preserves_security_gate(self):
        from types import SimpleNamespace
        from core.voice.stt import LocalSTT

        class _CalibrationModel:
            def __init__(self, segs):
                self._segs = segs

            def transcribe(self, _audio, **_kwargs):
                return iter(self._segs), SimpleNamespace(language="en", duration=1.2)

        # Single-word spoken "Yes." with low no_speech_prob calibrates above 0.55 gate.
        good_stt = LocalSTT(
            model_size="base",
            model=_CalibrationModel([SimpleNamespace(text=" Yes.", avg_logprob=-0.719, no_speech_prob=0.10)]),
        )
        good_res = good_stt.transcribe_detailed(np.ones(1600, dtype=np.float32) * 0.05)
        self.assertGreaterEqual(good_res.confidence, 0.55)

        # Noisy / low-probability segment remains strictly below the confirmation gate.
        noisy_stt = LocalSTT(
            model_size="base",
            model=_CalibrationModel([SimpleNamespace(text=" Yes.", avg_logprob=-1.2, no_speech_prob=0.45)]),
        )
        noisy_res = noisy_stt.transcribe_detailed(np.ones(1600, dtype=np.float32) * 0.05)
        self.assertLess(noisy_res.confidence, 0.55)

    def test_audio_output_stop_does_not_close_stream_from_foreign_thread_during_write(self):
        import time
        import soundfile as sf
        from core.voice.audio_output import AudioOutputPlayer

        with tempfile.TemporaryDirectory() as td:
            work_dir = Path(td)
            stream_holder: list[object] = []
            writing_started = threading.Event()
            stop_requested = threading.Event()

            class _SingleThreadStream:
                def __init__(self, **_kwargs):
                    self.owner_thread = None
                    self.aborted = False
                    self.stopped = False
                    self.closed = False
                    self.cross_thread_error = None

                def start(self):
                    self.owner_thread = threading.get_ident()

                def write(self, _block):
                    writing_started.set()
                    deadline = time.monotonic() + 0.03
                    while not stop_requested.is_set() and time.monotonic() < deadline:
                        time.sleep(0.002)

                def _check_owner(self):
                    if threading.get_ident() != self.owner_thread:
                        self.cross_thread_error = "Cross-thread PortAudio call"
                        raise RuntimeError(self.cross_thread_error)

                def abort(self):
                    self._check_owner()
                    self.aborted = True

                def stop(self):
                    self._check_owner()
                    self.stopped = True

                def close(self):
                    self._check_owner()
                    self.closed = True

            class _FakeWavTTS:
                supports_wav_synthesis = True
                supports_emotional_prosody = False

                def synthesize_to_wav(self, _text, output_path, *, rate_wpm=None):
                    p = Path(output_path)
                    # 0.5 seconds at 16 kHz -> 20 blocks of 25ms.
                    sf.write(str(p), np.ones(8000, dtype=np.float32) * 0.1, 16000)
                    return p

            def _factory(**kwargs):
                s = _SingleThreadStream(**kwargs)
                stream_holder.append(s)
                return s

            player = AudioOutputPlayer(VoiceConfig(), tts=_FakeWavTTS(), output_stream_factory=_factory)
            outcome: dict[str, object] = {}

            def _worker():
                outcome["completed"] = player.play_text_chunks(["Interrupt this playback."], temp_dir=work_dir)

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            self.assertTrue(writing_started.wait(timeout=2.0))
            stop_requested.set()
            player.stop()
            t.join(timeout=2.0)

            self.assertFalse(t.is_alive())
            self.assertFalse(outcome.get("completed"))
            self.assertTrue(player.cancelled)
            self.assertEqual(len(stream_holder), 1)
            stream = stream_holder[0]
            self.assertIsNone(stream.cross_thread_error)
            self.assertTrue(stream.aborted)
            self.assertTrue(stream.stopped)
            self.assertTrue(stream.closed)

    def test_silero_vad_disables_onnx_telemetry_offline(self):
        from core.voice.vad import SileroStreamingVAD

        calls: list[str] = []

        class _FakeSessionOptions:
            inter_op_num_threads = 0
            intra_op_num_threads = 0
            enable_cpu_mem_arena = True
            log_severity_level = 0

        class _FakeSession:
            def __init__(self, *_args, **_kwargs):
                pass

        with patch("onnxruntime.disable_telemetry_events", side_effect=lambda: calls.append("disabled")), patch(
            "onnxruntime.SessionOptions", _FakeSessionOptions
        ), patch("onnxruntime.InferenceSession", _FakeSession):
            SileroStreamingVAD()

        self.assertEqual(calls, ["disabled"])

    def test_interrupted_turn_id_never_resets_cancel_or_confirms_pending_action(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td).resolve()
            write_target = tmp_dir / "must_not_confirm.txt"
            orig_roots = settings.FILESYSTEM_ALLOWED_ROOTS
            settings.FILESYSTEM_ALLOWED_ROOTS = (tmp_dir,)
            try:
                assistant, _ = _build_isolated_assistant(tmp_dir, Mock())
                assistant.planner.create_plan = lambda _q: Mock(
                    steps=[
                        PlanStep(
                            "write_file",
                            {"path": str(write_target), "content": "unauthorized_stale_yes"},
                            "Write file",
                        )
                    ]
                )
                stt = Mock()
                cfg = VoiceConfig(voice_responses_enabled=False, recordings_dir=str(tmp_dir / "rec"))
                orch = VoiceOrchestrator(
                    assistant,
                    config=cfg,
                    stt=stt,
                    player=_StubPlayer(),
                    mic_factory=lambda _c: _FakeMicStream(),
                    vad_backend=lambda _f: 0.0,
                )
                orch.start_voice_mode(background_workers=False)
                audio = np.zeros(PIPELINE_SAMPLE_RATE // 2, dtype=np.float32)

                stt.transcribe_detailed.return_value = _tr("write file")
                stage_resp = orch.handle_utterance_audio(audio)
                self.assertIn("Proceed?", stage_resp)
                self.assertIsNotNone(assistant.pending_action)

                # Simulate worker queued with matching turn_id, interrupted during confirmation gate
                with orch._lock:
                    orch._turn_cancel_event.clear()
                    orch._turn_id += 1
                    queued_tid = orch._turn_id

                stt.transcribe_detailed.return_value = _tr("Yes")
                orig_gate = orch._check_voice_confirmation_gate

                def _gate_then_interrupt(stt_res):
                    gated = orig_gate(stt_res)
                    orch.interrupt("barge_in_before_confirm")
                    return gated

                orch._check_voice_confirmation_gate = _gate_then_interrupt
                res = orch.handle_utterance_audio(audio, turn_id=queued_tid)
                self.assertIsNone(res)
                self.assertFalse(write_target.exists())
                orch.close()
            finally:
                settings.FILESYSTEM_ALLOWED_ROOTS = orig_roots


if __name__ == "__main__":
    unittest.main()
