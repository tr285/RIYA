"""Comprehensive automated tests for RIYA v0.5 Phase 3 Onboarding, Setup & Recovery."""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from core.agent.assistant import RIYAAssistant
from core.database.migrations import CURRENT_SCHEMA_VERSION
from core.identity.manager import IdentityManager
from core.main import main as cli_main
from core.memory.database import MemoryDatabase
from core.memory.manager import MemoryManager
from core.onboarding import (
    InstallState,
    LocalAISetupService,
    OnboardingCoordinator,
    ReadinessDiagnostics,
    VoiceSetupService,
    CheckSeverity,
    detect_install_state,
    validate_gguf_file,
)
from core.onboarding.ui import OnboardingViewModel, WIZARD_STEP_DESCRIPTORS
from core.security.permissions import PermissionDecision, PermissionManager
from core.voice.audio_input import AudioDeviceInfo
from core.voice.config import VoiceConfig
from core.voice.errors import MicrophonePermissionError
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession
from core.voice.ui import VoiceUIController


def _make_fake_gguf(path: Path, size: int = 256) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\x00" * max(64, size - 4))
    return path


def _make_coordinator(tmp_path: Path, *, strict: bool = False) -> OnboardingCoordinator:
    db_path = tmp_path / "data" / "riya.db"
    users_root = tmp_path / "data" / "users"
    bridge = tmp_path / "missing_bridge.dylib"
    ai_svc = LocalAISetupService(project_root=tmp_path, bridge_path=bridge)
    voice_svc = VoiceSetupService(
        input_lister=lambda: [],
        output_lister=lambda: [],
        tts_voice_lister=lambda: [],
    )
    diag = ReadinessDiagnostics(ai_service=ai_svc, voice_service=voice_svc, project_root=tmp_path)
    return OnboardingCoordinator(
        database_path=db_path,
        users_root=users_root,
        ai_service=ai_svc,
        voice_service=voice_svc,
        diagnostics=diag,
        strict_permissions=strict,
    )


class _DummyMic:
    def __init__(self, *, level: float = 0.042, raise_exc: Exception | None = None):
        self._level = level
        self._raise_exc = raise_exc

    def probe_permission_and_read_level(self, duration: float = 0.25) -> float:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._level

    def stop(self) -> None:
        pass


class _DummyOutput:
    def __init__(self, *, raise_exc: Exception | None = None):
        self._raise_exc = raise_exc

    def play_text(self, _text: str) -> bool:
        if self._raise_exc is not None:
            raise self._raise_exc
        return True

    def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 1. Install detection & crash recovery
# ---------------------------------------------------------------------------
def test_detect_fresh_install(tmp_path: Path):
    db_path = tmp_path / "data" / "riya.db"
    det = detect_install_state(db_path)
    assert det.install_state == InstallState.FRESH_INSTALL
    assert det.needs_onboarding is True
    assert det.resume_step == "welcome"
    assert det.registered_users_count == 0


