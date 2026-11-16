"""Milestone 6.2 — Cross-Platform OS Abstraction Layer & Native macOS Integration tests."""

from __future__ import annotations

import errno
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from core.agent.assistant import RIYAAssistant
from core.device import (
    DEFAULT_PLATFORM_REGISTRY,
    LinuxPlatform,
    MacOSPlatform,
    PlatformAdapter,
    PlatformRegistry,
    UnsupportedPlatformCapabilityError,
    WindowsPlatform,
    detect_platform_name,
    get_platform_adapter,
)
from core.identity.manager import IdentityManager
from core.memory.database import MemoryDatabase
from core.security.permissions import (
    PermissionDecision,
    PermissionLifetime,
    PermissionManager,
)
from core.tools.filesystem.files import (
    inspect_file_metadata,
    inspect_file_metadata_structured,
)
from core.tools.system import macos
from core.voice.config import VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import VoiceSession
from core.voice.ui import AssistantInterface, VoiceUIController


class _StubSTT:
    backend_name = "faster-whisper"
    model_size = "base"

    def load_model(self) -> None:
        return None


class _StubTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str, *, cancel_event=None) -> bool:
        self.spoken.append(text)
        return True

    def stop(self) -> None:
        return None


def _build_env(tmp_path: Path, *, platform: PlatformAdapter | None = None):
    VoiceSession._active_session_id = None
    db_path = tmp_path / "m62_riya.db"
    users_root = tmp_path / "users"
    db = MemoryDatabase(db_path)
    identity_mgr = IdentityManager(db, users_root=users_root)
    perm_mgr = PermissionManager(identity_mgr, strict_default_deny=False)

    identity_mgr.create_user("tukaram", password="StrongPass#123", display_name="Tukaram")
    identity_mgr.create_user("bob", password="StrongPass#456", display_name="Bob")

    tukaram_id = identity_mgr.authenticate("tukaram", password="StrongPass#123")
    bob_id = identity_mgr.authenticate("bob", password="StrongPass#456")

    assistant = RIYAAssistant(
        identity_manager=identity_mgr,
        default_identity=tukaram_id,
        permission_manager=perm_mgr,
        strict_permissions=False,
        ai_backend="none",
        allow_degraded_ai=True,
        platform=platform,
    )
    cfg = VoiceConfig(
        recordings_dir=str(
            VoiceConfig.recordings_dir_for_user(identity_mgr.users_root, tukaram_id.user_id)
        )
    )
    session = VoiceSession(
        cfg,
        user_id=tukaram_id.user_id,
        auth_session_id=tukaram_id.session_id,
    )
    orch = VoiceOrchestrator(
        assistant,
        config=cfg,
        session=session,
        identity_manager=identity_mgr,
        permission_manager=perm_mgr,
        identity=tukaram_id,
    )
    ui = VoiceUIController(orch)
    return db, identity_mgr, perm_mgr, assistant, orch, ui, tukaram_id, bob_id


def test_platform_detection_adapter_registration_and_capability_discovery():
    # 1. Platform detection on actual Mac
    detected = detect_platform_name()
    assert detected == "macos"

    # 2. Default registry contains macos, windows, linux
    assert set(DEFAULT_PLATFORM_REGISTRY.list_platforms()) >= {"linux", "macos", "windows"}
    active_adapter = get_platform_adapter()
    assert isinstance(active_adapter, MacOSPlatform)
    assert isinstance(active_adapter, PlatformAdapter)

    info = active_adapter.platform_info()
    assert info.platform_id == "macos"
    assert info.is_supported is True
    assert info.os_version
    assert info.architecture in {"arm64", "x86_64"}

    # 3. Custom PlatformRegistry registration
    custom_reg = PlatformRegistry()
    custom_reg.register("macos", MacOSPlatform)
    custom_reg.register("windows", WindowsPlatform)
    custom_reg.register("linux", LinuxPlatform)
    assert isinstance(custom_reg.create("windows"), WindowsPlatform)
    assert isinstance(custom_reg.create("linux"), LinuxPlatform)
    with pytest.raises(ValueError):
        custom_reg.create("plan9")

    # 4. Capability discovery on macOS vs Windows/Linux
    mac_caps = active_adapter.capabilities()
    assert mac_caps.is_supported("system_information") is True
    assert mac_caps.is_supported("application_discovery") is True
    assert mac_caps.is_supported("application_launch") is True
    assert mac_caps.is_supported("filesystem_metadata") is True
    assert mac_caps.is_supported("clipboard_read") is True
    assert mac_caps.is_supported("clipboard_write") is True
    assert mac_caps.is_supported("notifications") is True
    assert mac_caps.is_supported("window_inspection") is True
    assert "Supported capabilities:" in active_adapter.describe_capabilities_text()


