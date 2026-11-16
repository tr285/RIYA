"""Phase 4 unit and integration tests: Native AI runtime & Advanced Conversation Intelligence."""

from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.agent.assistant import RIYAAssistant
from core.agent.context import ConversationContext, ConversationSummaryStore
from core.ai.errors import (
    ContextOverflowError,
    InferenceExecutionError,
    ModelLoadError,
    ModelNotFoundError,
    NativeAIError,
    map_native_error,
)
from core.ai.local_runtime import LocalLlamaRuntime
from core.ai.native.llama_runtime import NativeLlamaRuntime
from core.identity.manager import ConversationHistoryStore, IdentityManager
from core.memory.database import MemoryDatabase
from core.security.permissions import PermissionManager


def test_native_error_mapping_covers_bridge_codes():
    err4 = map_native_error(-4, context_size=4096)
    assert isinstance(err4, ContextOverflowError)
    assert isinstance(err4, NativeAIError)
    assert isinstance(err4, RuntimeError)
    assert "4096" in str(err4)

    for code in (-1, -2, -3, -5, -6):
        err = map_native_error(code)
        assert isinstance(err, InferenceExecutionError)
        assert err.code == code


def test_local_runtime_avoids_unnecessary_reloads_and_tracks_metrics(tmp_path):
    model_file = tmp_path / "dummy.gguf"
    model_file.write_bytes(b"GGUF")

    fake_native = Mock()
    fake_native.backend = "metal"
    fake_native.is_loaded = True
    fake_native.generate.return_value = "Hello from offline RIYA."

    with patch("core.ai.local_runtime.NativeLlamaRuntime", return_value=fake_native) as ctor:
        runtime = LocalLlamaRuntime(context_size=2048, max_tokens=64, backend="auto")
        runtime.load_model(model_file)
        assert ctor.call_count == 1
        assert runtime.is_loaded
        assert runtime.selected_backend == "metal"

        # Identical signature must NOT unload or reconstruct NativeLlamaRuntime.
        runtime.load_model(model_file)
        assert ctor.call_count == 1
        assert runtime.metrics.load_count == 1
        assert runtime.metrics.reload_skipped_count == 1

        # Calling ensure_loaded also skips reload when already active.
        assert runtime.ensure_loaded(model_file) is False
        assert runtime.metrics.reload_skipped_count == 2

        # force_reload=True triggers explicit unload + reload.
        runtime.load_model(model_file, force_reload=True)
        assert ctor.call_count == 2
        assert runtime.metrics.load_count == 2
        assert fake_native.close.call_count == 1

        reply = runtime.generate(
            "What can you do?",
            conversation_context=[
                {"role": "user", "content": "Hi RIYA"},
                {"role": "assistant", "content": "Hello!"},
            ],
            system_guidance="Keep answers concise.",
            memory_context="- [important_facts] project: RIYA offline assistant",
        )
        assert reply == "Hello from offline RIYA."
        gen_metrics = runtime.last_metrics
        assert gen_metrics is not None
        assert gen_metrics.operation == "generate"
        assert gen_metrics.success is True
        assert gen_metrics.latency_ms >= 0.0
        assert gen_metrics.rss_mb_after > 0.0
        assert gen_metrics.approx_prompt_tokens > 0
        assert gen_metrics.approx_output_tokens > 0

        summary = runtime.metrics_summary()
        assert summary["loaded"] is True
        assert summary["load_count"] == 2
        assert summary["reload_skipped_count"] == 2
        assert summary["successful_generations"] == 1
        assert summary["failed_generations"] == 0

        # Prompt passed to native engine includes structured isolation tags and guidance.
        sent_prompt = fake_native.generate.call_args.args[0]
        assert "<conversation_history>" in sent_prompt
        assert "<long_term_memory>" in sent_prompt
        assert "Keep answers concise." in sent_prompt
        assert "<user_input>" in sent_prompt

        runtime.unload_model()
        assert not runtime.is_loaded
        assert runtime.selected_backend == "unloaded"


def test_local_runtime_auto_backend_falls_back_to_cpu_cleanly(tmp_path):
    model_file = tmp_path / "dummy.gguf"
    model_file.write_bytes(b"GGUF")

    cpu_native = Mock()
    cpu_native.backend = "cpu"
    cpu_native.is_loaded = True

    def side_effect(_path, context_size=4096, backend="auto"):
        if backend == "auto":
            raise ModelLoadError("Metal init failed")
        return cpu_native

    with patch("core.ai.local_runtime.NativeLlamaRuntime", side_effect=side_effect):
        runtime = LocalLlamaRuntime(backend="auto")
        runtime.load_model(model_file)
        assert runtime.is_loaded
        assert runtime.selected_backend == "cpu"
        assert runtime.metrics_summary()["cpu_fallback_count"] == 1
        runtime.close()


def test_local_runtime_explicit_metal_does_not_mask_failure_and_context_manager(tmp_path):
    model_file = tmp_path / "dummy.gguf"
    model_file.write_bytes(b"GGUF")

    with patch(
        "core.ai.local_runtime.NativeLlamaRuntime",
        side_effect=ModelLoadError("Metal device unavailable"),
    ):
        with LocalLlamaRuntime(backend="metal") as runtime:
            with pytest.raises(ModelLoadError):
                runtime.load_model(model_file)
            assert not runtime.is_loaded
            assert runtime.selected_backend == "unloaded"

    with pytest.raises(ModelNotFoundError):
        LocalLlamaRuntime().load_model(tmp_path / "missing.gguf")


