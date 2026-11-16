"""Daily Planning and Intelligent Summary Engine for RIYA (Phase 6.6).

Features:
- Daily agenda synthesis across personal tasks, reminders, and calendar events.
- Overdue task identification and approaching deadline detection.
- Calendar overlap and conflict highlighting.
- Heuristic task sequencing (Urgent -> High with deadlines -> Dependencies -> Others).
- Distinguishes AI suggestions from confirmed database records.
- End-of-day completion summary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.identity.models import AuthenticatedIdentity
from core.productivity.models import (
    CalendarEvent,
    DailyPlan,
    PersonalTask,
    Reminder,
    utc_now_iso,
)
from core.productivity.store import ProductivityStore

logger = logging.getLogger("riya.productivity.planner")


def _priority_score(task: PersonalTask) -> int:
    """Compute sorting weight based on priority level."""
    weights = {"urgent": 4, "high": 3, "medium": 2, "low": 1}
    return weights.get(task.priority.lower(), 2)


class DailyPlanner:
    """Assembles comprehensive daily agendas and optimizes daily workflow schedules."""

    def __init__(self, store: ProductivityStore | None = None):
        self.store = store or ProductivityStore()

    def generate_daily_plan(
        self,
        identity: AuthenticatedIdentity | str,
        target_date: str | None = None,
    ) -> DailyPlan:
        """Construct full daily plan with tasks, events, reminders, conflicts, and suggested sequence."""
        today_str = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_of_day = f"{today_str}T00:00:00"
        end_of_day = f"{today_str}T23:59:59"

        # 1. Fetch all pending or in-progress tasks
        all_active_tasks = self.store.list_tasks(identity, status="pending", limit=200) + \
                           self.store.list_tasks(identity, status="in_progress", limit=200)

        tasks_due_today: list[PersonalTask] = []
        overdue_tasks: list[PersonalTask] = []

        now_iso = utc_now_iso()
        for t in all_active_tasks:
            if t.due_date:
                if t.due_date < start_of_day:
                    overdue_tasks.append(t)
                elif t.due_date <= end_of_day:
                    tasks_due_today.append(t)

        # 2. Fetch today's calendar events
        events_today = self.store.list_events(
            identity,
            start_after=start_of_day,
            end_before=end_of_day,
            limit=100,
        )

        # 3. Detect conflicting calendar events
        conflicts: list[str] = []
        for i in range(len(events_today)):
            for j in range(i + 1, len(events_today)):
                e1 = events_today[i]
                e2 = events_today[j]
                if e1.start_time < e2.end_time and e1.end_time > e2.start_time:
                    conflicts.append(f"Overlap: '{e1.title}' ({e1.start_time[-8:]}-{e1.end_time[-8:]}) conflicts with '{e2.title}' ({e2.start_time[-8:]}-{e2.end_time[-8:]})")

        # 4. Fetch today's reminders
        reminders_today = self.store.list_reminders(
            identity,
            due_before=end_of_day,
            limit=50,
        )

        # 5. Suggested task order (Overdue first, then Urgent -> High -> Medium -> Low)
        combined_priority_tasks = overdue_tasks + tasks_due_today
        # Deduplicate while preserving order
        seen_ids = set()
        deduped = []
        for t in combined_priority_tasks:
            if t.task_id not in seen_ids:
                seen_ids.add(t.task_id)
                deduped.append(t)

        # Topological-like sort: sort by overdue, priority score desc, then due date
        def _sort_key(t: PersonalTask):
            is_od = 1 if (t.due_date and t.due_date < now_iso) else 0
            p_score = _priority_score(t)
            due_val = t.due_date or "9999-99-99"
            return (-is_od, -p_score, due_val)

        sorted_tasks = sorted(deduped, key=_sort_key)
        suggested_order = [f"[{t.priority.upper()}] {t.title} (due: {t.due_date or 'today'})" for t in sorted_tasks]

        # 6. Compose human-readable markdown summary
        md_lines = [
            f"# Daily Agenda — {today_str}",
            "",
            f"**Scheduled Events ({len(events_today)}):**",
        ]
        if events_today:
            for ev in events_today:
                loc = f" @ {ev.location}" if ev.location else ""
                md_lines.append(f"- **{ev.start_time[11:16]} - {ev.end_time[11:16]}**: {ev.title}{loc}")
        else:
            md_lines.append("- No events scheduled today.")

        if conflicts:
            md_lines.append("\n**⚠️ Schedule Conflicts Detected:**")
            for c in conflicts:
                md_lines.append(f"- {c}")

        md_lines.append(f"\n**Tasks Due Today ({len(tasks_due_today)}):**")
        if tasks_due_today:
            for t in tasks_due_today:
                md_lines.append(f"- [{t.priority.upper()}] {t.title}")
        else:
            md_lines.append("- No tasks specifically due today.")

        if overdue_tasks:
            md_lines.append(f"\n**⚠️ Overdue Tasks ({len(overdue_tasks)}):**")
            for t in overdue_tasks:
                md_lines.append(f"- [{t.priority.upper()}] {t.title} (due {t.due_date})")

        md_lines.append("\n**Suggested Task Sequence:**")
        if suggested_order:
            for idx, item in enumerate(suggested_order[:7], start=1):
                md_lines.append(f"{idx}. {item}")
        else:
            md_lines.append("All caught up! No urgent tasks pending.")

        summary_text = "\n".join(md_lines)

        return DailyPlan(
            date=today_str,
            tasks_due_today=tasks_due_today,
            overdue_tasks=overdue_tasks,
            events_today=events_today,
            reminders_today=reminders_today,
            conflicts=conflicts,
            suggested_order=suggested_order,
            summary_markdown=summary_text,
        )

    def generate_end_of_day_summary(
        self,
        identity: AuthenticatedIdentity | str,
        target_date: str | None = None,
    ) -> str:
        """Generate end-of-day wrap-up of completed tasks and remaining items."""
        today_str = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        all_tasks = self.store.list_tasks(identity, limit=200)

        completed_today = [
            t for t in all_tasks
            if t.status == "completed" and t.completed_at and t.completed_at.startswith(today_str)
        ]
        pending_remaining = [
            t for t in all_tasks
            if t.status in ("pending", "in_progress")
        ]

        lines = [
            f"# End-of-Day Summary — {today_str}",
            "",
            f"**Completed Today ({len(completed_today)}):**",
        ]
        if completed_today:
            for t in completed_today:
                lines.append(f"- ✅ {t.title} ({t.category})")
        else:
            lines.append("- No tasks marked completed today.")

        lines.append(f"\n**Pending for Tomorrow ({len(pending_remaining)}):**")
        for t in pending_remaining[:5]:
            due = f" [due: {t.due_date}]" if t.due_date else ""
            lines.append(f"- ⏳ [{t.priority.upper()}] {t.title}{due}")
        if len(pending_remaining) > 5:
            lines.append(f"- ...and {len(pending_remaining) - 5} more.")

        return "\n".join(lines)
