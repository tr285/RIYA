"""Tests for Linux package installation structure and resource path correctness.

Verifies:
- DEB staging paths match standard Freedesktop Linux FHS locations.
- Launcher scripts have correct shebang, permissions, and security controls.
- Icon installation covers all required hicolor resolution paths.
- Desktop file contains required Freedesktop fields.
- AppDir usr/share paths are correctly structured.
- Launcher environment exports (PYTHONPATH, RIYA_ENV, XDG vars).
- Application code does not contain hardcoded developer paths.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
PKG_LINUX = REPO_ROOT / "packaging" / "linux"
APPDIR_PATH = DIST_DIR / "RIYA_1.0.0_x86_64.AppDir"
LAUNCHER_SRC = PKG_LINUX / "debian" / "riya-launcher"
APPRUN_SRC = PKG_LINUX / "appimage" / "AppRun"
DESKTOP_SRC = PKG_LINUX / "appimage" / "riya.desktop"


class TestLauncherScript:
    def test_launcher_exists(self):
        assert LAUNCHER_SRC.is_file()

    def test_launcher_executable(self):
        mode = LAUNCHER_SRC.stat().st_mode
        assert bool(mode & stat.S_IXUSR), "Launcher must be executable"

    def test_launcher_shebang(self):
        content = LAUNCHER_SRC.read_text()
        assert content.startswith("#!/usr/bin/env bash")

    def test_launcher_root_guard(self):
        content = LAUNCHER_SRC.read_text()
        assert "id -u" in content, "Must guard against root execution"
        assert "exit 1" in content

    def test_launcher_sets_xdg_data_home(self):
        content = LAUNCHER_SRC.read_text()
        assert "XDG_DATA_HOME" in content

    def test_launcher_sets_xdg_cache_home(self):
        content = LAUNCHER_SRC.read_text()
        assert "XDG_CACHE_HOME" in content

    def test_launcher_sets_pythonpath(self):
        content = LAUNCHER_SRC.read_text()
        assert "PYTHONPATH" in content

    def test_launcher_sets_riya_env_production(self):
        content = LAUNCHER_SRC.read_text()
        assert "RIYA_ENV" in content
        assert "production" in content

    def test_launcher_sets_riya_data_dir(self):
        content = LAUNCHER_SRC.read_text()
        assert "RIYA_DATA_DIR" in content

    def test_launcher_sets_riya_models_dir(self):
        content = LAUNCHER_SRC.read_text()
        assert "RIYA_MODELS_DIR" in content

    def test_launcher_no_hardcoded_dev_paths(self):
        content = LAUNCHER_SRC.read_text()
        assert "/Users/" not in content
        assert "/home/runner" not in content
        assert "/opt/anaconda" not in content

    def test_launcher_invokes_core_main(self):
        content = LAUNCHER_SRC.read_text()
        assert "core.main" in content

    def test_launcher_uses_exec(self):
        """exec replaces shell, clean process model"""
        content = LAUNCHER_SRC.read_text()
        assert "exec \"$PY_BIN\"" in content


class TestAppRunScript:
    def test_apprun_exists(self):
        assert APPRUN_SRC.is_file()

    def test_apprun_executable(self):
        mode = APPRUN_SRC.stat().st_mode
        assert bool(mode & stat.S_IXUSR)

    def test_apprun_shebang(self):
        content = APPRUN_SRC.read_text()
        assert content.startswith("#!/usr/bin/env bash")

    def test_apprun_root_guard(self):
        content = APPRUN_SRC.read_text()
        assert "id -u" in content

    def test_apprun_sets_appdir(self):
        content = APPRUN_SRC.read_text()
        assert "APPDIR" in content

    def test_apprun_sets_xdg_dirs(self):
        content = APPRUN_SRC.read_text()
        assert "XDG_DATA_HOME" in content
        assert "XDG_CACHE_HOME" in content

    def test_apprun_no_hardcoded_paths(self):
        content = APPRUN_SRC.read_text()
        assert "/Users/" not in content


class TestDesktopFile:
    def test_desktop_file_exists(self):
        assert DESKTOP_SRC.is_file()

    def test_desktop_name_field(self):
        content = DESKTOP_SRC.read_text()
        assert "Name=RIYA" in content

    def test_desktop_exec_field(self):
        content = DESKTOP_SRC.read_text()
        assert "Exec=riya" in content

    def test_desktop_icon_field(self):
        content = DESKTOP_SRC.read_text()
        assert "Icon=riya" in content

    def test_desktop_type_application(self):
        content = DESKTOP_SRC.read_text()
        assert "Type=Application" in content

    def test_desktop_terminal_false(self):
        content = DESKTOP_SRC.read_text()
        assert "Terminal=false" in content

    def test_desktop_categories(self):
        content = DESKTOP_SRC.read_text()
        assert "Categories=" in content


class TestAppDirInstallationPaths:
    """Verify the AppDir staging directory has FHS-compliant paths."""

    def test_appdir_exists(self):
        assert APPDIR_PATH.is_dir(), f"AppDir missing: {APPDIR_PATH}"

    def test_appdir_has_apprun(self):
        apprun = APPDIR_PATH / "AppRun"
        assert apprun.is_file()
        mode = apprun.stat().st_mode
        assert bool(mode & stat.S_IXUSR)

    def test_appdir_has_desktop_file(self):
        assert (APPDIR_PATH / "riya.desktop").is_file()

    def test_appdir_has_root_icon(self):
        assert (APPDIR_PATH / "riya.png").is_file()

    def test_appdir_has_core_code(self):
        core_dir = APPDIR_PATH / "opt" / "riya" / "core"
        assert core_dir.is_dir()

    def test_appdir_has_bin_launcher(self):
        launcher = APPDIR_PATH / "opt" / "riya" / "bin" / "riya"
        assert launcher.is_file()
        mode = launcher.stat().st_mode
        assert bool(mode & stat.S_IXUSR)

    def test_appdir_usr_share_applications(self):
        desktop = APPDIR_PATH / "usr" / "share" / "applications" / "riya.desktop"
        assert desktop.is_file()

    def test_appdir_usr_share_icons(self):
        icons_dir = APPDIR_PATH / "usr" / "share" / "icons" / "hicolor"
        assert icons_dir.is_dir()
        icon_files = list(icons_dir.rglob("riya.png"))
        assert len(icon_files) >= 5, f"Expected ≥5 icon sizes, found {len(icon_files)}"

    def test_appdir_no_gguf_embedded(self):
        gguf_files = list(APPDIR_PATH.rglob("*.gguf"))
        assert not gguf_files, f"GGUF model embedded in AppDir: {gguf_files}"

    def test_appdir_no_user_database(self):
        db_files = list(APPDIR_PATH.rglob("riya.db"))
        assert not db_files, f"User database in AppDir: {db_files}"


class TestPathConfiguration:
    """Verify cross-platform path resolution respects XDG standard."""

    def test_xdg_data_home_default(self):
        xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        from pathlib import Path as P
        expected = P(xdg) / "riya"
        assert str(expected).startswith(str(Path.home()))

    def test_paths_module_imports(self):
        from core.config.paths import (
            get_app_support_dir,
            get_database_path,
            get_models_dir,
            get_logs_dir,
            get_cache_dir,
        )
        # All must be callable without error
        # (They create dirs, so we just call them)
        for fn in (get_app_support_dir, get_database_path, get_models_dir, get_logs_dir, get_cache_dir):
            result = fn()
            assert result is not None

    def test_database_path_is_not_inside_install_dir(self):
        from core.config.paths import get_database_path
        db = get_database_path()
        # Database must not be inside /opt/riya or similar
        assert "/opt/riya" not in str(db)
        assert "site-packages" not in str(db)

    def test_models_dir_separate_from_app(self):
        from core.config.paths import get_models_dir
        models = get_models_dir()
        assert "/opt/riya" not in str(models)

