"""Phase 6 — Milestone 6.1: Unified Personal Assistant Interface verification suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.agent.assistant import RIYAAssistant
from core.agent.planner import Plan, PlanStep
from core.identity.manager import IdentityManager
from core.memory.database import MemoryDatabase
from core.onboarding.manager import OnboardingCoordinator
from core.security.permissions import PermissionManager
from core.tasks.models import TaskPlan, TaskStep
from core.voice.config import VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession
from core.voice.ui import (
    AssistantInterface,
    AssistantSnapshot,
    VoiceUIController,
    launch_voice_gui,
)


class _ScriptedAI:
    """Deterministic local AI stub for fast unit tests of interface state transitions."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or ['{"steps": []}', "Hello from RIYA local AI."])
        self.is_loaded = True
        self.selected_backend = "metal"
        self.calls: list[str] = []

    def generate(self, prompt: str, *args: Any, **kwargs: Any) -> str:
        self.calls.append(prompt)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0] if self.responses else "OK"

    def cancel(self) -> None:
        pass

    def reset_cancel(self) -> None:
        pass

    def unload_model(self) -> None:
        self.is_loaded = False

    def metrics_summary(self) -> dict[str, Any]:
        return {
            "loaded": self.is_loaded,
            "backend": self.selected_backend,
            "total_calls": len(self.calls),
        }


def _build_two_user_env(tmp_path: Path, ai: Any = None):
    db = MemoryDatabase(tmp_path / "phase6_m61.db")
    id_mgr = IdentityManager(db, users_root=tmp_path / "users")
    pm = PermissionManager(id_mgr, strict_default_deny=False)

    id_mgr.create_user("alice", password="AlicePassword123!", display_name="Alice")
    id_mgr.create_user("bob", password="BobPassword123!", display_name="Bob")
    alice = id_mgr.authenticate("alice", password="AlicePassword123!")
    bob = id_mgr.authenticate("bob", password="BobPassword123!")

    assistant = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        ai_backend="none",
        allow_degraded_ai=True,
    )
    if ai is not None:
        assistant.ai = ai
        assistant.planner.ai = ai
        assistant.ai_unavailable_reason = None
        assistant.model_path = tmp_path / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

    cfg_alice = VoiceConfig(
        recordings_dir=str(VoiceConfig.recordings_dir_for_user(id_mgr.users_root, alice.user_id))
    )
    session = VoiceSession(
        cfg_alice,
        user_id=alice.user_id,
        auth_session_id=alice.session_id,
    )
    orch = VoiceOrchestrator(
        assistant,
        config=cfg_alice,
        session=session,
        identity_manager=id_mgr,
        permission_manager=pm,
        identity=alice,
    )
    return db, id_mgr, pm, assistant, orch, alice, bob


def test_unified_interface_tkinter_window_and_controls(tmp_path: Path):
    """Criteria 1, 2, 10: Unified interface launches cleanly and binds to RIYA backend."""
    VoiceSession._active_session_id = None
    ai = _ScriptedAI(['{"steps": []}', "Unified UI ready."])
    _, _, _, assistant, orch, _, _ = _build_two_user_env(tmp_path, ai=ai)

    ui = AssistantInterface(orch)
    assert isinstance(ui, VoiceUIController)
    snap = ui.snapshot()
    assert isinstance(snap, AssistantSnapshot)
    assert snap.connectivity_status == "Offline-First (Local Only)"
    assert snap.model_status_label == "AI: Ready (metal)"
    assert snap.operation_state == "idle"
    assert ui.current_user_label() == "Alice (@alice)"

    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        outcome = launch_voice_gui(
            orch,
            root_window=root,
            run_mainloop=False,
        )
        assert outcome == "ready"
        assert "RIYA" in root.title()
        children = root.winfo_children()
        assert len(children) >= 5
    finally:
        orch.close()
        assistant.close()
        root.destroy()
        VoiceSession._active_session_id = None