def test_real_macos_system_info_and_application_discovery():
    adapter = MacOSPlatform()

    # Real system information
    sys_info = adapter.get_detailed_system_info()
    assert sys_info["platform"] == "macOS"
    assert sys_info["os_version"]
    assert sys_info["architecture"]
    assert sys_info["cpu_logical_cores"] >= 1
    assert sys_info["storage"]["total_bytes"] > 0
    assert sys_info["storage"]["free_bytes"] > 0

    summary_text = adapter.system_information()
    assert "macOS" in summary_text
    assert "Storage (/):" in summary_text

    storage_text = adapter.storage_status("/")
    assert "GiB available" in storage_text

    net_text = adapter.network_status()
    assert "Network:" in net_text

    # Real installed applications with bundle identifiers, versions, and paths
    apps = adapter.discover_installed_applications()
    assert len(apps) > 0
    sample_with_bundle = [a for a in apps if a.get("bundle_id") and a.get("path")]
    assert len(sample_with_bundle) > 0

    calc_meta = adapter.resolve_application_bundle("Calculator")
    assert calc_meta is not None
    assert calc_meta["name"].lower() == "calculator"
    assert calc_meta["installed"] is True
    assert calc_meta["path"].endswith(".app")
    assert "calculator" in (calc_meta.get("bundle_id") or "").lower()

    check_calc = adapter.check_application("Calculator")
    assert "Calculator" in check_calc and "is installed" in check_calc
    assert "bundle_id:" in check_calc

    check_missing = adapter.check_application("NonExistentAppXYZ987")
    assert "not installed" in check_missing.lower()


def test_application_launch_verification_and_injection_defenses():
    # Invalid application paths and command injection attempts must be rejected
    bad_inputs = [
        "",
        "../Calculator",
        "/Applications/Calculator.app",
        'Calculator"; do shell script "id"',
        "Calculator`whoami`",
        "Calculator$(id)",
        "Calculator|cat /etc/passwd",
        "-a",
    ]
    for payload in bad_inputs:
        with patch("core.tools.system.macos.subprocess.run") as mock_run:
            with pytest.raises(ValueError):
                macos.launch_and_verify_application(payload)
            with pytest.raises(ValueError):
                macos.resolve_application_bundle(payload)
            mock_run.assert_not_called()

    # Real harmless application launch & verification (or safe verified launch)
    adapter = MacOSPlatform()
    launch_res = adapter.launch_application_structured("Calculator")
    assert launch_res.status == "success"
    assert launch_res.verified is True
    assert launch_res.data["app_name"] == "Calculator"
    assert launch_res.data["running"] is True


