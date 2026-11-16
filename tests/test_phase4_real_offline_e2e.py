"""Real offline unmocked end-to-end tests using RIYA's local Qwen3 GGUF model & SQLite."""

from __future__ import annotations

from pathlib import Path

from core.agent.assistant import RIYAAssistant
from core.ai.local_runtime import LocalLlamaRuntime
from core.identity.manager import ConversationHistoryStore, IdentityManager
from core.learning.adaptation import PromptAdapter
from core.learning.evaluation import OfflineEvaluator
from core.learning.feedback import FeedbackStore
from core.memory.database import MemoryDatabase
from core.security.permissions import PermissionManager


MODEL_PATH = Path("/Users/tukaramgore/Projects/RIYA/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf")


def test_real_native_runtime_metal_cpu_metrics_and_no_unnecessary_reloads():
    assert MODEL_PATH.is_file(), f"Missing local GGUF model: {MODEL_PATH}"

    with LocalLlamaRuntime(
        context_size=2048,
        max_tokens=48,
        backend="auto",
        timeout_seconds=60.0,
    ) as runtime:
        runtime.load_model(MODEL_PATH)
        assert runtime.is_loaded
        assert runtime.selected_backend in {"metal", "cpu"}

        # Re-calling load / ensure_loaded with identical config skips reload.
        did_reload = runtime.ensure_loaded(MODEL_PATH)
        assert did_reload is False
        assert runtime.metrics.load_count == 1
        assert runtime.metrics.reload_skipped_count >= 1

        reply = runtime.generate(
            "In one short sentence, name the programming language Python.",
            max_tokens=40,
            timeout_seconds=60.0,
        )
        assert isinstance(reply, str) and len(reply.strip()) > 3
        summary = runtime.metrics_summary()
        assert summary["loaded"] is True
        assert summary["load_count"] == 1
        assert summary["successful_generations"] == 1
        assert summary["avg_latency_ms"] > 0.0
        assert summary["process_rss_mb"] > 0.0
        print(f"[E2E-Runtime-Metal] {summary} | reply={reply!r}")
    assert runtime.is_loaded is False

    # Verify explicit CPU fallback path works offline as well.
    with LocalLlamaRuntime(
        context_size=1024,
        max_tokens=16,
        backend="cpu",
        timeout_seconds=90.0,
    ) as cpu_runtime:
        cpu_runtime.load_model(MODEL_PATH)
        assert cpu_runtime.selected_backend == "cpu"
        cpu_reply = cpu_runtime.generate("Reply briefly: hello", max_tokens=12, timeout_seconds=90.0)
        assert isinstance(cpu_reply, str) and len(cpu_reply.strip()) > 0
        cpu_summary = cpu_runtime.metrics_summary()
        assert cpu_summary["successful_generations"] == 1
        print(f"[E2E-Runtime-CPU] {cpu_summary} | reply={cpu_reply!r}")