def test_text_conversation_streaming_and_per_user_history_hydration(tmp_path: Path):
    """Criteria 3, 4: Streaming text conversation + per-user history persistence & hydration."""
    VoiceSession._active_session_id = None
    ai = _ScriptedAI(
        [
            '{"steps": []}',
            "Welcome back Alice, your offline workspace is ready for today.",
            '{"steps": []}',
            "Hello Bob, this is your separate conversation history.",
        ]
    )
    _, id_mgr, pm, assistant, orch, alice, bob = _build_two_user_env(tmp_path, ai=ai)
    ui = AssistantInterface(orch)

    streamed_chunks: list[str] = []
    reply = ui.submit_text(
        "Hello RIYA, check my workspace.",
        on_chunk=lambda piece: streamed_chunks.append(piece),
    )
    assert "Welcome back Alice" in reply
    assert len(streamed_chunks) >= 1
    assert " ".join(streamed_chunks) == reply

    hist_alice = ui.conversation_history()
    assert len(hist_alice) == 2
    assert hist_alice[0]["role"] == "user"
    assert hist_alice[1]["role"] == "assistant"

    # Simulate closing the UI session and opening a fresh VoiceSession/Orchestrator for Alice:
    # persisted SQLite conversation history must hydrate automatically into the new UI session.
    orch.close()
    fresh_session = VoiceSession(
        orch.config,
        user_id=alice.user_id,
        auth_session_id=alice.session_id,
    )
    fresh_orch = VoiceOrchestrator(
        assistant,
        config=orch.config,
        session=fresh_session,
        identity_manager=id_mgr,
        permission_manager=pm,
        identity=alice,
    )
    fresh_ui = AssistantInterface(fresh_orch)
    hydrated = fresh_ui.snapshot().transcript
    assert len(hydrated) == 2
    assert hydrated[0].text == "Hello RIYA, check my workspace."
    assert "Welcome back Alice" in hydrated[1].text
    assert hydrated[0].status == "history"

    # Switch to Bob: Bob has zero prior turns, so transcript must not leak Alice's history.
    cfg_bob = VoiceConfig(
        recordings_dir=str(VoiceConfig.recordings_dir_for_user(id_mgr.users_root, bob.user_id))
    )
    assistant.set_identity(bob)
    fresh_orch.switch_identity(bob, config=cfg_bob)
    assert fresh_ui.current_user_label() == "Bob (@bob)"
    assert fresh_ui.snapshot().transcript == ()

    bob_reply = fresh_ui.submit_text("Hi RIYA, I am Bob.")
    assert "Hello Bob" in bob_reply
    assert len(fresh_ui.snapshot().transcript) == 2

    # Clearing Bob's conversation history removes Bob's persisted rows without touching Alice's.
    deleted_bob = fresh_ui.clear_conversation_history(clear_persisted=True)
    assert deleted_bob == 2
    assert fresh_ui.snapshot().transcript == ()

    assistant.set_identity(alice)
    fresh_orch.switch_identity(alice, config=orch.config)
    assert len(fresh_ui.snapshot().transcript) == 2
    assert fresh_ui.snapshot().transcript[0].text == "Hello RIYA, check my workspace."

    fresh_orch.close()
    assistant.close()
    VoiceSession._active_session_id = None