def test_filesystem_metadata_allowed_roots_symlinks_and_tcc_errors(tmp_path: Path):
    db, identity_mgr, perm_mgr, assistant, orch, ui, tukaram_id, bob_id = _build_env(tmp_path)

    ws = Path(tukaram_id.workspace_dir)
    sample_file = ws / "notes.txt"
    sample_file.write_text("hello milestone 6.2", encoding="utf-8")

    # Inspect metadata inside allowed user workspace via Assistant/ToolExecutor
    res = assistant.agent.execute("inspect_file_metadata", user_context=tukaram_id, path="notes.txt")
    assert res["status"] == "success"
    assert "notes.txt" in res["result"]
    assert '"type": "file"' in res["result"]

    # Symlink protection (1): symlink pointing outside workspace is blocked by allowed roots
    outside_secret = tmp_path / "outside_secret.txt"
    outside_secret.write_text("top secret", encoding="utf-8")
    symlink_file = ws / "linked_secret.txt"
    symlink_file.symlink_to(outside_secret)

    sym_outside_res = assistant.agent.execute(
        "inspect_file_metadata",
        user_context=tukaram_id,
        path="linked_secret.txt",
    )
    assert sym_outside_res["status"] == "failed"
    assert sym_outside_res.get("blocked") is True
    assert "access denied" in sym_outside_res["error"].lower()

    # Symlink protection (2): symlink pointing inside workspace is blocked by O_NOFOLLOW / lstat
    symlink_inside = ws / "linked_notes.txt"
    symlink_inside.symlink_to(sample_file)
    sym_inside_res = assistant.agent.execute(
        "inspect_file_metadata",
        user_context=tukaram_id,
        path="linked_notes.txt",
    )
    assert sym_inside_res["status"] == "failed"
    assert sym_inside_res.get("blocked") is True
    assert "symlink" in sym_inside_res["error"].lower() or "unsafe path" in sym_inside_res["error"].lower()

    # Path outside user's allowed workspace is blocked by PermissionManager / filesystem sandbox
    outside_res = assistant.agent.execute(
        "inspect_file_metadata",
        user_context=tukaram_id,
        path=str(outside_secret),
    )
    assert outside_res["status"] == "failed"
    assert outside_res.get("blocked") is True
    assert "access denied" in outside_res["error"].lower()

    # macOS TCC permission error (EACCES / EPERM) produces clear privacy message
    from core.identity.context import ExecutionContextScope

    with patch("core.tools.filesystem.files.os.open", side_effect=OSError(errno.EPERM, "Operation not permitted")):
        with ExecutionContextScope(identity_mgr.build_execution_context(tukaram_id)):
            with pytest.raises(PermissionError, match="macOS TCC privacy permissions"):
                inspect_file_metadata_structured(str(sample_file))
        tcc_res = assistant.agent.execute(
            "inspect_file_metadata",
            user_context=tukaram_id,
            path="notes.txt",
        )
        assert tcc_res["status"] == "failed"
        assert "macOS TCC privacy permissions" in tcc_res["error"]

    # Subprocess timeout handling on macOS adapter commands is safe and non-crashing
    from core.tools.system.macos import get_battery_status_structured

    with patch(
        "core.tools.system.macos.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["/usr/bin/pmset"], timeout=5.0),
    ):
        battery_timeout = get_battery_status_structured()
        assert battery_timeout["status"] == "timed_out"
        assert battery_timeout["has_battery"] is False


