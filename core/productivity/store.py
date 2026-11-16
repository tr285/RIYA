"""Persistent, user-isolated SQLite storage for RIYA Personal Productivity (Phase 6.6)."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.config.settings import PROJECT_ROOT
from core.identity.models import AuthenticatedIdentity
from core.productivity.models import (
    CalendarEvent,
    PersonalNote,
    PersonalTask,
    Reminder,
    utc_now_iso,
)

logger = logging.getLogger("riya.productivity.store")

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "riya.db"


def _extract_user_id(identity: AuthenticatedIdentity | str) -> str:
    """Extract validated user_id from identity object or raw ID string."""
    if isinstance(identity, AuthenticatedIdentity):
        uid = identity.user_id
    elif isinstance(identity, str):
        uid = identity.strip()
    else:
        raise ValueError("identity must be an AuthenticatedIdentity or non-empty string.")
    if not uid:
        raise ValueError("User ID cannot be empty.")
    return uid


class ProductivityStore:
    """Thread-safe, user-isolated persistent store for tasks, reminders, events, and notes."""

    def __init__(self, db_path: Any = None):
        if db_path is not None:
            if hasattr(db_path, "database_path"):
                self.db_path = Path(db_path.database_path)
            elif hasattr(db_path, "db_path"):
                self.db_path = Path(db_path.db_path)
            elif isinstance(db_path, (str, Path)):
                self.db_path = Path(db_path)
            else:
                self.db_path = DEFAULT_DB_PATH
        else:
            self.db_path = DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _get_connection(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30.0)
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        return con

    def _ensure_tables(self) -> None:
        with self._get_connection() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS personal_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'cancelled')),
                    due_date TEXT,
                    category TEXT NOT NULL DEFAULT 'general',
                    recurrence TEXT,
                    dependencies_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_personal_tasks_user_status
                ON personal_tasks (user_id, status)
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_personal_tasks_user_due
                ON personal_tasks (user_id, due_date)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES personal_tasks(task_id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    trigger_at TEXT NOT NULL,
                    recurring_rule TEXT,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'triggered', 'snoozed', 'dismissed', 'cancelled')),
                    snooze_count INTEGER NOT NULL DEFAULT 0,
                    delivered_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_user_trigger
                ON reminders (user_id, trigger_at, status)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    recurrence TEXT,
                    provider TEXT NOT NULL DEFAULT 'local',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_calendar_events_user_time
                ON calendar_events (user_id, start_time, end_time)
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS personal_notes (
                    note_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT 'general',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
                    linked_task_id TEXT REFERENCES personal_tasks(task_id) ON DELETE SET NULL,
                    linked_event_id TEXT REFERENCES calendar_events(event_id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_personal_notes_user_cat
                ON personal_notes (user_id, category)
                """
            )
            con.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_personal_notes_user_pinned
                ON personal_notes (user_id, pinned)
                """
            )
            con.commit()

    def _ensure_user(self, con: sqlite3.Connection, user_id: str) -> None:
        con.execute(
            "INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
            (user_id, user_id, user_id),
        )

    # =========================================================================
    # PERSONAL TASKS
    # =========================================================================
    def create_task(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        *,
        description: str = "",
        priority: str = "medium",
        due_date: str | None = None,
        category: str = "general",
        recurrence: str | None = None,
        dependencies: list[str] | None = None,
    ) -> PersonalTask:
        user_id = _extract_user_id(identity)
        cleaned_title = str(title).strip()
        if not cleaned_title:
            raise ValueError("Task title cannot be empty.")
        if priority not in {"low", "medium", "high", "urgent"}:
            priority = "medium"

        task_id = f"pt_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        deps_json = json.dumps(dependencies or [])

        with self._get_connection() as con:
            self._ensure_user(con, user_id)
            con.execute(
                """
                INSERT INTO personal_tasks (
                    task_id, user_id, title, description, priority, status,
                    due_date, category, recurrence, dependencies_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    user_id,
                    cleaned_title,
                    str(description).strip(),
                    priority,
                    due_date,
                    str(category).strip().lower() or "general",
                    recurrence,
                    deps_json,
                    now,
                    now,
                ),
            )
            con.commit()

        return PersonalTask(
            task_id=task_id,
            user_id=user_id,
            title=cleaned_title,
            description=str(description).strip(),
            priority=priority,
            status="pending",
            due_date=due_date,
            category=str(category).strip().lower() or "general",
            recurrence=recurrence,
            dependencies=dependencies or [],
            created_at=now,
            updated_at=now,
        )

    def get_task(
        self,
        identity: AuthenticatedIdentity | str,
        task_id: str,
    ) -> PersonalTask | None:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            row = con.execute(
                """
                SELECT task_id, user_id, title, description, priority, status,
                       due_date, category, recurrence, dependencies_json,
                       created_at, updated_at, completed_at
                FROM personal_tasks
                WHERE user_id = ? AND task_id = ?
                """,
                (user_id, task_id),
            ).fetchone()
            if not row:
                return None
            return PersonalTask.from_row(row)

    def list_tasks(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        overdue_only: bool = False,
        limit: int = 100,
    ) -> list[PersonalTask]:
        user_id = _extract_user_id(identity)
        query = """
            SELECT task_id, user_id, title, description, priority, status,
                   due_date, category, recurrence, dependencies_json,
                   created_at, updated_at, completed_at
            FROM personal_tasks
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]

        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category.strip().lower())
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        if overdue_only:
            now_iso = utc_now_iso()
            query += " AND due_date IS NOT NULL AND due_date < ? AND status != 'completed'"
            params.append(now_iso)

        query += " ORDER BY (due_date IS NULL), due_date ASC, created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._get_connection() as con:
            rows = con.execute(query, params).fetchall()
            return [PersonalTask.from_row(r) for r in rows]

    def update_task(
        self,
        identity: AuthenticatedIdentity | str,
        task_id: str,
        **updates: Any,
    ) -> PersonalTask | None:
        user_id = _extract_user_id(identity)
        existing = self.get_task(user_id, task_id)
        if not existing:
            return None

        allowed = {
            "title",
            "description",
            "priority",
            "status",
            "due_date",
            "category",
            "recurrence",
            "dependencies",
        }
        clauses = []
        vals: list[Any] = []
        now = utc_now_iso()

        for k, v in updates.items():
            if k not in allowed:
                continue
            if k == "dependencies":
                clauses.append("dependencies_json = ?")
                vals.append(json.dumps(v or []))
            elif k == "status" and v == "completed" and existing.status != "completed":
                clauses.append("status = ?, completed_at = ?")
                vals.extend(["completed", now])
            elif k == "status" and v != "completed":
                clauses.append("status = ?, completed_at = NULL")
                vals.append(v)
            else:
                clauses.append(f"{k} = ?")
                vals.append(v)

        if not clauses:
            return existing

        clauses.append("updated_at = ?")
        vals.append(now)
        vals.extend([user_id, task_id])

        with self._get_connection() as con:
            con.execute(
                f"UPDATE personal_tasks SET {', '.join(clauses)} WHERE user_id = ? AND task_id = ?",
                vals,
            )
            con.commit()

        return self.get_task(user_id, task_id)

    def complete_task(
        self,
        identity: AuthenticatedIdentity | str,
        task_id: str,
    ) -> PersonalTask | None:
        return self.update_task(identity, task_id, status="completed")

    def reopen_task(
        self,
        identity: AuthenticatedIdentity | str,
        task_id: str,
    ) -> PersonalTask | None:
        return self.update_task(identity, task_id, status="pending")

    def delete_task(
        self,
        identity: AuthenticatedIdentity | str,
        task_id: str,
    ) -> bool:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            cur = con.execute(
                "DELETE FROM personal_tasks WHERE user_id = ? AND task_id = ?",
                (user_id, task_id),
            )
            con.commit()
            return cur.rowcount > 0

    # =========================================================================
    # INTELLIGENT REMINDERS
    # =========================================================================
    def create_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        trigger_at: str,
        *,
        task_id: str | None = None,
        recurring_rule: str | None = None,
    ) -> Reminder:
        user_id = _extract_user_id(identity)
        cleaned_title = str(title).strip()
        if not cleaned_title:
            raise ValueError("Reminder title cannot be empty.")
        if not trigger_at or not str(trigger_at).strip():
            raise ValueError("Reminder trigger_at timestamp cannot be empty.")

        reminder_id = f"rem_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._get_connection() as con:
            self._ensure_user(con, user_id)
            con.execute(
                """
                INSERT INTO reminders (
                    reminder_id, user_id, task_id, title, trigger_at,
                    recurring_rule, status, snooze_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)
                """,
                (
                    reminder_id,
                    user_id,
                    task_id,
                    cleaned_title,
                    str(trigger_at).strip(),
                    recurring_rule,
                    now,
                ),
            )
            con.commit()

        return Reminder(
            reminder_id=reminder_id,
            user_id=user_id,
            task_id=task_id,
            title=cleaned_title,
            trigger_at=str(trigger_at).strip(),
            recurring_rule=recurring_rule,
            status="pending",
            snooze_count=0,
            created_at=now,
        )

    def get_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
    ) -> Reminder | None:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            row = con.execute(
                """
                SELECT reminder_id, user_id, task_id, title, trigger_at,
                       recurring_rule, status, snooze_count, delivered_at, created_at
                FROM reminders
                WHERE user_id = ? AND reminder_id = ?
                """,
                (user_id, reminder_id),
            ).fetchone()
            if not row:
                return None
            return Reminder.from_row(row)

    def list_reminders(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        status: str | None = None,
        due_before: str | None = None,
        limit: int = 100,
    ) -> list[Reminder]:
        user_id = _extract_user_id(identity)
        query = """
            SELECT reminder_id, user_id, task_id, title, trigger_at,
                   recurring_rule, status, snooze_count, delivered_at, created_at
            FROM reminders
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        if due_before:
            query += " AND trigger_at <= ?"
            params.append(due_before)

        query += " ORDER BY trigger_at ASC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._get_connection() as con:
            rows = con.execute(query, params).fetchall()
            return [Reminder.from_row(r) for r in rows]

    def update_reminder_status(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
        status: str,
        *,
        delivered: bool = False,
    ) -> Reminder | None:
        user_id = _extract_user_id(identity)
        now = utc_now_iso()
        deliv_val = now if delivered else None

        with self._get_connection() as con:
            if delivered:
                con.execute(
                    """
                    UPDATE reminders
                    SET status = ?, delivered_at = ?
                    WHERE user_id = ? AND reminder_id = ?
                    """,
                    (status, deliv_val, user_id, reminder_id),
                )
            else:
                con.execute(
                    """
                    UPDATE reminders
                    SET status = ?
                    WHERE user_id = ? AND reminder_id = ?
                    """,
                    (status, user_id, reminder_id),
                )
            con.commit()

        return self.get_reminder(user_id, reminder_id)

    def snooze_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
        minutes: int = 10,
    ) -> Reminder | None:
        user_id = _extract_user_id(identity)
        existing = self.get_reminder(user_id, reminder_id)
        if not existing:
            return None

        # Parse trigger_at or use now if already in the past
        now = datetime.now(timezone.utc)
        try:
            cur_time = datetime.fromisoformat(existing.trigger_at.replace("Z", "+00:00"))
            if cur_time.tzinfo is None:
                cur_time = cur_time.replace(tzinfo=timezone.utc)
        except Exception:
            cur_time = now

        base_time = max(cur_time, now)
        new_trigger = (base_time + timedelta(minutes=max(1, minutes))).isoformat()

        with self._get_connection() as con:
            con.execute(
                """
                UPDATE reminders
                SET status = 'snoozed', trigger_at = ?, snooze_count = snooze_count + 1
                WHERE user_id = ? AND reminder_id = ?
                """,
                (new_trigger, user_id, reminder_id),
            )
            con.commit()

        return self.get_reminder(user_id, reminder_id)

    def delete_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
    ) -> bool:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            cur = con.execute(
                "DELETE FROM reminders WHERE user_id = ? AND reminder_id = ?",
                (user_id, reminder_id),
            )
            con.commit()
            return cur.rowcount > 0

    # =========================================================================
    # CALENDAR EVENTS
    # =========================================================================
    def create_event(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        start_time: str,
        end_time: str,
        *,
        description: str = "",
        location: str = "",
        recurrence: str | None = None,
        provider: str = "local",
    ) -> CalendarEvent:
        user_id = _extract_user_id(identity)
        cleaned_title = str(title).strip()
        if not cleaned_title:
            raise ValueError("Event title cannot be empty.")
        if not start_time or not end_time:
            raise ValueError("Event start_time and end_time cannot be empty.")
        if end_time < start_time:
            raise ValueError("Event end_time cannot be earlier than start_time.")

        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

        with self._get_connection() as con:
            self._ensure_user(con, user_id)
            con.execute(
                """
                INSERT INTO calendar_events (
                    event_id, user_id, title, description, start_time,
                    end_time, location, recurrence, provider, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    user_id,
                    cleaned_title,
                    str(description).strip(),
                    start_time,
                    end_time,
                    str(location).strip(),
                    recurrence,
                    provider,
                    now,
                    now,
                ),
            )
            con.commit()

        return CalendarEvent(
            event_id=event_id,
            user_id=user_id,
            title=cleaned_title,
            description=str(description).strip(),
            start_time=start_time,
            end_time=end_time,
            location=str(location).strip(),
            recurrence=recurrence,
            provider=provider,
            created_at=now,
            updated_at=now,
        )

    def get_event(
        self,
        identity: AuthenticatedIdentity | str,
        event_id: str,
    ) -> CalendarEvent | None:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            row = con.execute(
                """
                SELECT event_id, user_id, title, description, start_time,
                       end_time, location, recurrence, provider, created_at, updated_at
                FROM calendar_events
                WHERE user_id = ? AND event_id = ?
                """,
                (user_id, event_id),
            ).fetchone()
            if not row:
                return None
            return CalendarEvent.from_row(row)

    def list_events(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        start_after: str | None = None,
        end_before: str | None = None,
        limit: int = 100,
    ) -> list[CalendarEvent]:
        user_id = _extract_user_id(identity)
        query = """
            SELECT event_id, user_id, title, description, start_time,
                   end_time, location, recurrence, provider, created_at, updated_at
            FROM calendar_events
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if start_after:
            query += " AND end_time >= ?"
            params.append(start_after)
        if end_before:
            query += " AND start_time <= ?"
            params.append(end_before)

        query += " ORDER BY start_time ASC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._get_connection() as con:
            rows = con.execute(query, params).fetchall()
            return [CalendarEvent.from_row(r) for r in rows]

    def detect_conflicts(
        self,
        identity: AuthenticatedIdentity | str,
        start_time: str,
        end_time: str,
        *,
        exclude_event_id: str | None = None,
    ) -> list[CalendarEvent]:
        user_id = _extract_user_id(identity)
        query = """
            SELECT event_id, user_id, title, description, start_time,
                   end_time, location, recurrence, provider, created_at, updated_at
            FROM calendar_events
            WHERE user_id = ?
              AND start_time < ?
              AND end_time > ?
        """
        params: list[Any] = [user_id, end_time, start_time]
        if exclude_event_id:
            query += " AND event_id != ?"
            params.append(exclude_event_id)

        query += " ORDER BY start_time ASC"

        with self._get_connection() as con:
            rows = con.execute(query, params).fetchall()
            return [CalendarEvent.from_row(r) for r in rows]

    def delete_event(
        self,
        identity: AuthenticatedIdentity | str,
        event_id: str,
    ) -> bool:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            cur = con.execute(
                "DELETE FROM calendar_events WHERE user_id = ? AND event_id = ?",
                (user_id, event_id),
            )
            con.commit()
            return cur.rowcount > 0

    # =========================================================================
    # PERSONAL NOTES & KNOWLEDGE STORE
    # =========================================================================
    def create_note(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        content: str,
        *,
        category: str = "general",
        tags: list[str] | None = None,
        pinned: bool = False,
        linked_task_id: str | None = None,
        linked_event_id: str | None = None,
    ) -> PersonalNote:
        user_id = _extract_user_id(identity)
        cleaned_title = str(title).strip()
        if not cleaned_title:
            raise ValueError("Note title cannot be empty.")

        note_id = f"not_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        tags_json = json.dumps([str(t).strip().lower() for t in (tags or []) if str(t).strip()])

        with self._get_connection() as con:
            self._ensure_user(con, user_id)
            con.execute(
                """
                INSERT INTO personal_notes (
                    note_id, user_id, title, content, category, tags_json,
                    pinned, archived, linked_task_id, linked_event_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    user_id,
                    cleaned_title,
                    str(content),
                    str(category).strip().lower() or "general",
                    tags_json,
                    1 if pinned else 0,
                    linked_task_id,
                    linked_event_id,
                    now,
                    now,
                ),
            )
            con.commit()

        return PersonalNote(
            note_id=note_id,
            user_id=user_id,
            title=cleaned_title,
            content=str(content),
            category=str(category).strip().lower() or "general",
            tags=tags or [],
            pinned=pinned,
            archived=False,
            linked_task_id=linked_task_id,
            linked_event_id=linked_event_id,
            created_at=now,
            updated_at=now,
        )

    def get_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
    ) -> PersonalNote | None:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            row = con.execute(
                """
                SELECT note_id, user_id, title, content, category, tags_json,
                       pinned, archived, linked_task_id, linked_event_id,
                       created_at, updated_at
                FROM personal_notes
                WHERE user_id = ? AND note_id = ?
                """,
                (user_id, note_id),
            ).fetchone()
            if not row:
                return None
            return PersonalNote.from_row(row)

    def list_notes(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        category: str | None = None,
        pinned_only: bool = False,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[PersonalNote]:
        user_id = _extract_user_id(identity)
        query = """
            SELECT note_id, user_id, title, content, category, tags_json,
                   pinned, archived, linked_task_id, linked_event_id,
                   created_at, updated_at
            FROM personal_notes
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if not include_archived:
            query += " AND archived = 0"
        if category:
            query += " AND category = ?"
            params.append(category.strip().lower())
        if pinned_only:
            query += " AND pinned = 1"

        query += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._get_connection() as con:
            rows = con.execute(query, params).fetchall()
            return [PersonalNote.from_row(r) for r in rows]

    def search_notes(
        self,
        identity: AuthenticatedIdentity | str,
        query_text: str,
        *,
        limit: int = 50,
    ) -> list[PersonalNote]:
        user_id = _extract_user_id(identity)
        q = f"%{query_text.strip().lower()}%"
        with self._get_connection() as con:
            rows = con.execute(
                """
                SELECT note_id, user_id, title, content, category, tags_json,
                       pinned, archived, linked_task_id, linked_event_id,
                       created_at, updated_at
                FROM personal_notes
                WHERE user_id = ?
                  AND (LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(category) LIKE ? OR LOWER(tags_json) LIKE ?)
                ORDER BY pinned DESC, updated_at DESC
                LIMIT ?
                """,
                (user_id, q, q, q, q, max(1, int(limit))),
            ).fetchall()
            return [PersonalNote.from_row(r) for r in rows]

    def update_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
        **updates: Any,
    ) -> PersonalNote | None:
        user_id = _extract_user_id(identity)
        existing = self.get_note(user_id, note_id)
        if not existing:
            return None

        allowed = {
            "title",
            "content",
            "category",
            "tags",
            "pinned",
            "archived",
            "linked_task_id",
            "linked_event_id",
        }
        clauses = []
        vals: list[Any] = []
        now = utc_now_iso()

        for k, v in updates.items():
            if k not in allowed:
                continue
            if k == "tags":
                clauses.append("tags_json = ?")
                vals.append(json.dumps([str(t).strip().lower() for t in (v or []) if str(t).strip()]))
            elif k in {"pinned", "archived"}:
                clauses.append(f"{k} = ?")
                vals.append(1 if v else 0)
            else:
                clauses.append(f"{k} = ?")
                vals.append(v)

        if not clauses:
            return existing

        clauses.append("updated_at = ?")
        vals.append(now)
        vals.extend([user_id, note_id])

        with self._get_connection() as con:
            con.execute(
                f"UPDATE personal_notes SET {', '.join(clauses)} WHERE user_id = ? AND note_id = ?",
                vals,
            )
            con.commit()

        return self.get_note(user_id, note_id)

    def delete_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
    ) -> bool:
        user_id = _extract_user_id(identity)
        with self._get_connection() as con:
            cur = con.execute(
                "DELETE FROM personal_notes WHERE user_id = ? AND note_id = ?",
                (user_id, note_id),
            )
            con.commit()
            return cur.rowcount > 0
