from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import Mock

import pytest

from core.agent.assistant import RIYAAssistant
from core.agent.tool_executor import ToolExecutor
from core.agent.tool_registry import ToolRegistry
from core.database.migrations import (
    CURRENT_SCHEMA_VERSION,
    apply_migrations,
    get_schema_version,
    rollback_migration,
)
from core.identity.manager import IdentityManager
from core.learning.adaptation import PromptAdapter
from core.learning.evaluation import OfflineEvaluator
from core.learning.feedback import FeedbackStore
from core.learning.preferences import PreferenceLearner
from core.memory.database import MemoryDatabase
from core.memory.manager import (
    MEMORY_CATEGORIES,
    MemoryConflictError,
    MemoryManager,
    expand_canonical_categories,
    resolve_canonical_category,
)
from core.memory.semantic_memory import MemorySemanticScorer
from core.security.confirmation import ConfirmationManager
from core.security.manager import SecurityManager
from core.security.permissions import PermissionManager
from core.self_improve.analyzer import analyze_proposed_code, resolve_safe_repo_target
from core.self_improve.manager import SelfImprovementManager
from core.tools.memory import remember as remember_tool


def _setup_two_users(tmp_path: Path, name: str = "phase4.db"):
    db = MemoryDatabase(tmp_path / name)
    id_mgr = IdentityManager(db, users_root=tmp_path / "users")
    pm = PermissionManager(id_mgr)
    id_mgr.create_user("alice", password="AlicePassword1", display_name="Alice")
    id_mgr.create_user("bob", password="BobPassword2", display_name="Bob")
    alice = id_mgr.authenticate("alice", password="AlicePassword1")
    bob = id_mgr.authenticate("bob", password="BobPassword2")
    return db, id_mgr, pm, alice, bob


def test_schema_v5_upgrade_rollback_and_real_legacy_db_copy(tmp_path):
    # 1. Test clean v4 -> v5 -> v4 -> v5 rollback and re-upgrade.
    db_file = tmp_path / "migrate_roundtrip.db"
    conn = sqlite3.connect(db_file)
    try:
        applied_v4 = apply_migrations(conn, target_version=4)
        assert applied_v4 == [1, 2, 3, 4]
        assert get_schema_version(conn) == 4

        applied_v5 = apply_migrations(conn, target_version=5)
        assert applied_v5 == [5]
        assert get_schema_version(conn) == 5

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "conversation_summaries",
            "user_feedback",
            "adaptive_profiles",
            "evaluation_datasets",
            "evaluation_runs",
            "self_improvement_proposals",
        } <= tables
        mem_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        assert {
            "importance",
            "access_count",
            "last_accessed_at",
            "expires_at",
            "tags",
            "source",
        } <= mem_cols

        rolled = rollback_migration(conn, target_version=4)
        assert rolled == [5]
        assert get_schema_version(conn) == 4
        tables_v4 = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "user_feedback" not in tables_v4
        assert "self_improvement_proposals" not in tables_v4

        assert apply_migrations(conn, target_version=5) == [5]
        assert get_schema_version(conn) == 5
    finally:
        conn.close()

    # 2. Verify temporary copy of real live data/riya.db preserves all legacy v0.4 memories intact.
    live_db = Path("/Users/tukaramgore/Projects/RIYA/data/riya.db")
    if live_db.is_file():
        db_copy = tmp_path / "riya_copy.db"
        shutil.copy2(live_db, db_copy)
        rc = sqlite3.connect(db_copy)
        try:
            pre_legacy = rc.execute(
                "SELECT id, category, key, value, legacy_unclaimed FROM memories WHERE legacy_unclaimed = 1 ORDER BY id"
            ).fetchall()
            rc.close()

            migrated_db = MemoryDatabase(db_copy)
            with migrated_db.connect() as mconn:
                assert get_schema_version(mconn) == CURRENT_SCHEMA_VERSION
                assert CURRENT_SCHEMA_VERSION >= 5
                post_legacy = mconn.execute(
                    "SELECT id, category, key, value, legacy_unclaimed FROM memories WHERE legacy_unclaimed = 1 ORDER BY id"
                ).fetchall()
                assert [tuple(r) for r in post_legacy] == [tuple(r) for r in pre_legacy]
        finally:
            try:
                rc.close()
            except Exception:
                pass


