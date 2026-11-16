"""Advanced file discovery and directory insight utilities for RIYA.

Implements natural-language query parsing, safe recursive traversal, and structured results.
All traversal is non-symlink-following and respects RIYA's allowed-root policy.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
from core.tools.filesystem.types import (
    CATEGORY_ALIASES,
    EXTENSION_TO_CATEGORY,
    FileRecord,
    normalize_extension,
)

# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------
MAX_DISCOVERY_DEPTH = 20
MAX_DISCOVERY_ENTRIES = 10_000
MAX_DISCOVERY_MATCHES = 200


# ---------------------------------------------------------------------------
# Natural-language query parser
# ---------------------------------------------------------------------------

def _parse_size_token(token: str) -> int:
    """Parse size specifications like '10MB', '2gb', returning bytes."""
    m = re.fullmatch(r"(?i)(\d+(?:\.\d*)?)\s*(b|kb|mb|gb|tb)", token.strip())
    if not m:
        return 0
    number = float(m.group(1))
    factor = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4}[
        m.group(2).lower()
    ]
    return int(number * factor)


def _parse_date_token(token: str) -> Optional[datetime]:
    """Parse date strings: YYYY-MM-DD or relative keywords (today/yesterday/last week…)."""
    token = token.lower().strip()
    now = datetime.now(timezone.utc)
    if token == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if token == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if token.startswith("last"):
        # Support "last week", "last month", "last year", "last 7 days" etc.
        rest = token[4:].strip()
        m = re.fullmatch(r"(\d+)?\s*(day|days|week|weeks|month|months|year|years)", rest)
        if m:
            n = int(m.group(1)) if m.group(1) else 1
            unit = m.group(2).rstrip("s")
            if unit == "day":
                return now - timedelta(days=n)
            if unit == "week":
                return now - timedelta(weeks=n)
            if unit == "month":
                return now - timedelta(days=30 * n)
            if unit == "year":
                return now - timedelta(days=365 * n)
    # ISO date
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(token, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


def parse_natural_file_query(query: str) -> Dict[str, Any]:
    """Extract structured filters from a free-form user query.

    Recognises:
    * extensions (.pdf, .py …)
    * categories (images, documents, code, audio, video, spreadsheets, data)
    * size constraints (larger than 10MB, smaller than 1GB)
    * date constraints (created after 2023-01-01, modified last week, modified today)
    * keywords (remaining unrecognised words)
    * recursive flag (non-recursive keyword sets recursive=False)

    Returns a dict with keys: extensions, categories, min_size, max_size,
    created_before, created_after, modified_before, modified_after,
    keywords, recursive.
    """
    result: Dict[str, Any] = {
        "extensions": set(),
        "categories": set(),
        "keywords": [],
        "min_size": None,
        "max_size": None,
        "created_before": None,
        "created_after": None,
        "modified_before": None,
        "modified_after": None,
        "recursive": True,
    }

    tokens = re.split(r"[ ,;]+", query.lower())
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if not t:
            i += 1
            continue

        # Non-recursive flag
        if t in {"nonrecursive", "non-recursive"}:
            result["recursive"] = False
            i += 1
            continue

        # Size filters: "larger than 10MB", "smaller than 1GB"
        if t in {"larger", "greater", "bigger"} and i + 2 < len(tokens) and tokens[i + 1] == "than":
            size = _parse_size_token(tokens[i + 2])
            if size:
                result["min_size"] = size
            i += 3
            continue
        if t in {"smaller", "less", "under"} and i + 2 < len(tokens) and tokens[i + 1] == "than":
            size = _parse_size_token(tokens[i + 2])
            if size:
                result["max_size"] = size
            i += 3
            continue

        # Date filters: "created/modified/updated after/before <date>" or "modified today/yesterday/last week"
        if t in {"created", "modified", "updated"}:
            # Try "modified after/before TOKEN"
            if i + 2 < len(tokens) and tokens[i + 1] in {"after", "before"}:
                direction = tokens[i + 1]
                dt = _parse_date_token(tokens[i + 2])
                if dt:
                    key = f"{'created' if t == 'created' else 'modified'}_{direction}"
                    result[key] = dt
                i += 3
                continue
            # Try "modified today / modified yesterday / modified last week"
            if i + 1 < len(tokens):
                next_token = tokens[i + 1]
                # Could be "last" followed by unit
                peek = next_token
                if next_token == "last" and i + 2 < len(tokens):
                    peek = f"last {tokens[i + 2]}"
                dt = _parse_date_token(peek)
                if dt:
                    key = "created_after" if t == "created" else "modified_after"
                    result[key] = dt
                    i += (3 if next_token == "last" and i + 2 < len(tokens) else 2)
                    continue

        # Extension shortcuts: .pdf, .py …
        if t.startswith("."):
            result["extensions"].add(normalize_extension(t))
            i += 1
            continue

        # Category words – normalise via CATEGORY_ALIASES
        normalised_cat = CATEGORY_ALIASES.get(t)
        if normalised_cat:
            result["categories"].add(normalised_cat)
            i += 1
            continue

        # Fallback: plain keyword
        result["keywords"].append(t)
        i += 1

    result["extensions"] = list(result["extensions"])
    result["categories"] = list(result["categories"])
    return result


# ---------------------------------------------------------------------------
# Core traversal
# ---------------------------------------------------------------------------

def _open_directory_at(parent_fd: int, name: str, path: Path) -> int:
    """Open a child directory entry without following symlinks."""
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise _access_error(path, exc) from exc


def _walk_directory(
    directory_fd: int,
    directory_path: Path,
    depth: int,
    filters: Dict[str, Any],
    matches: List[FileRecord],
    visited: int,
) -> Tuple[int, bool]:
    """Recursively walk a directory respecting limits and filters.

    Returns (updated_visited_count, depth_limited_flag).
    """
    depth_limited = False

    with _safe_scandir(directory_fd) as scan:
        for entry in scan:
            if visited >= MAX_DISCOVERY_ENTRIES or len(matches) >= MAX_DISCOVERY_MATCHES:
                break
            visited += 1

            # Reject sensitive paths first
            if _is_sensitive_component(entry.name):
                continue

            # Skip hidden files unless explicitly requested
            if not filters.get("include_hidden", False) and entry.name.startswith("."):
                continue

            entry_path = directory_path / entry.name
            entry_stat = _entry_stat(directory_fd, entry.name, entry_path)
            if entry_stat is None or stat.S_ISLNK(entry_stat.st_mode):
                # Never follow symlinks
                continue

            is_dir = stat.S_ISDIR(entry_stat.st_mode)

            if not is_dir:
                # ----- Apply file filters -----
                ext = entry_path.suffix.lower()

                # Extension filter
                if filters["extensions"] and ext not in filters["extensions"]:
                    continue

                # Category filter (use normalised EXTENSION_TO_CATEGORY values)
                if filters["categories"]:
                    cat = EXTENSION_TO_CATEGORY.get(ext, "other")
                    if cat not in filters["categories"]:
                        continue

                size = entry_stat.st_size
                # Size filters
                if filters["min_size"] is not None and size < filters["min_size"]:
                    continue
                if filters["max_size"] is not None and size > filters["max_size"]:
                    continue

                # Date filters
                mtime = datetime.fromtimestamp(entry_stat.st_mtime, tz=timezone.utc)
                ctime = datetime.fromtimestamp(
                    getattr(entry_stat, "st_birthtime", entry_stat.st_ctime), tz=timezone.utc
                )
                if filters["modified_after"] and mtime < filters["modified_after"]:
                    continue
                if filters["modified_before"] and mtime > filters["modified_before"]:
                    continue
                if filters["created_after"] and ctime < filters["created_after"]:
                    continue
                if filters["created_before"] and ctime > filters["created_before"]:
                    continue

                # Keyword match on filename
                if filters["keywords"]:
                    lowered = entry_path.name.lower()
                    if not any(kw in lowered for kw in filters["keywords"]):
                        continue

                record = FileRecord(
                    name=entry_path.name,
                    path=str(entry_path),
                    parent_dir=str(entry_path.parent),
                    extension=ext,
                    category=EXTENSION_TO_CATEGORY.get(ext, "other"),
                    type="file",
                    size_bytes=size,
                    modified_at=mtime.isoformat(),
                    created_at=ctime.isoformat(),
                    mtime_ns=entry_stat.st_mtime_ns,
                    mime_type="application/octet-stream",
                )
                matches.append(record)

            else:
                # ----- Recurse into subdirectory -----
                if not filters.get("recursive", True):
                    continue
                if depth + 1 >= MAX_DISCOVERY_DEPTH:
                    depth_limited = True
                    continue
                try:
                    child_fd = _open_directory_at(directory_fd, entry.name, entry_path)
                except (FilesystemAccessError, OSError):
                    continue
                try:
                    visited, child_limited = _walk_directory(
                        child_fd, entry_path, depth + 1, filters, matches, visited
                    )
                    if child_limited:
                        depth_limited = True
                finally:
                    os.close(child_fd)

    return visited, depth_limited


# ---------------------------------------------------------------------------
# Public API – advanced search
# ---------------------------------------------------------------------------

def search_files_advanced_structured(
    query: str = "",
    path: str = ".",
    extensions: Optional[Iterable[str]] = None,
    category: Optional[str] = None,
    modified_within_days: Optional[int] = None,
    modified_after: Optional[datetime] = None,
    modified_before: Optional[datetime] = None,
    created_after: Optional[datetime] = None,
    created_before: Optional[datetime] = None,
    min_size_bytes: Optional[int] = None,
    max_size_bytes: Optional[int] = None,
    recursive: bool = True,
    include_hidden: bool = False,
    content_query: Optional[str] = None,  # reserved for future content search
    sort_by: str = "relevance",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Search files with structured filters, optionally seeded from a natural-language query.

    Returns a list of FileRecord dicts (up to *limit*).
    """
    parsed = parse_natural_file_query(query) if query.strip() else {
        "extensions": [], "categories": [], "keywords": [],
        "min_size": None, "max_size": None,
        "created_before": None, "created_after": None,
        "modified_before": None, "modified_after": None,
        "recursive": True,
    }

    # Normalise explicit category argument through the alias table
    explicit_categories: set[str] = set()
    if category:
        normalised = CATEGORY_ALIASES.get(category.lower(), category.lower())
        explicit_categories.add(normalised)

    # Normalise explicit extensions
    explicit_extensions: set[str] = set()
    for ext in (extensions or []):
        normalised = normalize_extension(ext)
        if normalised:
            explicit_extensions.add(normalised)

    filters: Dict[str, Any] = {
        "extensions": list(set(parsed.get("extensions", [])) | explicit_extensions),
        "categories": list(set(parsed.get("categories", [])) | explicit_categories),
        "keywords": parsed.get("keywords", []),
        "min_size": min_size_bytes if min_size_bytes is not None else parsed.get("min_size"),
        "max_size": max_size_bytes if max_size_bytes is not None else parsed.get("max_size"),
        "modified_after": modified_after or parsed.get("modified_after"),
        "modified_before": modified_before or parsed.get("modified_before"),
        "created_after": created_after or parsed.get("created_after"),
        "created_before": created_before or parsed.get("created_before"),
        "recursive": recursive,
        "include_hidden": include_hidden,
    }

    if modified_within_days is not None:
        filters["modified_after"] = datetime.now(timezone.utc) - timedelta(days=modified_within_days)

    context = _path_context(path)
    root_fd = _open_directory(context)
    matches: List[FileRecord] = []
    try:
        _walk_directory(root_fd, context.resolved, 0, filters, matches, visited=0)
    finally:
        os.close(root_fd)

    # Sort results
    if sort_by == "size_desc":
        matches.sort(key=lambda r: r.size_bytes, reverse=True)
    elif sort_by == "size_asc":
        matches.sort(key=lambda r: r.size_bytes)
    elif sort_by == "modified_desc":
        matches.sort(key=lambda r: r.mtime_ns, reverse=True)
    elif sort_by == "modified_asc":
        matches.sort(key=lambda r: r.mtime_ns)
    elif sort_by == "name":
        matches.sort(key=lambda r: r.name.lower())
    # "relevance" keeps natural traversal order

    return [r.to_dict() for r in matches[:max(1, limit)]]


