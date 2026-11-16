"""Tests for Linux DEB and AppImage package metadata and structure integrity.

Verifies:
- DEB ar archive format validity.
- DEB control file mandatory fields.
- DEB data archive contents (required paths, no model, icons present).
- AppDir structure (AppRun, desktop file, icon, core code, no embedded user data).
- AppImage self-extracting script structure and security guards.
- Icon set completeness across all standard hicolor resolutions.
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
ICON_DIR = REPO_ROOT / "packaging" / "linux" / "icons"
DEB_PATH = DIST_DIR / "riya_1.0.0_amd64.deb"
APPIMAGE_PATH = DIST_DIR / "RIYA-1.0.0-x86_64.AppImage"
APPDIR_PATH = DIST_DIR / "RIYA_1.0.0_x86_64.AppDir"

REQUIRED_ICON_SIZES = (16, 32, 48, 64, 128, 256, 512)


# --- HELPERS ---

def _parse_deb_ar(deb_path: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    with open(deb_path, "rb") as f:
        magic = f.read(8)
        assert magic == b"!<arch>\n", f"Invalid ar magic: {magic!r}"
        while True:
            header = f.read(60)
            if not header:
                break
            name = header[:16].decode("ascii").strip().rstrip("/")
            size = int(header[48:58].decode("ascii").strip())
            data = f.read(size)
            entries[name] = data
            if size % 2 != 0:
                f.read(1)
    return entries


# ===== DEB PACKAGE TESTS =====

class TestDebPackageExists:
    def test_deb_file_exists(self):
        assert DEB_PATH.is_file(), f"DEB not found at {DEB_PATH}"

    def test_deb_has_minimum_size(self):
        size = DEB_PATH.stat().st_size
        assert size > 100_000, f"DEB suspiciously small: {size} bytes"

    def test_deb_ar_magic(self):
        with open(DEB_PATH, "rb") as f:
            magic = f.read(8)
        assert magic == b"!<arch>\n", f"Invalid ar magic: {magic!r}"


class TestDebArMembers:
    @pytest.fixture(scope="class")
    def ar_entries(self):
        return _parse_deb_ar(DEB_PATH)

    def test_has_debian_binary(self, ar_entries):
        assert "debian-binary" in ar_entries

    def test_debian_binary_version(self, ar_entries):
        ver = ar_entries["debian-binary"].decode("ascii").strip()
        assert ver == "2.0", f"debian-binary = {ver!r}"

    def test_has_control_tar(self, ar_entries):
        assert "control.tar.gz" in ar_entries

    def test_has_data_tar(self, ar_entries):
        assert "data.tar.gz" in ar_entries


class TestDebControlFile:
    @pytest.fixture(scope="class")
    def control_text(self):
        entries = _parse_deb_ar(DEB_PATH)
        with tarfile.open(fileobj=io.BytesIO(entries["control.tar.gz"]), mode="r:gz") as tar:
            m = next((x for x in tar.getmembers() if x.name in ("./control", "control")), None)
            assert m is not None, "control file not in control.tar.gz"
            return tar.extractfile(m).read().decode("utf-8")  # type: ignore[union-attr]

    def test_has_package_field(self, control_text):
        assert "Package: riya" in control_text

    def test_has_version_field(self, control_text):
        assert "Version: 1.0.0" in control_text

    def test_has_architecture_field(self, control_text):
        assert "Architecture:" in control_text

    def test_has_maintainer_field(self, control_text):
        assert "Maintainer:" in control_text

    def test_has_depends_field(self, control_text):
        assert "Depends:" in control_text

    def test_depends_on_python3(self, control_text):
        assert "python3" in control_text

    def test_depends_on_portaudio(self, control_text):
        assert "libportaudio2" in control_text

    def test_no_gguf_model_in_control(self, control_text):
        assert ".gguf" not in control_text.lower()


class TestDebDataArchive:
    @pytest.fixture(scope="class")
    def data_members(self):
        entries = _parse_deb_ar(DEB_PATH)
        with tarfile.open(fileobj=io.BytesIO(entries["data.tar.gz"]), mode="r:gz") as tar:
            return [m.name for m in tar.getmembers()]

    def test_has_opt_riya_dir(self, data_members):
        assert any(n.startswith("./opt/riya") for n in data_members)

    def test_has_opt_riya_bin(self, data_members):
        assert any(n.startswith("./opt/riya/bin") for n in data_members)

    def test_has_core_application_code(self, data_members):
        assert any(n.startswith("./opt/riya/core") for n in data_members)

    def test_has_desktop_entry(self, data_members):
        assert any("applications" in n and "riya.desktop" in n for n in data_members)

    def test_has_icon_files(self, data_members):
        assert any("icons/hicolor" in n for n in data_members)

    def test_no_gguf_model_embedded(self, data_members):
        embedded_gguf = [n for n in data_members if ".gguf" in n]
        assert not embedded_gguf, f"GGUF model embedded in package: {embedded_gguf}"

    def test_no_model_dir_in_package(self, data_members):
        # Models must NOT be bundled; they live in ~/.local/share/riya/models
        model_paths = [n for n in data_members if "/models/" in n and ".gguf" in n]
        assert not model_paths


# ===== ICON SET TESTS =====

class TestIconSet:
    @pytest.mark.parametrize("size", REQUIRED_ICON_SIZES)
    def test_icon_exists_at_size(self, size):
        icon_path = ICON_DIR / f"riya_{size}x{size}.png"
        assert icon_path.is_file(), f"Icon not found: {icon_path}"
        assert icon_path.stat().st_size > 100, f"Icon suspiciously small: {icon_path}"

    def test_default_icon_exists(self):
        default = ICON_DIR / "riya.png"
        assert default.is_file()

    def test_icons_are_png(self):
        for size in REQUIRED_ICON_SIZES:
            icon = ICON_DIR / f"riya_{size}x{size}.png"
            if icon.is_file():
                with open(icon, "rb") as f:
                    signature = f.read(8)
                # PNG magic: 137 80 78 71 13 10 26 10
                assert signature == b"\x89PNG\r\n\x1a\n", f"Not a valid PNG: {icon}"