def test_real_offline_assistant_e2e_conversation_memory_learning_and_isolation(tmp_path):
    assert MODEL_PATH.is_file(), f"Missing local GGUF model: {MODEL_PATH}"

    db = MemoryDatabase(tmp_path / "phase4_real_e2e.db")
    id_mgr = IdentityManager(db, users_root=tmp_path / "users")
    pm = PermissionManager(id_mgr)
    id_mgr.create_user("alice", password="AlicePassword1", display_name="Alice")
    id_mgr.create_user("bob", password="BobPassword2", display_name="Bob")
    alice = id_mgr.authenticate("alice", password="AlicePassword1")
    bob = id_mgr.authenticate("bob", password="BobPassword2")

    for user in (alice, bob):
        for perm in (
            "memory.read",
            "memory.write",
            "memory.delete",
            "personal.history",
            "learning.manage",
        ):
            pm.grant_permission(
                user,
                perm,
                decision="allow",
                lifetime="persistent",
                explicit_user_approval=True,
            )

    assistant = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        model_path=MODEL_PATH,
        ai_backend="auto",
    )
    try:
        assistant.inference_timeout_seconds = 60.0

        # 1. Real Qwen3 multi-turn conversation + follow-up + summarization + metrics.
        turn1 = assistant.process("Explain what SQLite is in one short sentence.")
        assert len(turn1.strip()) > 5
        turn2 = assistant.process("Name one advantage of it over a client-server database.")
        assert len(turn2.strip()) > 5
        assert not turn2.startswith("I couldn't complete")
        print(f"[E2E-Turn1] {turn1!r}")
        print(f"[E2E-Turn2] {turn2!r}")

        metrics = assistant.ai_metrics()
        assert metrics["loaded"] is True
        assert metrics["load_count"] == 1
        assert metrics["successful_generations"] >= 2
        assert metrics["process_rss_mb"] > 0.0

        conv_summary = assistant.summarize_conversation()
        assert conv_summary and len(conv_summary.strip()) > 10
        print(f"[E2E-Assistant] metrics={metrics} | summary={conv_summary!r}")

        # 2. Memory save, semantic/exact recall, and non-silent overwrite confirmation flow.
        r1 = assistant.process("Remember that my primary language is Python")
        if "proceed?" in r1.lower():
            r1 = assistant.process("yes")
        assert "python" in assistant.process("What do you remember about primary language?").lower()

        conflict_prompt = assistant.process("Remember that my primary language is Rust")
        assert "overwrite" in conflict_prompt.lower()
        assert "python" in conflict_prompt.lower()
        cancel_reply = assistant.process("no")
        assert "cancel" in cancel_reply.lower()
        assert "python" in assistant.process("What do you remember about primary language?").lower()

        conflict_prompt2 = assistant.process("Remember that my primary language is Rust")
        assert "overwrite" in conflict_prompt2.lower()
        confirm_reply = assistant.process("yes")
        assert "rust" in confirm_reply.lower() or "remembered" in confirm_reply.lower()
        assert "rust" in assistant.process("What do you remember about primary language?").lower()

        # 3. Adaptive feedback -> preference profile -> real turn adaptation + offline evaluation.
        _, updated_profile = assistant.record_feedback(
            "correction",
            correction_text="Be concise and use bullet points.",
            tags=["concise", "bullets"],
        )
        assert updated_profile is not None
        assert updated_profile.response_style == "concise"
        assert updated_profile.formatting_preference == "bullets"
        adapted = PromptAdapter.adapt(updated_profile)
        assert "concise" in adapted.system_guidance.lower()

        evaluator = OfflineEvaluator(id_mgr, permission_manager=pm)
        evaluator.add_case(
            alice,
            dataset_name="e2e_qwen_offline",
            prompt="What is 2 + 2? Reply cleanly.",
            expected_keywords=["4"],
            max_latency_ms=90000,
        )
        eval_report = evaluator.run_dataset(
            alice,
            dataset_name="e2e_qwen_offline",
            responder=assistant.process,
        )
        assert eval_report.total_cases == 1
        assert eval_report.passed_cases == 1
        assert eval_report.avg_latency_ms > 0.0
        print(
            f"[E2E-Eval] pass_rate={eval_report.pass_rate} avg_latency_ms={eval_report.avg_latency_ms} "
            f"reply={eval_report.results[0].response!r}"
        )
    finally:
        assistant.close()

    # 4. Restart recovery & strict two-user isolation with fresh RIYAAssistant instance.
    restarted = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        model_path=MODEL_PATH,
        ai_backend="auto",
    )
    try:
        assert len(restarted.context) >= 2
        # Recent window preserves latest turns while evicted earlier turns ("SQLite") persist in rolling_summary.
        combined_hydrated = (
            restarted.context.rolling_summary
            + "\n"
            + "\n".join(m.content for m in restarted.context.messages())
        ).lower()
        assert "sqlite" in combined_hydrated
        assert any("rust" in m.content.lower() or "2 + 2" in m.content.lower() for m in restarted.context.messages())

        # Bob must see zero Alice history, zero Alice memories, zero Alice feedback/eval runs.
        hist_store = ConversationHistoryStore(id_mgr, permission_manager=pm)
        assert hist_store.get_history(bob) == []
        restarted.set_identity(bob)
        assert len(restarted.context) == 0
        bob_recall = restarted.process("What do you remember about primary language?")
        assert "rust" not in bob_recall.lower() and "python" not in bob_recall.lower()
        bob_hist = hist_store.get_history(bob)
        assert all("sqlite" not in h["content"].lower() and "rust" not in h["content"].lower() for h in bob_hist)
        assert FeedbackStore(id_mgr, permission_manager=pm).list_feedback(bob) == []
        assert OfflineEvaluator(id_mgr, permission_manager=pm).list_runs(bob) == []
    finally:
        restarted.close()