def test_native_runtime_translates_negative_c_error_codes():
    native = NativeLlamaRuntime.__new__(NativeLlamaRuntime)
    native.handle = ctypes.c_void_p(0x9999)
    native.backend = "cpu"
    native.context_size = 2048
    native._lock = None  # intentionally test lazy _ensure_state_attrs
    del native._lock

    native.lib = SimpleNamespace(
        riya_generate=lambda *_args: -4,
        riya_free_model=lambda _h: None,
    )
    with pytest.raises(ContextOverflowError):
        native.generate("hello world", max_tokens=16, timeout_seconds=1.0)

    native.lib = SimpleNamespace(
        riya_generate=lambda *_args: -5,
        riya_free_model=lambda _h: None,
    )
    with pytest.raises(InferenceExecutionError):
        native.generate("hello world", max_tokens=16, timeout_seconds=1.0)
    native.close()


def test_conversation_context_rolling_summary_followup_and_incomplete_clarification():
    ctx = ConversationContext(max_messages=4, max_message_chars=400, max_total_chars=1200)

    # Empty context + bare / anaphoric prompts trigger clear clarification requests.
    bare = ctx.analyze_turn("open")
    assert bare.is_incomplete is True
    assert "what would you like me to open" in bare.clarification_prompt.lower()

    no_history_followup = ctx.analyze_turn("explain it")
    assert no_history_followup.is_incomplete is True
    assert no_history_followup.clarification_prompt is not None

    # Populate turns beyond max_messages=4 to trigger rolling summarization.
    ctx.add_user("Tell me about Python decorators and Project Apollo")
    ctx.add_assistant("Decorators wrap functions cleanly without altering their signature.")
    ctx.add_user("I also keep notes in ~/Documents/notes.txt")
    ctx.add_assistant("Got it, notes are at ~/Documents/notes.txt.")
    ctx.add_user("Remind me how SQLite WAL mode works")
    ctx.add_assistant("WAL appends writes cleanly to a separate journal file.")

    assert len(ctx) == 4
    assert ctx.rolling_summary != ""
    assert "Python decorators" in ctx.rolling_summary or "Decorators" in ctx.rolling_summary

    # Follow-up resolves against active entities/subject cleanly.
    follow = ctx.analyze_turn("Tell me more about it")
    assert follow.is_follow_up is True
    assert follow.is_incomplete is False
    assert follow.resolved_subject is not None
    assert "[Resolved recent reference/subject:" in follow.resolved_prompt

    prompt_ctx = ctx.format_for_prompt()
    assert "Earlier summary:" in prompt_ctx
    assert "Active topic/reference:" in prompt_ctx
    assert ctx.summarize() != ""


def test_conversation_history_and_summary_persistence_isolated_per_user(tmp_path):
    db = MemoryDatabase(tmp_path / "phase4_conv.db")
    id_mgr = IdentityManager(db, users_root=tmp_path / "users")
    pm = PermissionManager(id_mgr)
    id_mgr.create_user("alice", password="AlicePassword1", display_name="Alice")
    id_mgr.create_user("bob", password="BobPassword2", display_name="Bob")
    alice = id_mgr.authenticate("alice", password="AlicePassword1")
    bob = id_mgr.authenticate("bob", password="BobPassword2")
    pm.grant_permission(
        alice,
        "personal.history",
        decision="allow",
        lifetime="persistent",
        explicit_user_approval=True,
    )
    pm.grant_permission(
        bob,
        "personal.history",
        decision="allow",
        lifetime="persistent",
        explicit_user_approval=True,
    )

    ai = Mock()
    ai.is_loaded = True
    ai.selected_backend = "cpu"
    ai.generate.side_effect = ['{"steps": []}', "Quantum computers use qubits."]

    assistant1 = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        allow_degraded_ai=True,
    )
    assistant1.ai = ai
    assistant1.planner.ai = ai

    # Incomplete bare input must ask clarifying question without calling planner/AI.
    clarify = assistant1.process("delete")
    assert "what would you like me to delete" in clarify.lower()
    assert ai.generate.call_count == 0

    reply = assistant1.process("Explain quantum computing simply")
    assert "qubits" in reply.lower()
    assistant1.close()

    # Re-open fresh RIYAAssistant for Alice -> context hydrates from persisted SQLite history + summary!
    ai2 = Mock()
    ai2.is_loaded = True
    ai2.selected_backend = "cpu"
    ai2.generate.side_effect = ['{"steps": []}', "Qubits can exist in superposition."]
    assistant2 = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        allow_degraded_ai=True,
    )
    assistant2.ai = ai2
    assistant2.planner.ai = ai2

    assert len(assistant2.context) >= 2
    assert any("quantum" in m.content.lower() for m in assistant2.context.messages())

    # Switch to Bob -> Bob sees zero Alice conversation turns or summaries.
    assistant2.set_identity(bob)
    assert len(assistant2.context) == 0
    assert assistant2.context.rolling_summary == ""

    sum_store = ConversationSummaryStore(id_mgr, permission_manager=pm)
    assert sum_store.get_summary(alice) is not None
    assert sum_store.get_summary(bob) is None
    hist_store = ConversationHistoryStore(id_mgr, permission_manager=pm)
    assert len(hist_store.get_history(alice)) >= 2
    assert hist_store.get_history(bob) == []
    assistant2.close()
