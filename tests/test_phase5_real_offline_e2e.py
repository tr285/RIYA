"""Unmocked offline Phase 5 E2E verification using local Qwen3 GGUF on real SQLite & workspace."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

import pytest

from core.agent.assistant import RIYAAssistant
from core.identity.manager import IdentityManager
from core.memory.database import MemoryDatabase
from core.security.permissions import PermissionManager
from core.tasks.models import StepStatus, TaskPlan, TaskStatus, TaskStep

MODEL_PATH = Path("/Users/tukaramgore/Projects/RIYA/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf")


def test_real_offline_phase5_qwen3_task_planning_confirmation_execution_and_restart_recovery(tmp_path):
    assert MODEL_PATH.is_file(), f"Missing local GGUF model: {MODEL_PATH}"

    db_path = tmp_path / "phase5_real_e2e.db"
    users_root = tmp_path / "users"

    db = MemoryDatabase(db_path)
    id_mgr = IdentityManager(db, users_root=users_root)
    pm = PermissionManager(id_mgr)
    id_mgr.create_user("alice", password="AlicePassword1", display_name="Alice")
    alice = id_mgr.authenticate("alice", password="AlicePassword1")

    for perm in (
        "filesystem.read",
        "filesystem.create",
        "filesystem.modify",
        "filesystem.directory",
        "compute.calculate",
        "terminal.execute",
        "system.info",
        "memory.read",
        "tasks.manage",
        "skills.manage",
        "learning.manage",
        "self_improve.propose",
    ):
        pm.grant_permission(
            alice,
            perm,
            decision="allow",
            lifetime="persistent",
            explicit_user_approval=True,
        )

    ws_alice = id_mgr.user_workspace(alice.user_id)
    (ws_alice / "sample_report.txt").write_text("Phase 5 Qwen3 offline verification source\n", encoding="utf-8")

    t_load_start = time.perf_counter()
    assistant = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        model_path=MODEL_PATH,
        ai_backend="auto",
    )
    init_ms = round((time.perf_counter() - t_load_start) * 1000.0, 2)

    try:
        assistant.inference_timeout_seconds = 120.0

        # 1. Real Qwen3 task decomposition -> DAG validation -> step execution
        t_plan_start = time.perf_counter()
        plan = assistant.coordinator.plan_task(
            alice,
            "Calculate (18 * 24) + 8 and list the files in the current workspace folder '.'",
            conversation_context="",
        )
        plan_ms = round((time.perf_counter() - t_plan_start) * 1000.0, 2)
        print(f"[Phase5-E2E-Plan] plan_ms={plan_ms} steps={[(s.step_id, s.action, s.parameters, s.depends_on) for s in plan.steps]}")
        assert len(plan.steps) >= 1
        assert any(s.action in {"calculate", "list_files"} for s in plan.steps)

        exec_res = assistant.run_task_plan(plan)
        assert exec_res.status == TaskStatus.COMPLETED
        assert exec_res.verified is True
        print(f"[Phase5-E2E-Exec] status={exec_res.status.value} summary={exec_res.summary!r}")

        # 2. Real Qwen3 multi-step task via assistant.process('task: ...') -> staged confirmation -> approved execution
        t_task_start = time.perf_counter()
        confirm_prompt = assistant.process(
            "task: Calculate 15 * 12 and write the result '180' into notes/e2e_result.txt"
        )
        print(f"[Phase5-E2E-TaskConfirm] reply={confirm_prompt!r}")

        # If write_file is paused awaiting confirmation, approve it cleanly via RIYAAssistant confirmation path
        if "proceed?" in confirm_prompt.lower() and assistant.pending_action is not None:
            approved_reply = assistant.process("yes")
            print(f"[Phase5-E2E-TaskApproved] reply={approved_reply!r}")

        task_ms = round((time.perf_counter() - t_task_start) * 1000.0, 2)
        e2e_file = ws_alice / "notes" / "e2e_result.txt"
        assert e2e_file.is_file(), f"Expected real file write after approval, saw files={list(ws_alice.rglob('*'))}"
        assert "180" in e2e_file.read_text(encoding="utf-8")

        # Verify built-in skill + repeated workflow proposal
        skill_res = assistant.run_skill("coding_assistant", inputs={"path": ".", "file": "sample_report.txt"})
        assert skill_res.success is True
        assert "Phase 5 Qwen3" in skill_res.output["preview"]

        # 3. Real persistence + simulated process restart recovery (idempotent non-duplication of step 1)
        recoverable_plan = TaskPlan(
            goal="Check current time then save persistent checkpoint note",
            steps=[
                TaskStep(step_id="r1", action="current_time"),
                TaskStep(
                    step_id="r2",
                    action="write_file",
                    parameters={"path": "notes/restarted.txt", "content": "recovered after restart"},
                    depends_on=["r1"],
                ),
            ],
        )
        paused_before_restart = assistant.run_task_plan(recoverable_plan)
        assert paused_before_restart.status == TaskStatus.AWAITING_CONFIRMATION
        assert assistant.pending_action is not None
        assert assistant.pending_action["tool"] == "write_file"

        # Metrics before shutting down assistant instance 1
        metrics1 = assistant.ai_metrics()
        print(f"[Phase5-E2E-Metrics1] init_ms={init_ms} task_ms={task_ms} metrics={metrics1}")
        assert metrics1["loaded"] is True
        assert metrics1["successful_generations"] >= 2
    finally:
        assistant.close()

    # Reopen brand-new DB, IdentityManager, PermissionManager & RIYAAssistant (restarting app from scratch)
    db2 = MemoryDatabase(db_path)
    id_mgr2 = IdentityManager(db2, users_root=users_root)
    pm2 = PermissionManager(id_mgr2)
    alice2 = id_mgr2.authenticate("alice", password="AlicePassword1")

    time_calls = {"count": 0}
    assistant2 = RIYAAssistant(
        identity_manager=id_mgr2,
        default_identity=alice2,
        require_identity=True,
        permission_manager=pm2,
        ai_backend="none",
        allow_degraded_ai=True,
    )
    try:
        orig_time = assistant2.agent.tools.get("current_time").function
        assistant2.agent.tools._tools["current_time"].function = (
            lambda: (time_calls.__setitem__("count", time_calls["count"] + 1), orig_time())[1]
        )
        recoverable = assistant2.list_recoverable_tasks()
        assert any(item["task_id"] == recoverable_plan.task_id for item in recoverable)

        # Resume after restart -> demands fresh user confirmation, and r1 is NOT re-run!
        reprompt = assistant2._format_task_result(assistant2.resume_task(recoverable_plan.task_id))
        assert "proceed?" in reprompt.lower()
        assert time_calls["count"] == 0  # r1 remained COMPLETED from DB checkpoint!

        resumed_reply = assistant2.process("yes")
        print(f"[Phase5-E2E-Restarted] reply={resumed_reply!r}")
        restarted_file = id_mgr2.user_workspace(alice2.user_id) / "notes" / "restarted.txt"
        assert restarted_file.is_file()
        assert "recovered after restart" in restarted_file.read_text(encoding="utf-8")
        assert time_calls["count"] == 0

        # Confirm zero unredacted confirmation tokens leaked into task SQLite tables
        with sqlite3.connect(db_path) as raw_conn:
            step_rows = raw_conn.execute("SELECT parameters_json, result_json FROM task_steps").fetchall()
            for p_json, r_json in step_rows:
                assert "confirmation_token" not in (p_json or "")
                assert "AlicePassword1" not in (p_json or "") + (r_json or "")
    finally:
        assistant2.close()
