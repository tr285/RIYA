"""Security and functional regression tests for RIYA v0.5 Phase 1 multi-user isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import stat
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent.agent import RiyaAgent
from core.agent.assistant import RIYAAssistant
from core.agent.context import ConversationContext
from core.agent.executor import CommandExecutor
from core.agent.intent import IntentDetector
from core.agent.planner import AgentPlanner, Plan, PlanStep
from core.agent.tool_decision import ToolDecisionEngine
from core.agent.tool_executor import ToolExecutor
from core.agent.tool_registry import ToolRegistry
from core.database import MigrationError
from core.database.migrations import CURRENT_SCHEMA_VERSION
from core.identity import (
    AuthenticatedIdentity,
    AuthenticationError,
    ConversationHistoryStore,
    IdentityError,
    IdentityManager,
    InvalidIdentityError,
    MissingIdentityError,
    UserAlreadyExistsError,
    UserPermissionStore,
    UserPreferencesService,
    UserProfile,
)
from core.memory.database import MemoryDatabase
from core.memory.manager import MemoryManager
from core.memory.service import MemoryService
from core.tools.filesystem import files as fs_files
from core.tools.memory import forget_memory, list_memories, recall_memory, remember
from core.voice.config import VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession


def _make_auth_fixture(tmp_path: Path):
    db = MemoryDatabase(tmp_path / "riya.db")
    id_mgr = IdentityManager(db, users_root=tmp_path / "users")
    alice_prof = id_mgr.create_user(
        "alice",
        password="AliceSecret!1",
        display_name="Alice",
        auth_method="local_password",
    )
    bob_prof = id_mgr.create_user(
        "bob",
        password="BobSecret!2",
        display_name="Bob",
        auth_method="local_password",
    )
    alice = id_mgr.authenticate("alice", password="AliceSecret!1")
    bob = id_mgr.authenticate("bob", password="BobSecret!2")
    return db, id_mgr, alice_prof, bob_prof, alice, bob


def _build_assistant_with_db(
    id_mgr: IdentityManager,
    ai_runtime,
    *,
    default_identity: AuthenticatedIdentity | None = None,
    require_identity: bool = True,
) -> RIYAAssistant:
    assistant = RIYAAssistant.__new__(RIYAAssistant)
    assistant.identity_manager = id_mgr
    assistant.require_identity = require_identity
    assistant.permission_store = UserPermissionStore(id_mgr)
    assistant.preferences_service = UserPreferencesService(id_mgr)
    assistant.history_store = ConversationHistoryStore(id_mgr)
    assistant._active_identity = None
    assistant._user_contexts = {}
    assistant.agent = RiyaAgent(
        identity_manager=id_mgr,
        permission_store=assistant.permission_store,
        require_identity=require_identity,
    )
    assistant.semantic = None
    assistant.ai = ai_runtime
    assistant.model_path = None
    assistant.intent_detector = IntentDetector()
    assistant.command_executor = CommandExecutor(assistant.agent)
    assistant.memory = MemoryService(
        manager=MemoryManager(
            database=id_mgr.database,
            identity_manager=id_mgr,
            require_user=require_identity,
        )
    )
    assistant.planner = AgentPlanner(ai_runtime, assistant.agent.tools)
    assistant.context = ConversationContext(max_messages=12)
    assistant.pending_action = None
    assistant.pending_plan = None
    assistant._cancel_event = threading.Event()
    if default_identity is not None:
        assistant.set_identity(default_identity)
    return assistant


class _StubAI:
    def __init__(self, reply: str = "Okay, done."):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_user_creation_credential_hashing_no_plaintext_and_session_lifecycle(tmp_path):
    db = MemoryDatabase(tmp_path / "riya.db")
    id_mgr = IdentityManager(db, users_root=tmp_path / "users")

    profile = id_mgr.create_user(
        "Tukaram",
        password="VerySecretPassword99!",
        display_name="Tukaram",
        auth_method="local_password",
    )
    assert profile.username == "tukaram"
    assert profile.user_id.startswith("usr_")
    assert profile.is_authenticated is False

    # Duplicate username rejected.
    with pytest.raises(UserAlreadyExistsError):
        id_mgr.create_user("tukaram", password="AnotherPassword1!")

    # Wrong password and wrong auth method rejected.
    with pytest.raises(AuthenticationError):
        id_mgr.authenticate("tukaram", password="WrongPassword!")
    with pytest.raises(AuthenticationError):
        id_mgr.authenticate("tukaram", auth_method="local_unlock")

    identity = id_mgr.authenticate("tukaram", password="VerySecretPassword99!")
    assert "VerySecretPassword99!" not in repr(identity)
    assert identity.session_token not in repr(identity)
    assert id_mgr.validate_session(identity).user_id == profile.user_id

    # Inspect raw DB: zero plaintext secret or raw token stored.
    with db.connect() as conn:
        user_row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (profile.user_id,),
        ).fetchone()
        session_row = conn.execute(
            "SELECT * FROM user_sessions WHERE session_id = ?",
            (identity.session_id,),
        ).fetchone()

    assert user_row["hash_algorithm"] == "pbkdf2_sha256"
    assert "VerySecretPassword99!" not in dict(user_row).values()
    assert session_row["token_hash"] != identity.session_token
    assert len(session_row["token_hash"]) == 64

    # DB and user workspace files have restrictive permissions on POSIX.
    db_mode = stat.S_IMODE(db.database_path.stat().st_mode)
    ws_mode = stat.S_IMODE(Path(profile.workspace_dir).stat().st_mode)
    assert (db_mode & 0o077) == 0
    assert (ws_mode & 0o077) == 0

    # Logout revokes session cleanly.
    assert id_mgr.logout(identity) is True
    with pytest.raises(InvalidIdentityError):
        id_mgr.validate_session(identity)

    # Expired session rejected cleanly.
    short_mgr = IdentityManager(db, users_root=tmp_path / "users", session_ttl_seconds=1.0)
    short_id = short_mgr.authenticate("tukaram", password="VerySecretPassword99!")
    with db.connect() as conn:
        conn.execute(
            "UPDATE user_sessions SET expires_at = '2000-01-01T00:00:00+00:00' WHERE session_id = ?",
            (short_id.session_id,),
        )
        conn.commit()
    with pytest.raises(InvalidIdentityError):
        short_mgr.validate_session(short_id)


def test_user_id_immutability_and_profile_change_preserves_ownership(tmp_path):
    db, id_mgr, alice_prof, _, alice, _ = _make_auth_fixture(tmp_path)
    mem_a = MemoryManager(db, identity=alice, identity_manager=id_mgr, require_user=True)
    mem_a.remember("personal", "favorite_color", "teal")

    with pytest.raises(IdentityError):
        id_mgr.update_profile(alice, display_name="Alice2", user_id="usr_00000000000000000000000000000000")
    with pytest.raises(IdentityError):
        id_mgr.update_profile(alice, username="mallory")

    updated = id_mgr.update_profile(alice, display_name="Alice Renamed")
    assert updated.user_id == alice_prof.user_id
    assert updated.display_name == "Alice Renamed"
    assert mem_a.recall("personal", "favorite_color") == "teal"


def test_user_profile_and_forged_tokens_fail_closed(tmp_path):
    db, id_mgr, alice_prof, _, alice, _ = _make_auth_fixture(tmp_path)

    # UserProfile must NEVER work where an AuthenticatedIdentity is required.
    with pytest.raises(InvalidIdentityError):
        id_mgr.validate_session(alice_prof)

    with pytest.raises(InvalidIdentityError):
        MemoryManager(db, identity=alice_prof, identity_manager=id_mgr, require_user=True)

    registry = ToolRegistry()
    registry.register("recall_memory", "Recall", recall_memory, parameters={"category", "key"})
    executor = ToolExecutor(registry, identity_manager=id_mgr, require_identity=True)

    res_profile = executor.execute("recall_memory", user_context=alice_prof, category="a", key="b")
    assert res_profile["status"] == "authorization_failed"

    forged = AuthenticatedIdentity(
        user_id=alice.user_id,
        username=alice.username,
        display_name=alice.display_name,
        session_id=alice.session_id,
        session_token="forged_token_value_123456",
        workspace_dir=alice.workspace_dir,
        auth_method=alice.auth_method,
        expires_at=alice.expires_at,
    )
    res_forged = executor.execute("recall_memory", user_context=forged, category="a", key="b")
    assert res_forged["status"] == "authorization_failed"

    # Tampered user_id paired with valid session_id/token rejected.
    cross_tampered = AuthenticatedIdentity(
        user_id="usr_ffffffffffffffffffffffffffffffff",
        username=alice.username,
        display_name=alice.display_name,
        session_id=alice.session_id,
        session_token=alice.session_token,
        workspace_dir=alice.workspace_dir,
        auth_method=alice.auth_method,
        expires_at=alice.expires_at,
    )
    with pytest.raises(InvalidIdentityError):
        id_mgr.validate_session(cross_tampered)


def test_memory_isolation_same_keys_and_fail_closed_without_user(tmp_path):
    db, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)

    # Without identity on a multi-user DB, MemoryManager fails closed.
    anon_mem = MemoryManager(db)
    with pytest.raises(MissingIdentityError):
        anon_mem.remember("personal", "token", "nope")
    with pytest.raises(MissingIdentityError):
        anon_mem.list_memories()

    with pytest.raises(MissingIdentityError):
        MemoryManager(db, user_id=None)
    with pytest.raises(MissingIdentityError):
        MemoryManager(db, user_id="")

    mem_a = MemoryManager(db, identity=alice, identity_manager=id_mgr, require_user=True)
    mem_b = MemoryManager(db, identity=bob, identity_manager=id_mgr, require_user=True)

    mem_a.remember("personal", "api_note", "alice-secret-value")
    mem_b.remember("personal", "api_note", "bob-secret-value")
    mem_b.remember("work", "project", "apollo")

    assert mem_a.recall("personal", "api_note") == "alice-secret-value"
    assert mem_b.recall("personal", "api_note") == "bob-secret-value"
    assert mem_a.recall("work", "project") is None
    assert [m["key"] for m in mem_a.list_memories()] == ["api_note"]
    assert not any("apollo" in str(m) for m in mem_a.search("apollo"))

    # Alice cannot update or delete Bob's memory.
    assert mem_a.update_memory("work", "project", "hijacked") is False
    assert mem_a.forget("work", "project") is False
    assert mem_b.recall("work", "project") == "apollo"

    # Clear category only affects Alice.
    assert mem_a.clear_category("personal") == 1
    assert mem_b.recall("personal", "api_note") == "bob-secret-value"


def test_filesystem_isolation_blocks_cross_user_and_unauthenticated_access(tmp_path):
    _, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)

    agent = RiyaAgent(identity_manager=id_mgr, require_identity=True)

    # Unauthenticated file execution fails closed.
    unauth = agent.execute("read_file", path="secret.txt")
    assert unauth["status"] == "authorization_failed"

    # Alice writes inside her isolated workspace.
    pending = agent.execute(
        "write_file",
        user_context=alice,
        path="secret.txt",
        content="Alice private plan",
    )
    assert pending["status"] == "confirmation_required"
    written = agent.execute(
        "write_file",
        confirmation_token=pending["confirmation_token"],
        user_context=alice,
        path="secret.txt",
        content="Alice private plan",
    )
    assert written["status"] == "success"

    alice_file = Path(alice.workspace_dir) / "secret.txt"
    assert alice_file.exists()
    assert alice_file.read_text(encoding="utf-8") == "Alice private plan"

    # Alice can read her own file.
    read_a = agent.execute("read_file", user_context=alice, path="secret.txt")
    assert read_a["status"] == "success"
    assert read_a["result"] == "Alice private plan"

    # Bob cannot read Alice's relative filename or Alice's absolute path.
    read_b_rel = agent.execute("read_file", user_context=bob, path="secret.txt")
    assert read_b_rel["status"] == "success"
    assert "File not found" in read_b_rel["result"]

    read_b_abs = agent.execute("read_file", user_context=bob, path=str(alice_file))
    assert read_b_abs["status"] == "failed"  # FilesystemAccessError raised inside tool
    # Verify Bob's workspace never received Alice's file, and Alice's file is untouched.
    assert not (Path(bob.workspace_dir) / "secret.txt").exists()

    # Cross-user write rejected and path cannot escape via '..'.
    escape_b = agent.execute(
        "write_file",
        user_context=bob,
        path=f"../../{alice.user_id}/workspace/pwned.txt",
        content="pwn",
    )
    assert escape_b["status"] == "confirmation_required"
    escape_exec = agent.execute(
        "write_file",
        confirmation_token=escape_b["confirmation_token"],
        user_context=bob,
        path=f"../../{alice.user_id}/workspace/pwned.txt",
        content="pwn",
    )
    assert escape_exec["status"] == "failed"
    assert not (Path(alice.workspace_dir) / "pwned.txt").exists()


def test_confirmation_token_bound_to_user_and_session_and_invalidated_on_switch(tmp_path):
    _, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)
    alice_second_session = id_mgr.authenticate("alice", password="AliceSecret!1")

    agent = RiyaAgent(identity_manager=id_mgr, require_identity=True)

    issue_a = agent.execute(
        "write_file",
        user_context=alice,
        path="plan.txt",
        content="confirmed",
    )
    assert issue_a["status"] == "confirmation_required"
    token_a = issue_a["confirmation_token"]

    # Bob cannot redeem Alice's confirmation token.
    bob_replay = agent.execute(
        "write_file",
        confirmation_token=token_a,
        user_context=bob,
        path="plan.txt",
        content="confirmed",
    )
    assert bob_replay["status"] == "authorization_failed"
    assert not (Path(bob.workspace_dir) / "plan.txt").exists()

    # Another session of Alice also cannot redeem Session 1's token.
    issue_a2 = agent.execute(
        "write_file",
        user_context=alice,
        path="plan2.txt",
        content="confirmed",
    )
    token_a2 = issue_a2["confirmation_token"]
    other_session_replay = agent.execute(
        "write_file",
        confirmation_token=token_a2,
        user_context=alice_second_session,
        path="plan2.txt",
        content="confirmed",
    )
    assert other_session_replay["status"] == "authorization_failed"

    # RIYAAssistant user switching revokes pending confirmation and blocks cross-user approval.
    assistant = _build_assistant_with_db(id_mgr, _StubAI(), default_identity=alice)
    prompt_res = assistant.process("Remember that my pet is Milo")
    assert "Proceed?" in prompt_res
    assert assistant.pending_action is not None

    # Direct process under Bob cannot approve Alice's pending action.
    cross_res = assistant.process("yes", user_context=bob)
    assert "different user session" in cross_res.lower()

    # Switching identity clears pending confirmations completely.
    assistant.set_identity(bob)
    assert assistant.pending_action is None
    assert MemoryManager(id_mgr.database, identity=alice, identity_manager=id_mgr, require_user=True).list_memories() == []


def test_llm_supplied_user_id_or_identity_parameters_always_rejected(tmp_path):
    _, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)

    registry = ToolRegistry()
    registry.register("remember", "Remember", remember, parameters={"category", "key", "value"})

    for reserved in ("user_id", "session_id", "owner_id", "auth_token", "identity", "confirmation_token"):
        with pytest.raises(ValueError):
            registry.register("bad_tool", "Bad", lambda **kw: "ok", parameters={reserved})
        with pytest.raises(ValueError):
            registry.validate_parameters("remember", {"category": "c", "key": "k", "value": "v", reserved: bob.user_id})

    # Planner rejects LLM step supplying reserved user_id.
    spoof_ai = _StubAI(
        '{"steps":[{"action":"remember","parameters":{"category":"c","key":"k","value":"v","user_id":"'
        + bob.user_id
        + '"},"description":"spoof"}]}'
    )
    planner = AgentPlanner(spoof_ai, registry)
    with pytest.raises(ValueError):
        planner.create_plan("Remember my note")

    # ToolDecisionEngine rejects LLM JSON supplying owner_id / user_id.
    decision_ai = _StubAI(
        '{"tool":"remember","parameters":{"category":"c","key":"k","value":"v","user_id":"'
        + bob.user_id
        + '"},"reason":"spoof"}'
    )
    decision_engine = ToolDecisionEngine(decision_ai, registry)
    with pytest.raises(ValueError):
        decision_engine.decide("Remember my note")

    # ToolExecutor blocks reserved params directly in execute and execute_plan.
    executor = ToolExecutor(registry, identity_manager=id_mgr, require_identity=True)
    spoof_exec = executor.execute(
        "remember",
        user_context=alice,
        category="c",
        key="k",
        value="v",
        user_id=bob.user_id,
    )
    assert spoof_exec["status"] == "authorization_failed"

    forged_plan = Plan(
        goal="spoof",
        steps=[PlanStep(action="remember", parameters={"category": "c", "key": "k", "value": "v", "user_id": bob.user_id}, description="spoof")],
    )
    plan_res = executor.execute_plan(forged_plan, user_context=alice)
    assert plan_res[0]["result"]["status"] == "authorization_failed"


def test_per_user_preferences_voice_settings_and_permissions_isolated(tmp_path):
    _, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)

    prefs = UserPreferencesService(id_mgr)
    prefs.set_preference(alice, "preferences", "theme", "solarized")
    prefs.set_preference(bob, "preferences", "theme", "midnight")
    prefs.set_preference(alice, "automation", "morning_briefing", {"enabled": True})

    assert prefs.get_preference(alice, "preferences", "theme") == "solarized"
    assert prefs.get_preference(bob, "preferences", "theme") == "midnight"
    assert prefs.get_preference(bob, "automation", "morning_briefing") is None

    # Per-user VoiceConfig isolation & file permissions.
    cfg_a = VoiceConfig(voice="Samantha", speech_rate_wpm=185)
    cfg_b = VoiceConfig(voice="Daniel", speech_rate_wpm=165)
    path_a = cfg_a.save_for_user(id_mgr.users_root, alice.user_id)
    path_b = cfg_b.save_for_user(id_mgr.users_root, bob.user_id)
    assert path_a != path_b
    assert (stat.S_IMODE(path_a.stat().st_mode) & 0o077) == 0

    loaded_a = VoiceConfig.load_for_user(id_mgr.users_root, alice.user_id)
    loaded_b = VoiceConfig.load_for_user(id_mgr.users_root, bob.user_id)
    assert loaded_a.voice == "Samantha"
    assert loaded_b.voice == "Daniel"
    assert str(Path(loaded_a.recordings_dir)).startswith(str(id_mgr.user_voice_dir(alice.user_id)))
    assert str(Path(loaded_b.recordings_dir)).startswith(str(id_mgr.user_voice_dir(bob.user_id)))

    # Per-user tool permission overrides.
    perms = UserPermissionStore(id_mgr)
    perms.set_tool_policy(alice, "read_file", "deny")
    perms.set_tool_policy(bob, "read_file", "allow")

    agent = RiyaAgent(
        identity_manager=id_mgr,
        permission_store=perms,
        require_identity=True,
    )
    (Path(alice.workspace_dir) / "a.txt").write_text("hi", encoding="utf-8")
    (Path(bob.workspace_dir) / "b.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(PermissionError):
        agent.execute("read_file", user_context=alice, path="a.txt")

    res_bob = agent.execute("read_file", user_context=bob, path="b.txt")
    assert res_bob["status"] == "success"
    assert res_bob["result"] == "hello"


def test_concurrent_sessions_isolated_without_bleeding(tmp_path):
    db, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)
    agent = RiyaAgent(identity_manager=id_mgr, require_identity=True)
    history = ConversationHistoryStore(id_mgr)

    def _run_user(identity: AuthenticatedIdentity, tag: str, count: int):
        mem = MemoryManager(db, identity=identity, identity_manager=id_mgr, require_user=True)
        for idx in range(count):
            mem.remember("concurrent", f"k{idx}", f"{tag}-{idx}")
            history.append_message(identity, "user", f"{tag}-msg-{idx}")
            pending = agent.execute(
                "write_file",
                user_context=identity,
                path=f"f{idx}.txt",
                content=f"{tag}-file-{idx}",
            )
            res = agent.execute(
                "write_file",
                confirmation_token=pending["confirmation_token"],
                user_context=identity,
                path=f"f{idx}.txt",
                content=f"{tag}-file-{idx}",
            )
            assert res["status"] == "success"

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_a = pool.submit(_run_user, alice, "ALICE", 6)
        f_b = pool.submit(_run_user, bob, "BOB", 6)
        f_a.result()
        f_b.result()

    mem_a = MemoryManager(db, identity=alice, identity_manager=id_mgr, require_user=True)
    mem_b = MemoryManager(db, identity=bob, identity_manager=id_mgr, require_user=True)
    assert all(m["value"].startswith("ALICE-") for m in mem_a.list_memories())
    assert all(m["value"].startswith("BOB-") for m in mem_b.list_memories())
    assert all(m["content"].startswith("ALICE-") for m in history.get_history(alice))
    assert all(m["content"].startswith("BOB-") for m in history.get_history(bob))
    assert (Path(alice.workspace_dir) / "f5.txt").read_text(encoding="utf-8") == "ALICE-file-5"
    assert (Path(bob.workspace_dir) / "f5.txt").read_text(encoding="utf-8") == "BOB-file-5"


def test_v04_migration_preserves_legacy_data_isolated_and_supports_rollback(tmp_path):
    legacy_db_path = tmp_path / "legacy_v04.db"
    raw = sqlite3.connect(legacy_db_path)
    raw.execute(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category, key)
        )
        """
    )
    raw.execute(
        "INSERT INTO memories (category, key, value) VALUES ('personal', 'owner', 'LegacyValue')"
    )
    raw.commit()
    raw.close()

    # Apply v0.5 migration via MemoryDatabase.
    db = MemoryDatabase(legacy_db_path)
    assert db.schema_version() == CURRENT_SCHEMA_VERSION
    assert db.count_legacy_unclaimed_memories() == 1

    id_mgr = IdentityManager(db, users_root=tmp_path / "users")
    id_mgr.create_user("alice", password="AlicePassword1")
    id_mgr.create_user("bob", password="BobPassword2")
    alice = id_mgr.authenticate("alice", password="AlicePassword1")
    bob = id_mgr.authenticate("bob", password="BobPassword2")

    # Unclaimed v0.4 data must NOT silently leak to either user.
    mem_a = MemoryManager(db, identity=alice, identity_manager=id_mgr, require_user=True)
    mem_b = MemoryManager(db, identity=bob, identity_manager=id_mgr, require_user=True)
    assert mem_a.list_memories() == []
    assert mem_b.list_memories() == []

    # Rollback to v1 succeeds cleanly when there are no colliding cross-user keys.
    rolled = db.rollback_to(1)
    assert rolled == list(range(CURRENT_SCHEMA_VERSION, 1, -1))
    assert db.schema_version() == 1
    with db.connect() as conn:
        legacy_row = conn.execute("SELECT value FROM memories WHERE key = 'owner'").fetchone()
    assert legacy_row["value"] == "LegacyValue"

    # Re-initialize forward migration to v2 cleanly and verify explicit claiming.
    db2 = MemoryDatabase(legacy_db_path)
    assert db2.schema_version() == CURRENT_SCHEMA_VERSION
    assert db2.count_legacy_unclaimed_memories() == 1

    id_mgr2 = IdentityManager(db2, users_root=tmp_path / "users")
    id_mgr2.create_user("alice", password="AlicePassword1")
    id_mgr2.create_user("bob", password="BobPassword2")
    alice2 = id_mgr2.authenticate("alice", password="AlicePassword1")
    bob2 = id_mgr2.authenticate("bob", password="BobPassword2")

    mem_a2 = MemoryManager(db2, identity=alice2, identity_manager=id_mgr2, require_user=True)
    mem_b2 = MemoryManager(db2, identity=bob2, identity_manager=id_mgr2, require_user=True)
    assert mem_a2.claim_legacy_memories(alice2) == 1
    assert db2.count_legacy_unclaimed_memories() == 0
    assert mem_a2.recall("personal", "owner") == "LegacyValue"
    assert mem_b2.recall("personal", "owner") is None