def test_long_term_memory_categories_scoring_duplicates_ttl_export_and_isolation(tmp_path):
    db, id_mgr, pm, alice, bob = _setup_two_users(tmp_path, "mem_p4.db")
    for user in (alice, bob):
        for perm in ("memory.read", "memory.write", "memory.delete"):
            pm.grant_permission(
                user,
                perm,
                decision="allow",
                lifetime="persistent",
                explicit_user_approval=True,
            )

    scorer = MemorySemanticScorer(enable_embeddings=False)  # deterministic offline lexical/trigram fallback
    mem_a = MemoryManager(
        db,
        identity=alice,
        identity_manager=id_mgr,
        permission_manager=pm,
        require_user=True,
        prevent_silent_overwrite=True,
        semantic_scorer=scorer,
    )
    mem_b = MemoryManager(
        db,
        identity=bob,
        identity_manager=id_mgr,
        permission_manager=pm,
        require_user=True,
        prevent_silent_overwrite=True,
        semantic_scorer=scorer,
    )

    assert len(MEMORY_CATEGORIES) == 6
    assert resolve_canonical_category("personal") == "important_facts"
    assert resolve_canonical_category("preferences") == "user_preferences"
    assert "user_preferences" in expand_canonical_categories("user_preferences")

    # Store memories across canonical categories + legacy aliases.
    mem_a.store_memory(
        "user_preferences",
        "response_style",
        "concise bullet answers for engineering summaries",
        importance=0.95,
        tags=["style", "format"],
    )
    mem_a.store_memory(
        "personal_instructions",
        "code_review_rule",
        "Always check SQLite foreign keys and permission checks first",
        importance=0.9,
        tags=["security"],
    )
    mem_a.store_memory(
        "projects",
        "riya_v05",
        "Building RIYA offline assistant with local Qwen3 inference",
        importance=0.85,
        tags=["ai", "local"],
    )
    mem_a.store_memory(
        "important_facts",
        "hometown",
        "Pune, India",
        importance=0.7,
    )

    # Exact duplicate returns idempotent message, never overwrites unnecessarily.
    dup_exact = mem_a.check_duplicate("user_preferences", "response_style", "concise bullet answers for engineering summaries")
    assert dup_exact.status == "exact_duplicate"
    same_msg = mem_a.store_memory("user_preferences", "response_style", "concise bullet answers for engineering summaries")
    assert "already up to date" in same_msg.lower()

    # Key conflict blocks silent overwrite unless overwrite=True.
    dup_conflict = mem_a.check_duplicate("user_preferences", "response_style", "long narrative essays")
    assert dup_conflict.status == "key_conflict"
    with pytest.raises(MemoryConflictError) as exc_info:
        mem_a.store_memory("user_preferences", "response_style", "long narrative essays")
    assert exc_info.value.existing_record is not None
    assert "concise bullet" in exc_info.value.existing_record["value"]

    # Near duplicate detection across keys (substring & lexical overlap >= 0.85).
    dup_near = mem_a.check_duplicate(
        "user_preferences",
        "bullet_style_alias",
        "Always use concise bullet answers for engineering summaries",
    )
    assert dup_near.status == "near_duplicate"

    # Explicit edit updates existing record safely.
    assert mem_a.edit_memory(
        "projects",
        "riya_v05",
        "Building RIYA offline assistant with local Qwen3 GGUF and SQLite",
        importance=0.98,
    )
    assert "SQLite" in (mem_a.recall("projects", "riya_v05") or "")

    # Hybrid scoring & access count -> frequently_used.
    for _ in range(3):
        hits = mem_a.search("Qwen3 local assistant", limit=5, include_scores=True, record_access=True)
        assert hits
        assert hits[0]["key"] == "riya_v05"
        assert hits[0]["relevance_score"] > 0.3

    freq = mem_a.frequently_used(limit=5)
    assert freq and freq[0]["key"] == "riya_v05"
    assert freq[0]["access_count"] >= 3

    # Also validate cached offline SentenceTransformer scoring when installed in environment.
    cached_scorer = MemorySemanticScorer()
    if cached_scorer._model() is not None:
        mem_sem = MemoryManager(
            db,
            identity=alice,
            identity_manager=id_mgr,
            permission_manager=pm,
            require_user=True,
            semantic_scorer=cached_scorer,
        )
        sem_hits = mem_sem.search("Which software project uses a quantized language model locally?", limit=3, include_scores=True)
        assert sem_hits and sem_hits[0]["key"] == "riya_v05"

    # TTL expiration & pruning.
    mem_a.store_memory(
        "important_facts",
        "otp_reminder",
        "Temporary token 12345",
        expires_at="2000-01-01 00:00:00",
    )
    assert mem_a.recall("important_facts", "otp_reminder") is None
    assert not any(r["key"] == "otp_reminder" for r in mem_a.list_memories())
    assert any(r["key"] == "otp_reminder" for r in mem_a.list_memories(include_expired=True))
    pruned = mem_a.prune_expired()
    assert pruned == 1
    assert not any(r["key"] == "otp_reminder" for r in mem_a.list_memories(include_expired=True))

    # Cross-user isolation & export.
    mem_b.store_memory("important_facts", "secret", "Bob private note")
    exported_a = mem_a.export_memories()
    exported_b = mem_b.export_memories()
    assert all(r["key"] != "secret" for r in exported_a["memories"])
    assert len(exported_b["memories"]) == 1
    assert mem_b.search("Qwen3") == []
    assert mem_b.clear_category("user_preferences") == 0
    assert mem_a.recall("user_preferences", "response_style") is not None


