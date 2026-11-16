"""User-isolated incremental file index and file operation audit logger backed by SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

from core.identity.context import get_current_execution_context
from core.tools.filesystem.types import FileRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileIndexStore:
    """
    Incremental per-user file metadata index and operation audit store.

    Security & Privacy Guarantees:
    - Never stores private document contents (only file path, size, mtime_ns, extension, category, and optional SHA-256).
    - Every row is strictly scoped to a validated user_id.
    - Indexed paths are always revalidated on disk (checking existence, non-symlink status, and mtime_ns) before use.
    """

    def __init__(self, database: Any):
        self.database = database

    def _connect(self) -> sqlite3.Connection:
        return self.database.connect()

    def upsert_records(
        self,
        user_id: str,
        records: Iterable[FileRecord],
    ) -> int:
        if not user_id or not isinstance(user_id, str):
            return 0
        now = _utc_now()
        rows_to_upsert = []
        for rec in records:
            if rec.type != "file":
                continue
            rows_to_upsert.append(
                (
                    f"fidx_{uuid4().hex}",
                    user_id,
                    rec.path,
                    rec.parent_dir,
                    rec.name,
                    rec.extension,
                    rec.category,
                    rec.mime_type,
                    int(rec.size_bytes),
                    int(rec.mtime_ns),
                    rec.modified_at,
                    rec.created_at,
                    rec.sha256,
                    now,
                )
            )
        if not rows_to_upsert:
            return 0
        conn = self._connect()
        try:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO file_index_entries (
                        entry_id, user_id, resolved_path, parent_dir, file_name,
                        extension, file_category, mime_type, size_bytes, mtime_ns,
                        modified_at, created_at, sha256, indexed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, resolved_path) DO UPDATE SET
                        parent_dir = excluded.parent_dir,
                        file_name = excluded.file_name,
                        extension = excluded.extension,
                        file_category = excluded.file_category,
                        mime_type = excluded.mime_type,
                        size_bytes = excluded.size_bytes,
                        mtime_ns = excluded.mtime_ns,
                        modified_at = excluded.modified_at,
                        created_at = excluded.created_at,
                        sha256 = COALESCE(excluded.sha256, file_index_entries.sha256),
                        indexed_at = excluded.indexed_at
                    """,
                    rows_to_upsert,
                )
            return len(rows_to_upsert)
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def get_cached_sha256(
        self,
        user_id: str,
        resolved_path: str,
        *,
        size_bytes: int,
        mtime_ns: int,
    ) -> str | None:
        if not user_id:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT sha256
                FROM file_index_entries
                WHERE user_id = ? AND resolved_path = ? AND size_bytes = ? AND mtime_ns = ?
                """,
                (user_id, resolved_path, int(size_bytes), int(mtime_ns)),
            ).fetchone()
            if row is not None and row["sha256"]:
                return str(row["sha256"])
            return None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def remove_path(self, user_id: str, resolved_path: str) -> None:
        if not user_id:
            return
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM file_index_entries WHERE user_id = ? AND resolved_path = ?",
                    (user_id, resolved_path),
                )
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    def list_valid_indexed_files(
        self,
        user_id: str,
        *,
        parent_prefix: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return indexed file entries for user_id after revalidating that each file still exists and is not a symlink."""
        if not user_id:
            return []
        conn = self._connect()
        try:
            if parent_prefix:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM file_index_entries
                    WHERE user_id = ? AND (parent_dir = ? OR parent_dir LIKE ?)
                    ORDER BY mtime_ns DESC
                    LIMIT ?
                    """,
                    (user_id, parent_prefix, f"{parent_prefix.rstrip('/')}/%", int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM file_index_entries
                    WHERE user_id = ?
                    ORDER BY mtime_ns DESC
                    LIMIT ?
                    """,
                    (user_id, int(limit)),
                ).fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()

        valid: list[dict[str, Any]] = []
        stale_paths: list[str] = []
        for row in rows:
            path_str = str(row["resolved_path"])
            p = Path(path_str)
            try:
                if p.is_symlink() or not p.is_file():
                    stale_paths.append(path_str)
                    continue
                st = p.stat()
                valid.append(
                    {
                        "name": str(row["file_name"]),
                        "path": path_str,
                        "parent_dir": str(row["parent_dir"]),
                        "extension": str(row["extension"]),
                        "category": str(row["file_category"]),
                        "mime_type": str(row["mime_type"]),
                        "size_bytes": int(st.st_size),
                        "mtime_ns": int(st.st_mtime_ns),
                        "modified_at": str(row["modified_at"]),
                        "created_at": str(row["created_at"]),
                        "sha256": row["sha256"] if int(st.st_mtime_ns) == int(row["mtime_ns"]) else None,
                    }
                )
            except OSError:
                stale_paths.append(path_str)

        for stale in stale_paths:
            self.remove_path(user_id, stale)
        return valid

    def log_operation(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
        operation_type: str,
        source_path: str,
        destination_path: str = "",
        status: str = "completed",
        details: dict[str, Any] | None = None,
    ) -> str | None:
        if not user_id:
            return None
        op_id = f"fop_{uuid4().hex}"
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO file_operation_logs (
                        operation_id, user_id, session_id, operation_type,
                        source_path, destination_path, status, details_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        op_id,
                        user_id,
                        session_id,
                        str(operation_type),
                        str(source_path),
                        str(destination_path or ""),
                        str(status),
                        json.dumps(details or {}, ensure_ascii=False),
                        _utc_now(),
                    ),
                )
            return op_id
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def list_operations(
        self,
        user_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not user_id:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM file_operation_logs
                WHERE user_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                try:
                    details = json.loads(row["details_json"] or "{}")
                except Exception:
                    details = {}
                out.append(
                    {
                        "operation_id": str(row["operation_id"]),
                        "user_id": str(row["user_id"]),
                        "session_id": row["session_id"],
                        "operation_type": str(row["operation_type"]),
                        "source_path": str(row["source_path"]),
                        "destination_path": str(row["destination_path"]),
                        "status": str(row["status"]),
                        "details": details,
                        "created_at": str(row["created_at"]),
                    }
                )
            return out
        except sqlite3.Error:
            return []
        finally:
            conn.close()


def resolve_active_file_index_store() -> tuple[FileIndexStore | None, str | None, str | None]:
    """Return (FileIndexStore, user_id, session_id) from the current ExecutionContext when available."""
    ctx = get_current_execution_context()
    if ctx is None:
        return None, None, None
    user_id = getattr(ctx, "user_id", None)
    session_id = getattr(ctx, "session_id", None)
    mem_mgr = getattr(ctx, "memory_manager", None)
    db = getattr(mem_mgr, "database", None) if mem_mgr is not None else None
    if db is None or not user_id:
        return None, user_id, session_id
    return FileIndexStore(db), str(user_id), str(session_id) if session_id else None
