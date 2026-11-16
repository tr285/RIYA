"""Unified Productivity Service for RIYA (Phase 6.6).

Integrates:
- Personal Task Management
- Intelligent Reminder Engine & Daemon
- Calendar & Event Management
- Notes & Knowledge Store
- Daily Planning & Intelligent Summaries
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.identity.models import AuthenticatedIdentity
from core.productivity.calendar import CalendarManager
from core.productivity.models import (
    CalendarEvent,
    DailyPlan,
    PersonalNote,
    PersonalTask,
    Reminder,
)
from core.productivity.notes import NotesManager
from core.productivity.planner import DailyPlanner
from core.productivity.reminders import ReminderEngine
from core.productivity.store import ProductivityStore

logger = logging.getLogger("riya.productivity.service")


class ProductivityService:
    """Unified coordinator for all personal productivity features in RIYA."""

    def __init__(self, db_path: str | Path | None = None):
        self.store = ProductivityStore(db_path)
        self.reminders = ReminderEngine(self.store)
        self.calendar = CalendarManager(self.store)
        self.notes = NotesManager(self.store)
        self.planner = DailyPlanner(self.store)

    # -------------------------------------------------------------------------
    # Task Management
    # -------------------------------------------------------------------------
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
        return self.store.create_task(
            identity,
            title,
            description=description,
            priority=priority,
            due_date=due_date,
            category=category,
            recurrence=recurrence,
            dependencies=dependencies,
        )

    def get_task(self, identity: AuthenticatedIdentity | str, task_id: str) -> PersonalTask | None:
        return self.store.get_task(identity, task_id)

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
        return self.store.list_tasks(
            identity,
            status=status,
            category=category,
            priority=priority,
            overdue_only=overdue_only,
            limit=limit,
        )

    def complete_task(self, identity: AuthenticatedIdentity | str, task_id: str) -> PersonalTask | None:
        return self.store.complete_task(identity, task_id)

    def reopen_task(self, identity: AuthenticatedIdentity | str, task_id: str) -> PersonalTask | None:
        return self.store.reopen_task(identity, task_id)

    def update_task(self, identity: AuthenticatedIdentity | str, task_id: str, **updates: Any) -> PersonalTask | None:
        return self.store.update_task(identity, task_id, **updates)

    def delete_task(self, identity: AuthenticatedIdentity | str, task_id: str) -> bool:
        return self.store.delete_task(identity, task_id)

    # -------------------------------------------------------------------------
    # Reminder Engine
    # -------------------------------------------------------------------------
    def create_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        trigger_at: str,
        *,
        task_id: str | None = None,
        recurring_rule: str | None = None,
    ) -> Reminder:
        return self.reminders.schedule_reminder(
            identity,
            title,
            trigger_at,
            task_id=task_id,
            recurring_rule=recurring_rule,
        )

    def list_reminders(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        status: str | None = None,
        due_before: str | None = None,
        limit: int = 100,
    ) -> list[Reminder]:
        return self.store.list_reminders(identity, status=status, due_before=due_before, limit=limit)

    def snooze_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
        minutes: int = 10,
    ) -> Reminder | None:
        return self.reminders.snooze(identity, reminder_id, minutes=minutes)

    def cancel_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
    ) -> bool:
        return self.reminders.cancel(identity, reminder_id)

    def start_reminder_daemon(self, identity: AuthenticatedIdentity | str, interval_seconds: float = 5.0) -> None:
        self.reminders.start_daemon(identity, interval_seconds=interval_seconds)

    def stop_reminder_daemon(self) -> None:
        self.reminders.stop_daemon()

    # -------------------------------------------------------------------------
    # Calendar & Events
    # -------------------------------------------------------------------------
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
    ) -> tuple[CalendarEvent, list[CalendarEvent]]:
        return self.calendar.schedule_event(
            identity,
            title,
            start_time,
            end_time,
            description=description,
            location=location,
            recurrence=recurrence,
            check_conflicts=True,
        )

    def list_events(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        start_after: str | None = None,
        end_before: str | None = None,
        limit: int = 100,
    ) -> list[CalendarEvent]:
        return self.calendar.provider.list_events(identity, start_after=start_after, end_before=end_before, limit=limit)

    def get_daily_schedule(self, identity: AuthenticatedIdentity | str, date_str: str | None = None) -> list[CalendarEvent]:
        return self.calendar.get_daily_schedule(identity, date_str=date_str)

    def get_weekly_schedule(self, identity: AuthenticatedIdentity | str, start_date_str: str | None = None) -> list[CalendarEvent]:
        return self.calendar.get_weekly_schedule(identity, start_date_str=start_date_str)

    def delete_event(self, identity: AuthenticatedIdentity | str, event_id: str) -> bool:
        return self.calendar.cancel_event(identity, event_id)

    # -------------------------------------------------------------------------
    # Notes & Knowledge Store
    # -------------------------------------------------------------------------
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
        return self.notes.create_note(
            identity,
            title,
            content,
            category=category,
            tags=tags,
            pinned=pinned,
            linked_task_id=linked_task_id,
            linked_event_id=linked_event_id,
        )

    def get_note(self, identity: AuthenticatedIdentity | str, note_id: str) -> PersonalNote | None:
        return self.notes.get_note(identity, note_id)

    def list_notes(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        category: str | None = None,
        pinned_only: bool = False,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[PersonalNote]:
        return self.notes.list_notes(
            identity,
            category=category,
            pinned_only=pinned_only,
            include_archived=include_archived,
            limit=limit,
        )

    def search_notes(self, identity: AuthenticatedIdentity | str, query: str, *, limit: int = 50) -> list[PersonalNote]:
        return self.notes.search_notes(identity, query, limit=limit)

    def update_note(self, identity: AuthenticatedIdentity | str, note_id: str, **updates: Any) -> PersonalNote | None:
        return self.notes.update_note(identity, note_id, **updates)

    def delete_note(self, identity: AuthenticatedIdentity | str, note_id: str) -> bool:
        return self.notes.delete_note(identity, note_id)

    # -------------------------------------------------------------------------
    # Daily Planning & Summaries
    # -------------------------------------------------------------------------
    def get_daily_plan(self, identity: AuthenticatedIdentity | str, date_str: str | None = None) -> DailyPlan:
        return self.planner.generate_daily_plan(identity, target_date=date_str)

    def get_end_of_day_summary(self, identity: AuthenticatedIdentity | str, date_str: str | None = None) -> str:
        return self.planner.generate_end_of_day_summary(identity, target_date=date_str)
