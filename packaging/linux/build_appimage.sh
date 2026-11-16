#!/usr/bin/env bash
# Shell wrapper to build portable Linux AppImage for RIYA
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PY_BIN="${RIYA_PYTHON:-python3}"
exec "$PY_BIN" "$SCRIPT_DIR/build_appimage.py" "$@"

