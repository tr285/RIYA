"""User-isolated sandboxed SQLite query/update tool."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from core.tools.filesystem.files import _path_context

MAX_SQL_CHARS = 2000
MAX_RESULT_ROWS = 50
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}\.db$")
_FORBIDDEN_SQL_PREFIXES = (
    "attach",
    "detach",
    "vacuum",
    "reindex",
)
_ALLOWED_SQL_PREFIXES = (
    "select",
    "with",
    "pragma",
    "create",
    "insert",
    "update",
    "delete",
)
_BLOCKED_SQLITE_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
    }
)


class LocalDatabaseSecurityError(PermissionError):
    """Raised when local DB SQL violates isolation or safety checks."""


def _validate_db_name(db_name: str) -> str:
    if not isinstance(db_name, str) or not db_name.strip():
        raise ValueError("db_name must be a non-empty string ending in .db.")
    cleaned = db_name.strip()
    lower = cleaned.lower()
    if lower in {"riya.db", "main.db"} or ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise LocalDatabaseSecurityError("Access to system or core RIYA database files is denied.")
    if not _DB_NAME_RE.fullmatch(cleaned):
        raise ValueError("db_name must match ^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}\\.db$")
    return cleaned


def _validate_sql_statement(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql must be a non-empty string.")
    cleaned = sql.strip()
    if len(cleaned) > MAX_SQL_CHARS:
        raise ValueError(f"SQL query exceeds {MAX_SQL_CHARS} characters.")
    # Prevent multi-statement stacking (allow optional trailing semicolon only).
    body = cleaned[:-1].strip() if cleaned.endswith(";") else cleaned
    if ";" in body:
        raise LocalDatabaseSecurityError("Multiple stacked SQL statements are not allowed.")
    lower = body.lstrip().lower()
    if lower.startswith(_FORBIDDEN_SQL_PREFIXES) or "load_extension" in lower or "writable_schema" in lower:
        raise LocalDatabaseSecurityError("Blocked privileged SQLite directive.")
    first_word = lower.split(None, 1)[0]
    if first_word not in _ALLOWED_SQL_PREFIXES:
        raise LocalDatabaseSecurityError(f"SQL statement '{first_word}' is not allowed in local sandbox DB.")
    if first_word == "pragma" and not lower.startswith(("pragma table_info(", "pragma table_list")):
        raise LocalDatabaseSecurityError("Only PRAGMA table_info / table_list are allowed.")
    return body


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger_name: str | None) -> int:
    if action in _BLOCKED_SQLITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA:
        if (arg1 or "").lower() not in {"table_info", "table_list"}:
            return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        if (arg2 or "").lower() in {"load_extension", "fts3_tokenizer"}:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def local_db_query(sql: str, db_name: str = "workspace.db") -> str:
    """Run a single validated SQLite query or update in the user's sandboxed workspace DB."""
    safe_db_name = _validate_db_name(db_name)
    safe_sql = _validate_sql_statement(sql)

    # Resolve user's workspace root safely via _path_context (which strips sensitive paths).
    ws_ctx = _path_context(".")
    db_dir = ws_ctx.resolved / "local_databases"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = (db_dir / safe_db_name).resolve()
    if db_dir.resolve() not in db_path.parents:
        raise LocalDatabaseSecurityError("Invalid database target path.")

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.set_authorizer(_authorizer)
    try:
        cursor = conn.execute(safe_sql)
        first_word = safe_sql.lstrip().lower().split(None, 1)[0]
        if first_word in {"select", "with", "pragma"}:
            rows = cursor.fetchmany(MAX_RESULT_ROWS)
            payload = [dict(row) for row in rows]
            return json.dumps({"rows": payload, "count": len(payload)}, ensure_ascii=False)
        conn.commit()
        return json.dumps({"status": "ok", "rows_affected": int(cursor.rowcount)}, ensure_ascii=False)
    finally:
        conn.close()
