"""File organization utilities for RIYA Intelligent File Management.

Operations provided:
  * copy_file   – copy a file to a new location (no overwrite unless explicit)
  * move_file   – move/rename a file across the allowed filesystem
  * rename_file – rename a file within the same directory
  * delete_file – delete a single file (requires explicit confirmation in executor)
  * organize_files – bulk organization with dry-run preview and optional execution

Security requirements:
  * Allowed-root policy on BOTH source and destination.
  * Symlink rejection on source and destination.
  * No silent overwrites (conflict detection first).
  * Never performs destructive operations automatically;
    callers (ToolExecutor) must gate on ConfirmationManager tokens.
  * Atomic operations where the OS supports them (shutil.move → os.rename).
  * Post-condition verification after every operation.
  * Operation logs written to FileIndexStore when available.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.tools.filesystem.files import (
    FilesystemAccessError,
    _allowed_roots,
    _is_sensitive_component,
    _is_within_allowed_root,
    _path_context,
    _reject_symlinks_under_roots,
)
from core.tools.filesystem.index_store import resolve_active_file_index_store
from core.tools.filesystem.types import EXTENSION_TO_CATEGORY, normalize_extension


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_and_validate(path: str) -> Path:
    """Resolve *path* through the full allowed-root + symlink policy.

    Returns the resolved absolute Path.
    Raises FilesystemAccessError on any policy violation.
    """
    context = _path_context(path)
    return context.resolved


def _assert_is_regular_file(p: Path, label: str = "source") -> None:
    """Raise FilesystemAccessError if *p* is not a regular file (or if it is a symlink)."""
    if p.is_symlink():
        raise FilesystemAccessError(f"Access denied: {label} is a symlink: {p}")
    if not p.exists():
        raise FilesystemAccessError(f"{label.capitalize()} file not found: {p}")
    if not p.is_file():
        raise FilesystemAccessError(f"{label.capitalize()} path is not a regular file: {p}")


def _assert_destination_free(dst: Path) -> None:
    """Raise FilesystemAccessError if *dst* already exists (conflict guard)."""
    if dst.exists() or dst.is_symlink():
        raise FilesystemAccessError(
            f"Destination already exists (use overwrite=True to allow): {dst}"
        )


def _validate_destination_path(dst: Path) -> None:
    """Validate destination path: within allowed roots, no symlink components, no sensitive names."""
    roots = _allowed_roots()
    _reject_symlinks_under_roots(dst, roots)
    if not _is_within_allowed_root(dst):
        raise FilesystemAccessError(
            f"Access denied: destination is outside allowed roots: {dst}"
        )
    if any(_is_sensitive_component(part) for part in dst.parts):
        raise FilesystemAccessError(
            f"Access denied: destination contains a protected path component: {dst}"
        )


def _log_operation(
    operation_type: str,
    source: Path,
    destination: Optional[Path],
    status: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Write an audit log entry to FileIndexStore if one is available."""
    store, user_id, session_id = resolve_active_file_index_store()
    if store is None or not user_id:
        return
    store.log_operation(
        user_id=user_id,
        session_id=session_id,
        operation_type=operation_type,
        source_path=str(source),
        destination_path=str(destination) if destination else "",
        status=status,
        details=details or {},
    )


def _verify_exists(path: Path) -> bool:
    """Return True if *path* is a regular, non-symlink file."""
    try:
        return path.exists() and not path.is_symlink() and path.is_file()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Single-file operations (structured)
# ---------------------------------------------------------------------------