def test_autonomous_task_submission_progress_approval_rejection_and_skills(tmp_path: Path):
    """Criteria 5, 6, 7, 8: Multi-step task submission, progress, approval/rejection & skill results."""
    VoiceSession._active_session_id = None
    ai = _ScriptedAI()
    _, _, _, assistant, orch, alice, _ = _build_two_user_env(tmp_path, ai=ai)
    ui = AssistantInterface(orch)

    alice_ws = Path(alice.workspace_dir)
    target_file = alice_ws / "m61_task_output.txt"
    rejected_file = alice_ws / "m61_rejected.txt"

    # Stage a 3-step autonomous task plan:
    # Step 1: list_files (safe, executes immediately)
    # Step 2: write_file (sensitive, pauses for user approval)
    # Step 3: read_file (depends on step_2, executes after approval)
    def _mock_decompose(request: str, conversation_context: str | None = None) -> TaskPlan:
        if "rejected" in request:
            return TaskPlan(
                goal=request,
                steps=[
                    TaskStep(
                        step_id="step_1",
                        action="write_file",
                        description="Write rejected file",
                        parameters={"path": str(rejected_file), "content": "should not exist"},
                    )
                ],
            )
        return TaskPlan(
            goal=request,
            steps=[
                TaskStep(
                    step_id="step_1",
                    action="list_files",
                    description="List workspace files",
                    parameters={"path": str(alice_ws)},
                ),
                TaskStep(
                    step_id="step_2",
                    action="write_file",
                    description="Create milestone 6.1 note",
                    parameters={"path": str(target_file), "content": "Milestone 6.1 verified"},
                    depends_on=["step_1"],
                ),
                TaskStep(
                    step_id="step_3",
                    action="read_file",
                    description="Read back milestone 6.1 note",
                    parameters={"path": str(target_file)},
                    depends_on=["step_2"],
                ),
            ],
        )

    assistant.planner.decompose_task = _mock_decompose  # type: ignore[method-assign]

    # 1. Submit the multi-step task from the unified interface.
    sub_res = ui.submit_task("List workspace, write m61_task_output.txt, and read it back")
    assert sub_res["status"] == "awaiting_confirmation"
    assert sub_res["requires_confirmation"] is True
    assert sub_res["steps_completed"] == 1
    assert sub_res["steps_total"] == 3
    assert not target_file.exists()

    # Verify real-time progress events & pending approval banner state in snapshot.
    snap = ui.snapshot()
    assert snap.operation_state == "awaiting_confirmation"
    assert snap.pending_confirmation is not None
    assert snap.pending_confirmation["tool"] == "write_file"
    assert snap.pending_confirmation["task_id"] == sub_res["task_id"]
    assert snap.pending_confirmation["risk_tier"] in {"sensitive-confirm", "protected-default-deny"}
    assert len(ui.task_progress_history()) >= 2

    # 2. Approve the pending action through the unified interface.
    approval_reply = ui.approve_pending_action()
    assert target_file.exists()
    assert target_file.read_text(encoding="utf-8") == "Milestone 6.1 verified"
    assert "Milestone 6.1 verified" in approval_reply or "Completed" in approval_reply

    snap_after = ui.snapshot()
    assert snap_after.pending_confirmation is None
    assert snap_after.active_task_progress is not None
    assert snap_after.active_task_progress["status"] == "completed"

    # Verify tool execution results are recorded and inspectable.
    tool_outcomes = ui.last_tool_results()
    executed_tools = [r["tool"] for r in tool_outcomes]
    assert "list_files" in executed_tools
    assert "write_file" in executed_tools
    assert "read_file" in executed_tools

    # 3. Submit a second task and REJECT the confirmation request through the interface.
    sub_reject = ui.submit_task("Create rejected file")
    assert sub_reject["status"] == "awaiting_confirmation"
    assert ui.snapshot().pending_confirmation is not None

    reject_reply = ui.reject_pending_action()
    assert "cancelled" in reject_reply.lower()
    assert not rejected_file.exists()
    assert ui.snapshot().pending_confirmation is None
    task_detail = ui.get_task_detail(sub_reject["task_id"])
    assert task_detail is not None
    assert task_detail["status"] == "cancelled"

    # 4. Run a built-in skill through the unified interface.
    skills = ui.list_skills()
    assert any(s["skill_id"] == "system_diagnostics" for s in skills)
    skill_res = ui.run_skill("system_diagnostics")
    assert skill_res["status"] == "success"
    assert skill_res["verified"] is True
    assert any(r["tool"] == "skill:system_diagnostics" for r in ui.last_tool_results())

    orch.close()
    assistant.close()
    VoiceSession._active_session_id = None


