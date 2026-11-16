"""Productivity tools for RIYA (Phase 6.6).

Exposes user-isolated personal tasks, reminders, calendar events, notes,
and daily planning tools to the RIYA agent and assistant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.identity.context import get_current_execution_context
from core.productivity.service import ProductivityService

_shared_service: ProductivityService | None = None


def get_productivity_service(db_path: str | Path | None = None) -> ProductivityService:
    """Retrieve or create the shared ProductivityService instance."""
    global _shared_service
    if _shared_service is None or db_path is not None:
        _shared_service = ProductivityService(db_path)
    return _shared_service


def set_productivity_service(service: ProductivityService) -> None:
    """Explicitly inject a ProductivityService instance."""
    global _shared_service
    _shared_service = service


def _resolve_identity() -> str:
    ctx = get_current_execution_context()
    if ctx is not None and ctx.user_id:
        return ctx.user_id
    return "default_user"


# =============================================================================
# Task Management Tools
# =============================================================================

def create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    due_date: str = "",
    category: str = "general",
    recurrence: str = "",
) -> dict[str, Any]:
    """Create a new personal task for the active user."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    due = due_date if due_date else None
    rec = recurrence if recurrence else None
    task = svc.create_task(
        identity,
        title=title,
        description=description,
        priority=priority,
        due_date=due,
        category=category,
        recurrence=rec,
    )
    return {
        "status": "success",
        "message": f"Task '{task.title}' created successfully.",
        "task": task.to_dict(),
    }


def get_task(task_id: str) -> dict[str, Any]:
    """Get details of a specific personal task."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    task = svc.get_task(identity, task_id)
    if task is None:
        return {"status": "error", "error": f"Task '{task_id}' not found."}
    return {"status": "success", "task": task.to_dict()}


def list_tasks(
    status: str = "",
    category: str = "",
    priority: str = "",
    overdue_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """List personal tasks for the active user with optional filters."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    st = status if status else None
    cat = category if category else None
    pri = priority if priority else None
    tasks = svc.list_tasks(
        identity,
        status=st,
        category=cat,
        priority=pri,
        overdue_only=overdue_only,
        limit=limit,
    )
    return {
        "status": "success",
        "count": len(tasks),
        "tasks": [t.to_dict() for t in tasks],
    }


def update_task(
    task_id: str,
    title: str = "",
    description: str = "",
    status: str = "",
    priority: str = "",
    due_date: str = "",
) -> dict[str, Any]:
    """Update attributes of an existing task."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    updates: dict[str, Any] = {}
    if title:
        updates["title"] = title
    if description:
        updates["description"] = description
    if status:
        updates["status"] = status
    if priority:
        updates["priority"] = priority
    if due_date:
        updates["due_date"] = due_date

    updated = svc.update_task(identity, task_id, **updates)
    if updated is None:
        return {"status": "error", "error": f"Task '{task_id}' not found."}
    return {
        "status": "success",
        "message": f"Task '{task_id}' updated.",
        "task": updated.to_dict(),
    }


def complete_task(task_id: str) -> dict[str, Any]:
    """Mark a task as completed."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    task = svc.complete_task(identity, task_id)
    if task is None:
        return {"status": "error", "error": f"Task '{task_id}' not found."}
    return {
        "status": "success",
        "message": f"Task '{task.title}' marked as completed.",
        "task": task.to_dict(),
    }


def reopen_task(task_id: str) -> dict[str, Any]:
    """Reopen a completed or cancelled task."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    task = svc.reopen_task(identity, task_id)
    if task is None:
        return {"status": "error", "error": f"Task '{task_id}' not found."}
    return {
        "status": "success",
        "message": f"Task '{task.title}' reopened.",
        "task": task.to_dict(),
    }


def delete_task(task_id: str) -> dict[str, Any]:
    """Delete a task permanently. Requires explicit confirmation."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    deleted = svc.delete_task(identity, task_id)
    if not deleted:
        return {"status": "error", "error": f"Task '{task_id}' not found."}
    return {"status": "success", "message": f"Task '{task_id}' deleted."}