def test_voice_session_and_orchestrator_switch_identity_clears_transcript(tmp_path):
    _, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)
    assistant = _build_assistant_with_db(id_mgr, _StubAI("Hello!"), default_identity=alice)

    cfg_a = VoiceConfig(voice="Samantha").updated(
        recordings_dir=str(VoiceConfig.recordings_dir_for_user(id_mgr.users_root, alice.user_id))
    )
    cfg_a.save_for_user(id_mgr.users_root, alice.user_id)
    session = VoiceSession(cfg_a, user_id=alice.user_id, auth_session_id=alice.session_id)
    orch = VoiceOrchestrator(assistant, config=cfg_a, session=session)
    orch.session.append_transcript(role="user", text="Alice private voice note", modality="voice")
    assert len(orch.session.transcript_entries()) == 1

    cfg_b = VoiceConfig(voice="Daniel").updated(
        recordings_dir=str(VoiceConfig.recordings_dir_for_user(id_mgr.users_root, bob.user_id))
    )
    cfg_b.save_for_user(id_mgr.users_root, bob.user_id)
    assistant.set_identity(bob)
    orch.switch_identity(bob, config=cfg_b)

    assert orch.session.user_id == bob.user_id
    assert orch.session.transcript_entries() == []
    assert orch.config.voice == "Daniel"
    assert orch.mode == "text"
    orch.close()


