"""Debian Package (.deb) builder for RIYA Universal Personal AI Assistant.

Produces a compliant Debian binary package for Ubuntu 24.04 LTS and Debian
derivatives with full Freedesktop desktop integration and user data preservation.
Supports both native `dpkg-deb` and pure-Python POSIX `ar` package compilation.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import Any

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.config.settings import APP_NAME, APP_VERSION


def get_default_architecture() -> str:
    """Map machine architecture to Debian architecture string."""
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    elif machine in {"aarch64", "arm64"}:
        return "arm64"
    return "amd64"


def write_ar_archive(output_path: Path, files: list[tuple[str, bytes, int, int]]) -> None:
    """Create a standardized GNU/Debian 'ar' archive in pure Python.
    
    files: list of tuples (filename, content_bytes, mode, mtime)
    """
    with open(output_path, "wb") as f:
        # Magic 8-byte header
        f.write(b"!<arch>\n")
        for filename, content, mode, mtime in files:
            # Identifier: 16 chars, right padded
            name_field = f"{filename}/".ljust(16)[:16].encode("ascii")
            mtime_field = str(int(mtime)).ljust(12)[:12].encode("ascii")
            uid_field = b"0".ljust(6)
            gid_field = b"0".ljust(6)
            mode_field = oct(mode)[2:].rjust(6, "0").ljust(8)[:8].encode("ascii")
            size_field = str(len(content)).ljust(10)[:10].encode("ascii")
            fmagic = b"`\n"

            header = name_field + mtime_field + uid_field + gid_field + mode_field + size_field + fmagic
            if len(header) != 60:
                raise ValueError(f"Invalid ar header length: {len(header)}")
            f.write(header)
            f.write(content)
            # 2-byte alignment padding
            if len(content) % 2 != 0:
                f.write(b"\n")


def build_deb(
    *,
    output_dir: str | Path | None = None,
    architecture: str | None = None,
    version: str | None = None,
    clean_staging: bool = True,
) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    arch = architecture or get_default_architecture()
    ver = version or APP_VERSION
    dist_dir = Path(output_dir).resolve() if output_dir else repo_root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    staging_dir = dist_dir / f"riya_{ver}_{arch}_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 1. Directory Structure
    opt_riya = staging_dir / "opt" / "riya"
    opt_bin = opt_riya / "bin"
    opt_core = opt_riya / "core"
    usr_bin = staging_dir / "usr" / "bin"
    usr_apps = staging_dir / "usr" / "share" / "applications"
    usr_icons_base = staging_dir / "usr" / "share" / "icons" / "hicolor"
    debian_dir = staging_dir / "DEBIAN"

    opt_bin.mkdir(parents=True, exist_ok=True)
    usr_bin.mkdir(parents=True, exist_ok=True)
    usr_apps.mkdir(parents=True, exist_ok=True)
    debian_dir.mkdir(parents=True, exist_ok=True)

    # 2. Copy application core code (exclude caches, tests, git, and native build trees)
    def _ignore_patterns(directory: str, files: list[str]) -> set[str]:
        ignored = set()
        p = Path(directory)
        for f in files:
            if f in {"__pycache__", ".git", ".DS_Store", "tests", "llama.cpp"}:
                ignored.add(f)
            elif f.endswith((".pyc", ".pyo", ".o", ".a", ".gguf")):
                ignored.add(f)
            elif "llama.cpp" in p.parts:
                ignored.add(f)
        return ignored

    shutil.copytree(repo_root / "core", opt_core, ignore=_ignore_patterns)

    # 3. Copy launcher script
    launcher_src = repo_root / "packaging" / "linux" / "debian" / "riya-launcher"
    launcher_dest = opt_bin / "riya"
    shutil.copy2(launcher_src, launcher_dest)
    launcher_dest.chmod(0o755)

    # Link /usr/bin/riya -> /opt/riya/bin/riya
    # Inside staging data.tar.gz, /usr/bin/riya is a symlink or standalone wrapper
    usr_bin_riya = usr_bin / "riya"
    try:
        usr_bin_riya.symlink_to("/opt/riya/bin/riya")
    except OSError:
        # Fallback if symlink fails in filesystem
        shutil.copy2(launcher_src, usr_bin_riya)
        usr_bin_riya.chmod(0o755)

    # 4. Desktop entry
    desktop_src = repo_root / "packaging" / "linux" / "appimage" / "riya.desktop"
    shutil.copy2(desktop_src, usr_apps / "riya.desktop")
    (usr_apps / "riya.desktop").chmod(0o644)

    # 5. Icons installation across standard resolutions
    icon_pkg_dir = repo_root / "packaging" / "linux" / "icons"
    standard_sizes = (16, 32, 48, 64, 128, 256, 512)
    for s in standard_sizes:
        icon_size_dir = usr_icons_base / f"{s}x{s}" / "apps"
        icon_size_dir.mkdir(parents=True, exist_ok=True)
        src_icon = icon_pkg_dir / f"riya_{s}x{s}.png"
        if not src_icon.exists():
            from packaging.linux.icons.generate_icons import generate_all_icons
            generate_all_icons(icon_pkg_dir)
        shutil.copy2(src_icon, icon_size_dir / "riya.png")
        (icon_size_dir / "riya.png").chmod(0o644)

    # 6. Calculate Installed-Size in KiB
    total_bytes = 0
    for root, _, files in os.walk(staging_dir):
        if "DEBIAN" in Path(root).parts:
            continue
        for f in files:
            fp = Path(root) / f
            if not fp.is_symlink():
                total_bytes += fp.stat().st_size
    installed_size_kib = max(1, total_bytes // 1024)

    # 7. Generate DEBIAN/control
    control_content = (
        f"Package: riya\n"
        f"Version: {ver}\n"
        f"Section: utils\n"
        f"Priority: optional\n"
        f"Architecture: {arch}\n"
        f"Installed-Size: {installed_size_kib}\n"
        f"Maintainer: Tukaram <developer@riya.ai>\n"
        f"Depends: python3 (>= 3.10), python3-tk, python3-pil, python3-numpy, python3-psutil, libportaudio2, libsndfile1, libgomp1, espeak-ng | piper, libnotify-bin\n"
        f"Recommends: wl-clipboard | xclip\n"
        f"Homepage: https://riya.ai\n"
        f"Description: Universal offline-first personal AI assistant\n"
        f" RIYA is an offline-first personal AI assistant providing private local neural\n"
        f" intelligence, speech recognition, speech synthesis, and desktop OS integration.\n"
        f" Powered by local Qwen3 GGUF inference, faster-whisper STT, Silero VAD, and\n"
        f" multi-backend Linux TTS without cloud dependencies or external telemetry.\n"
    )
    (debian_dir / "control").write_text(control_content, encoding="utf-8")
    (debian_dir / "control").chmod(0o644)

    # 8. Copy maintainer scripts (postinst, prerm, postrm)
    for script_name in ("postinst", "prerm", "postrm"):
        script_src = repo_root / "packaging" / "linux" / "debian" / script_name
        if script_src.exists():
            script_dest = debian_dir / script_name
            shutil.copy2(script_src, script_dest)
            script_dest.chmod(0o755)

    # 9. Generate DEBIAN/md5sums
    md5_lines = []
    for root, _, files in sorted(os.walk(staging_dir)):
        if "DEBIAN" in Path(root).parts:
            continue
        for f in sorted(files):
            fp = Path(root) / f
            if not fp.is_symlink():
                rel = fp.relative_to(staging_dir)
                digest = hashlib.md5(fp.read_bytes()).hexdigest()
                md5_lines.append(f"{digest}  {rel}")
    (debian_dir / "md5sums").write_text("\n".join(md5_lines) + "\n", encoding="utf-8")
    (debian_dir / "md5sums").chmod(0o644)

    # 10. Compile .deb package
    deb_filename = f"riya_{ver}_{arch}.deb"
    final_deb_path = dist_dir / deb_filename
    if final_deb_path.exists():
        final_deb_path.unlink()

    # Try dpkg-deb first if available
    dpkg_deb = shutil.which("dpkg-deb")
    if dpkg_deb:
        subprocess.run(
            [dpkg_deb, "--build", "--root-owner-group", str(staging_dir), str(final_deb_path)],
            check=True,
            capture_output=True,
        )
    else:
        # Pure-Python POSIX 'ar' package generation
        # Step A: debian-binary
        debian_binary = b"2.0\n"

        # Step B: control.tar.gz
        control_buf = io.BytesIO()
        with tarfile.open(fileobj=control_buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
            for item in sorted(debian_dir.iterdir()):
                arcname = f"./{item.name}"
                tar.add(item, arcname=arcname)
        control_tar_gz = control_buf.getvalue()

        # Step C: data.tar.gz
        data_buf = io.BytesIO()
        with tarfile.open(fileobj=data_buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
            for item in sorted(staging_dir.iterdir()):
                if item.name == "DEBIAN":
                    continue
                tar.add(item, arcname=f"./{item.name}")
        data_tar_gz = data_buf.getvalue()

        # Step D: Assemble into .deb archive
        ar_files = [
            ("debian-binary", debian_binary, 0o644, 0),
            ("control.tar.gz", control_tar_gz, 0o644, 0),
            ("data.tar.gz", data_tar_gz, 0o644, 0),
        ]
        write_ar_archive(final_deb_path, ar_files)

    if clean_staging and staging_dir.exists():
        shutil.rmtree(staging_dir)

    return final_deb_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Debian package for RIYA")
    parser.add_argument("--output-dir", type=str, default=None, help="Output destination directory")
    parser.add_argument("--arch", type=str, default=None, help="Target architecture (amd64, arm64)")
    parser.add_argument("--version", type=str, default=None, help="Package version string")
    parser.add_argument("--keep-staging", action="store_true", help="Keep intermediate staging directory")
    args = parser.parse_args()

    deb_path = build_deb(
        output_dir=args.output_dir,
        architecture=args.arch,
        version=args.version,
        clean_staging=not args.keep_staging,
    )
    print(f"Built DEB package: {deb_path} ({deb_path.stat().st_size:,} bytes)")
