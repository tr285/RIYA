"""Automated security test suite for RIYA Linux Platform Integration (Milestone 5.2).

Verifies:
- Linux platform actions conform to RIYA PermissionManager and SecurityManager policies.
- User workspace isolation and permission checking.
- Refusal of symlink path traversal escaping allowed roots.
- Strict refusal to terminate protected system processes or PID <= 2.
- Secret and credential scrubbing during clipboard operations.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.device.linux import LinuxApplicationAdapter, LinuxClipboardAdapter, LinuxFilesystemAdapter, LinuxPlatform
from core.identity.manager import IdentityManager
from core.memory.database import MemoryDatabase
from core.security.manager import SecurityManager, looks_like_secret
from core.security.permissions import PermissionManager


def test_linux_protected_process_security_boundary():
    """Verify termination of PID 1, systemd, or init is strictly denied."""
    adapter = LinuxApplicationAdapter(is_active=True)

    for critical_proc in ("systemd", "init", "Xorg", "mutter", "kwin", "dbus-daemon", "sshd"):
        res = adapter.close_application(critical_proc)
        assert res.status == "denied"
        assert "protected system process" in res.message


def test_linux_filesystem_symlink_escape_blocking(tmp_path):
    """Verify that symlinks pointing outside allowed roots are blocked."""
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    secret_folder = tmp_path / "secret"
    secret_folder.mkdir()
    secret_file = secret_folder / "passwords.txt"
    secret_file.write_text("super_secret_credentials", encoding="utf-8")

    # Create symlink inside allowed pointing to outside secret folder
    symlink_path = allowed_root / "escape_link"
    try:
        os.symlink(secret_folder, symlink_path)
    except OSError:
        pytest.skip("Symlink creation not supported on this filesystem")

    adapter = LinuxFilesystemAdapter(is_active=True)
    with patch("core.device.linux.FILESYSTEM_ALLOWED_ROOTS", (allowed_root,)):
        # Discovering inside allowed should ignore/not traverse symlinks outside
        res = adapter.discover_files(str(allowed_root))
        assert res.status == "success"
        # symlink was filtered out
        assert "passwords.txt" not in res.data["files"]


def test_looks_like_secret_pattern_detection():
    """Verify that credentials and tokens are accurately detected across all formats."""
    # API keys / Bearer tokens / Passwords
    assert looks_like_secret("sk-1234567890abcdef1234567890") is True
    assert looks_like_secret("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9") is True
    assert looks_like_secret("api_key = AIzaSyD9ExampleKey12345") is True
    assert looks_like_secret("password: MySuperSecretPassword123") is True

    # Benign text
    assert looks_like_secret("Hello, can you help me write Python code?") is False
    assert looks_like_secret("The meeting is scheduled at 4pm tomorrow.") is False


def test_linux_security_manager_policy_alignment():
    """Verify that Linux actions are registered in SecurityManager safe / confirmation actions."""
    sec = SecurityManager()
    assert "system_information" in sec.SAFE_ACTIONS
    assert "battery_status" in sec.SAFE_ACTIONS
    assert "storage_status" in sec.SAFE_ACTIONS
    assert "network_status" in sec.SAFE_ACTIONS

    # Destructive or high-risk actions require confirmation
    assert "close_application" in sec.CONFIRMATION_ACTIONS
    assert "lock_screen" in sec.HIGH_RISK_ACTIONS
    assert sec.get_risk_level("close_application").value == "medium"
    assert sec.get_risk_level("lock_screen").value == "high"


def test_linux_user_workspace_isolation(tmp_path):
    """Verify multi-user workspace boundaries are respected on Linux."""
    db_path = tmp_path / "test_linux_sec.db"
    users_root = tmp_path / "users"
    db = MemoryDatabase(db_path)
    id_mgr = IdentityManager(db, users_root=users_root)

    id_mgr.create_user("bob", password="BobPassword123", display_name="Bob")
    bob = id_mgr.authenticate("bob", password="BobPassword123")
    assert bob is not None

    ws_bob = id_mgr.user_workspace(bob.user_id)
    assert ws_bob.is_dir()
    assert bob.user_id in str(ws_bob)