def test_detect_existing_v04_install_preserves_memories(tmp_path: Path):
    db_path = tmp_path / "data" / "riya.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL
        );
        INSERT INTO memories (category, key, value) VALUES ('user', 'name', 'Tukaram');
        INSERT INTO memories (category, key, value) VALUES ('preference', 'city', 'Pune');
        """
    )
    conn.commit()
    conn.close()

    det = detect_install_state(db_path)
    assert det.install_state == InstallState.EXISTING_V04
    assert det.needs_onboarding is True
    assert det.legacy_memories_count == 2

    coord = _make_coordinator(tmp_path)
    db, _, _, _ = coord.initialize_services()
    assert db.count_legacy_unclaimed_memories() == 2


def test_detect_existing_v05_user_without_onboarding_metadata(tmp_path: Path):
    db_path = tmp_path / "data" / "riya.db"
    db = MemoryDatabase(db_path)
    id_mgr = IdentityManager(database=db, users_root=tmp_path / "users")
    id_mgr.create_user("alice", password="Password123!")

    det = detect_install_state(db_path)
    assert det.install_state == InstallState.EXISTING_V05
    assert det.needs_onboarding is False
    assert det.registered_users_count == 1


def test_interrupted_setup_resumes_exact_step_and_recovers_corrupt_checkpoint(tmp_path: Path):
    coord = _make_coordinator(tmp_path)
    coord.start_or_resume_setup(step="ai_engine")
    det = coord.detect_initial_state()
    assert det.install_state == InstallState.SETUP_IN_PROGRESS
    assert det.can_resume is True
    assert det.resume_step == "ai_engine"

    # Simulate crash writing corrupted JSON into checkpoint file
    coord.checkpoint_path.write_text("{corrupt json", encoding="utf-8")
    det_corrupt = coord.detect_initial_state()
    assert det_corrupt.install_state == InstallState.NEEDS_RECOVERY
    assert det_corrupt.checkpoint_corrupted is True

    vm = OnboardingViewModel(coord)
    assert vm.initial_route() == "recovery"
    notes = vm.recover_installation()
    assert any("Corrupted checkpoint backed up" in n for n in notes)
    backups = list(coord.checkpoint_path.parent.glob("onboarding_checkpoint.json.corrupt.*"))
    assert len(backups) == 1

    # DB state still cleanly detects interrupted setup
    det_repaired = coord.detect_initial_state()
    assert det_repaired.install_state == InstallState.SETUP_IN_PROGRESS


def test_corrupted_database_triggers_needs_recovery(tmp_path: Path):
    db_path = tmp_path / "data" / "riya.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite db at all")

    det = detect_install_state(db_path)
    assert det.install_state == InstallState.NEEDS_RECOVERY
    assert det.database_accessible is False
    assert det.database_error is not None


# ---------------------------------------------------------------------------
# 2. Local accounts, zero plaintext secrets & v0.4 opt-in memory claiming
# ---------------------------------------------------------------------------
def test_account_creation_auth_no_plaintext_secret_and_opt_in_legacy_claim(tmp_path: Path):
    # Pre-seed legacy v0.4 data
    db_path = tmp_path / "data" / "riya.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL
        );
        INSERT INTO memories (category, key, value) VALUES ('fact', 'hobby', 'chess');
        """
    )
    conn.commit()
    conn.close()

    coord = _make_coordinator(tmp_path)
    vm = OnboardingViewModel(coord)
    assert vm.initial_route() == "onboarding"

    with pytest.raises(ValueError, match="confirmation did not match"):
        vm.create_account(
            username="tukaram",
            auth_method="local_password",
            secret="SecretPass99!",
            confirm_secret="DifferentPass!",
        )

    identity, claimed = vm.create_account(
        username="tukaram",
        display_name="Tukaram",
        auth_method="local_password",
        secret="SecretPass99!",
        confirm_secret="SecretPass99!",
        claim_legacy_memories=True,
    )
    assert claimed == 1
    assert identity.username == "tukaram"

    # Second user cannot access Tukaram's claimed memory
    vm.logout()
    assert coord.active_identity is None
    bob_id, bob_claimed = vm.create_account(
        username="bob",
        auth_method="local_pin",
        secret="1234",
        confirm_secret="1234",
        claim_legacy_memories=True,
    )
    assert bob_claimed == 0

    db, id_mgr, _, _ = coord.initialize_services()
    mm = MemoryManager(db, identity_manager=id_mgr)
    assert mm.for_identity(bob_id).list_memories() == []

    # Plaintext password/PIN must NEVER appear in sqlite tables or checkpoint JSON
    raw_checkpoint = coord.checkpoint_path.read_text(encoding="utf-8") if coord.checkpoint_path.exists() else ""
    assert "SecretPass99!" not in raw_checkpoint
    assert "1234" not in raw_checkpoint

    # Wrong password rejected
    with pytest.raises(Exception):
        vm.sign_in("tukaram", secret="WrongPass")
    reauth, _ = vm.sign_in("tukaram", secret="SecretPass99!")
    assert len(mm.for_identity(reauth).list_memories()) == 1