def test_clipboard_notifications_window_metadata_permissions_and_secret_redaction(tmp_path: Path):
    db, identity_mgr, perm_mgr, assistant, orch, ui, tukaram_id, bob_id = _build_env(tmp_path)

    # 1. Clipboard is default-deny until system.clipboard is granted
    with pytest.raises(PermissionError, match="system.clipboard"):
        assistant.agent.execute("clipboard_read", user_context=tukaram_id)

    # Grant system.clipboard for session
    ui.grant_permission("system.clipboard", decision="allow", lifetime="session")

    # Reading normal clipboard content works (mocked pbpaste so host clipboard is not mutated)
    with patch(
        "core.tools.system.macos._run_executable",
        return_value=subprocess.CompletedProcess(["/usr/bin/pbpaste"], 0, " Meeting notes at 3pm ", ""),
    ):
        read_res = assistant.agent.execute("clipboard_read", user_context=tukaram_id)
    assert read_res["status"] == "success"
    assert "Meeting notes at 3pm" in read_res["result"]

    # Reading secret-like clipboard content redacts the secret in output and audit/outcome logs
    secret_value = "api_key=sk-live9876543210secretvalue"
    with patch(
        "core.tools.system.macos._run_executable",
        return_value=subprocess.CompletedProcess(["/usr/bin/pbpaste"], 0, secret_value, ""),
    ):
        secret_read = assistant.agent.execute("clipboard_read", user_context=tukaram_id)
    assert secret_read["status"] == "success"
    assert "sk-live9876543210secretvalue" not in secret_read["result"]
    assert "redacted" in secret_read["result"].lower()

    # Writing clipboard requires confirmation even when system.clipboard is granted ALLOW
    write_pending = assistant.agent.execute(
        "clipboard_write",
        user_context=tukaram_id,
        text=secret_value,
    )
    assert write_pending["status"] == "confirmation_required"
    token = write_pending["confirmation_token"]

    # Preview and outcome recording must redact secret-like clipboard text
    preview = assistant._format_action_preview("clipboard_write", {"text": secret_value})
    assert "sk-live9876543210secretvalue" not in preview
    assert "REDACTED" in preview

    with patch(
        "core.tools.system.macos.subprocess.run",
        return_value=subprocess.CompletedProcess(["/usr/bin/pbcopy"], 0, "", ""),
    ) as mock_pbcopy:
        write_done = assistant.agent.execute(
            "clipboard_write",
            confirmation_token=token,
            user_context=tukaram_id,
            text=secret_value,
        )
    assert write_done["status"] == "success"
    assert mock_pbcopy.call_args.kwargs["input"] == secret_value
    assert mock_pbcopy.call_args.kwargs["shell"] is False

    # 2. Notifications require system.notification and use safe argv (no interpolation)
    with pytest.raises(PermissionError, match="system.notification"):
        assistant.agent.execute(
            "send_notification",
            user_context=tukaram_id,
            title="Reminder",
            message="Take a break",
        )

    ui.grant_permission("system.notification", decision="allow", lifetime="session")
    malicious_title = 'Alert" & do shell script "id" --'
    with patch(
        "core.tools.system.macos.subprocess.run",
        return_value=subprocess.CompletedProcess(["/usr/bin/osascript"], 0, "SENT\n", ""),
    ) as mock_notify:
        notif_res = assistant.agent.execute(
            "send_notification",
            user_context=tukaram_id,
            title=malicious_title,
            message="Body text",
            subtitle="Sub",
        )
    assert notif_res["status"] == "success"
    cmd = mock_notify.call_args.args[0]
    sep_idx = cmd.index("--")
    script_parts = [cmd[i + 1] for i in range(sep_idx) if cmd[i] == "-e"]
    assert not any(malicious_title in part for part in script_parts)
    assert cmd[sep_idx + 1 :] == [malicious_title, "Body text", "Sub"]

    # 3. Active window metadata requires desktop.automate and handles missing Accessibility permission
    with pytest.raises(PermissionError, match="desktop.automate"):
        assistant.agent.execute("active_window_info", user_context=tukaram_id)

    ui.grant_permission("desktop.automate", decision="allow", lifetime="session")
    with patch(
        "core.tools.system.macos._run_osascript",
        return_value=subprocess.CompletedProcess(
            ["/usr/bin/osascript"],
            1,
            "",
            "System Events got an error: osascript is not allowed assistive access. (-25211)",
        ),
    ):
        win_res = assistant.agent.execute("active_window_info", user_context=tukaram_id)
    assert win_res["status"] == "success"
    assert "Accessibility" in win_res["result"]


