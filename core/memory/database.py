import os
import sqlite3
from pathlib import Path

from core.database.migrations import (
    CURRENT_SCHEMA_VERSION,
    apply_migrations,
    get_schema_version,
    rollback_migration,
)


from core.config.settings import DATA_DIR, DATABASE_PATH, PROJECT_ROOT


class MemoryDatabase:
    """SQLite database used by RIYA's local memory and multi-user identity system."""

    def __init__(self, database_path: str | Path = DATABASE_PATH):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _restrict_permissions(self) -> None:
        if self.database_path.exists():
            try:
                os.chmod(self.database_path, 0o600)
            except OSError:
                pass

    def _initialize(self) -> None:
        connection = self.connect()
        try:
            apply_migrations(connection, target_version=CURRENT_SCHEMA_VERSION)
        finally:
            connection.close()
        self._restrict_permissions()

    def schema_version(self) -> int:
        connection = self.connect()
        try:
            return get_schema_version(connection)
        finally:
            connection.close()

    def rollback_to(self, target_version: int = 1) -> list[int]:
        connection = self.connect()
        try:
            return rollback_migration(connection, target_version=target_version)
        finally:
            connection.close()

    def has_registered_users(self) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'users'
                """
            ).fetchone()
            if row is None:
                return False
            user_row = connection.execute(
                "SELECT 1 FROM users LIMIT 1"
            ).fetchone()
            return user_row is not None
        finally:
            connection.close()

    def count_legacy_unclaimed_memories(self) -> int:
        connection = self.connect()
        try:
            cols = {
                row[1]
                for row in connection.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "legacy_unclaimed" not in cols:
                return 0
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM memories
                WHERE user_id IS NULL AND legacy_unclaimed = 1
                """
            ).fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            connection.close()