# ---------------------------------------------------------------------------
# 3. AI discovery, GGUF validation, inference readiness & Limited Mode
# ---------------------------------------------------------------------------
def test_ai_validation_inference_verification_and_limited_mode(tmp_path: Path):
    tiny_gguf = tmp_path / "models" / "tiny.gguf"
    tiny_gguf.parent.mkdir(parents=True, exist_ok=True)
    tiny_gguf.write_bytes(b"SHORT")
    ok_tiny, err_tiny, _ = validate_gguf_file(tiny_gguf)
    assert ok_tiny is False
    assert "truncated" in (err_tiny or "")

    bad_gguf = tmp_path / "models" / "broken.gguf"
    bad_gguf.write_bytes(b"NOT_GGUF_DATA" * 10)
    ok, err, _ = validate_gguf_file(bad_gguf)
    assert ok is False
    assert "GGUF" in (err or "")

    # Missing model & missing dylib -> degraded mode assistant stays honest and functional
    coord = _make_coordinator(tmp_path)
    ident = coord.create_local_account(username="alice", auth_method="local_unlock")
    ver = coord.configure_and_verify_ai(model_path=bad_gguf)
    assert ver.success is False
    assert coord.ai_inference_verified is False

    # Even with valid-header dummy GGUF, broken dylib prevents verification
    good_header = _make_fake_gguf(tmp_path / "models" / "test.gguf")
    ver2 = coord.configure_and_verify_ai(model_path=good_header)
    assert ver2.success is False
    assert "not found" in (ver2.error_message or "").lower()

    # Continue without AI -> degraded mode works cleanly
    coord.continue_without_ai()
    assistant, orchestrator = coord.build_runtime_session(ident)
    try:
        st = assistant.ai_status()
        assert st["loaded"] is False
        assert st["degraded_mode"] is True

        # Deterministic commands work offline even without GGUF model
        t_resp = assistant.process("what time is it")
        assert ":" in t_resp or "AM" in t_resp or "PM" in t_resp

        # Conversational LLM prompt fails honestly in Limited Mode instead of crashing/lying
        chat_resp = assistant.process("Explain quantum entanglement briefly")
        assert "Limited Mode" in chat_resp or "no local AI model" in chat_resp.lower()
    finally:
        orchestrator.close()
        assistant.close()


def test_ai_verify_model_with_inference_probe(tmp_path: Path):
    model = _make_fake_gguf(tmp_path / "models" / "fake.gguf")
    dylib = tmp_path / "libriya_llama.dylib"
    dylib.write_bytes(b"")  # will patch ctypes.CDLL + LocalLlamaRuntime

    svc = LocalAISetupService(project_root=tmp_path, bridge_path=dylib)

    class _FakeRuntime:
        def __init__(self, **_kwargs):
            self.selected_backend = "metal"
            self.is_loaded = False

        def load_model(self, _path):
            self.is_loaded = True

        def generate(self, _prompt, timeout_seconds=45.0):
            return "READY"

        def unload_model(self):
            self.is_loaded = False

    from types import SimpleNamespace

    with patch(
        "core.onboarding.ai_setup.ctypes.CDLL",
        return_value=SimpleNamespace(
            riya_load_model_with_backend=lambda *a: 0,
            riya_get_backend=lambda *a: b"metal",
            riya_generate=lambda *a: b"READY",
            riya_free_model=lambda *a: None,
        ),
    ), patch("core.onboarding.ai_setup.LocalLlamaRuntime", side_effect=_FakeRuntime):
        res = svc.verify_model_with_inference(model, backend="auto")
        assert res.success is True
        assert res.backend_used == "metal"
        assert res.output_preview == "READY"

        status = svc.inspect_environment(selected_model_path=model, inference_verified=True)
        assert status.inference_verified is True
        assert status.native_library_loadable is True


