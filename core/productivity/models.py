"""Data models for RIYA Intelligent Personal Productivity (Phase 6.6)."""

from __future__ import annotations

from enum import Enum
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReminderStatus(str, Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    SNOOZED = "snoozed"
    CANCELLED = "cancelled"


class ReminderRecurrence(str, Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKDAYS = "weekdays"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class EventRecurrence(str, Enum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PersonalTask:
    """User-scoped to-do / personal productivity task."""

    task_id: str
    user_id: str
    title: str
    description: str = ""
    priority: str = "medium"  # low, medium, high, urgent
    status: str = "pending"   # pending, in_progress, completed, cancelled
    due_date: str | None = None
    category: str = "general"
    recurrence: str | None = None  # daily, weekly, monthly, or None
    dependencies: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None

    @property
    def is_overdue(self) -> bool:
        if not self.due_date or self.status in {TaskStatus.COMPLETED, "completed", TaskStatus.CANCELLED, "cancelled"}:
            return False
        try:
            due_dt = datetime.fromisoformat(self.due_date)
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            return due_dt < datetime.now(timezone.utc)
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any] | tuple) -> PersonalTask:
        if isinstance(row, dict):
            deps = row.get("dependencies_json")
            if isinstance(deps, str):
                try:
                    deps = json.loads(deps)
                except Exception:
                    deps = []
            elif not isinstance(deps, list):
                deps = row.get("dependencies", [])
            return cls(
                task_id=row["task_id"],
                user_id=row["user_id"],
                title=row["title"],
                description=row.get("description", ""),
                priority=row.get("priority", "medium"),
                status=row.get("status", "pending"),
                due_date=row.get("due_date"),
                category=row.get("category", "general"),
                recurrence=row.get("recurrence"),
                dependencies=list(deps or []),
                created_at=row.get("created_at", utc_now_iso()),
                updated_at=row.get("updated_at", utc_now_iso()),
                completed_at=row.get("completed_at"),
            )
        # Tuple row order matching SELECT
        deps = []
        try:
            deps = json.loads(row[9]) if row[9] else []
        except Exception:
            deps = []
        return cls(
            task_id=row[0],
            user_id=row[1],
            title=row[2],
            description=row[3],
            priority=row[4],
            status=row[5],
            due_date=row[6],
            category=row[7],
            recurrence=row[8],
            dependencies=deps,
            created_at=row[10],
            updated_at=row[11],
            completed_at=row[12],
        )


@dataclass
class Reminder:
    """Time-based alert / notification linked to tasks or independent."""

    reminder_id: str
    user_id: str
    title: str
    trigger_at: str
    task_id: str | None = None
    recurring_rule: str | None = None  # daily, weekly, monthly, None
    status: str = "pending"  # pending, triggered, snoozed, dismissed, cancelled
    snooze_count: int = 0
    delivered_at: str | None = None
    created_at: str = field(default_factory=utc_now_iso)

    @property
    def is_due(self) -> bool:
        if self.status not in {ReminderStatus.PENDING, "pending"}:
            return False
        try:
            trig_dt = datetime.fromisoformat(self.trigger_at)
            if trig_dt.tzinfo is None:
                trig_dt = trig_dt.replace(tzinfo=timezone.utc)
            return trig_dt <= datetime.now(timezone.utc)
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any] | tuple) -> Reminder:
        if isinstance(row, dict):
            return cls(
                reminder_id=row["reminder_id"],
                user_id=row["user_id"],
                title=row["title"],
                trigger_at=row["trigger_at"],
                task_id=row.get("task_id"),
                recurring_rule=row.get("recurring_rule"),
                status=row.get("status", "pending"),
                snooze_count=int(row.get("snooze_count", 0)),
                delivered_at=row.get("delivered_at"),
                created_at=row.get("created_at", utc_now_iso()),
            )
        return cls(
            reminder_id=row[0],
            user_id=row[1],
            task_id=row[2],
            title=row[3],
            trigger_at=row[4],
            recurring_rule=row[5],
            status=row[6],
            snooze_count=int(row[7]),
            delivered_at=row[8],
            created_at=row[9],
        )