def test_two_user_e2e_offline_assistant_flow(tmp_path):
    _, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)
    ai = _StubAI("Chat response")
    assistant = _build_assistant_with_db(id_mgr, ai, default_identity=alice)

    # User A saves memory through assistant confirmation flow + retrieves it.
    res1 = assistant.process("Remember that my favorite city is Kyoto")
    assert "Proceed?" in res1
    res2 = assistant.process("yes")
    assert "Remembered" in res2
    assert assistant.process("What do you remember about favorite city?") == "Kyoto"

    # Switch to User B: User B has isolated ConversationContext, ConversationHistoryStore, and memories.
    assistant.set_identity(bob)
    recall_b = assistant.process("What do you remember about favorite city?")
    assert "Kyoto" not in recall_b
    assert "don't have" in recall_b.lower() or "couldn't" in recall_b.lower()

    hist_a = assistant.history_store.get_history(alice)
    hist_b = assistant.history_store.get_history(bob)
    assert any("Kyoto" in item["content"] for item in hist_a)
    assert not any("Kyoto" in item["content"] for item in hist_b)

    # Logout makes assistant fail closed on next request.
    assistant.logout()
    unauth_reply = assistant.process("What is my favorite city?")
    assert "authentication required" in unauth_reply.lower()


def test_rollback_blocks_when_multiuser_memory_keys_collide(tmp_path):
    db, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)
    mem_a = MemoryManager(db, identity=alice, identity_manager=id_mgr, require_user=True)
    mem_b = MemoryManager(db, identity=bob, identity_manager=id_mgr, require_user=True)

    mem_a.remember("personal", "nickname", "Ace")
    mem_b.remember("personal", "nickname", "Beacon")

    with pytest.raises(MigrationError):
        db.rollback_to(1)

    assert db.schema_version() == CURRENT_SCHEMA_VERSION
    assert mem_a.recall("personal", "nickname") == "Ace"
    assert mem_b.recall("personal", "nickname") == "Beacon"


