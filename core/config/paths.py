"""Standardized platform path resolver for RIYA.

Handles clean separation between application bundle binaries (read-only)
and mutable user state (databases, models, logs, audio caches) adhering to
macOS Human Interface Guidelines and XDG base directory standards.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_running_in_bundle() -> bool:
    """Return True if executing from inside a packaged macOS .app bundle."""
    exe_str = str(sys.executable).lower()
    file_str = str(Path(__file__).resolve()).lower()
    return ".app/contents/" in exe_str or ".app/contents/" in file_str or os.environ.get("RIYA_BUNDLE_APP") == "1"


def is_development_mode() -> bool:
    """Return True if running in a source checkout with accessible repo data."""
    if os.environ.get("RIYA_ENV", "").lower() in {"production", "prod"}:
        return False
    if is_running_in_bundle():
        return False
    # If running from checkout and repository data directory exists and is writable
    repo_data = PROJECT_ROOT / "data"
    return repo_data.exists() and os.access(repo_data, os.W_OK)


def get_app_support_dir() -> Path:
    """Return the user-level application support directory."""
    override = os.environ.get("RIYA_APP_SUPPORT_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "RIYA"
    elif sys.platform == "win32":
        app_data = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        path = Path(app_data) / "RIYA"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        path = Path(xdg_data) / "riya"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_data_dir() -> Path:
    """Return the directory where mutable application databases and states reside."""
    override = os.environ.get("RIYA_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif is_development_mode():
        path = PROJECT_ROOT / "data"
    else:
        path = get_app_support_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_database_path() -> Path:
    """Return the primary SQLite database path."""
    override = os.environ.get("RIYA_DATABASE_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return get_user_data_dir() / "riya.db"


def get_models_dir() -> Path:
    """Return the primary directory where local GGUF models are stored."""
    override = os.environ.get("RIYA_MODELS_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif is_development_mode() and (PROJECT_ROOT / "models").exists():
        path = PROJECT_ROOT / "models"
    else:
        path = get_app_support_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_model_search_paths() -> list[Path]:
    """Return an ordered list of search roots to locate local GGUF neural models."""
    candidates: list[Path] = [
        get_models_dir(),
        get_app_support_dir() / "models",
        PROJECT_ROOT / "models",
        Path.home() / ".cache" / "huggingface" / "hub",
        Path.home() / "Library" / "Caches" / "llama.cpp",
    ]
    seen: set[str] = set()
    valid_paths: list[Path] = []
    for cand in candidates:
        try:
            resolved = cand.expanduser().resolve()
            key = str(resolved)
            if key not in seen and resolved.exists():
                seen.add(key)
                valid_paths.append(resolved)
        except OSError:
            continue
    return valid_paths


def get_logs_dir() -> Path:
    """Return the directory designated for diagnostic logs."""
    override = os.environ.get("RIYA_LOGS_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Logs" / "RIYA"
    elif is_development_mode():
        path = PROJECT_ROOT / "logs"
    else:
        path = get_app_support_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir() -> Path:
    """Return the temporary cache directory for voice recordings and downloads."""
    override = os.environ.get("RIYA_CACHE_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Caches" / "RIYA"
    elif is_development_mode():
        path = PROJECT_ROOT / "cache"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
        path = Path(xdg_cache) / "riya"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_recordings_dir() -> Path:
    """Return the directory for temporary voice WAV recordings."""
    path = get_cache_dir() / "voice_recordings"
    path.mkdir(parents=True, exist_ok=True)
    return path