def test_tool_executor_and_assistant_prevent_silent_overwrite_unless_confirmed(tmp_path):
    db, id_mgr, pm, alice, bob = _setup_two_users(tmp_path, "mem_confirm.db")
    # Even when user grants persistent ALLOW on memory.write, key conflicts MUST require confirmation!
    for user in (alice, bob):
        for perm in ("memory.read", "memory.write", "memory.delete", "personal.history"):
            pm.grant_permission(
                user,
                perm,
                decision="allow",
                lifetime="persistent",
                explicit_user_approval=True,
            )

    # 1. Direct ToolExecutor check.
    registry = ToolRegistry()
    registry.register("remember", "Remember info", remember_tool, parameters={"category", "key", "value"})
    executor = ToolExecutor(
        registry,
        identity_manager=id_mgr,
        permission_manager=pm,
        require_identity=True,
    )
    first_req = executor.execute(
        "remember",
        user_context=alice,
        category="user_preferences",
        key="editor",
        value="VS Code",
    )
    assert first_req["status"] == "confirmation_required"
    first = executor.execute(
        "remember",
        user_context=alice,
        confirmation_token=first_req["confirmation_token"],
        category="user_preferences",
        key="editor",
        value="VS Code",
    )
    assert first["status"] == "success"

    # Attempt overwrite via ToolExecutor -> blocked until explicit confirmation!
    conflict = executor.execute(
        "remember",
        user_context=alice,
        category="user_preferences",
        key="editor",
        value="Neovim",
    )
    assert conflict["status"] == "confirmation_required"
    assert conflict["conflict"]["status"] == "key_conflict"
    assert conflict["conflict"]["existing_value"] == "VS Code"

    # Forged or wrong user token fails.
    forged = executor.execute(
        "remember",
        user_context=alice,
        confirmation_token="forged-token-123",
        category="user_preferences",
        key="editor",
        value="Neovim",
    )
    assert forged["status"] == "authorization_failed"

    wrong_user = executor.execute(
        "remember",
        user_context=bob,
        confirmation_token=conflict["confirmation_token"],
        category="user_preferences",
        key="editor",
        value="Neovim",
    )
    assert wrong_user["status"] == "authorization_failed"

    # Attempted hijack burns the old token; Alice must obtain a fresh confirmation token.
    replayed_burned = executor.execute(
        "remember",
        user_context=alice,
        confirmation_token=conflict["confirmation_token"],
        category="user_preferences",
        key="editor",
        value="Neovim",
    )
    assert replayed_burned["status"] == "authorization_failed"

    conflict2 = executor.execute(
        "remember",
        user_context=alice,
        category="user_preferences",
        key="editor",
        value="Neovim",
    )
    assert conflict2["status"] == "confirmation_required"
    assert conflict2["conflict"]["status"] == "key_conflict"

    # Valid confirmation overwrites cleanly and consumes token.
    ok = executor.execute(
        "remember",
        user_context=alice,
        confirmation_token=conflict2["confirmation_token"],
        category="user_preferences",
        key="editor",
        value="Neovim",
    )
    assert ok["status"] == "success"

    # 2. Full RIYAAssistant conversational confirmation flow (cancel then confirm).
    ai = Mock()
    ai.is_loaded = True
    ai.selected_backend = "cpu"
    ai.generate.return_value = '{"steps": []}'
    assistant = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        allow_degraded_ai=True,
        ai_backend="none",
    )
    assistant.ai = ai
    assistant.planner.ai = ai

    p1 = assistant.process("Remember that my favorite city is Kyoto")
    if "proceed?" in p1.lower():
        p1 = assistant.process("yes")
    assert "kyoto" in assistant.process("What do you remember about favorite city?").lower()

    # Conflicting update -> assistant explicitly asks whether to overwrite; "no" preserves Kyoto.
    p_conflict = assistant.process("Remember that my favorite city is Osaka")
    assert "overwrite" in p_conflict.lower()
    assert "kyoto" in p_conflict.lower()
    cancel_msg = assistant.process("no")
    assert "cancel" in cancel_msg.lower()
    assert "kyoto" in assistant.process("What do you remember about favorite city?").lower()

    # Confirm overwrite -> updates cleanly to Osaka!
    p_conflict2 = assistant.process("Remember that my favorite city is Osaka")
    assert "overwrite" in p_conflict2.lower()
    confirm_msg = assistant.process("yes")
    assert "remembered" in confirm_msg.lower() or "osaka" in confirm_msg.lower()
    assert "osaka" in assistant.process("What do you remember about favorite city?").lower()
    assistant.close()


