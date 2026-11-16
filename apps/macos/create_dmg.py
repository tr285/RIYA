"""Create a distributable macOS DMG installer for RIYA."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def build_dmg(
    *,
    app_bundle_path: Path | None = None,
    output_dmg_path: Path | None = None,
    overwrite: bool = True,
) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    app_path = (
        app_bundle_path.resolve()
        if app_bundle_path
        else repo_root / "dist" / "RIYA.app"
    )

    if not app_path.exists():
        from apps.macos.create_app_bundle import build_app_bundle
        app_path = build_app_bundle(output_dir=repo_root / "dist")

    dist_dir = repo_root / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    dmg_path = (
        output_dmg_path.resolve()
        if output_dmg_path
        else dist_dir / "RIYA-v1.0.0-AppleSilicon.dmg"
    )

    if dmg_path.exists():
        if not overwrite:
            raise FileExistsError(f"DMG already exists: {dmg_path}")
        dmg_path.unlink()

    staging_dir = dist_dir / "dmg_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Copy RIYA.app into staging
        print(f"Copying {app_path.name} to staging...")
        dest_app = staging_dir / "RIYA.app"
        subprocess.run(["cp", "-a", str(app_path), str(dest_app)], check=True)

        # 2. Create symlink to /Applications for standard drag-and-drop
        apps_symlink = staging_dir / "Applications"
        os.symlink("/Applications", apps_symlink)

        # 3. Add Quick Start Readme inside the DMG
        readme_path = staging_dir / "Quick Start & Installation.txt"
        readme_path.write_text(
            "RIYA — Universal Personal AI Assistant (v1.0)\n"
            "=============================================\n\n"
            "INSTALLATION INSTRUCTIONS:\n"
            "1. Drag 'RIYA.app' into the 'Applications' folder shortcut.\n"
            "2. Open 'RIYA' from your Applications folder or Spotlight (Cmd + Space).\n"
            "3. If prompted on first launch, allow microphone permissions for voice mode.\n"
            "4. Follow the built-in welcome wizard to select or verify your local model.\n\n"
            "100% OFFLINE & PRIVATE:\n"
            "- All neural inference runs locally on your Mac's Apple Silicon GPU.\n"
            "- No voice, transcripts, or memories are transmitted to cloud servers.\n\n"
            "Enjoy your private personal AI assistant!\n",
            encoding="utf-8",
        )

        # 4. Invoke hdiutil to create compressed read-only DMG
        hdiutil = shutil.which("hdiutil")
        if not hdiutil:
            raise RuntimeError("hdiutil command not found on this macOS system.")

        print(f"Building disk image: {dmg_path}...")
        cmd = [
            hdiutil,
            "create",
            "-volname",
            "RIYA Installer",
            "-srcfolder",
            str(staging_dir),
            "-ov",
            "-format",
            "UDZO",
            "-imagekey",
            "zlib-level=9",
            str(dmg_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        print(f"Created macOS DMG installer: {dmg_path}")
        return dmg_path

    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create RIYA.dmg installer.")
    parser.add_argument("--app", type=str, default=None, help="Path to existing RIYA.app")
    parser.add_argument("--output", type=str, default=None, help="Target DMG path")
    args = parser.parse_args(argv)

    app_path = Path(args.app) if args.app else None
    out_path = Path(args.output) if args.output else None
    dmg = build_dmg(app_bundle_path=app_path, output_dmg_path=out_path)
    print(f"DMG Size: {dmg.stat().st_size / (1024*1024):.2f} MB")


if __name__ == "__main__":
    main(sys.argv[1:])