# ---------------------------------------------------------------------------
# 4. Permission-gated Voice Setup & Corrupted Voice Config Recovery
# ---------------------------------------------------------------------------
def test_voice_tests_blocked_without_permission_and_corrupt_config_recovered(tmp_path: Path):
    db = MemoryDatabase(tmp_path / "data" / "riya.db")
    id_mgr = IdentityManager(database=db, users_root=tmp_path / "data" / "users")
    pm = PermissionManager(id_mgr)
    alice = id_mgr.create_user("alice", auth_method="local_unlock")
    ident = id_mgr.authenticate("alice", auth_method="local_unlock")

    # Seed corrupt voice_settings.json
    vpath = VoiceConfig.path_for_user(id_mgr.users_root, alice.user_id)
    vpath.parent.mkdir(parents=True, exist_ok=True)
    vpath.write_text("{corrupt voice json", encoding="utf-8")

    mic_probed = {"called": False}
    spk_probed = {"called": False}

    def _mic_factory(_cfg):
        mic_probed["called"] = True
        return _DummyMic(level=0.05)

    def _out_factory(_cfg):
        spk_probed["called"] = True
        return _DummyOutput()

    voice_svc = VoiceSetupService(
        input_lister=lambda: [AudioDeviceInfo(0, "Built-in Mic", 1, 16000.0, True)],
        output_lister=lambda: [AudioDeviceInfo(1, "Built-in Speakers", 2, 44100.0, True)],
        mic_factory=_mic_factory,
        player_factory=_out_factory,
        tts_voice_lister=lambda: [{"name": "Samantha", "locale": "en_US"}],
    )
    ai_svc = LocalAISetupService(project_root=tmp_path, bridge_path=tmp_path / "missing.dylib")
    coord = OnboardingCoordinator(
        database_path=tmp_path / "data" / "riya.db",
        users_root=tmp_path / "data" / "users",
        ai_service=ai_svc,
        voice_service=voice_svc,
    )
    coord.active_identity = ident
    coord.initialize_services()

    # Load corrupt voice config safely -> warns + returns defaults
    loaded_cfg, err_msg = coord.load_current_voice_config()
    assert err_msg is not None
    assert loaded_cfg.language == "en"
    backup = coord.recover_corrupted_voice_config(alice.user_id)
    assert backup is not None and backup.exists()

    # Check honest language labeling
    vst = coord.inspect_voice(config=loaded_cfg)
    by_code = dict(vst.language_options)
    assert "verified" in by_code["en"]
    assert "untested" in by_code["hi"].lower()
    assert "untested" in by_code["mr"].lower()
    assert "untested" in by_code["auto"].lower()

    # Hardware tests MUST be blocked before user permission
    mic_res = coord.test_microphone(config=loaded_cfg)
    assert mic_res.passed is False
    assert mic_res.blocked_by_permission is True
    assert mic_probed["called"] is False

    spk_res = coord.test_speaker(config=loaded_cfg)
    assert spk_res.passed is False
    assert spk_res.blocked_by_permission is True
    assert spk_probed["called"] is False

    # Selecting custom audio devices is blocked before voice.device permission
    with pytest.raises(PermissionError):
        coord.save_voice_setup(VoiceConfig(input_device="Built-in Mic"))

    # Under strict permissions or explicit DENY, saving any voice config is blocked
    strict_coord = _make_coordinator(tmp_path, strict=True)
    strict_ident = strict_coord.sign_in("alice")
    with pytest.raises(PermissionError):
        strict_coord.save_voice_setup(loaded_cfg)

    # Re-authenticate primary coord session after strict_coord signed in
    ident = coord.sign_in("alice")

    # Grant explicit one_time permissions and verify execution + consumption
    _, _, live_pm, _ = coord.initialize_services()
    live_pm.grant_permission(
        ident,
        "voice.microphone",
        decision="allow",
        lifetime="one_time",
        actor="user_test",
        explicit_user_approval=True,
    )
    mic_res2 = coord.test_microphone(config=loaded_cfg)
    assert mic_res2.passed is True
    assert mic_res2.measured_level == pytest.approx(0.05)
    assert mic_probed["called"] is True

    # Second call fails because one_time was consumed!
    mic_res3 = coord.test_microphone(config=loaded_cfg)
    assert mic_res3.blocked_by_permission is True

    # Grant voice.speaker one_time -> speaker test runs and consumes it
    live_pm.grant_permission(
        ident,
        "voice.speaker",
        decision="allow",
        lifetime="one_time",
        actor="user_test",
        explicit_user_approval=True,
    )
    spk_res2 = coord.test_speaker(config=loaded_cfg)
    assert spk_res2.passed is True
    assert spk_probed["called"] is True

    # Grant voice.device session -> custom device saves cleanly to Alice's profile
    live_pm.grant_permission(
        ident,
        "voice.device",
        decision="allow",
        lifetime="session",
        actor="user_test",
        explicit_user_approval=True,
    )
    saved = coord.save_voice_setup(VoiceConfig(input_device="Built-in Mic"))
    assert saved.input_device == "Built-in Mic"
    assert VoiceConfig.path_for_user(id_mgr.users_root, ident.user_id).is_file()