# =============================================================================
# Reminder Engine Tools
# =============================================================================

def create_reminder(
    title: str,
    trigger_at: str,
    recurring_rule: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    """Schedule a reminder for a specific date and time."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    rec = recurring_rule if recurring_rule else None
    tid = task_id if task_id else None
    reminder = svc.create_reminder(
        identity,
        title=title,
        trigger_at=trigger_at,
        recurring_rule=rec,
        task_id=tid,
    )
    return {
        "status": "success",
        "message": f"Reminder '{reminder.title}' scheduled for {reminder.trigger_at}.",
        "reminder": reminder.to_dict(),
    }


def list_reminders(
    status: str = "",
    due_before: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """List reminders for the active user."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    st = status if status else None
    due = due_before if due_before else None
    reminders = svc.list_reminders(identity, status=st, due_before=due, limit=limit)
    return {
        "status": "success",
        "count": len(reminders),
        "reminders": [r.to_dict() for r in reminders],
    }


def snooze_reminder(reminder_id: str, minutes: int = 10) -> dict[str, Any]:
    """Snooze an active reminder by the specified number of minutes."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    reminder = svc.snooze_reminder(identity, reminder_id, minutes=minutes)
    if reminder is None:
        return {"status": "error", "error": f"Reminder '{reminder_id}' not found."}
    return {
        "status": "success",
        "message": f"Reminder '{reminder.title}' snoozed to {reminder.trigger_at}.",
        "reminder": reminder.to_dict(),
    }


def cancel_reminder(reminder_id: str) -> dict[str, Any]:
    """Cancel a reminder. Requires explicit confirmation."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    cancelled = svc.cancel_reminder(identity, reminder_id)
    if not cancelled:
        return {"status": "error", "error": f"Reminder '{reminder_id}' not found."}
    return {"status": "success", "message": f"Reminder '{reminder_id}' cancelled."}


# =============================================================================
# Calendar & Event Tools
# =============================================================================

def create_calendar_event(
    title: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    recurrence: str = "",
) -> dict[str, Any]:
    """Schedule a calendar event with conflict checking."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    rec = recurrence if recurrence else None
    event, conflicts = svc.create_event(
        identity,
        title=title,
        start_time=start_time,
        end_time=end_time,
        description=description,
        location=location,
        recurrence=rec,
    )
    res: dict[str, Any] = {
        "status": "success",
        "message": f"Event '{event.title}' scheduled from {event.start_time} to {event.end_time}.",
        "event": event.to_dict(),
    }
    if conflicts:
        res["conflicts_detected"] = [c.to_dict() for c in conflicts]
        res["warning"] = f"Warning: {len(conflicts)} conflicting event(s) found."
    return res


def list_calendar_events(
    start_after: str = "",
    end_before: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """List calendar events in a specified date/time range."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    sa = start_after if start_after else None
    eb = end_before if end_before else None
    events = svc.list_events(identity, start_after=sa, end_before=eb, limit=limit)
    return {
        "status": "success",
        "count": len(events),
        "events": [e.to_dict() for e in events],
    }


def get_daily_schedule(date_str: str = "") -> dict[str, Any]:
    """Retrieve full calendar schedule for a given date."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    d = date_str if date_str else None
    events = svc.get_daily_schedule(identity, date_str=d)
    return {
        "status": "success",
        "count": len(events),
        "events": [e.to_dict() for e in events],
    }


def get_weekly_schedule(start_date_str: str = "") -> dict[str, Any]:
    """Retrieve a 7-day calendar schedule starting on a given date."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    d = start_date_str if start_date_str else None
    events = svc.get_weekly_schedule(identity, start_date_str=d)
    return {
        "status": "success",
        "count": len(events),
        "events": [e.to_dict() for e in events],
    }


def delete_calendar_event(event_id: str) -> dict[str, Any]:
    """Delete a calendar event. Requires explicit confirmation."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    deleted = svc.delete_event(identity, event_id)
    if not deleted:
        return {"status": "error", "error": f"Event '{event_id}' not found."}
    return {"status": "success", "message": f"Event '{event_id}' cancelled."}


# =============================================================================
# Notes & Personal Knowledge Tools
# =============================================================================

def create_note(
    title: str,
    content: str,
    category: str = "general",
    tags: str = "",
    pinned: bool = False,
    linked_task_id: str = "",
    linked_event_id: str = "",
) -> dict[str, Any]:
    """Create a new personal note."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    tid = linked_task_id if linked_task_id else None
    eid = linked_event_id if linked_event_id else None
    note = svc.create_note(
        identity,
        title=title,
        content=content,
        category=category,
        tags=tag_list,
        pinned=pinned,
        linked_task_id=tid,
        linked_event_id=eid,
    )
    return {
        "status": "success",
        "message": f"Note '{note.title}' created.",
        "note": note.to_dict(),
    }


def get_note(note_id: str) -> dict[str, Any]:
    """Retrieve details and content of a personal note."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    note = svc.get_note(identity, note_id)
    if note is None:
        return {"status": "error", "error": f"Note '{note_id}' not found."}
    return {"status": "success", "note": note.to_dict()}


def list_notes(
    category: str = "",
    pinned_only: bool = False,
    include_archived: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """List notes with optional filters."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    cat = category if category else None
    notes = svc.list_notes(
        identity,
        category=cat,
        pinned_only=pinned_only,
        include_archived=include_archived,
        limit=limit,
    )
    return {
        "status": "success",
        "count": len(notes),
        "notes": [n.to_dict() for n in notes],
    }


def search_notes(query: str, limit: int = 10) -> dict[str, Any]:
    """Search personal notes by keywords in title, content, or tags."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    notes = svc.search_notes(identity, query=query, limit=limit)
    return {
        "status": "success",
        "count": len(notes),
        "notes": [n.to_dict() for n in notes],
    }


def update_note(
    note_id: str,
    title: str = "",
    content: str = "",
    category: str = "",
    tags: str = "",
    pinned: bool = False,
    archived: bool = False,
) -> dict[str, Any]:
    """Update an existing personal note."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    updates: dict[str, Any] = {}
    if title:
        updates["title"] = title
    if content:
        updates["content"] = content
    if category:
        updates["category"] = category
    if tags:
        updates["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if pinned:
        updates["pinned"] = pinned
    if archived:
        updates["archived"] = archived

    note = svc.update_note(identity, note_id, **updates)
    if note is None:
        return {"status": "error", "error": f"Note '{note_id}' not found."}
    return {
        "status": "success",
        "message": f"Note '{note.title}' updated.",
        "note": note.to_dict(),
    }


def delete_note(note_id: str) -> dict[str, Any]:
    """Delete a note permanently. Requires explicit confirmation."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    deleted = svc.delete_note(identity, note_id)
    if not deleted:
        return {"status": "error", "error": f"Note '{note_id}' not found."}
    return {"status": "success", "message": f"Note '{note_id}' deleted."}


# =============================================================================
# Daily Planning & Summaries
# =============================================================================

def get_daily_plan(date_str: str = "") -> dict[str, Any]:
    """Synthesize daily schedule, prioritized tasks, and reminders."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    d = date_str if date_str else None
    plan = svc.get_daily_plan(identity, date_str=d)
    return {
        "status": "success",
        "plan": plan.to_dict(),
    }


def get_end_of_day_summary(date_str: str = "") -> dict[str, Any]:
    """Generate structured markdown summary of daily productivity and accomplishments."""
    svc = get_productivity_service()
    identity = _resolve_identity()
    d = date_str if date_str else None
    summary_text = svc.get_end_of_day_summary(identity, date_str=d)
    return {
        "status": "success",
        "summary": summary_text,
    }