def copy_file_structured(
    source_path: str,
    destination_path: str,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Copy *source_path* to *destination_path*.

    Returns {"status": "success"|"failed", "source": …, "destination": …, "error": …}.
    """
    try:
        src = _resolve_and_validate(source_path)
        _assert_is_regular_file(src, "source")

        # Destination: resolve parent first, then derive full path
        dst_raw = Path(destination_path).expanduser()
        if not dst_raw.is_absolute():
            roots = _allowed_roots()
            if roots:
                dst_raw = roots[0] / dst_raw
        dst = dst_raw.resolve()
        _validate_destination_path(dst)

        if not overwrite:
            _assert_destination_free(dst)

        # Create parent directories if needed
        dst.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(str(src), str(dst))

        if not _verify_exists(dst):
            raise FilesystemAccessError(f"Copy succeeded but destination is not a regular file: {dst}")

        _log_operation("copy", src, dst, "completed")
        return {"status": "success", "source": str(src), "destination": str(dst), "error": ""}
    except FilesystemAccessError as e:
        return {"status": "failed", "source": source_path, "destination": destination_path, "error": str(e)}
    except Exception as e:
        return {"status": "failed", "source": source_path, "destination": destination_path, "error": str(e)}


def move_file_structured(
    source_path: str,
    destination_path: str,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Move *source_path* to *destination_path*."""
    try:
        src = _resolve_and_validate(source_path)
        _assert_is_regular_file(src, "source")

        dst_raw = Path(destination_path).expanduser()
        if not dst_raw.is_absolute():
            roots = _allowed_roots()
            if roots:
                dst_raw = roots[0] / dst_raw
        dst = dst_raw.resolve()
        _validate_destination_path(dst)

        if not overwrite:
            _assert_destination_free(dst)

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

        if not _verify_exists(dst):
            raise FilesystemAccessError(f"Move succeeded but destination is not a regular file: {dst}")

        _log_operation("move", src, dst, "completed")
        return {"status": "success", "source": str(src), "destination": str(dst), "error": ""}
    except FilesystemAccessError as e:
        return {"status": "failed", "source": source_path, "destination": destination_path, "error": str(e)}
    except Exception as e:
        return {"status": "failed", "source": source_path, "destination": destination_path, "error": str(e)}


def rename_file_structured(
    source_path: str,
    new_name: str,
) -> Dict[str, Any]:
    """Rename a file within its current directory.

    *new_name* must be a bare filename (no directory separators).
    """
    try:
        if os.sep in new_name or "/" in new_name or "\\" in new_name:
            raise FilesystemAccessError(
                "new_name must be a bare filename without directory separators."
            )
        if not new_name.strip():
            raise FilesystemAccessError("new_name cannot be empty.")
        if _is_sensitive_component(new_name):
            raise FilesystemAccessError(f"New name is a protected filename: {new_name!r}")

        src = _resolve_and_validate(source_path)
        _assert_is_regular_file(src, "source")

        dst = src.parent / new_name
        _validate_destination_path(dst)
        _assert_destination_free(dst)

        src.rename(dst)

        if not _verify_exists(dst):
            raise FilesystemAccessError(f"Rename succeeded but destination is not a regular file: {dst}")

        _log_operation("rename", src, dst, "completed")
        return {"status": "success", "source": str(src), "destination": str(dst), "new_name": new_name, "error": ""}
    except FilesystemAccessError as e:
        return {"status": "failed", "source": source_path, "new_name": new_name, "error": str(e)}
    except Exception as e:
        return {"status": "failed", "source": source_path, "new_name": new_name, "error": str(e)}


def delete_file_structured(
    source_path: str,
) -> Dict[str, Any]:
    """Delete a single regular file.

    **Important**: This function performs an irreversible operation.
    The ToolExecutor is responsible for requiring a valid ConfirmationManager
    token and the 'filesystem.delete' permission before invoking this function.
    """
    try:
        src = _resolve_and_validate(source_path)
        _assert_is_regular_file(src, "source")

        _log_operation("delete", src, None, "initiated")
        src.unlink()

        if src.exists():
            raise FilesystemAccessError(f"Delete failed: file still exists at {src}")

        _log_operation("delete", src, None, "completed")
        return {"status": "success", "deleted_path": str(src), "error": ""}
    except FilesystemAccessError as e:
        return {"status": "failed", "source": source_path, "error": str(e)}
    except Exception as e:
        return {"status": "failed", "source": source_path, "error": str(e)}


# ---------------------------------------------------------------------------
# User-facing string wrappers
# ---------------------------------------------------------------------------

def copy_file(source_path: str, destination_path: str, overwrite: bool = False) -> str:
    r = copy_file_structured(source_path, destination_path, overwrite)
    return json.dumps(r, ensure_ascii=False)


def move_file(source_path: str, destination_path: str, overwrite: bool = False) -> str:
    r = move_file_structured(source_path, destination_path, overwrite)
    return json.dumps(r, ensure_ascii=False)


def rename_file(source_path: str, new_name: str) -> str:
    r = rename_file_structured(source_path, new_name)
    return json.dumps(r, ensure_ascii=False)


def delete_file(source_path: str) -> str:
    r = delete_file_structured(source_path)
    return json.dumps(r, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Bulk organization
# ---------------------------------------------------------------------------

def _strategy_destination(
    file_path: Path,
    base_dir: Path,
    strategy: str,
    custom_rules: Dict[str, List[str]],
) -> Tuple[Path, str]:
    """Return (destination_path, category_label) for a given file and strategy."""
    ext = file_path.suffix.lower()
    if strategy in {"by_category", "by_type"}:
        cat = EXTENSION_TO_CATEGORY.get(ext, "other")
        return base_dir / cat / file_path.name, cat
    if strategy == "by_extension":
        label = ext.lstrip(".") or "no_extension"
        return base_dir / label / file_path.name, label
    if strategy == "by_date":
        try:
            mtime = file_path.stat().st_mtime
            label = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m")
        except OSError:
            label = "unknown_date"
        return base_dir / label / file_path.name, label
    if strategy == "custom":
        for folder, extensions in custom_rules.items():
            if ext in [normalize_extension(e) for e in extensions]:
                return base_dir / folder / file_path.name, folder
        return base_dir / "other" / file_path.name, "other"
    raise ValueError(f"Unknown organization strategy: {strategy!r}")


def organize_files_structured(
    path: str = ".",
    strategy: str = "by_category",
    custom_rules_json: str = "",
    execute: bool = False,
) -> Dict[str, Any]:
    """Plan or execute bulk file organization.

    Strategies: "by_category" (default), "by_extension", "by_date", "custom".

    When *execute=False* (default): returns a dry-run preview plan.
    When *execute=True*: executes non-conflicting moves and returns results.

    **Important**: The ToolExecutor is responsible for requiring confirmation
    before calling this with execute=True.
    """
    # Parse custom rules
    custom_rules: Dict[str, List[str]] = {}
    if custom_rules_json and custom_rules_json.strip():
        try:
            custom_rules = json.loads(custom_rules_json)
            if not isinstance(custom_rules, dict):
                custom_rules = {}
        except json.JSONDecodeError:
            custom_rules = {}

    # Validate path
    try:
        context = _path_context(path)
    except FilesystemAccessError as e:
        return {"status": "failed", "error": str(e)}

    base_dir = context.resolved

    # Scan top-level files only (non-recursive for safety)
    plan: List[Dict[str, Any]] = []
    conflict_count = 0
    affected_count = 0

    try:
        entries = list(base_dir.iterdir())
    except OSError as e:
        return {"status": "failed", "error": f"Cannot read directory: {e}"}

    for entry in sorted(entries, key=lambda e: e.name.lower()):
        if entry.is_symlink() or not entry.is_file():
            continue
        if _is_sensitive_component(entry.name):
            continue

        try:
            dst, cat_label = _strategy_destination(entry, base_dir, strategy, custom_rules)
        except (OSError, ValueError):
            continue

        # Validate destination is within allowed roots
        try:
            _validate_destination_path(dst)
        except FilesystemAccessError:
            continue

        # Detect conflicts
        conflict = dst.exists() and not dst.is_symlink() and dst.is_file() and dst != entry
        if conflict:
            conflict_count += 1

        affected_count += 1
        plan.append({
            "source_path": str(entry),
            "destination_path": str(dst),
            "operation": "move",
            "category_or_folder": cat_label,
            "conflict": conflict,
        })

    if not execute:
        return {
            "status": "planned",
            "strategy": strategy,
            "base_path": str(base_dir),
            "total_files": affected_count,
            "conflict_count": conflict_count,
            "plan": plan,
            "error": "",
        }

    # Execute non-conflicting moves
    executed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    rollback_map: List[Dict[str, str]] = []

    for item in plan:
        if item["conflict"]:
            skipped.append({"source": item["source_path"], "reason": "conflict"})
            continue
        src = Path(item["source_path"])
        dst = Path(item["destination_path"])
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            if _verify_exists(dst):
                rollback_map.append({"from": str(dst), "to": str(src)})
                executed.append({"source": str(src), "destination": str(dst), "status": "moved"})
                _log_operation("organize_move", src, dst, "completed", {"strategy": strategy})
            else:
                skipped.append({"source": str(src), "reason": "post-move verification failed"})
        except (OSError, shutil.Error) as e:
            skipped.append({"source": str(src), "reason": str(e)})

    return {
        "status": "completed",
        "strategy": strategy,
        "base_path": str(base_dir),
        "total_planned": affected_count,
        "executed_count": len(executed),
        "skipped_count": len(skipped),
        "conflict_count": conflict_count,
        "executed": executed,
        "skipped": skipped,
        "rollback_map": rollback_map,
        "error": "",
    }


def organize_files(
    path: str = ".",
    strategy: str = "by_category",
    execute: bool = False,
) -> str:
    """User-facing wrapper returning JSON string of the organization plan or result."""
    try:
        result = organize_files_structured(path, strategy=strategy, execute=execute)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except FilesystemAccessError as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)