def test_voice_microphone_os_permission_denied_handled_gracefully(tmp_path: Path):
    voice_svc = VoiceSetupService(
        input_lister=lambda: [AudioDeviceInfo(0, "Mac Mic", 1, 16000.0, True)],
        output_lister=lambda: [],
        mic_factory=lambda _cfg: _DummyMic(raise_exc=MicrophonePermissionError("macOS mic denied")),
    )
    db = MemoryDatabase(tmp_path / "data" / "riya.db")
    id_mgr = IdentityManager(database=db, users_root=tmp_path / "users")
    pm = PermissionManager(id_mgr)
    id_mgr.create_user("bob", auth_method="local_unlock")
    ident = id_mgr.authenticate("bob", auth_method="local_unlock")
    pm.grant_permission(ident, "voice.microphone", decision="allow", lifetime="session", actor="test")

    res = voice_svc.run_microphone_test(ident, config=VoiceConfig(), permission_manager=pm)
    assert res.passed is False
    assert res.blocked_by_permission is False
    assert res.error_kind == "os_permission_denied"
    assert "System Settings" in res.message


# ---------------------------------------------------------------------------
# 5. Permission onboarding NEVER auto-grants permissions + Diagnostics gating
# ---------------------------------------------------------------------------
def test_onboarding_never_auto_grants_permissions_and_completes_cleanly(tmp_path: Path):
    coord = _make_coordinator(tmp_path)
    vm = OnboardingViewModel(coord)
    vm.begin_setup()
    ident, _ = vm.create_account(username="tukaram", auth_method="local_unlock")
    vm.continue_without_ai()
    vm.skip_voice()
    vm.acknowledge_permissions()

    _, _, pm, _ = coord.initialize_services()
    assert pm.list_permissions(ident, include_revoked=True) == []

    # Persistent grant still requires explicit_user_approval=True (checkbox starts unchecked)
    with pytest.raises(Exception):
        vm.grant_explicit_permission(
            "voice.microphone",
            decision="allow",
            lifetime="persistent",
            explicit_persistent_ack=False,
        )

    report = vm.finish_onboarding()
    assert report.mandatory_passed is True
    assert report.can_enter_app is True
    assert "Local LLM conversation" in report.degraded_capabilities

    # Subsequent launch detects SETUP_COMPLETED and routes straight to Login!
    vm2 = OnboardingViewModel(_make_coordinator(tmp_path))
    assert vm2.initial_route() == "login"
    assert coord.detect_initial_state().schema_version == CURRENT_SCHEMA_VERSION


def test_diagnostics_blocks_completion_when_no_user_authenticated(tmp_path: Path):
    coord = _make_coordinator(tmp_path)
    report = coord.run_diagnostics()
    assert report.mandatory_passed is False
    auth_chk = report.by_id("identity_session")
    assert auth_chk is not None
    assert auth_chk.status in {CheckSeverity.NOT_CONFIGURED, CheckSeverity.FAILED}

    with pytest.raises(RuntimeError, match="mandatory checks"):
        coord.complete_onboarding()


