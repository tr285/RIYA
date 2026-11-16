#!/usr/bin/env bash
# Portable double-clickable macOS launcher for RIYA Desktop UI (offline-first).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

find_python() {
    local candidates=()
    if [[ -n "${RIYA_PYTHON:-}" ]]; then
        candidates+=("$RIYA_PYTHON")
    fi
    if [[ -x "$REPO_ROOT/.venv/bin/python3" ]]; then
        candidates+=("$REPO_ROOT/.venv/bin/python3")
    fi
    if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python3" ]]; then
        candidates+=("$VIRTUAL_ENV/bin/python3")
    fi
    if [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python3" ]]; then
        candidates+=("$CONDA_PREFIX/bin/python3")
    fi
    for py in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3" /opt/anaconda3/bin/python3; do
        candidates+=("$py")
    done

    for cand in "${candidates[@]}"; do
        if command -v "$cand" >/dev/null 2>&1 || [[ -x "$cand" ]]; then
            if "$cand" -c "import tkinter" >/dev/null 2>&1; then
                echo "$cand"
                return 0
            fi
        fi
    done
    return 1
}

if ! PY_BIN="$(find_python)"; then
    MSG="RIYA could not find a Python 3 environment with Tkinter GUI support. Install Python 3 with Tk or set RIYA_PYTHON."
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display alert \"RIYA Launch Error\" message \"$MSG\" as critical" >/dev/null 2>&1 || true
    fi
    echo "$MSG" >&2
    exit 1
fi

exec "$PY_BIN" -m core.main --ui "$@"
