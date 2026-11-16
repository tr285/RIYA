"""Generate a native standalone double-clickable macOS RIYA.app bundle."""

from __future__ import annotations

import argparse
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys


def build_app_bundle(
    *,
    output_dir: str | Path | None = None,
    python_executable: str | None = None,
    overwrite: bool = True,
    sign: bool = True,
) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    target_parent = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else repo_root / "dist"
    )
    target_parent.mkdir(parents=True, exist_ok=True)
    app_path = target_parent / "RIYA.app"

    if app_path.exists():
        if not overwrite:
            raise FileExistsError(f"Bundle already exists: {app_path}")
        shutil.rmtree(app_path)

    contents_dir = app_path / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy or generate AppIcon.icns
    icon_src = Path(__file__).parent / "AppIcon.icns"
    if not icon_src.exists():
        from apps.macos.generate_icon import generate_icon
        generate_icon(icon_src)
    if icon_src.exists():
        shutil.copy2(icon_src, resources_dir / "AppIcon.icns")

    # 2. Bundle application package into Contents/Resources/app
    app_pkg_dir = resources_dir / "app"
    app_pkg_dir.mkdir(parents=True, exist_ok=True)
    core_dest = app_pkg_dir / "core"
    if core_dest.exists():
        shutil.rmtree(core_dest)

    def _ignore_unneeded(directory: str, files: list[str]) -> set[str]:
        ignored = set()
        p = Path(directory)
        for f in files:
            if f in {"__pycache__", ".git", ".DS_Store"}:
                ignored.add(f)
            elif f.endswith((".pyc", ".pyo", ".o", ".a")):
                ignored.add(f)
            elif "llama.cpp" in p.parts:
                if f in {"docs", "models", "benches", "tests", "pocs", "CMakeFiles"}:
                    ignored.add(f)
        return ignored

    shutil.copytree(repo_root / "core", core_dest, ignore=_ignore_unneeded)

    # 3. Executable launcher script
    py_exec = python_executable or sys.executable
    launcher_path = macos_dir / "RIYA"
    launcher_script = (
        "#!/usr/bin/env bash\n"
        "# Native standalone launcher for RIYA macOS Desktop Assistant\n"
        "set -euo pipefail\n"
        '\n'
        'BIN_DIR="$(cd "$(dirname "$0")" && pwd)"\n'
        'CONTENTS_DIR="$(cd "$BIN_DIR/.." && pwd)"\n'
        'RESOURCES_DIR="$CONTENTS_DIR/Resources"\n'
        f'REPO_FALLBACK="{repo_root}"\n'
        '\n'
        '# 1. Determine Application Source Directory\n'
        'if [[ -d "$RESOURCES_DIR/app/core" ]]; then\n'
        '  APP_CODE_DIR="$RESOURCES_DIR/app"\n'
        'elif [[ -d "$REPO_FALLBACK/core" ]]; then\n'
        '  APP_CODE_DIR="$REPO_FALLBACK"\n'
        'else\n'
        '  APP_CODE_DIR="$RESOURCES_DIR"\n'
        'fi\n'
        '\n'
        '# 2. Establish Standard macOS Application Storage Paths\n'
        'USER_APP_SUPPORT="$HOME/Library/Application Support/RIYA"\n'
        'USER_DATA_DIR="$USER_APP_SUPPORT/data"\n'
        'USER_MODELS_DIR="$USER_APP_SUPPORT/models"\n'
        'USER_LOGS_DIR="$HOME/Library/Logs/RIYA"\n'
        'USER_CACHE_DIR="$HOME/Library/Caches/RIYA"\n'
        '\n'
        'mkdir -p "$USER_DATA_DIR" "$USER_MODELS_DIR" "$USER_LOGS_DIR" "$USER_CACHE_DIR"\n'
        '\n'
        'export RIYA_APP_SUPPORT_DIR="$USER_APP_SUPPORT"\n'
        'export RIYA_DATA_DIR="$USER_DATA_DIR"\n'
        'export RIYA_MODELS_DIR="$USER_MODELS_DIR"\n'
        'export RIYA_LOGS_DIR="$USER_LOGS_DIR"\n'
        'export RIYA_CACHE_DIR="$USER_CACHE_DIR"\n'
        'export RIYA_BUNDLE_APP="1"\n'
        'export PYTHONPATH="$APP_CODE_DIR${PYTHONPATH:+:$PYTHONPATH}"\n'
        'export PYTHONUNBUFFERED=1\n'
        'export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES\n'
        f'DEFAULT_PY="{py_exec}"\n'
        '\n'
        '# 3. Locate Python Runtime\n'
        'find_python() {\n'
        '  if [[ -n "${RIYA_PYTHON:-}" && -x "$RIYA_PYTHON" ]]; then echo "$RIYA_PYTHON"; return 0; fi\n'
        '  if [[ -x "$RESOURCES_DIR/runtime/bin/python3" ]]; then echo "$RESOURCES_DIR/runtime/bin/python3"; return 0; fi\n'
        '  if [[ -x "$DEFAULT_PY" ]]; then echo "$DEFAULT_PY"; return 0; fi\n'
        '  if [[ -x "$REPO_FALLBACK/.venv/bin/python3" ]]; then echo "$REPO_FALLBACK/.venv/bin/python3"; return 0; fi\n'
        '  for cand in /opt/anaconda3/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/miniconda3/bin/python3" /usr/bin/python3; do\n'
        '    if [[ -x "$cand" ]]; then echo "$cand"; return 0; fi\n'
        '  done\n'
        '  command -v python3 || true\n'
        '}\n'
        'PY_BIN="$(find_python)"\n'
        'if [[ -z "${PY_BIN:-}" || ! -x "$PY_BIN" ]]; then\n'
        '  osascript -e \'display alert "RIYA Launch Error" message "Compatible Python 3 runtime not found on this Mac." as critical\' || true\n'
        '  exit 1\n'
        'fi\n'
        '\n'
        'cd "$APP_CODE_DIR"\n'
        'exec "$PY_BIN" -m core.main --ui "$@"\n'
    )
    launcher_path.write_text(launcher_script, encoding="utf-8")
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 4. macOS Info.plist
    info_plist = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "RIYA",
        "CFBundleExecutable": "RIYA",
        "CFBundleIconFile": "AppIcon",
        "CFBundleIdentifier": "com.riya.assistant",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "RIYA",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSSupportsAutomaticGraphicsSwitching": True,
        "NSMicrophoneUsageDescription": (
            "RIYA uses your microphone locally for speech recognition and voice commands. "
            "Audio is processed entirely on your Mac and is never transmitted to the cloud."
        ),
        "NSSpeechRecognitionUsageDescription": (
            "RIYA uses speech recognition to transcribe your voice commands locally on your Mac."
        ),
    }
    with (contents_dir / "Info.plist").open("wb") as handle:
        plistlib.dump(info_plist, handle)

    # 4. Ad-hoc codesign if available
    if sign and shutil.which("codesign"):
        entitlements_src = Path(__file__).parent / "entitlements.plist"
        cmd = ["codesign", "--force", "--deep", "-s", "-"]
        if entitlements_src.exists():
            cmd.extend(["--entitlements", str(entitlements_src)])
        cmd.append(str(app_path))
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except Exception:
            pass

    return app_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build RIYA.app macOS desktop launcher bundle.")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory in which to create RIYA.app")
    parser.add_argument("--python", type=str, default=None, help="Python executable path for bundle")
    args = parser.parse_args(argv)
    bundle = build_app_bundle(output_dir=args.output_dir, python_executable=args.python)
    print(f"Created macOS app bundle: {bundle}")


if __name__ == "__main__":
    main(sys.argv[1:])