# ---------------------------------------------------------------------------
# 6. VoiceUIController helpers, CLI flags, & macOS app bundle builder
# ---------------------------------------------------------------------------
def test_voice_ui_controller_and_cli_diagnostics_and_launcher(tmp_path: Path, capsys):
    coord = _make_coordinator(tmp_path)
    ident = coord.create_local_account(username="alice", auth_method="local_unlock")
    coord.continue_without_ai()
    coord.acknowledge_permissions_reviewed()
    coord.complete_onboarding()

    assistant, orchestrator = coord.build_runtime_session(ident)
    try:
        ctrl = VoiceUIController(orchestrator)
        assert "@alice" in ctrl.current_user_label()
        loaded, summary = ctrl.ai_status_summary()
        assert loaded is False
        assert "Limited" in summary

        # Store a memory and confirm controller exposes only Alice's memory
        assistant.memory.manager.for_identity(ident).remember("note", "fav_color", "indigo")
        mems = ctrl.list_memories()
        assert any(m["key"] == "fav_color" and m["value"] == "indigo" for m in mems)

        orchestrator.session.append_transcript(role="user", text="hello", modality="text")
        assert len(ctrl.snapshot().transcript) == 1
        ctrl.clear_transcript_history()
        assert len(ctrl.snapshot().transcript) == 0
    finally:
        orchestrator.close()
        assistant.close()

    # CLI --diagnostics runs non-interactively
    cli_main(["--diagnostics", "--database", str(tmp_path / "data" / "riya.db")])
    out = capsys.readouterr().out
    assert "System Readiness" in out
    assert "Database & Migrations" in out
    assert "Permission & Trust Engine" in out

    # CLI --setup interactive completion
    setup_db = tmp_path / "cli_setup" / "riya.db"
    inputs = iter(
        [
            "cliuser",       # username
            "CLI User",      # display_name
            "local_unlock",  # auth method
            "skip",          # skip AI model -> Limited Mode
            "n",             # save voice config? N
            "",              # Enter after permissions guide
            "exit",          # quit REPL cleanly after setup
        ]
    )
    with patch("builtins.input", side_effect=lambda _prompt="": next(inputs)):
        cli_main(["--setup", "--database", str(setup_db)])
    det = detect_install_state(setup_db)
    assert det.install_state == InstallState.SETUP_COMPLETED

    # macOS launcher + bundle builder checks
    launcher = Path("apps/macos/Launch RIYA.command")
    assert launcher.exists()
    assert bool(launcher.stat().st_mode & stat.S_IXUSR)

    from apps.macos.create_app_bundle import build_app_bundle

    bundle = build_app_bundle(output_dir=tmp_path)
    assert (bundle / "Contents" / "Info.plist").is_file()
    exe = bundle / "Contents" / "MacOS" / "RIYA"
    assert exe.is_file()
    assert bool(exe.stat().st_mode & stat.S_IXUSR)
    assert "RIYA" in (bundle / "Contents" / "Info.plist").read_text(encoding="utf-8", errors="ignore")
    assert len(WIZARD_STEP_DESCRIPTORS) == 6


def test_desktop_tkinter_renders_all_onboarding_and_login_views(tmp_path: Path):
    import tkinter as tk
    from core.onboarding.ui import _DesktopOnboardingWindow

    coord = _make_coordinator(tmp_path)
    ident = coord.create_local_account(username="tkuser", auth_method="local_unlock")
    coord.continue_without_ai()
    coord.acknowledge_permissions_reviewed()
    coord.complete_onboarding()

    root = tk.Tk()
    root.withdraw()
    try:
        vm = OnboardingViewModel(coord)
        win = _DesktopOnboardingWindow(root, vm)
        for route, step in [
            ("recovery", "welcome"),
            ("onboarding", "welcome"),
            ("onboarding", "account"),
            ("onboarding", "ai_engine"),
            ("onboarding", "voice"),
            ("onboarding", "permissions"),
            ("onboarding", "diagnostics"),
            ("login", "welcome"),
        ]:
            win._route = route
            vm.current_step = step
            win.render()
            root.update_idletasks()
        assert coord.active_identity is not None and coord.active_identity.user_id == ident.user_id
    finally:
        root.destroy()

