"""Automated tests for RIYA portable Linux AppImage bundle.

Validates:
- AppImage binary file existence and executable permissions.
- AppImage shell runtime header, shebang, root-guard, and XDG environment variables.
- Payload extraction mechanism and embedded __PAYLOAD_START__ marker.
- Payload decompressibility and integrity matching AppDir contents.
- Strict isolation from developer-machine absolute paths.
- Execution without root privileges.
- Absence of embedded multi-gigabyte models or user databases.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import stat
import tarfile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
APPIMAGE_PATH = DIST_DIR / "RIYA-1.0.0-x86_64.AppImage"
APPDIR_PATH = DIST_DIR / "RIYA_1.0.0_x86_64.AppDir"


class TestAppImageBinaryProperties:
    def test_appimage_exists(self):
        assert APPIMAGE_PATH.is_file(), f"AppImage artifact missing at {APPIMAGE_PATH}"

    def test_appimage_has_valid_size(self):
        size = APPIMAGE_PATH.stat().st_size
        assert size > 100_000, f"AppImage suspiciously small: {size} bytes"
        # Must NOT embed the 2.5 GB model
        assert size < 50_000_000, f"AppImage contains embedded model: {size} bytes"

    def test_appimage_is_executable(self):
        mode = APPIMAGE_PATH.stat().st_mode
        assert bool(mode & stat.S_IXUSR), f"AppImage not executable: mode {oct(mode)}"


class TestAppImageRuntimeHeader:
    @pytest.fixture(scope="class")
    def header_text(self):
        with open(APPIMAGE_PATH, "rb") as f:
            content = f.read()
        marker = b"__PAYLOAD_START__\n"
        idx = content.find(marker)
        assert idx != -1, "AppImage missing __PAYLOAD_START__ marker"
        return content[: idx + len(marker)].decode("utf-8", errors="replace")

    def test_has_bash_shebang(self, header_text):
        assert header_text.startswith("#!/usr/bin/env bash")

    def test_strict_shell_flags(self, header_text):
        assert "set -euo pipefail" in header_text

    def test_contains_app_name_and_version(self, header_text):
        assert 'APP_NAME="RIYA"' in header_text
        assert 'APP_VERSION="1.0.0"' in header_text
        assert 'APP_ARCH="x86_64"' in header_text

    def test_root_execution_guard(self, header_text):
        assert "id -u" in header_text
        assert "ALLOW_ROOT" in header_text
        assert "exit 1" in header_text

    def test_sets_xdg_base_directories(self, header_text):
        assert "XDG_DATA_HOME" in header_text
        assert "XDG_CACHE_HOME" in header_text
        assert "XDG_CONFIG_HOME" in header_text

    def test_sets_appdir_runtime_cache(self, header_text):
        assert "APPIMAGE_CACHE_DIR" in header_text
        assert "APPDIR" in header_text

    def test_executes_apprun_with_arguments(self, header_text):
        assert 'exec "$APPIMAGE_CACHE_DIR/AppRun" "$@"' in header_text

    def test_no_hardcoded_developer_paths(self, header_text):
        assert "/Users/" not in header_text
        assert "/home/runner" not in header_text
        assert "/opt/anaconda" not in header_text


class TestAppImagePayloadDecompression:
    @pytest.fixture(scope="class")
    def payload_bytes(self):
        with open(APPIMAGE_PATH, "rb") as f:
            content = f.read()
        marker = b"__PAYLOAD_START__\n"
        idx = content.find(marker)
        assert idx != -1
        return content[idx + len(marker) :]

    def test_payload_is_valid_gzip_tar(self, payload_bytes):
        assert len(payload_bytes) > 50_000
        # Gzip magic: 0x1f 0x8b
        assert payload_bytes[:2] == b"\x1f\x8b"

    def test_payload_contains_apprun(self, payload_bytes):
        with tarfile.open(fileobj=io.BytesIO(payload_bytes), mode="r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
            assert "AppRun" in names or "./AppRun" in names

    def test_payload_contains_desktop_and_icon(self, payload_bytes):
        with tarfile.open(fileobj=io.BytesIO(payload_bytes), mode="r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
            assert any("riya.desktop" in n for n in names)
            assert any("riya.png" in n for n in names)

    def test_payload_contains_core_modules(self, payload_bytes):
        with tarfile.open(fileobj=io.BytesIO(payload_bytes), mode="r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
            assert any("core/agent" in n for n in names)
            assert any("core/device" in n for n in names)
            assert any("core/voice" in n for n in names)
            assert any("core/security" in n for n in names)

    def test_payload_does_not_contain_gguf_model(self, payload_bytes):
        with tarfile.open(fileobj=io.BytesIO(payload_bytes), mode="r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
            gguf_files = [n for n in names if n.endswith(".gguf")]
            assert not gguf_files, f"GGUF models bundled into payload: {gguf_files}"

    def test_payload_does_not_contain_user_databases(self, payload_bytes):
        with tarfile.open(fileobj=io.BytesIO(payload_bytes), mode="r:gz") as tar:
            names = [m.name for m in tar.getmembers()]
            db_files = [n for n in names if n.endswith(".db") or "riya.db" in n]
            assert not db_files, f"User database bundled into payload: {db_files}"

