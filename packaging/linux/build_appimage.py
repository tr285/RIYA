"""AppImage builder for RIYA Universal Personal AI Assistant.

Assembles a validated Freedesktop-compliant AppDir hierarchy and produces a
portable self-contained Linux AppImage (e.g., `dist/RIYA-1.0.0-x86_64.AppImage`).
Supports native `appimagetool` when installed, as well as a portable self-extracting
AppImage runtime wrapper for cross-platform packaging.
"""

from __future__ import annotations

import argparse
import base64
import gzip
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


def get_default_appimage_arch() -> str:
    """Map machine architecture to AppImage architecture string."""
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    elif machine in {"aarch64", "arm64"}:
        return "aarch64"
    return "x86_64"


def create_appdir(
    output_appdir: Path,
    repo_root: Path,
    version: str,
    arch: str,
) -> Path:
    """Assemble standard AppDir directory hierarchy."""
    if output_appdir.exists():
        shutil.rmtree(output_appdir)
    output_appdir.mkdir(parents=True, exist_ok=True)

    # 1. Base directories
    usr_bin = output_appdir / "usr" / "bin"
    usr_apps = output_appdir / "usr" / "share" / "applications"
    usr_icons = output_appdir / "usr" / "share" / "icons" / "hicolor"
    opt_bin = output_appdir / "opt" / "riya" / "bin"
    opt_core = output_appdir / "opt" / "riya" / "core"

    usr_bin.mkdir(parents=True, exist_ok=True)
    usr_apps.mkdir(parents=True, exist_ok=True)
    usr_icons.mkdir(parents=True, exist_ok=True)
    opt_bin.mkdir(parents=True, exist_ok=True)

    # 2. Copy core application code
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

    # 3. Copy launcher scripts
    launcher_src = repo_root / "packaging" / "linux" / "debian" / "riya-launcher"
    shutil.copy2(launcher_src, opt_bin / "riya")
    (opt_bin / "riya").chmod(0o755)

    apprun_src = repo_root / "packaging" / "linux" / "appimage" / "AppRun"
    shutil.copy2(apprun_src, output_appdir / "AppRun")
    (output_appdir / "AppRun").chmod(0o755)

    # Link /usr/bin/riya -> ../../opt/riya/bin/riya
    try:
        (usr_bin / "riya").symlink_to("../../opt/riya/bin/riya")
    except OSError:
        shutil.copy2(launcher_src, usr_bin / "riya")
        (usr_bin / "riya").chmod(0o755)

    # 4. Copy desktop file to root and usr/share/applications
    desktop_src = repo_root / "packaging" / "linux" / "appimage" / "riya.desktop"
    shutil.copy2(desktop_src, output_appdir / "riya.desktop")
    shutil.copy2(desktop_src, usr_apps / "riya.desktop")
    (output_appdir / "riya.desktop").chmod(0o644)
    (usr_apps / "riya.desktop").chmod(0o644)

    # 5. Icons in root and standard hicolor hierarchy
    icon_pkg_dir = repo_root / "packaging" / "linux" / "icons"
    standard_sizes = (16, 32, 48, 64, 128, 256, 512)
    for s in standard_sizes:
        icon_size_dir = usr_icons / f"{s}x{s}" / "apps"
        icon_size_dir.mkdir(parents=True, exist_ok=True)
        src_icon = icon_pkg_dir / f"riya_{s}x{s}.png"
        if not src_icon.exists():
            from packaging.linux.icons.generate_icons import generate_all_icons
            generate_all_icons(icon_pkg_dir)
        shutil.copy2(src_icon, icon_size_dir / "riya.png")
        (icon_size_dir / "riya.png").chmod(0o644)

    # Root icon
    root_icon = output_appdir / "riya.png"
    shutil.copy2(icon_pkg_dir / "riya_512x512.png", root_icon)
    root_icon.chmod(0o644)

    return output_appdir