def test_memory_controls_permission_errors_recovery_and_status_indicators(tmp_path: Path):
    """Criteria 9, 10, 11: Memory controls, permission enforcement, error & recovery display."""
    VoiceSession._active_session_id = None
    ai = _ScriptedAI()
    _, id_mgr, _, assistant, orch, alice, bob = _build_two_user_env(tmp_path, ai=ai)
    ui = AssistantInterface(orch)

    # 1. Connectivity and model status inspection.
    conn = ui.connectivity_status()
    assert conn["offline_mode"] is True
    assert conn["online_enabled"] is False
    model_st = ui.model_status()
    assert model_st["loaded"] is True
    assert model_st["backend"] == "metal"
    assert model_st["model_name"] == "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

    # 2. Memory CRUD through the unified interface (isolated per user).
    save_msg = ui.remember_fact("editor_theme", "Solarized Dark", category="user_preferences")
    assert "Remembered" in save_msg
    rows = ui.list_memories()
    assert any(r["key"] == "editor_theme" and r["value"] == "Solarized Dark" for r in rows)

    hits = ui.search_memories("Solarized")
    assert len(hits) == 1
    assert hits[0]["key"] == "editor_theme"

    # Bob cannot see Alice's memories.
    cfg_bob = VoiceConfig(
        recordings_dir=str(VoiceConfig.recordings_dir_for_user(id_mgr.users_root, bob.user_id))
    )
    assistant.set_identity(bob)
    orch.switch_identity(bob, config=cfg_bob)
    assert ui.list_memories() == []

    # Switch back to Alice and delete the memory.
    cfg_alice = VoiceConfig(
        recordings_dir=str(VoiceConfig.recordings_dir_for_user(id_mgr.users_root, alice.user_id))
    )
    assistant.set_identity(alice)
    orch.switch_identity(alice, config=cfg_alice)
    assert ui.forget_fact("editor_theme", category="user_preferences") is True
    assert ui.list_memories() == []

    # 3. Deny filesystem.create for Alice and verify accurate error & recovery message display.
    ui.deny_permission("filesystem.create", lifetime="session", reason="Test UI error banner")
    assistant.planner.create_plan = lambda *a, **kw: Plan(  # type: ignore[method-assign]
        steps=[
            PlanStep(
                action="create_folder",
                description="Create blocked folder",
                parameters={"path": str(Path(alice.workspace_dir) / "blocked_dir")},
            )
        ]
    )
    resp = ui.submit_text("Create a folder named blocked_dir")
    assert "couldn't authorize" in resp.lower() or "denied" in resp.lower()

    err_snap = ui.snapshot()
    assert err_snap.error_message is not None
    assert "create_folder" in err_snap.error_message
    assert err_snap.recovery_message is not None
    assert "Permissions" in err_snap.recovery_message

    ui.clear_error()
    assert ui.snapshot().error_message is None

    orch.close()
    assistant.close()
    VoiceSession._active_session_id = None


def test_real_offline_qwen3_unified_interface_e2e(tmp_path: Path):
    """Unmocked local Qwen3-4B Metal E2E driving text conversation, approval & task execution via UI."""
    VoiceSession._active_session_id = None
    coord = OnboardingCoordinator(
        database_path=tmp_path / "real_m61.db",
        users_root=tmp_path / "users",
    )
    coord.create_local_account(
        username="tukaram",
        display_name="Tukaram",
        secret="TukaramPass123!",
    )
    assistant, orch = coord.build_runtime_session()

    try:
        ui = AssistantInterface(orch)
        model_info = ui.model_status()
        assert model_info["loaded"] is True
        assert model_info["backend"] in {"metal", "cpu"}
        assert ui.connectivity_status()["offline_mode"] is True

        # 1. Real local Qwen3 text conversation with progressive chunk streaming.
        chunks: list[str] = []
        r1 = ui.submit_text(
            "Reply with only the number: what is 9 multiplied by 7?",
            on_chunk=lambda c: chunks.append(c),
        )
        assert "63" in r1
        assert len(chunks) >= 1

        # 2. Trigger a confirmation-gated memory write via natural language, inspect pending_confirmation,
        # and approve it through the unified interface.
        r2 = ui.submit_text("Remember that my project codename is Horizon61")
        snap_pending = ui.snapshot()
        assert "Proceed?" in r2
        assert snap_pending.pending_confirmation is not None
        assert snap_pending.pending_confirmation["tool"] == "remember"

        r3 = ui.approve_pending_action()
        assert "Remembered" in r3
        assert ui.snapshot().pending_confirmation is None
        stored = ui.list_memories()
        assert any("Horizon61" in str(m.get("value", "")) for m in stored)
    finally:
        orch.close()
        assistant.close()
        VoiceSession._active_session_id = None
