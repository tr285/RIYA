"""RIYA Intelligent Personal Productivity Subsystem (Phase 6.6).

Provides:
- Personal Task Management (priorities, statuses, deadlines, dependencies)
- Intelligent Reminder Engine (recurring, missed alert recovery, background daemon)
- Calendar & Event Management (schedules, provider abstraction, conflict detection)
- Notes & Personal Knowledge (markdown, categories, tagging, pinning, search)
- Daily Planning & Intelligent Summaries (agenda synthesis, task sequencing)
"""

from __future__ import annotations

from core.productivity.models import (
    CalendarEvent,
    DailyPlan,
    EventRecurrence,
    PersonalNote,
    PersonalTask,
    Reminder,
    ReminderRecurrence,
    ReminderStatus,
    TaskPriority,
    TaskStatus,
)
from core.productivity.calendar import (
    CalendarManager,
    CalendarProvider,
    LocalCalendarProvider,
)
from core.productivity.notes import NotesManager
from core.productivity.planner import DailyPlanner
from core.productivity.reminders import ReminderEngine
from core.productivity.service import ProductivityService
from core.productivity.store import ProductivityStore

__all__ = [
    "PersonalTask",
    "TaskPriority",
    "TaskStatus",
    "Reminder",
    "ReminderRecurrence",
    "ReminderStatus",
    "CalendarEvent",
    "EventRecurrence",
    "PersonalNote",
    "DailyPlan",
    "ProductivityStore",
    "ReminderEngine",
    "CalendarManager",
    "CalendarProvider",
    "LocalCalendarProvider",
    "NotesManager",
    "DailyPlanner",
    "ProductivityService",
]