def build_appimage(
    *,
    output_dir: str | Path | None = None,
    architecture: str | None = None,
    version: str | None = None,
    keep_appdir: bool = True,
) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    arch = architecture or get_default_appimage_arch()
    ver = version or APP_VERSION
    dist_dir = Path(output_dir).resolve() if output_dir else repo_root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    appdir_path = dist_dir / f"RIYA_{ver}_{arch}.AppDir"
    create_appdir(appdir_path, repo_root, ver, arch)

    appimage_filename = f"RIYA-{ver}-{arch}.AppImage"
    final_appimage_path = dist_dir / appimage_filename

    if final_appimage_path.exists():
        final_appimage_path.unlink()

    # If native appimagetool is available, execute it
    appimagetool = shutil.which("appimagetool")
    if appimagetool:
        env = os.environ.copy()
        env["ARCH"] = arch
        subprocess.run(
            [appimagetool, str(appdir_path), str(final_appimage_path)],
            env=env,
            check=True,
            capture_output=True,
        )
    else:
        # Cross-platform portable self-extracting AppImage builder
        # Packs AppDir into tar.gz payload appended to portable self-extracting bash runtime
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
            for item in sorted(appdir_path.iterdir()):
                tar.add(item, arcname=item.name)
        payload = tar_buf.getvalue()

        # Portable runtime header
        runtime_header = (
            "#!/usr/bin/env bash\n"
            "# RIYA Portable Linux AppImage Executable\n"
            "# Self-contained application payload with automatic AppDir execution\n"
            "set -euo pipefail\n"
            "\n"
            f"APP_NAME=\"RIYA\"\n"
            f"APP_VERSION=\"{ver}\"\n"
            f"APP_ARCH=\"{arch}\"\n"
            "\n"
            "# 1. Guard against unintended root execution\n"
            "if [[ \"$(id -u)\" -eq 0 ]] && [[ \"${ALLOW_ROOT:-0}\" != \"1\" ]]; then\n"
            "    echo \"[SECURITY WARNING] RIYA AppImage should not be run as root.\" >&2\n"
            "    echo \"Run as standard desktop user to preserve personal data isolation.\" >&2\n"
            "    exit 1\n"
            "fi\n"
            "\n"
            "# 2. Establish XDG base directories for user data (host filesystem, NOT inside AppImage)\n"
            "export XDG_DATA_HOME=\"${XDG_DATA_HOME:-$HOME/.local/share}\"\n"
            "export XDG_CACHE_HOME=\"${XDG_CACHE_HOME:-$HOME/.cache}\"\n"
            "export XDG_CONFIG_HOME=\"${XDG_CONFIG_HOME:-$HOME/.config}\"\n"
            "\n"
            "# 3. Establish portable AppImage extraction cache path\n"
            "APPIMAGE_CACHE_DIR=\"$XDG_CACHE_HOME/riya/appimage_runtimes/$APP_VERSION-$APP_ARCH\"\n"
            "\n"
            "SELF_PATH=\"$(cd \"$(dirname \"$0\")\" && pwd)/$(basename \"$0\")\"\n"
            "\n"
            "# 4. Extract payload if not already extracted or outdated\n"
            "if [[ ! -f \"$APPIMAGE_CACHE_DIR/AppRun\" ]] || [[ \"$SELF_PATH\" -nt \"$APPIMAGE_CACHE_DIR/AppRun\" ]]; then\n"
            "    mkdir -p \"$APPIMAGE_CACHE_DIR\"\n"
            "    # Locate PAYLOAD_START line offset\n"
            "    PAYLOAD_LINE=$(awk '/^__PAYLOAD_START__/ {print NR + 1; exit 0; }' \"$SELF_PATH\")\n"
            "    tail -n +\"$PAYLOAD_LINE\" \"$SELF_PATH\" | tar -xz -C \"$APPIMAGE_CACHE_DIR\"\n"
            "fi\n"
            "\n"
            "# 5. Execute AppRun from extracted runtime cache\n"
            "export APPDIR=\"$APPIMAGE_CACHE_DIR\"\n"
            "exec \"$APPIMAGE_CACHE_DIR/AppRun\" \"$@\"\n"
            "\n"
            "exit 0\n"
            "__PAYLOAD_START__\n"
        ).encode("utf-8")

        with open(final_appimage_path, "wb") as f:
            f.write(runtime_header)
            f.write(payload)

        final_appimage_path.chmod(0o755)

    if not keep_appdir and appdir_path.exists():
        shutil.rmtree(appdir_path)

    return final_appimage_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build portable Linux AppImage for RIYA")
    parser.add_argument("--output-dir", type=str, default=None, help="Output destination directory")
    parser.add_argument("--arch", type=str, default=None, help="Target architecture (x86_64, aarch64)")
    parser.add_argument("--version", type=str, default=None, help="Package version string")
    parser.add_argument("--no-appdir", action="store_true", help="Remove AppDir staging directory after build")
    args = parser.parse_args()

    appimage_path = build_appimage(
        output_dir=args.output_dir,
        architecture=args.arch,
        version=args.version,
        keep_appdir=not args.no_appdir,
    )
    print(f"Built AppImage: {appimage_path} ({appimage_path.stat().st_size:,} bytes)")

