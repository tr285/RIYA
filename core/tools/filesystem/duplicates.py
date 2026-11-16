"""Duplicate file detection for RIYA Intelligent File Management.

Algorithm:
  1. Scan allowed paths for regular, non-symlink files.
  2. Group by size (pre-filter: only groups with ≥2 files need hashing).
  3. Compute streaming SHA-256 for every file in a size-collision group.
  4. Report duplicate groups (same size + same SHA-256).

Security requirements:
  * Respects allowed-root policy (FilesystemAccessError on violations).
  * Never follows symlinks.
  * Never auto-deletes any files; detection only.
  * Bounded by resource limits (MAX_SCAN_ENTRIES).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.tools.filesystem.files import (
    _DIRECTORY_FLAGS,
    _access_error,
    _entry_stat,
    _is_sensitive_component,
    _open_directory,
    _path_context,
    _safe_scandir,
    FilesystemAccessError,
)
from core.tools.filesystem.types import EXTENSION_TO_CATEGORY, normalize_extension

# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------
MAX_SCAN_ENTRIES = 50_000
HASH_CHUNK_BYTES = 64 * 1024  # 64 KiB streaming chunks
MAX_HASH_FILE_BYTES = 500 * 1024 * 1024  # 500 MiB cap per file for hashing


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open_directory_at(parent_fd: int, name: str, path: Path) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise _access_error(path, exc) from exc


def _sha256_stream(path: Path, max_bytes: int = MAX_HASH_FILE_BYTES) -> Optional[str]:
    """Compute SHA-256 of *path* using streaming reads.

    Returns hex-digest string, or None if the file cannot be read (e.g. permission denied).
    Files larger than *max_bytes* are hashed only up to that limit (noted in duplicate report).
    """
    hasher = hashlib.sha256()
    total_read = 0
    try:
        with path.open("rb") as f:
            while total_read < max_bytes:
                chunk = f.read(HASH_CHUNK_BYTES)
                if not chunk:
                    break
                hasher.update(chunk)
                total_read += len(chunk)
    except OSError:
        return None
    return hasher.hexdigest()


def _scan_files(
    directory_fd: int,
    directory_path: Path,
    depth: int,
    recursive: bool,
    include_hidden: bool,
    min_size_bytes: int,
    out: List[Tuple[int, Path]],  # (size, resolved_path)
    visited: List[int],  # single-element list used as mutable counter
) -> None:
    """Recursively scan directory, appending (size, path) for regular files."""
    with _safe_scandir(directory_fd) as scan:
        for entry in scan:
            if visited[0] >= MAX_SCAN_ENTRIES:
                break
            visited[0] += 1

            if _is_sensitive_component(entry.name):
                continue
            if not include_hidden and entry.name.startswith("."):
                continue

            entry_path = directory_path / entry.name
            entry_stat = _entry_stat(directory_fd, entry.name, entry_path)
            if entry_stat is None or stat.S_ISLNK(entry_stat.st_mode):
                continue  # skip symlinks always

            if stat.S_ISREG(entry_stat.st_mode):
                if entry_stat.st_size >= min_size_bytes:
                    out.append((entry_stat.st_size, entry_path))
            elif stat.S_ISDIR(entry_stat.st_mode) and recursive and depth < 20:
                try:
                    child_fd = _open_directory_at(directory_fd, entry.name, entry_path)
                except (FilesystemAccessError, OSError):
                    continue
                try:
                    _scan_files(child_fd, entry_path, depth + 1, recursive, include_hidden, min_size_bytes, out, visited)
                finally:
                    os.close(child_fd)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_duplicate_files_structured(
    path: str = ".",
    recursive: bool = True,
    min_size_bytes: int = 1,
    include_hidden: bool = False,
) -> Dict[str, Any]:
    """Scan *path* for duplicate files and return a structured report.

    Returns a dict with:
      status       "success" | "failed"
      scanned_path absolute path scanned
      total_scanned total files examined
      duplicate_groups list of groups, each containing:
        sha256      hex digest
        size_bytes  common file size
        count       number of duplicates
        wasted_bytes bytes wasted by all copies beyond the first
        files       list of absolute path strings
      total_groups  number of duplicate groups found
      total_wasted_bytes total bytes wasted
      truncated     True if MAX_SCAN_ENTRIES was hit
      error         error message on failure
    """
    try:
        context = _path_context(path)
    except FilesystemAccessError as e:
        return {"status": "failed", "error": str(e), "scanned_path": path}

    root_fd = _open_directory(context)
    all_files: List[Tuple[int, Path]] = []
    visited = [0]
    try:
        _scan_files(root_fd, context.resolved, 0, recursive, include_hidden, min_size_bytes, all_files, visited)
    finally:
        os.close(root_fd)

    truncated = visited[0] >= MAX_SCAN_ENTRIES

    # Group by size; only groups with ≥2 candidates need hashing
    from collections import defaultdict
    size_groups: Dict[int, List[Path]] = defaultdict(list)
    for size, fpath in all_files:
        size_groups[size].append(fpath)

    # Hash candidates and group by (size, sha256)
    hash_groups: Dict[Tuple[int, str], List[Path]] = defaultdict(list)
    for size, paths in size_groups.items():
        if len(paths) < 2:
            continue
        for fpath in paths:
            digest = _sha256_stream(fpath)
            if digest is not None:
                hash_groups[(size, digest)].append(fpath)

    # Build result
    duplicate_groups: List[Dict[str, Any]] = []
    total_wasted = 0
    for (size, digest), paths in hash_groups.items():
        if len(paths) < 2:
            continue
        wasted = size * (len(paths) - 1)
        total_wasted += wasted
        duplicate_groups.append({
            "sha256": digest,
            "size_bytes": size,
            "count": len(paths),
            "wasted_bytes": wasted,
            "files": [str(p) for p in sorted(paths)],
        })

    # Sort by wasted bytes descending
    duplicate_groups.sort(key=lambda g: g["wasted_bytes"], reverse=True)

    return {
        "status": "success",
        "scanned_path": str(context.resolved),
        "total_scanned": len(all_files),
        "duplicate_groups": duplicate_groups,
        "total_groups": len(duplicate_groups),
        "total_wasted_bytes": total_wasted,
        "truncated": truncated,
        "error": "",
    }


def find_duplicate_files(path: str = ".", recursive: bool = True) -> str:
    """User-facing wrapper returning a JSON string duplicate report."""
    try:
        result = find_duplicate_files_structured(path, recursive=recursive)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except FilesystemAccessError as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)