def test_cross_user_plan_confirmation_and_context_isolation(tmp_path):
    _, id_mgr, _, _, alice, bob = _make_auth_fixture(tmp_path)
    # Planner produces a multi-step file plan on first request, then chat response on follow-up.
    plan_json = (
        '{"steps":['
        '{"action":"write_file","parameters":{"path":"diary.txt","content":"Alice diary"},"description":"Write diary"}'
        ']}'
    )
    ai = _StubAI(plan_json)
    assistant = _build_assistant_with_db(id_mgr, ai, default_identity=alice)

    plan_prompt = assistant.process("Create a file diary.txt with Alice diary")
    assert "Proceed?" in plan_prompt
    assert assistant.pending_plan is not None

    # Bob cannot approve Alice's pending multi-step plan.
    cross_res = assistant.process("yes", user_context=bob)
    assert "different user session" in cross_res.lower()
    assert not (Path(bob.workspace_dir) / "diary.txt").exists()

    # Alice confirms her own plan cleanly.
    done_res = assistant.process("yes")
    assert "diary.txt" in done_res
    assert (Path(alice.workspace_dir) / "diary.txt").read_text(encoding="utf-8") == "Alice diary"

    # Switching to Bob switches ConversationContext and never leaks Alice's messages into chat prompt.
    class _ChatOnlyAI:
        def __init__(self):
            self.prompts: list[str] = []

        def generate(self, prompt: str, **_kwargs) -> str:
            self.prompts.append(f"{prompt}\n{_kwargs.get('conversation_context', '')}")
            if "planning engine" in prompt:
                return '{"steps": []}'
            return "Hello Bob"

    ai2 = _ChatOnlyAI()
    assistant.ai = ai2
    assistant.planner.ai = ai2
    assistant.set_identity(bob)
    bob_reply = assistant.process("Hello")
    assert "Hello Bob" in bob_reply
    assert len(ai2.prompts) >= 2
    assert all("Alice diary" not in p for p in ai2.prompts)


def test_authenticate_token_local_unlock_and_extensible_verifier(tmp_path):
    db = MemoryDatabase(tmp_path / "riya.db")
    id_mgr = IdentityManager(db, users_root=tmp_path / "users")

    # Custom offline verifier plugin.
    id_mgr.register_auth_verifier(
        "hardware_key_stub",
        lambda _row, secret: secret == "hardware-presence-ok",
    )

    id_mgr.create_user("localonly", auth_method="local_unlock", display_name="Local Only")
    local_id = id_mgr.authenticate("localonly", auth_method="local_unlock")
    resolved = id_mgr.authenticate_token(local_id.session_token)
    assert resolved.user_id == local_id.user_id

    id_mgr.create_user("hwuser", password="hardware-presence-ok", auth_method="hardware_key_stub")
    with pytest.raises(AuthenticationError):
        id_mgr.authenticate("hwuser", password="wrong")
    hw_id = id_mgr.authenticate("hwuser", password="hardware-presence-ok")
    assert hw_id.auth_method == "hardware_key_stub"

