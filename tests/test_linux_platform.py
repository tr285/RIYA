"""Automated test suite for RIYA Linux Platform Integration (Milestone 5.2).

Covers:
- Linux platform detection, initialization, and capability reporting.
- Linux system information parsing (/etc/os-release, CPU, memory, storage, battery, network).
- Linux application discovery via XDG .desktop entries and secure launching.
- Linux process safeguards (protected system services cannot be killed).
- Linux filesystem operations with boundary enforcement.
- Linux clipboard operations with Wayland/X11 detection and credential scrubbing.
- Linux local desktop notifications via notify-send.
- Linux window inspection and Wayland security isolation reporting.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from core.device.linux import (
    LinuxApplicationAdapter,
    LinuxClipboardAdapter,
    LinuxFilesystemAdapter,
    LinuxNotificationAdapter,
    LinuxPlatform,
    LinuxSystemInformationAdapter,
    LinuxWindowManagerAdapter,
    _clean_exec_command,
    _get_display_server,
    _parse_os_release,
)
from core.device.platform import (
    PlatformInfo,
    PlatformOperationResult,
    UnsupportedPlatformCapabilityError,
)


# =============================================================================
# 1. Platform Detection & Inactive vs Active Mode
# =============================================================================

def test_linux_platform_inactive_mode_fail_closed_on_macos():
    """Verify that by default on non-Linux host, LinuxPlatform truthfully fails closed."""
    # When not forced and on Darwin, is_active is False
    with patch("sys.platform", "darwin"):
        lp = LinuxPlatform(force_active=False)
        info = lp.platform_info()
        assert info.os_name == "linux"
        assert info.is_supported is False

        caps = lp.capabilities()
        assert caps.platform_available is False
        assert len(caps.supported_capabilities) == 0

        # Sub-adapters report unsupported
        res = lp.clipboard_adapter.read_clipboard()
        assert res.status == "unsupported"
        assert res.supported is False
        assert "Linux native OS integration is not yet implemented" in res.message

        # Direct methods raise UnsupportedPlatformCapabilityError
        with pytest.raises(UnsupportedPlatformCapabilityError):
            lp.open_application("firefox")

        with pytest.raises(UnsupportedPlatformCapabilityError):
            lp.lock_screen()


def test_linux_platform_active_mode_capabilities():
    """Verify that when active, LinuxPlatform reports full capabilities."""
    lp = LinuxPlatform(force_active=True)
    info = lp.platform_info()
    assert info.os_name == "linux"
    assert info.is_supported is True

    caps = lp.capabilities()
    assert caps.platform_available is True
    assert caps.supports("system_info")
    assert caps.supports("app_launch")
    assert caps.supports("app_discovery")
    assert caps.supports("app_close")
    assert caps.supports("clipboard_read")
    assert caps.supports("clipboard_write")
    assert caps.supports("local_notification")
    assert caps.supports("window_inspection")
    assert caps.supports("screenshot")
    assert caps.supports("volume_control")
    assert caps.supports("screen_lock")

    # Security restrictions remain unsupported
    assert not caps.supports("unrestricted_shell")
    assert not caps.supports("background_clipboard_monitoring")
    assert not caps.supports("kernel_extension_loading")


# =============================================================================
# 2. System Information Adapter Tests
# =============================================================================

def test_linux_system_info_parsing(tmp_path):
    """Test parsing of /etc/os-release and detailed system telemetry."""
    fake_os_release = tmp_path / "os-release"
    fake_os_release.write_text(
        'NAME="Ubuntu"\nVERSION="24.04 LTS (Noble Numbat)"\nID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n',
        encoding="utf-8",
    )

    with patch("core.device.linux.Path") as mock_path:
        # Route /etc/os-release to our fake file
        def path_side_effect(p):
            if str(p) in ("/etc/os-release", "/usr/lib/os-release"):
                return fake_os_release
            return Path(p)

        mock_path.side_effect = path_side_effect

        adapter = LinuxSystemInformationAdapter(is_active=True)
        res = adapter.get_system_info()
        assert res.status == "success"
        assert res.supported is True
        assert res.data["distribution"] == "Ubuntu 24.04 LTS"
        assert res.data["distribution_id"] == "ubuntu"
        assert "Ubuntu" in res.message


def test_linux_storage_info():
    """Test Linux storage inspection."""
    adapter = LinuxSystemInformationAdapter(is_active=True)
    res = adapter.get_storage_info("/")
    assert res.status == "success"
    assert res.supported is True
    assert "total_gib" in res.data
    assert "available_gib" in res.data
    assert res.data["total_gib"] > 0


def test_linux_battery_status_ac_desktop_fallback():
    """Test battery reporting when running on a desktop/VM without physical battery."""
    adapter = LinuxSystemInformationAdapter(is_active=True)
    with patch("psutil.sensors_battery", return_value=None):
        with patch("core.device.linux.Path.is_dir", return_value=False):
            res = adapter.get_battery_status()
            assert res.status == "success"
            assert res.supported is True
            assert res.data["has_battery"] is False
            assert "AC power" in res.message


def test_linux_network_status():
    """Test Linux network adapter online/offline detection."""
    adapter = LinuxSystemInformationAdapter(is_active=True)
    res = adapter.get_network_status()
    assert res.status == "success"
    assert res.supported is True
    assert "is_connected" in res.data


# =============================================================================
# 3. Application Adapter Tests
# =============================================================================

def test_clean_exec_command():
    """Test stripping XDG desktop field codes."""
    assert _clean_exec_command("firefox %u") == ["firefox"]
    assert _clean_exec_command("/usr/bin/vlc --started-from-file %F") == ["/usr/bin/vlc", "--started-from-file"]
    assert _clean_exec_command("gedit %f") == ["gedit"]


def test_desktop_file_discovery_and_search(tmp_path):
    """Test scanning .desktop files in XDG applications folder."""
    app_dir = tmp_path / "applications"
    app_dir.mkdir(parents=True)

    (app_dir / "firefox.desktop").write_text(
        "[Desktop Entry]\nName=Firefox Web Browser\nExec=firefox %u\nType=Application\nComment=Browse the Web\nCategories=Network;WebBrowser;\n",
        encoding="utf-8",
    )
    (app_dir / "calculator.desktop").write_text(
        "[Desktop Entry]\nName=Calculator\nExec=gnome-calculator\nType=Application\nComment=Perform arithmetic\nCategories=Utility;Calculator;\n",
        encoding="utf-8",
    )
    (app_dir / "hidden.desktop").write_text(
        "[Desktop Entry]\nName=Hidden Service\nExec=hidden-tool\nType=Application\nNoDisplay=true\n",
        encoding="utf-8",
    )

    with patch("core.device.linux._XDG_APPLICATION_DIRS", (app_dir,)):
        adapter = LinuxApplicationAdapter(is_active=True)
        installed = adapter.list_installed_applications()
        assert "Firefox Web Browser" in installed
        assert "Calculator" in installed
        assert "Hidden Service" not in installed

        matches = adapter.search_applications("browser")
        assert "Firefox Web Browser" in matches.data["applications"]

        details_res = adapter.get_application_details("firefox")
        assert details_res.status == "success"
        assert details_res.data["binary"] == "firefox"


def test_application_launch_safety():
    """Test safe application launch via parameter arrays without shell invocation."""
    adapter = LinuxApplicationAdapter(is_active=True)

    # Empty name fails validation
    res_empty = adapter.launch_application("")
    assert res_empty.status == "failed"

    # Nonexistent binary fails gracefully
    res_missing = adapter.launch_application("definitely_nonexistent_riya_binary_12345")
    assert res_missing.status == "failed"
    assert "not found" in res_missing.message.lower()

    # Successful mocked launch
    with patch("shutil.which", return_value="/usr/bin/gedit"):
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 9876
            mock_popen.return_value = mock_proc

            res = adapter.launch_application("gedit")
            assert res.status == "success"
            assert res.data["pid"] == 9876
            # Verify shell=False was strictly enforced
            assert mock_popen.call_args.kwargs.get("shell") is False


def test_process_safeguards_protected_processes():
    """Verify RIYA refuses to kill critical Linux system processes."""
    adapter = LinuxApplicationAdapter(is_active=True)

    for protected in ("systemd", "init", "Xorg", "mutter", "kwin", "pipewire", "riya"):
        res = adapter.close_application(protected)
        assert res.status == "denied"
        assert "protected system process" in res.message


# =============================================================================
# 4. Filesystem Adapter Tests
# =============================================================================

def test_linux_filesystem_allowed_roots_enforcement(tmp_path):
    """Test that file discovery is sandboxed within allowed filesystem roots."""
    adapter = LinuxFilesystemAdapter(is_active=True)

    # Create allowed folder
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    (allowed_dir / "doc1.txt").write_text("Hello", encoding="utf-8")

    with patch("core.device.linux.FILESYSTEM_ALLOWED_ROOTS", (allowed_dir,)):
        # Allowed directory discovery succeeds
        res_ok = adapter.discover_files(str(allowed_dir))
        assert res_ok.status == "success"
        assert "doc1.txt" in res_ok.data["files"]

        # Forbidden directory discovery is blocked
        forbidden_dir = tmp_path / "forbidden"
        forbidden_dir.mkdir()
        res_blocked = adapter.discover_files(str(forbidden_dir))
        assert res_blocked.status == "denied"
        assert "outside permitted" in res_blocked.message


def test_linux_filesystem_metadata(tmp_path):
    """Test inspecting metadata on Linux."""
    test_file = tmp_path / "sample.log"
    test_file.write_text("1234567890", encoding="utf-8")

    adapter = LinuxFilesystemAdapter(is_active=True)
    res = adapter.inspect_metadata(str(test_file))
    assert res.status == "success"
    assert res.data["size_bytes"] == 10
    assert res.data["type"] == "file"


# =============================================================================
# 5. Clipboard Adapter Tests
# =============================================================================

def test_linux_clipboard_secret_scrubbing():
    """Verify that clipboard read scrubs sensitive tokens matching secret patterns."""
    adapter = LinuxClipboardAdapter(is_active=True)

    mock_run = MagicMock()
    mock_run.return_code = 0
    mock_run.stdout = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret_token_12345"
    mock_run.returncode = 0

    with patch("shutil.which", return_value="/usr/bin/xclip"):
        with patch("subprocess.run", return_value=mock_run):
            res = adapter.read_clipboard()
            assert res.status == "success"
            assert "redacted" in res.message.lower()


def test_linux_clipboard_write():
    """Test copying text to Linux clipboard via wl-copy or xclip."""
    adapter = LinuxClipboardAdapter(is_active=True)

    mock_run = MagicMock()
    mock_run.returncode = 0

    with patch("shutil.which", return_value="/usr/bin/wl-copy"):
        with patch("core.device.linux._get_display_server", return_value="wayland"):
            with patch("subprocess.run", return_value=mock_run) as mock_sub:
                res = adapter.write_clipboard("Sample copied text")
                assert res.status == "success"
                assert "Successfully copied 18 characters" in res.message
                assert mock_sub.call_args.args[0] == ["wl-copy"]


# =============================================================================
# 6. Notification & Window Management Tests
# =============================================================================

def test_linux_notification_send():
    """Test desktop notification delivery via notify-send."""
    adapter = LinuxNotificationAdapter(is_active=True)

    mock_run = MagicMock()
    mock_run.returncode = 0

    with patch("shutil.which", return_value="/usr/bin/notify-send"):
        with patch("subprocess.run", return_value=mock_run) as mock_sub:
            res = adapter.send_notification("RIYA Alert", "Task completed.", subtitle="System")
            assert res.status == "success"
            cmd = mock_sub.call_args.args[0]
            assert cmd == ["/usr/bin/notify-send", "-a", "RIYA", "RIYA Alert - System", "Task completed."]


def test_linux_window_manager_wayland_isolation():
    """Verify Wayland client isolation is truthfully reported without errors."""
    adapter = LinuxWindowManagerAdapter(is_active=True)

    with patch("core.device.linux._get_display_server", return_value="wayland"):
        res = adapter.get_active_window_info()
        assert res.status == "success"
        assert res.data["display_server"] == "wayland"
        assert res.data["restricted"] is True
        assert "Wayland display server for client security isolation" in res.message


# =============================================================================
# 7. Unified LinuxPlatform Direct Methods Tests
# =============================================================================

def test_linux_platform_direct_convenience_methods():
    """Test high-level LinuxPlatform methods in active mode."""
    lp = LinuxPlatform(force_active=True)

    # Time
    cur_time = lp.current_time()
    assert len(cur_time) == 19

    # Volume control with pactl
    with patch("shutil.which", side_effect=lambda x: "/usr/bin/pactl" if x == "pactl" else None):
        with patch("subprocess.run") as mock_sub:
            msg = lp.set_volume(75)
            assert "Volume set to 75% via pactl." in msg
            assert mock_sub.call_args.args[0] == ["/usr/bin/pactl", "set-sink-volume", "@DEFAULT_SINK@", "75%"]

            mute_msg = lp.mute_volume()
            assert "Audio muted via pactl." in mute_msg

            unmute_msg = lp.unmute_volume()
            assert "Audio unmuted via pactl." in unmute_msg

    # Screen lock with loginctl
    with patch("shutil.which", return_value="/usr/bin/loginctl"):
        with patch("subprocess.run") as mock_sub:
            lock_msg = lp.lock_screen()
            assert "Session locked via loginctl." in lock_msg
            assert mock_sub.call_args.args[0] == ["/usr/bin/loginctl", "lock-session"]