def test_adaptive_learning_feedback_preferences_evaluation_and_assistant_adaptation(tmp_path):
    db, id_mgr, pm, alice, bob = _setup_two_users(tmp_path, "learn.db")
    fb_store = FeedbackStore(id_mgr, permission_manager=pm)
    pref_learner = PreferenceLearner(id_mgr, permission_manager=pm, feedback_store=fb_store)
    evaluator = OfflineEvaluator(id_mgr, permission_manager=pm)

    # Fail-closed without learning.manage permission.
    with pytest.raises(PermissionError):
        fb_store.record_feedback(
            alice,
            prompt="How are you?",
            response="Good.",
            rating="positive",
        )

    for user in (alice, bob):
        for perm in ("learning.manage", "personal.history"):
            pm.grant_permission(
                user,
                perm,
                decision="allow",
                lifetime="persistent",
                explicit_user_approval=True,
            )

    e1 = fb_store.record_feedback(
        alice,
        prompt="Summarize design",
        response="Long paragraph...",
        rating="correction",
        correction_text="Too long, be concise and use bullet points with technical tone.",
        tags=["style", "concise"],
    )
    fb_store.record_feedback(
        alice,
        prompt="Explain architecture",
        response="- Core\n- DB",
        rating="positive",
    )
    stats_a = fb_store.feedback_stats(alice)
    assert stats_a == {"positive": 1, "negative": 0, "correction": 1, "total": 2}
    assert fb_store.list_feedback(bob) == []
    jsonl = fb_store.export_corrections_jsonl(alice)
    assert "Too long, be concise" in jsonl

    profile_a = pref_learner.learn_from_feedback(alice, e1)
    assert profile_a.response_style == "concise"
    assert profile_a.formatting_preference == "bullets"
    assert profile_a.tone == "technical"
    assert profile_a.correction_count == 1
    assert pref_learner.get_profile(bob).response_style == "balanced"

    # Security permissions remain untouched by adaptive learning!
    assert pm.evaluate(alice, "shell.execute", actor="test").blocked

    adapted = PromptAdapter.adapt(profile_a)
    assert "concise" in adapted.system_guidance.lower()
    assert adapted.suggested_max_tokens <= 256

    # Offline evaluation dataset & report persisted cleanly.
    evaluator.add_case(
        alice,
        dataset_name="core_qa",
        prompt="What language is RIYA written in?",
        expected_keywords=["Python"],
        forbidden_keywords=["cloud API"],
        max_latency_ms=2000,
    )
    evaluator.add_case(
        alice,
        dataset_name="core_qa",
        prompt="Does RIYA run offline?",
        expected_keywords=["offline"],
    )
    report = evaluator.run_dataset(
        alice,
        dataset_name="core_qa",
        responder=lambda q: "RIYA is written in Python and runs offline locally." if "language" in q.lower() else "Yes, offline.",
    )
    assert report.total_cases == 2
    assert report.passed_cases == 2
    assert report.pass_rate == 1.0
    runs = evaluator.list_runs(alice, dataset_name="core_qa")
    assert len(runs) == 1
    assert evaluator.list_runs(bob) == []

    # Reset profile works cleanly.
    pref_learner.reset_profile(alice)
    assert pref_learner.get_profile(alice).response_style == "balanced"

    # End-to-end adaptive wiring inside RIYAAssistant.
    ai = Mock()
    ai.is_loaded = True
    ai.selected_backend = "cpu"
    ai.generate.side_effect = [
        '{"steps": []}',
        "Long paragraph explanation.",
        '{"steps": []}',
        "- Concise technical point",
    ]
    assistant = RIYAAssistant(
        identity_manager=id_mgr,
        default_identity=alice,
        require_identity=True,
        permission_manager=pm,
        allow_degraded_ai=True,
        ai_backend="none",
    )
    assistant.ai = ai
    assistant.planner.ai = ai

    _ = assistant.process("Explain caching")
    _, updated_profile = assistant.record_feedback(
        "correction",
        correction_text="Make answers concise and use bullet points.",
        tags=["concise"],
    )
    assert updated_profile is not None
    assert updated_profile.response_style == "concise"
    assert updated_profile.formatting_preference == "bullets"

    _ = assistant.process("Explain indexing")
    # Fourth generate call (second response turn) must carry system_guidance reflecting concise/bullets.
    last_call_kwargs = ai.generate.call_args_list[-1].kwargs
    assert "concise" in last_call_kwargs.get("system_guidance", "").lower()
    assistant.close()