@dataclass
class CalendarEvent:
    """Scheduled calendar appointment / event."""

    event_id: str
    user_id: str
    title: str
    start_time: str
    end_time: str
    description: str = ""
    location: str = ""
    recurrence: str | None = None
    provider: str = "local"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def overlaps_with(self, other: CalendarEvent) -> bool:
        try:
            s1 = datetime.fromisoformat(self.start_time)
            e1 = datetime.fromisoformat(self.end_time)
            s2 = datetime.fromisoformat(other.start_time)
            e2 = datetime.fromisoformat(other.end_time)
            if s1.tzinfo is None:
                s1 = s1.replace(tzinfo=timezone.utc)
            if e1.tzinfo is None:
                e1 = e1.replace(tzinfo=timezone.utc)
            if s2.tzinfo is None:
                s2 = s2.replace(tzinfo=timezone.utc)
            if e2.tzinfo is None:
                e2 = e2.replace(tzinfo=timezone.utc)
            return max(s1, s2) < min(e1, e2)
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any] | tuple) -> CalendarEvent:
        if isinstance(row, dict):
            return cls(
                event_id=row["event_id"],
                user_id=row["user_id"],
                title=row["title"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                description=row.get("description", ""),
                location=row.get("location", ""),
                recurrence=row.get("recurrence"),
                provider=row.get("provider", "local"),
                created_at=row.get("created_at", utc_now_iso()),
                updated_at=row.get("updated_at", utc_now_iso()),
            )
        return cls(
            event_id=row[0],
            user_id=row[1],
            title=row[2],
            description=row[3],
            start_time=row[4],
            end_time=row[5],
            location=row[6],
            recurrence=row[7],
            provider=row[8],
            created_at=row[9],
            updated_at=row[10],
        )


@dataclass
class PersonalNote:
    """User-scoped markdown note with categories, tags, and entity links."""

    note_id: str
    user_id: str
    title: str
    content: str
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    archived: bool = False
    linked_task_id: str | None = None
    linked_event_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def overlaps_with(self, other: CalendarEvent) -> bool:
        try:
            s1 = datetime.fromisoformat(self.start_time)
            e1 = datetime.fromisoformat(self.end_time)
            s2 = datetime.fromisoformat(other.start_time)
            e2 = datetime.fromisoformat(other.end_time)
            if s1.tzinfo is None:
                s1 = s1.replace(tzinfo=timezone.utc)
            if e1.tzinfo is None:
                e1 = e1.replace(tzinfo=timezone.utc)
            if s2.tzinfo is None:
                s2 = s2.replace(tzinfo=timezone.utc)
            if e2.tzinfo is None:
                e2 = e2.replace(tzinfo=timezone.utc)
            return max(s1, s2) < min(e1, e2)
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any] | tuple) -> PersonalNote:
        if isinstance(row, dict):
            tags = row.get("tags_json")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            elif not isinstance(tags, list):
                tags = row.get("tags", [])
            return cls(
                note_id=row["note_id"],
                user_id=row["user_id"],
                title=row["title"],
                content=row["content"],
                category=row.get("category", "general"),
                tags=list(tags or []),
                pinned=bool(row.get("pinned", False)),
                archived=bool(row.get("archived", False)),
                linked_task_id=row.get("linked_task_id"),
                linked_event_id=row.get("linked_event_id"),
                created_at=row.get("created_at", utc_now_iso()),
                updated_at=row.get("updated_at", utc_now_iso()),
            )
        tags = []
        try:
            tags = json.loads(row[5]) if row[5] else []
        except Exception:
            tags = []
        return cls(
            note_id=row[0],
            user_id=row[1],
            title=row[2],
            content=row[3],
            category=row[4],
            tags=tags,
            pinned=bool(row[6]),
            archived=bool(row[7]),
            linked_task_id=row[8],
            linked_event_id=row[9],
            created_at=row[10],
            updated_at=row[11],
        )


@dataclass
class DailyPlan:
    """Aggregated daily agenda, task priorities, conflict warnings, and summaries."""

    date: str
    tasks_due_today: list[PersonalTask] = field(default_factory=list)
    overdue_tasks: list[PersonalTask] = field(default_factory=list)
    events_today: list[CalendarEvent] = field(default_factory=list)
    reminders_today: list[Reminder] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    suggested_order: list[str] = field(default_factory=list)
    summary_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "tasks_due_today": [t.to_dict() for t in self.tasks_due_today],
            "overdue_tasks": [t.to_dict() for t in self.overdue_tasks],
            "events_today": [e.to_dict() for e in self.events_today],
            "reminders_today": [r.to_dict() for r in self.reminders_today],
            "conflicts": list(self.conflicts),
            "suggested_order": list(self.suggested_order),
            "summary_markdown": self.summary_markdown,
        }
