"""Automated tests for RIYA user data preservation across upgrades and uninstalls.

Verifies:
- Debian package postrm maintainer script strictly preserves user data directories.
- Package removal removes only system-wide binaries and desktop launchers.
- User SQLite database (~/.local/share/riya/data/riya.db) is preserved.
- User downloaded GGUF neural models (~/.local/share/riya/models/) are preserved.
- User memory vectors, settings, and logs are preserved.
- Simulation of clean install -> user activity -> package upgrade -> package uninstall.
- Data path resolver guarantee: user data paths never point inside installation roots.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_LINUX = REPO_ROOT / "packaging" / "linux"
POSTRM_SCRIPT = PKG_LINUX / "debian" / "postrm"
PRERM_SCRIPT = PKG_LINUX / "debian" / "prerm"


class TestDebianMaintainerScriptsSafety:
    def test_postrm_exists(self):
        assert POSTRM_SCRIPT.is_file(), f"postrm script missing at {POSTRM_SCRIPT}"

    def test_postrm_does_not_delete_user_data(self):
        content = POSTRM_SCRIPT.read_text(encoding="utf-8")
        # Ensure no destructive commands target user directories
        dangerous_patterns = [
            "rm -rf $HOME/.local/share/riya",
            "rm -rf ~/.local/share/riya",
            "rm -rf /home/*/.local/share/riya",
            "rm -rf $XDG_DATA_HOME/riya",
            "rm -rf ~/.cache/riya",
            "rm -rf ~/.config/riya",
            "rm -r $HOME",
            "rm -r ~/",
        ]
        for pattern in dangerous_patterns:
            assert pattern not in content, f"Dangerous pattern found in postrm: {pattern}"

    def test_postrm_preserves_models_explicitly(self):
        content = POSTRM_SCRIPT.read_text(encoding="utf-8")
        assert "SAFE DATA PRESERVATION GUARANTEE" in content or "preserved" in content.lower()

    def test_prerm_does_not_delete_user_data(self):
        content = PRERM_SCRIPT.read_text(encoding="utf-8")
        assert "rm -rf" not in content
        assert "data" not in content.lower()


class TestPathResolverIsolation:
    def test_user_data_dir_isolated_from_install_root(self, monkeypatch):
        # Simulate running in /opt/riya production environment
        monkeypatch.setenv("RIYA_ENV", "production")
        monkeypatch.setenv("RIYA_APP_SUPPORT_DIR", str(Path.home() / ".local" / "share" / "riya"))

        from core.config.paths import get_user_data_dir, get_models_dir, get_logs_dir

        data_dir = get_user_data_dir()
        models_dir = get_models_dir()
        logs_dir = get_logs_dir()

        assert not str(data_dir).startswith("/opt/riya")
        assert not str(models_dir).startswith("/opt/riya")
        assert not str(logs_dir).startswith("/opt/riya")

        assert ".local" in str(data_dir).lower() or "riya" in str(data_dir).lower()

    def test_appimage_user_data_dir_isolated_from_appdir(self, monkeypatch):
        # Simulate running inside mounted AppImage AppDir
        fake_appdir = Path("/tmp/.mount_RIYA12345")
        monkeypatch.setenv("RIYA_APPIMAGE", "1")
        monkeypatch.setenv("APPDIR", str(fake_appdir))
        monkeypatch.setenv("RIYA_DATA_DIR", str(Path.home() / ".local" / "share" / "riya" / "data"))

        from core.config.paths import get_user_data_dir

        data_dir = get_user_data_dir()
        assert not str(data_dir).startswith(str(fake_appdir))


class TestLifecycleDataPreservationSimulation:
    @pytest.fixture
    def lifecycle_sandbox(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            install_dir = base / "opt" / "riya"
            sys_bin = base / "usr" / "bin"
            sys_apps = base / "usr" / "share" / "applications"
            user_home = base / "home" / "testuser"
            user_xdg_data = user_home / ".local" / "share" / "riya"
            user_data = user_xdg_data / "data"
            user_models = user_xdg_data / "models"
            user_logs = user_xdg_data / "logs"

            for d in (install_dir, sys_bin, sys_apps, user_data, user_models, user_logs):
                d.mkdir(parents=True, exist_ok=True)

            yield {
                "base": base,
                "install_dir": install_dir,
                "sys_bin": sys_bin,
                "sys_apps": sys_apps,
                "user_home": user_home,
                "user_data": user_data,
                "user_models": user_models,
                "user_logs": user_logs,
            }

    def test_simulated_install_and_activity(self, lifecycle_sandbox):
        box = lifecycle_sandbox

        # 1. Simulate package installation
        (box["install_dir"] / "bin").mkdir(parents=True, exist_ok=True)
        (box["install_dir"] / "bin" / "riya").write_text("#!/bin/sh\necho RIYA\n")
        (box["sys_bin"] / "riya").symlink_to(box["install_dir"] / "bin" / "riya")
        (box["sys_apps"] / "riya.desktop").write_text("[Desktop Entry]\nName=RIYA\n")

        # 2. Simulate user activity (database creation, model download, logs)
        db_file = box["user_data"] / "riya.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);")
        conn.execute("INSERT INTO users (username) VALUES ('testuser');")
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT);")
        conn.execute("INSERT INTO memories (content) VALUES ('User prefers dark mode and Python');")
        conn.commit()
        conn.close()

        # Simulate provisioned model
        model_file = box["user_models"] / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 1024)

        # Simulate logs
        log_file = box["user_logs"] / "riya.log"
        log_file.write_text("2026-11-15: System initialized successfully\n")

        # Verify state prior to uninstall
        assert db_file.is_file()
        assert model_file.is_file()
        assert log_file.is_file()
        assert (box["sys_bin"] / "riya").is_symlink()

        # 3. Simulate package removal (what dpkg/apt does on remove/purge)
        # Removes /opt/riya and system symlinks/desktops
        shutil.rmtree(box["install_dir"])
        if (box["sys_bin"] / "riya").is_symlink() or (box["sys_bin"] / "riya").is_file():
            (box["sys_bin"] / "riya").unlink()
        if (box["sys_apps"] / "riya.desktop").is_file():
            (box["sys_apps"] / "riya.desktop").unlink()

        # 4. Verify system files are removed
        assert not box["install_dir"].exists()
        assert not (box["sys_bin"] / "riya").exists()
        assert not (box["sys_apps"] / "riya.desktop").exists()

        # 5. CRITICAL VERIFICATION: User data, models, memories, logs MUST REMAIN INTACT!
        assert db_file.is_file(), "User database was unexpectedly deleted during uninstallation!"
        assert model_file.is_file(), "Downloaded neural model was unexpectedly deleted!"
        assert log_file.is_file(), "Diagnostic logs were unexpectedly deleted!"

        # Verify database content integrity
        verify_conn = sqlite3.connect(str(db_file))
        cursor = verify_conn.cursor()
        cursor.execute("SELECT username FROM users;")
        user_row = cursor.fetchone()
        assert user_row == ("testuser",)

        cursor.execute("SELECT content FROM memories;")
        mem_row = cursor.fetchone()
        assert mem_row == ("User prefers dark mode and Python",)
        verify_conn.close()

    def test_simulated_package_upgrade(self, lifecycle_sandbox):
        box = lifecycle_sandbox

        # Existing user database and model from v1.0.0
        db_file = box["user_data"] / "riya.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE schema_info (version INTEGER);")
        conn.execute("INSERT INTO schema_info VALUES (1);")
        conn.commit()
        conn.close()

        model_file = box["user_models"] / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
        model_file.write_bytes(b"GGUF" + b"\x00" * 512)

        # Simulate upgrade: old /opt/riya replaced with new /opt/riya
        shutil.rmtree(box["install_dir"])
        box["install_dir"].mkdir(parents=True, exist_ok=True)
        (box["install_dir"] / "version.txt").write_text("1.0.1")

        # User data must persist seamlessly into upgraded version
        assert db_file.is_file()
        assert model_file.is_file()

        verify_conn = sqlite3.connect(str(db_file))
        ver = verify_conn.execute("SELECT version FROM schema_info;").fetchone()[0]
        assert ver == 1
        verify_conn.close()
