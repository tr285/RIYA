"""Tests for RIYA Phase 6 Milestone 6.6: Intelligent Personal Productivity Subsystem.

Covers:
- Personal Task models, priorities, recurrence, dependencies, and lifecycle.
- ProductivityStore SQLite persistence and strict user isolation.
- ReminderEngine: scheduling, snoozing, recurring recalculation, and missed reminder recovery.
- CalendarManager: scheduling, daily/weekly schedules, and conflict detection.
- NotesManager: markdown note creation, categorization, tags, pinning, and search.
- DailyPlanner: cohesive agenda synthesis, task sequencing, and end-of-day summaries.
- ProductivityService: unified facade coordination and daemon controls.
- Productivity agent tools in core/tools/productivity.py and execution via RiyaAgent / ToolExecutor.
- Security confirmation gates for delete actions (delete_task, cancel_reminder, delete_calendar_event, delete_note).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import uuid
import pytest

from core.identity.context import ExecutionContext, ExecutionContextScope
from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity, UserProfile
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
from core.productivity.store import ProductivityStore
from core.productivity.reminders import ReminderEngine
from core.productivity.calendar import CalendarManager
from core.productivity.notes import NotesManager
from core.productivity.planner import DailyPlanner
from core.productivity.service import ProductivityService
from core.tools.productivity import (
    create_task,
    get_task,
    list_tasks,
    update_task,
    complete_task,
    reopen_task,
    delete_task,
    create_reminder,
    list_reminders,
    snooze_reminder,
    cancel_reminder,
    create_calendar_event,
    list_calendar_events,
    get_daily_schedule,
    get_weekly_schedule,
    delete_calendar_event,
    create_note,
    get_note,
    list_notes,
    search_notes,
    update_note,
    delete_note,
    get_daily_plan,
    get_end_of_day_summary,
    set_productivity_service,
)
from core.agent.agent import RiyaAgent
from core.agent.tool_registry import ToolRegistry
from core.agent.tool_executor import ToolExecutor
from core.security.manager import RiskLevel


@pytest.fixture
def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_productivity.db"
    return db_file


@pytest.fixture
def prod_store(temp_db: Path):
    return ProductivityStore(temp_db)


@pytest.fixture
def prod_service(temp_db: Path):
    svc = ProductivityService(temp_db)
    set_productivity_service(svc)
    return svc


@pytest.fixture
def user_a():
    return "user_alice_001"


@pytest.fixture
def user_b():
    return "user_bob_002"


# =============================================================================
# 1. Models & Validation Tests
# =============================================================================

def test_task_model_validation():
    task = PersonalTask(
        task_id="t1",
        user_id="u1",
        title="Review Q3 Report",
        priority=TaskPriority.HIGH,
        status=TaskStatus.PENDING,
    )
    assert task.title == "Review Q3 Report"
    assert task.priority == TaskPriority.HIGH
    assert not task.is_overdue
    d = task.to_dict()
    assert d["task_id"] == "t1"
    assert d["priority"] == "high"
    assert d["status"] == "pending"


def test_task_is_overdue_check():
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    task = PersonalTask(
        task_id="t2",
        user_id="u1",
        title="Old Task",
        due_date=yesterday,
        status=TaskStatus.PENDING,
    )
    assert task.is_overdue

    # If completed, not overdue
    task_done = PersonalTask(
        task_id="t3",
        user_id="u1",
        title="Old Done Task",
        due_date=yesterday,
        status=TaskStatus.COMPLETED,
    )
    assert not task_done.is_overdue


def test_reminder_model_properties():
    now_utc = datetime.now(timezone.utc)
    past_time = (now_utc - timedelta(minutes=5)).isoformat()
    future_time = (now_utc + timedelta(hours=2)).isoformat()

    r_due = Reminder(
        reminder_id="r1",
        user_id="u1",
        title="Standup",
        trigger_at=past_time,
        status=ReminderStatus.PENDING,
    )
    assert r_due.is_due

    r_future = Reminder(
        reminder_id="r2",
        user_id="u1",
        title="Lunch",
        trigger_at=future_time,
        status=ReminderStatus.PENDING,
    )
    assert not r_future.is_due


def test_calendar_event_conflicts():
    ev1 = CalendarEvent(
        event_id="e1",
        user_id="u1",
        title="Sprint Planning",
        start_time="2026-11-15T10:00:00+00:00",
        end_time="2026-11-15T11:00:00+00:00",
    )
    ev_overlap = CalendarEvent(
        event_id="e2",
        user_id="u1",
        title="1:1 Sync",
        start_time="2026-11-15T10:30:00+00:00",
        end_time="2026-11-15T11:30:00+00:00",
    )
    ev_no_overlap = CalendarEvent(
        event_id="e3",
        user_id="u1",
        title="Team Demo",
        start_time="2026-11-15T11:00:00+00:00",
        end_time="2026-11-15T12:00:00+00:00",
    )
    assert ev1.overlaps_with(ev_overlap)
    assert ev_overlap.overlaps_with(ev1)
    assert not ev1.overlaps_with(ev_no_overlap)


# =============================================================================
# 2. Productivity Store CRUD & User Isolation Tests
# =============================================================================

def test_task_store_crud_and_isolation(prod_store: ProductivityStore, user_a: str, user_b: str):
    # User A creates a task
    t_a = prod_store.create_task(
        user_a,
        title="Secret Task A",
        priority="high",
        due_date="2026-11-20T17:00:00+00:00",
        category="work",
    )
    assert t_a.task_id
    assert t_a.title == "Secret Task A"

    # User B cannot see User A's task
    assert prod_store.get_task(user_b, t_a.task_id) is None
    tasks_b = prod_store.list_tasks(user_b)
    assert len(tasks_b) == 0

    # User A can retrieve and update it
    got_a = prod_store.get_task(user_a, t_a.task_id)
    assert got_a is not None
    assert got_a.title == "Secret Task A"

    # Update task
    updated_a = prod_store.update_task(user_a, t_a.task_id, priority="urgent", status="in_progress")
    assert updated_a is not None
    assert updated_a.priority == TaskPriority.URGENT
    assert updated_a.status == TaskStatus.IN_PROGRESS

    # Complete task
    completed = prod_store.complete_task(user_a, t_a.task_id)
    assert completed is not None
    assert completed.status == TaskStatus.COMPLETED
    assert completed.completed_at is not None

    # User B cannot delete User A's task
    assert not prod_store.delete_task(user_b, t_a.task_id)
    # User A can delete
    assert prod_store.delete_task(user_a, t_a.task_id)
    assert prod_store.get_task(user_a, t_a.task_id) is None


# =============================================================================
# 3. Reminder Engine Tests
# =============================================================================

def test_reminder_scheduling_and_snooze(prod_service: ProductivityService, user_a: str):
    now_utc = datetime.now(timezone.utc)
    due_iso = (now_utc + timedelta(hours=1)).isoformat()

    r = prod_service.create_reminder(user_a, "Drink Water", due_iso)
    assert r.reminder_id
    assert r.status == ReminderStatus.PENDING

    # Snooze reminder
    snoozed = prod_service.snooze_reminder(user_a, r.reminder_id, minutes=15)
    assert snoozed is not None
    assert snoozed.snooze_count == 1
    # Trigger time should be later
    assert snoozed.trigger_at > due_iso

    # Cancel reminder
    assert prod_service.cancel_reminder(user_a, r.reminder_id)
    cancelled = prod_service.store.get_reminder(user_a, r.reminder_id)
    assert cancelled is None


def test_missed_reminder_recovery(prod_service: ProductivityService, user_a: str):
    # Create an overdue reminder (simulating system being offline when reminder was due)
    past_due = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    r = prod_service.store.create_reminder(user_a, "Missed Meeting Alert", past_due)
    assert r.status == ReminderStatus.PENDING

    # Run check_due_reminders
    triggered = prod_service.reminders.check_due_reminders(user_a)
    assert len(triggered) >= 1
    assert any(rem.reminder_id == r.reminder_id for rem in triggered)

    # Status should now be TRIGGERED
    updated = prod_service.store.get_reminder(user_a, r.reminder_id)
    assert updated is not None
    assert updated.status == ReminderStatus.TRIGGERED


def test_recurring_reminder_next_trigger(prod_service: ProductivityService, user_a: str):
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    due_iso = now.isoformat()

    r = prod_service.create_reminder(user_a, "Daily Standup", due_iso, recurring_rule="daily")
    assert r.recurring_rule == ReminderRecurrence.DAILY

    # Trigger it
    triggered = prod_service.reminders.check_due_reminders(user_a)
    assert any(rem.reminder_id == r.reminder_id for rem in triggered)

    # Next occurrence should automatically be scheduled for tomorrow
    reminders = prod_service.list_reminders(user_a, status="pending")
    standup_pending = [rem for rem in reminders if "Daily Standup" in rem.title]
    assert len(standup_pending) == 1
    assert standup_pending[0].trigger_at > due_iso


# =============================================================================
# 4. Calendar Manager Tests
# =============================================================================

def test_calendar_schedule_and_conflicts(prod_service: ProductivityService, user_a: str):
    ev1, conflicts = prod_service.create_event(
        user_a,
        title="Product Architecture Review",
        start_time="2026-11-15T14:00:00+00:00",
        end_time="2026-11-15T15:30:00+00:00",
        location="Conference Room A",
    )
    assert ev1.event_id
    assert len(conflicts) == 0

    # Overlapping event
    ev2, conflicts2 = prod_service.create_event(
        user_a,
        title="Quick Sync",
        start_time="2026-11-15T14:30:00+00:00",
        end_time="2026-11-15T15:00:00+00:00",
    )
    assert ev2.event_id
    assert len(conflicts2) == 1
    assert conflicts2[0].event_id == ev1.event_id

    # Daily schedule retrieval
    day_events = prod_service.get_daily_schedule(user_a, "2026-11-15")
    assert len(day_events) == 2

    # Delete event
    assert prod_service.delete_event(user_a, ev1.event_id)
    day_events_after = prod_service.get_daily_schedule(user_a, "2026-11-15")
    assert len(day_events_after) == 1


# =============================================================================
# 5. Notes & Knowledge Store Tests
# =============================================================================

def test_notes_creation_tagging_and_search(prod_service: ProductivityService, user_a: str, user_b: str):
    note1 = prod_service.create_note(
        user_a,
        title="Architecture Decision: Offline AI",
        content="We chose native llama.cpp for local offline privacy and zero latency.",
        category="engineering",
        tags=["ai", "architecture", "privacy"],
        pinned=True,
    )
    assert note1.note_id
    assert note1.pinned

    note2 = prod_service.create_note(
        user_a,
        title="Shopping List",
        content="Apples, coffee beans, oat milk.",
        category="personal",
        tags=["shopping"],
    )

    # User B creates note
    note_b = prod_service.create_note(
        user_b,
        title="Bob's Private Notes",
        content="Super confidential information.",
        category="secret",
    )

    # Search notes
    results_ai = prod_service.search_notes(user_a, "llama.cpp")
    assert len(results_ai) == 1
    assert results_ai[0].note_id == note1.note_id

    # Search by tag
    results_tag = prod_service.search_notes(user_a, "privacy")
    assert len(results_tag) == 1
    assert results_tag[0].title == "Architecture Decision: Offline AI"

    # User A cannot search or find User B's note
    assert len(prod_service.search_notes(user_a, "confidential")) == 0
    # User B cannot see User A's notes
    assert len(prod_service.search_notes(user_b, "llama.cpp")) == 0

    # Delete note
    assert prod_service.delete_note(user_a, note2.note_id)
    assert prod_service.get_note(user_a, note2.note_id) is None


# =============================================================================
# 6. Daily Planner Tests
# =============================================================================

def test_daily_planner_synthesis(prod_service: ProductivityService, user_a: str):
    target_date = "2026-11-15"

    # 1. Add overdue task
    prod_service.create_task(
        user_a,
        title="Finish Quarterly Audit",
        priority="urgent",
        due_date="2026-11-14T18:00:00+00:00",
    )
    # 2. Add today's task
    prod_service.create_task(
        user_a,
        title="Review PR 42",
        priority="high",
        due_date="2026-11-15T16:00:00+00:00",
    )
    # 3. Add calendar event
    prod_service.create_event(
        user_a,
        title="Team Standup",
        start_time="2026-11-15T09:30:00+00:00",
        end_time="2026-11-15T10:00:00+00:00",
    )
    # 4. Add reminder
    prod_service.create_reminder(
        user_a,
        title="Send status email",
        trigger_at="2026-11-15T11:00:00+00:00",
    )

    plan = prod_service.get_daily_plan(user_a, date_str=target_date)
    assert isinstance(plan, DailyPlan)
    assert len(plan.events_today) == 1
    assert len(plan.tasks_due_today) >= 1
    assert len(plan.overdue_tasks) >= 1

    summary = prod_service.get_end_of_day_summary(user_a, date_str=target_date)
    assert "End-of-Day Summary" in summary
    assert "Finish Quarterly Audit" in summary or "Review PR 42" in summary


# =============================================================================
# 7. Agent Tools & Security Confirmation Gate Tests
# =============================================================================

def test_productivity_tool_functions_in_context(prod_service: ProductivityService):
    ws_dir = Path(tempfile.mkdtemp())
    ctx = ExecutionContext(
        user_id="alice_test_user",
        session_id="session_123",
        workspace_dir=ws_dir,
        allowed_roots=(ws_dir,),
        voice_dir=ws_dir / "voice",
    )

    with ExecutionContextScope(ctx):
        # 1. create_task
        res_t = create_task(title="Write Unit Tests", priority="high", due_date="2026-11-20")
        assert res_t["status"] == "success"
        tid = res_t["task"]["task_id"]

        # 2. list_tasks
        res_list = list_tasks()
        assert res_list["status"] == "success"
        assert any(t["task_id"] == tid for t in res_list["tasks"])

        # 3. complete_task
        res_done = complete_task(task_id=tid)
        assert res_done["status"] == "success"
        assert res_done["task"]["status"] == "completed"

        # 4. create_reminder
        res_r = create_reminder(title="Submit Timesheet", trigger_at="2026-11-20T17:00:00+00:00")
        assert res_r["status"] == "success"
        rid = res_r["reminder"]["reminder_id"]

        # 5. snooze_reminder
        res_s = snooze_reminder(reminder_id=rid, minutes=30)
        assert res_s["status"] == "success"

        # 6. create_calendar_event
        res_e = create_calendar_event(
            title="Design Review",
            start_time="2026-11-20T14:00:00+00:00",
            end_time="2026-11-20T15:00:00+00:00",
        )
        assert res_e["status"] == "success"
        eid = res_e["event"]["event_id"]

        # 7. create_note & search_notes
        res_n = create_note(title="Meeting Notes", content="Discussed roadmap milestones.", tags="roadmap,dev")
        assert res_n["status"] == "success"
        nid = res_n["note"]["note_id"]

        res_search = search_notes(query="milestones")
        assert res_search["status"] == "success"
        assert any(n["note_id"] == nid for n in res_search["notes"])

        # 8. get_daily_plan & summary
        res_plan = get_daily_plan(date_str="2026-11-20")
        assert res_plan["status"] == "success"
        assert "plan" in res_plan

        res_summ = get_end_of_day_summary(date_str="2026-11-20")
        assert res_summ["status"] == "success"
        assert "End-of-Day Summary" in res_summ["summary"]


def test_destructive_productivity_tools_require_confirmation(prod_service: ProductivityService):
    agent = RiyaAgent()
    ws_dir = Path(tempfile.mkdtemp())
    ident = AuthenticatedIdentity(
        user_id="secure_alice",
        username="alice",
        display_name="Alice",
        session_id="sess_abc",
        session_token="token_xyz",
        workspace_dir=str(ws_dir),
        auth_method="local",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
    )
    ctx = ExecutionContext(
        user_id="secure_alice",
        session_id="sess_abc",
        workspace_dir=ws_dir,
        allowed_roots=(ws_dir,),
        voice_dir=ws_dir / "voice",
        identity=ident,
    )

    with ExecutionContextScope(ctx):
        # Create records directly first
        t = prod_service.create_task("secure_alice", "Task to delete")
        r = prod_service.create_reminder("secure_alice", "Reminder to cancel", "2026-11-20T10:00:00+00:00")
        ev, _ = prod_service.create_event(
            "secure_alice", "Event to delete", "2026-11-20T10:00:00+00:00", "2026-11-20T11:00:00+00:00"
        )
        n = prod_service.create_note("secure_alice", "Note to delete", "Will be deleted")

        # 1. Attempt delete_task without token -> confirmation_required
        res_del_task = agent.execute("delete_task", task_id=t.task_id)
        assert res_del_task["status"] == "confirmation_required"
        token = res_del_task["confirmation_token"]
        # Now execute with confirmation token -> success
        res_confirmed = agent.execute("delete_task", confirmation_token=token, task_id=t.task_id)
        assert res_confirmed["status"] == "success"

        # 2. Attempt cancel_reminder without token -> confirmation_required
        res_del_rem = agent.execute("cancel_reminder", reminder_id=r.reminder_id)
        assert res_del_rem["status"] == "confirmation_required"
        token_rem = res_del_rem["confirmation_token"]
        res_rem_confirmed = agent.execute("cancel_reminder", confirmation_token=token_rem, reminder_id=r.reminder_id)
        assert res_rem_confirmed["status"] == "success"

        # 3. Attempt delete_calendar_event without token -> confirmation_required
        res_del_ev = agent.execute("delete_calendar_event", event_id=ev.event_id)
        assert res_del_ev["status"] == "confirmation_required"
        token_ev = res_del_ev["confirmation_token"]
        res_ev_confirmed = agent.execute("delete_calendar_event", confirmation_token=token_ev, event_id=ev.event_id)
        assert res_ev_confirmed["status"] == "success"

        # 4. Attempt delete_note without token -> confirmation_required
        res_del_n = agent.execute("delete_note", note_id=n.note_id)
        assert res_del_n["status"] == "confirmation_required"
        token_n = res_del_n["confirmation_token"]
        res_n_confirmed = agent.execute("delete_note", confirmation_token=token_n, note_id=n.note_id)
        assert res_n_confirmed["status"] == "success"