def test_controlled_self_improvement_security_sandbox_confirm_apply_and_rollback(tmp_path):
    # Create isolated fake repo inside tmp_path so real codebase files are never modified.
    repo_root = tmp_path / "repo"
    (repo_root / "core").mkdir(parents=True)
    (repo_root / "tests").mkdir(parents=True)
    (repo_root / "core" / "__init__.py").write_text("", encoding="utf-8")
    (repo_root / "core" / "sample_helper.py").write_text(
        "def add_one(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    (repo_root / "tests" / "test_sample_helper.py").write_text(
        "from core.sample_helper import add_one\n\ndef test_add_one():\n    assert add_one(2) == 3\n",
        encoding="utf-8",
    )

    db, id_mgr, pm, alice, bob = _setup_two_users(tmp_path, "self_imp.db")
    mgr = SelfImprovementManager(
        id_mgr,
        permission_manager=pm,
        project_root=repo_root,
        snapshots_root=tmp_path / "snapshots",
    )
    cm = ConfirmationManager()

    # 1. Fail-closed without self_improve.propose permission.
    with pytest.raises(PermissionError):
        mgr.propose_modification(
            alice,
            title="Improve helper",
            problem_description="Add docstring",
            target_file="core/sample_helper.py",
            proposed_content="def add_one(x: int) -> int:\n    return x + 1\n",
        )

    pm.grant_permission(
        alice,
        "self_improve.propose",
        decision="allow",
        lifetime="persistent",
        explicit_user_approval=True,
    )

    # 2. Protected security/identity/db paths & path escapes blocked.
    for forbidden_target in (
        "core/security/manager.py",
        "core/identity/manager.py",
        "core/database/migrations.py",
        "../outside.py",
        "/tmp/abs.py",
        "notes.txt",
    ):
        with pytest.raises((PermissionError, ValueError)):
            resolve_safe_repo_target(repo_root, forbidden_target)

    sym_link = repo_root / "core" / "evil_link.py"
    sym_link.symlink_to(repo_root / "core" / "sample_helper.py")
    with pytest.raises(ValueError):
        resolve_safe_repo_target(repo_root, "core/evil_link.py")

    # 3. Dangerous / network / eval / shell=True payloads blocked before sandbox runs.
    unsafe_prop = mgr.propose_modification(
        alice,
        title="Unsafe exfil",
        problem_description="Test blocking unsafe code",
        target_file="core/sample_helper.py",
        proposed_content="import requests\ndef add_one(x: int) -> int:\n    eval('1+1')\n    return x + 1\n",
    )
    checked_unsafe = mgr.verify_proposal(alice, unsafe_prop.proposal_id)
    assert not checked_unsafe.security_review_passed
    assert checked_unsafe.risk_level == "blocked"
    assert checked_unsafe.status == "failed"

    direct_static = analyze_proposed_code(
        "core/sample_helper.py",
        "import subprocess\ndef bad():\n    subprocess.run('ls', shell=True)\n",
    )
    assert not direct_static.passed

    # 4. Failing sandbox tests keep live repo untouched & prevent apply.
    broken_prop = mgr.propose_modification(
        alice,
        title="Broken logic",
        problem_description="Intentionally break add_one to verify sandbox gate",
        target_file="core/sample_helper.py",
        proposed_content="def add_one(x: int) -> int:\n    return x + 2\n",
    )
    verified_broken = mgr.verify_proposal(
        alice,
        broken_prop.proposal_id,
        test_files=("tests/test_sample_helper.py",),
    )
    assert verified_broken.static_analysis_passed
    assert verified_broken.security_review_passed
    assert not verified_broken.tests_passed
    assert verified_broken.status == "failed"
    assert "return x + 1" in (repo_root / "core" / "sample_helper.py").read_text(encoding="utf-8")

    # 5. Valid patch: propose -> verify in sandbox -> user diff review -> fail without apply permission/confirm -> authorized apply -> rollback.
    improved_src = (
        '"""Small numeric helper."""\n\n'
        "def add_one(x: int) -> int:\n"
        '    """Return integer incremented by one."""\n'
        "    return x + 1\n"
    )
    good_prop = mgr.propose_modification(
        alice,
        title="Document add_one helper",
        problem_description="Missing module and function docstrings.",
        target_file="core/sample_helper.py",
        proposed_content=improved_src,
    )
    verified_good = mgr.verify_proposal(
        alice,
        good_prop.proposal_id,
        test_files=("tests/test_sample_helper.py",),
    )
    assert verified_good.static_analysis_passed
    assert verified_good.security_review_passed
    assert verified_good.tests_passed
    assert verified_good.status == "awaiting_authorization"

    review = mgr.present_review(alice, good_prop.proposal_id)
    assert "+def add_one" not in review["diff"] or '"""Small numeric helper."""' in review["diff"]
    assert review["tests_passed"] is True

    # Bob cannot inspect or confirm Alice's proposal.
    with pytest.raises(PermissionError):
        mgr.present_review(bob, good_prop.proposal_id)

    # Cannot request apply confirmation without self_improve.apply permission.
    with pytest.raises(PermissionError):
        mgr.request_apply_confirmation(alice, good_prop.proposal_id, cm)

    pm.grant_permission(
        alice,
        "self_improve.apply",
        decision="allow",
        lifetime="persistent",
        explicit_user_approval=True,
    )
    token = mgr.request_apply_confirmation(alice, good_prop.proposal_id, cm)

    # Reject forged / wrong token.
    with pytest.raises(PermissionError):
        mgr.authorize_and_apply(
            alice,
            good_prop.proposal_id,
            confirmation_token="wrong-token",
            confirmation_manager=cm,
        )
    assert "Small numeric helper" not in (repo_root / "core" / "sample_helper.py").read_text(encoding="utf-8")

    # Apply with legitimate token.
    applied = mgr.authorize_and_apply(
        alice,
        good_prop.proposal_id,
        confirmation_token=token,
        confirmation_manager=cm,
    )
    assert applied.status == "applied"
    assert applied.rollback_snapshot_dir is not None
    assert (repo_root / "core" / "sample_helper.py").read_text(encoding="utf-8") == improved_src

    # Token reuse fails cleanly.
    with pytest.raises((PermissionError, ValueError, RuntimeError)):
        mgr.authorize_and_apply(
            alice,
            good_prop.proposal_id,
            confirmation_token=token,
            confirmation_manager=cm,
        )

    # Authorized rollback restores exact original file content.
    rb_token = mgr.request_rollback_confirmation(alice, good_prop.proposal_id, cm)
    rolled_back = mgr.rollback_proposal(
        alice,
        good_prop.proposal_id,
        confirmation_token=rb_token,
        confirmation_manager=cm,
    )
    assert rolled_back.status == "rolled_back"
    assert (repo_root / "core" / "sample_helper.py").read_text(encoding="utf-8") == (
        "def add_one(x: int) -> int:\n    return x + 1\n"
    )
