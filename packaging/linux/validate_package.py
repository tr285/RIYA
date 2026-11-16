"""Package structure and metadata validator for RIYA Linux distribution artifacts.

Validates:
- DEB package: ar archive magic, control.tar.gz, data.tar.gz structure, mandatory paths.
- AppImage: AppDir structure, AppRun executable, desktop file, icon, correct permissions.
- Launcher scripts: root-execution guard, shebang, XDG directory setup.
- Control file: required Debian metadata fields.
- Icon set: all standard hicolor resolutions present.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
from dataclasses import dataclass, field
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass
class ValidationResult:
    name: str
    passed: bool
    detail: str


@dataclass
class PackageValidationReport:
    artifact_path: str
    artifact_type: str  # 'deb' | 'appimage' | 'appdir'
    checks: list[ValidationResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[ValidationResult]:
        return [c for c in self.checks if not c.passed]


def _add(report: PackageValidationReport, name: str, passed: bool, detail: str) -> None:
    report.checks.append(ValidationResult(name=name, passed=passed, detail=detail))


# ------ DEB PACKAGE VALIDATOR ------

def _parse_deb_ar(deb_path: Path) -> dict[str, bytes]:
    """Parse GNU ar archive (.deb) and return entry name -> contents."""
    entries: dict[str, bytes] = {}
    with open(deb_path, "rb") as f:
        magic = f.read(8)
        if magic != b"!<arch>\n":
            raise ValueError(f"Invalid ar magic: {magic!r}")
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


def validate_deb(deb_path: Path) -> PackageValidationReport:
    report = PackageValidationReport(
        artifact_path=str(deb_path),
        artifact_type="deb",
    )

    # 1. File exists and is non-empty
    _add(report, "deb_exists", deb_path.is_file() and deb_path.stat().st_size > 100,
         f"DEB size: {deb_path.stat().st_size:,} bytes" if deb_path.is_file() else "File not found")

    if not deb_path.is_file():
        return report

    # 2. Valid ar magic
    try:
        entries = _parse_deb_ar(deb_path)
        _add(report, "ar_magic_valid", True, f"ar entries: {list(entries.keys())}")
    except Exception as e:
        _add(report, "ar_magic_valid", False, str(e))
        return report

    # 3. Required ar members
    for member in ("debian-binary", "control.tar.gz", "data.tar.gz"):
        _add(report, f"ar_has_{member.replace('.', '_').replace('-', '_')}",
             member in entries, f"Present: {member in entries}")

    # 4. debian-binary version
    if "debian-binary" in entries:
        ver = entries["debian-binary"].decode("ascii").strip()
        _add(report, "debian_binary_version", ver == "2.0", f"debian-binary = '{ver}'")

    # 5. Control fields
    if "control.tar.gz" in entries:
        try:
            with tarfile.open(fileobj=io.BytesIO(entries["control.tar.gz"]), mode="r:gz") as tar:
                control_member = next(
                    (m for m in tar.getmembers() if m.name in ("./control", "control")), None
                )
                if control_member:
                    control_text = tar.extractfile(control_member).read().decode("utf-8")  # type: ignore[union-attr]
                    for field_name in ("Package:", "Version:", "Architecture:", "Maintainer:", "Depends:"):
                        _add(report, f"control_has_{field_name.rstrip(':').lower()}",
                             field_name in control_text, f"'{field_name}' in control")
                    # Must NOT contain model (gguf)
                    _add(report, "control_no_model_ref",
                         ".gguf" not in control_text.lower(),
                         "No GGUF model reference in control")
                else:
                    _add(report, "control_file_found", False, "control file not in control.tar.gz")
        except Exception as e:
            _add(report, "control_tar_valid", False, str(e))

    # 6. Data archive contents
    if "data.tar.gz" in entries:
        try:
            with tarfile.open(fileobj=io.BytesIO(entries["data.tar.gz"]), mode="r:gz") as tar:
                member_names = [m.name for m in tar.getmembers()]

                # Critical paths
                for required_path in (
                    "./opt/riya",
                    "./opt/riya/bin",
                    "./opt/riya/core",
                    "./usr/share/applications",
                ):
                    _add(report, f"data_has_{required_path.replace('./', '').replace('/', '_')}",
                         any(n.startswith(required_path) for n in member_names),
                         f"Path prefix {required_path!r} in data.tar.gz")

                # Must NOT embed model
                has_gguf = any(".gguf" in n for n in member_names)
                _add(report, "data_no_gguf_model", not has_gguf,
                     "No .gguf model file embedded in package" if not has_gguf else
                     "FAIL: GGUF model is embedded in package!")

                # Icon files
                has_icons = any("icons/hicolor" in n for n in member_names)
                _add(report, "data_has_icons", has_icons, "hicolor icon hierarchy present")

        except Exception as e:
            _add(report, "data_tar_valid", False, str(e))

    return report


# ------ APPIMAGE / APPDIR VALIDATOR ------

def validate_appdir(appdir: Path) -> PackageValidationReport:
    report = PackageValidationReport(
        artifact_path=str(appdir),
        artifact_type="appdir",
    )

    _add(report, "appdir_exists", appdir.is_dir(), str(appdir))
    if not appdir.is_dir():
        return report

    # 1. AppRun executable
    apprun = appdir / "AppRun"
    _add(report, "apprun_exists", apprun.is_file(), str(apprun))
    if apprun.is_file():
        apprun_mode = apprun.stat().st_mode
        _add(report, "apprun_executable",
             bool(apprun_mode & stat.S_IXUSR),
             f"AppRun mode: {oct(apprun_mode)}")
        content = apprun.read_text(encoding="utf-8", errors="replace")
        _add(report, "apprun_has_shebang", content.startswith("#!/"), f"First line: {content.splitlines()[0]!r}")
        _add(report, "apprun_sets_xdg", "XDG_DATA_HOME" in content, "Sets XDG_DATA_HOME")
        _add(report, "apprun_root_guard", "id -u" in content, "Root execution guard present")
        _add(report, "apprun_no_hardcoded_paths",
             "/Users/" not in content and "/home/runner" not in content,
             "No developer-machine paths embedded")

    # 2. Desktop file
    desktop = appdir / "riya.desktop"
    _add(report, "desktop_file_exists", desktop.is_file(), str(desktop))
    if desktop.is_file():
        desktop_text = desktop.read_text(encoding="utf-8")
        for required_field in ("Name=RIYA", "Exec=riya", "Icon=riya", "Type=Application"):
            _add(report, f"desktop_has_{required_field.split('=')[0].lower()}",
                 required_field in desktop_text, f"'{required_field}' in desktop file")

    # 3. Icon
    icon = appdir / "riya.png"
    _add(report, "root_icon_exists", icon.is_file(), str(icon))
    if icon.is_file():
        _add(report, "root_icon_has_content", icon.stat().st_size > 500,
             f"Icon size: {icon.stat().st_size} bytes")

    # 4. Application code
    core_dir = appdir / "opt" / "riya" / "core"
    _add(report, "core_app_exists", core_dir.is_dir(), str(core_dir))

    # 5. No GGUF model embedded
    gguf_files = list(appdir.rglob("*.gguf"))
    _add(report, "no_gguf_embedded", len(gguf_files) == 0,
         f"No .gguf in AppDir" if not gguf_files else f"FAIL: {gguf_files}")

    # 6. User data must NOT reside in AppDir
    user_data_markers = ["riya.db", "memories.json"]
    embedded_user_data = [str(f) for m in user_data_markers for f in appdir.rglob(m)]
    _add(report, "no_user_data_embedded", len(embedded_user_data) == 0,
         "No user database inside AppDir" if not embedded_user_data else
         f"FAIL: User data found in AppDir: {embedded_user_data}")

    return report


def validate_appimage(appimage_path: Path) -> PackageValidationReport:
    report = PackageValidationReport(
        artifact_path=str(appimage_path),
        artifact_type="appimage",
    )
    _add(report, "appimage_exists", appimage_path.is_file(), str(appimage_path))
    if not appimage_path.is_file():
        return report

    size_mb = appimage_path.stat().st_size / (1024 * 1024)
    _add(report, "appimage_has_content", appimage_path.stat().st_size > 100_000,
         f"AppImage size: {size_mb:.1f} MB")

    appimage_mode = appimage_path.stat().st_mode
    _add(report, "appimage_executable",
         bool(appimage_mode & stat.S_IXUSR),
         f"AppImage mode: {oct(appimage_mode)}")

    # Check the script header
    with open(appimage_path, "rb") as f:
        header_bytes = f.read(16384)  # read 16 KB to capture full shell script header
    try:
        header_text = header_bytes.decode("utf-8", errors="replace")
    except Exception:
        header_text = ""

    _add(report, "appimage_has_shebang",
         header_text.startswith("#!/"), f"First line: {header_text.splitlines()[0][:60]!r}")
    _add(report, "appimage_has_payload_marker",
         "__PAYLOAD_START__" in header_text, "PAYLOAD_START marker present")
    _add(report, "appimage_root_guard", "id -u" in header_text, "Root guard present in script")
    _add(report, "appimage_sets_xdg_dirs",
         "XDG_DATA_HOME" in header_text, "XDG_DATA_HOME in launcher")
    _add(report, "appimage_no_dev_paths",
         "/Users/" not in header_text and "/home/runner" not in header_text,
         "No developer-machine paths in header")

    return report


# ------ ICON SET VALIDATOR ------

def validate_icons(icon_dir: Path) -> PackageValidationReport:
    report = PackageValidationReport(
        artifact_path=str(icon_dir),
        artifact_type="icons",
    )
    standard_sizes = (16, 32, 48, 64, 128, 256, 512)
    for s in standard_sizes:
        icon_file = icon_dir / f"riya_{s}x{s}.png"
        _add(report, f"icon_{s}x{s}_exists",
             icon_file.is_file() and icon_file.stat().st_size > 100,
             f"{icon_file.name}: {icon_file.stat().st_size if icon_file.is_file() else 'missing'} bytes")

    default_icon = icon_dir / "riya.png"
    _add(report, "icon_default_exists", default_icon.is_file(), str(default_icon))
    return report


def print_validation_report(report: PackageValidationReport) -> None:
    sep = "=" * 68
    status = "PASS" if report.all_passed else f"FAIL ({len(report.failures)} checks failed)"
    print(sep)
    print(f"  Package Validation: {report.artifact_type.upper()} — {status}")
    print(f"  Artifact: {Path(report.artifact_path).name}")
    print(sep)
    for check in report.checks:
        icon = "✓" if check.passed else "✗"
        print(f"  {icon} {check.name:<45} {check.detail[:50]}")
    print(sep)


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[2]
    dist = repo_root / "dist"
    icon_dir = repo_root / "packaging" / "linux" / "icons"

    deb = dist / "riya_1.0.0_amd64.deb"
    appimage = dist / "RIYA-1.0.0-x86_64.AppImage"
    appdir = dist / "RIYA_1.0.0_x86_64.AppDir"

    all_pass = True
    for report in [
        validate_icons(icon_dir),
        validate_deb(deb),
        validate_appdir(appdir),
        validate_appimage(appimage),
    ]:
        print_validation_report(report)
        if not report.all_passed:
            all_pass = False

    sys.exit(0 if all_pass else 1)