def test_unsupported_platforms_windows_and_linux_reported_accurately(tmp_path: Path):
    win_adapter = WindowsPlatform()
    lin_adapter = LinuxPlatform()

    for adapter in (win_adapter, lin_adapter):
        assert adapter.platform_info().is_supported is False
        assert adapter.capabilities().supported_keys() == []
        res = adapter.system_info_adapter.get_system_info()
        assert res.status == "unsupported"
        assert res.unsupported is True
        with pytest.raises(UnsupportedPlatformCapabilityError):
            adapter.system_information()
        with pytest.raises(UnsupportedPlatformCapabilityError):
            adapter.open_application("Calculator")

    # Wire WindowsPlatform into RIYAAssistant & AssistantInterface and verify unsupported outcome
    _, _, _, assistant, _, ui, tukaram_id, _ = _build_env(
        tmp_path / "win_env",
        platform=win_adapter,
    )
    assert isinstance(ui, AssistantInterface)
    p_info = ui.platform_info()
    assert p_info["platform_id"] == "windows"
    assert p_info["is_supported"] is False

    reply = ui.submit_text("What macOS version am I running?")
    assert "not supported on the current platform" in reply.lower()
    outcomes = ui.last_tool_results()
    assert outcomes[-1]["status"] == "unsupported"
    assert outcomes[-1]["outcome_category"] == "unsupported"
    err = ui.last_error_info()
    assert err is not None
    assert err["outcome_category"] == "unsupported"


def test_milestone_6_1_interface_integration_user_isolation_and_audit_logging(tmp_path: Path):
    db, identity_mgr, perm_mgr, assistant, orch, ui, tukaram_id, bob_id = _build_env(tmp_path)

    # 1. "What macOS version am I running?"
    r1 = ui.submit_text("What macOS version am I running?")
    assert "macOS" in r1
    assert ui.last_tool_results()[-1]["tool"] == "system_information"
    assert ui.last_tool_results()[-1]["outcome_category"] == "successful"

    # 2. "How much disk space is available?"
    r2 = ui.submit_text("How much disk space is available?")
    assert "GiB available" in r2
    assert ui.last_tool_results()[-1]["tool"] == "storage_status"
    assert ui.last_tool_results()[-1]["outcome_category"] == "successful"

    # 3. "Which applications are installed?"
    r3 = ui.submit_text("Which applications are installed?")
    assert "installed macOS application(s)" in r3
    assert ui.last_tool_results()[-1]["tool"] == "check_application"

    # 4. "Check whether Calculator is installed."
    r4 = ui.submit_text("Check whether Calculator is installed.")
    assert "Calculator" in r4 and "is installed" in r4
    assert "bundle_id:" in r4

    # 5. "Open Calculator." -> denied when system.open_app not granted -> grant -> confirmation -> approve
    r5_denied = ui.submit_text("Open Calculator.")
    assert "blocked" in r5_denied.lower() or "couldn't complete" in r5_denied.lower()
    assert ui.last_tool_results()[-1]["outcome_category"] == "denied"
    assert ui.last_error_info()["outcome_category"] == "denied"

    ui.grant_permission("system.open_app", decision="allow", lifetime="session")
    r5_confirm = ui.submit_text("Open Calculator.")
    assert "Proceed?" in r5_confirm
    pending = ui.pending_confirmation_info()
    assert pending is not None
    assert pending["tool"] == "open_application"

    # User isolation: Bob cannot see Tukaram's tool outcomes or approve Tukaram's confirmation
    orch.switch_identity(bob_id)
    assert ui.pending_confirmation_info() is None
    assert ui.last_tool_results() == []

    # Switch back to Tukaram, re-request Open Calculator, and approve
    orch.switch_identity(tukaram_id)
    ui.submit_text("Open Calculator.")
    r5_approved = ui.approve_pending_action()
    assert "Opened Calculator." in r5_approved
    assert ui.last_tool_results()[-1]["tool"] == "open_application"
    assert ui.last_tool_results()[-1]["outcome_category"] == "successful"

    # Audit log contains permission events for Tukaram and none of Bob's private data
    audit_events = ui.audit_history(limit=25)
    assert len(audit_events) >= 2
    assert all(ev.user_id == tukaram_id.user_id for ev in audit_events)
