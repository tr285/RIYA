from pathlib import Path
from core.config.paths import (
    PROJECT_ROOT,
    get_app_support_dir,
    get_cache_dir,
    get_database_path,
    get_logs_dir,
    get_models_dir,
    get_recordings_dir,
    get_user_data_dir,
)


APP_NAME = "RIYA"
APP_VERSION = "1.0.0"
DEVELOPER_NAME = "Tukaram"

OFFLINE_FIRST = True

DATA_DIR = get_user_data_dir()
DATABASE_PATH = get_database_path()
MODELS_DIR = get_models_dir()
LOGS_DIR = get_logs_dir()
CACHE_DIR = get_cache_dir()
RECORDINGS_DIR = get_recordings_dir()

# Filesystem tools may access only these roots. Paths are resolved before this
# policy is checked, so a symlink cannot be used to escape an allowed root.
FILESYSTEM_ALLOWED_ROOTS = (
    PROJECT_ROOT,
    get_app_support_dir(),
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
)

RIYA_IDENTITY = (
    "RIYA is an offline-first, cross-platform personal AI assistant."
)