def search_files_advanced(
    query: str = "",
    path: str = ".",
    **kwargs: Any,
) -> str:
    """User-facing wrapper returning JSON string from search_files_advanced_structured."""
    try:
        records = search_files_advanced_structured(query, path, **kwargs)
        return json.dumps(records, ensure_ascii=False, indent=2)
    except FilesystemAccessError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public API – directory insights
# ---------------------------------------------------------------------------

def analyze_directory_insights_structured(
    path: str = ".",
    top_n: int = 10,
) -> Dict[str, Any]:
    """Collect high-level statistics for a single directory (non-recursive).

    Returns total file/dir counts, total bytes, and top-N listings for:
    largest files, most recently modified, oldest, and category distribution.
    """
    context = _path_context(path)
    dir_fd = _open_directory(context)

    total_files = 0
    total_dirs = 0
    total_bytes = 0
    all_records: List[FileRecord] = []
    category_counts: Dict[str, int] = {}

    try:
        with _safe_scandir(dir_fd) as scan:
            for entry in scan:
                if _is_sensitive_component(entry.name):
                    continue
                entry_path = context.resolved / entry.name
                entry_stat = _entry_stat(dir_fd, entry.name, entry_path)
                if entry_stat is None or stat.S_ISLNK(entry_stat.st_mode):
                    continue

                if stat.S_ISDIR(entry_stat.st_mode):
                    total_dirs += 1
                    continue

                if not stat.S_ISREG(entry_stat.st_mode):
                    continue

                total_files += 1
                size = entry_stat.st_size
                total_bytes += size
                ext = entry_path.suffix.lower()
                cat = EXTENSION_TO_CATEGORY.get(ext, "other")
                category_counts[cat] = category_counts.get(cat, 0) + 1

                mtime = datetime.fromtimestamp(entry_stat.st_mtime, tz=timezone.utc)
                ctime = datetime.fromtimestamp(
                    getattr(entry_stat, "st_birthtime", entry_stat.st_ctime), tz=timezone.utc
                )
                rec = FileRecord(
                    name=entry.name,
                    path=str(entry_path),
                    parent_dir=str(context.resolved),
                    extension=ext,
                    category=cat,
                    type="file",
                    size_bytes=size,
                    modified_at=mtime.isoformat(),
                    created_at=ctime.isoformat(),
                    mtime_ns=entry_stat.st_mtime_ns,
                    mime_type="application/octet-stream",
                )
                all_records.append(rec)
    finally:
        os.close(dir_fd)

    # Build top-N lists
    n = max(1, top_n)
    largest = sorted(all_records, key=lambda r: r.size_bytes, reverse=True)[:n]
    most_recent = sorted(all_records, key=lambda r: r.mtime_ns, reverse=True)[:n]
    oldest = sorted(all_records, key=lambda r: r.mtime_ns)[:n]

    return {
        "total_files": total_files,
        "total_directories": total_dirs,
        "total_bytes": total_bytes,
        "largest_files": [r.to_dict() for r in largest],
        "most_recent_files": [r.to_dict() for r in most_recent],
        "oldest_files": [r.to_dict() for r in oldest],
        "category_distribution": category_counts,
    }


def file_insights(path: str = ".", top_n: int = 10) -> str:
    """User-facing wrapper returning JSON string of directory insights."""
    try:
        data = analyze_directory_insights_structured(path, top_n)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except FilesystemAccessError as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
