"""Intelligent Reminder Engine for RIYA (Phase 6.6).

Features:
- One-time and recurring reminders (daily, weekly, monthly).
- Time-zone aware ISO 8601 timestamps.
- Snoozing and cancellation.
- Missed-reminder recovery across application restarts.
- Notification dispatch through the platform notification adapter.
- Background monitoring daemon with thread-safe lifecycle controls.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from core.identity.models import AuthenticatedIdentity
from core.productivity.models import Reminder, utc_now_iso
from core.productivity.store import ProductivityStore

logger = logging.getLogger("riya.productivity.reminders")


def _compute_next_recurrence(current_iso: str, rule: str) -> str:
    """Calculate the next ISO 8601 trigger timestamp for a recurring reminder."""
    try:
        dt = datetime.fromisoformat(current_iso.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)

    rule_norm = rule.strip().lower()
    if rule_norm == "daily":
        next_dt = dt + timedelta(days=1)
    elif rule_norm == "weekly":
        next_dt = dt + timedelta(weeks=1)
    elif rule_norm == "monthly":
        # Rough 30-day offset for monthly recurrence
        next_dt = dt + timedelta(days=30)
    else:
        next_dt = dt + timedelta(days=1)

    return next_dt.isoformat()


class ReminderEngine:
    """Coordinates reminder scheduling, persistence, snoozing, and local delivery."""

    def __init__(
        self,
        store: ProductivityStore | None = None,
        *,
        notifier: Callable[[str, str], bool] | None = None,
    ):
        self.store = store or ProductivityStore()
        self._custom_notifier = notifier
        self._daemon_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._active_listeners: list[Callable[[Reminder], None]] = []

    def add_listener(self, listener: Callable[[Reminder], None]) -> None:
        """Register a callback for triggered reminders."""
        with self._lock:
            self._active_listeners.append(listener)

    def dispatch_notification(self, title: str, message: str) -> bool:
        """Deliver a system notification via custom notifier or platform adapter."""
        if self._custom_notifier is not None:
            try:
                return bool(self._custom_notifier(title, message))
            except Exception as exc:
                logger.warning("Custom notification dispatch failed: %s", exc)
                return False

        try:
            from core.device import get_platform_adapter

            adapter = get_platform_adapter()
            if hasattr(adapter, "show_notification"):
                res = adapter.show_notification(title, message)
                return bool(getattr(res, "success", True))
        except Exception as exc:
            logger.debug("Platform notification dispatch unavailable: %s", exc)

        return True

    def schedule_reminder(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        trigger_at: str,
        *,
        task_id: str | None = None,
        recurring_rule: str | None = None,
    ) -> Reminder:
        """Schedule a persistent reminder."""
        return self.store.create_reminder(
            identity,
            title,
            trigger_at,
            task_id=task_id,
            recurring_rule=recurring_rule,
        )

    def snooze(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
        minutes: int = 10,
    ) -> Reminder | None:
        """Snooze a pending or triggered reminder by N minutes."""
        return self.store.snooze_reminder(identity, reminder_id, minutes=minutes)

    def cancel(
        self,
        identity: AuthenticatedIdentity | str,
        reminder_id: str,
    ) -> bool:
        """Cancel/delete a reminder."""
        return self.store.delete_reminder(identity, reminder_id)

    def check_and_deliver_due_reminders(
        self,
        identity: AuthenticatedIdentity | str,
    ) -> list[Reminder]:
        """Check for all due or overdue reminders and dispatch notifications."""
        now_iso = utc_now_iso()
        due = self.store.list_reminders(identity, status="pending", due_before=now_iso)
        snoozed_due = self.store.list_reminders(identity, status="snoozed", due_before=now_iso)
        all_due = due + snoozed_due

        delivered: list[Reminder] = []
        for rem in all_due:
            # Deliver notification
            self.dispatch_notification("RIYA Reminder", rem.title)

            # Update status to triggered / delivered
            updated = self.store.update_reminder_status(
                identity,
                rem.reminder_id,
                status="triggered",
                delivered=True,
            )
            if updated:
                delivered.append(updated)

                # Notify listeners
                with self._lock:
                    listeners = list(self._active_listeners)
                for cb in listeners:
                    try:
                        cb(updated)
                    except Exception:
                        pass

                # If recurring, automatically schedule the next occurrence
                if rem.recurring_rule:
                    next_trigger = _compute_next_recurrence(rem.trigger_at, rem.recurring_rule)
                    self.store.create_reminder(
                        identity,
                        rem.title,
                        next_trigger,
                        task_id=rem.task_id,
                        recurring_rule=rem.recurring_rule,
                    )

        return delivered

    def recover_missed_reminders(
        self,
        identity: AuthenticatedIdentity | str,
    ) -> list[Reminder]:
        """Recover any reminders that became due while the app was offline/closed."""
        return self.check_and_deliver_due_reminders(identity)

    # Alias for convenience
    check_due_reminders = check_and_deliver_due_reminders

    # =========================================================================
    # DAEMON THREAD LIFECYCLE
    # =========================================================================
    def start_daemon(
        self,
        identity: AuthenticatedIdentity | str,
        interval_seconds: float = 5.0,
    ) -> None:
        """Start background polling thread to trigger reminders automatically."""
        with self._lock:
            if self._daemon_thread is not None and self._daemon_thread.is_alive():
                return
            self._stop_event.clear()

            def _poll_worker():
                # Initial missed reminder recovery
                try:
                    self.recover_missed_reminders(identity)
                except Exception as exc:
                    logger.debug("Initial missed-reminder recovery error: %s", exc)

                while not self._stop_event.is_set():
                    try:
                        self.check_and_deliver_due_reminders(identity)
                    except Exception as exc:
                        logger.debug("Reminder check cycle error: %s", exc)
                    self._stop_event.wait(timeout=interval_seconds)

            self._daemon_thread = threading.Thread(
                target=_poll_worker,
                name="riya-reminder-daemon",
                daemon=True,
            )
            self._daemon_thread.start()

    def stop_daemon(self, timeout: float = 2.0) -> None:
        """Stop background polling thread cleanly."""
        with self._lock:
            self._stop_event.set()
            t = self._daemon_thread
            self._daemon_thread = None

        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=timeout)
