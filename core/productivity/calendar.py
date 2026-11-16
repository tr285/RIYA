"""Calendar and Event Management Engine for RIYA (Phase 6.6).

Features:
- Local offline event management with conflict detection.
- Daily and weekly schedule views.
- Overlap detection with detailed conflict reporting.
- Provider abstraction for extensible future integrations (e.g. CalDAV / ICS).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

from core.identity.models import AuthenticatedIdentity
from core.productivity.models import CalendarEvent, utc_now_iso
from core.productivity.store import ProductivityStore

logger = logging.getLogger("riya.productivity.calendar")


class CalendarProvider(ABC):
    """Abstract contract for calendar event storage and synchronizers."""

    @abstractmethod
    def create_event(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        start_time: str,
        end_time: str,
        **kwargs: Any,
    ) -> CalendarEvent:
        pass

    @abstractmethod
    def list_events(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        start_after: str | None = None,
        end_before: str | None = None,
        limit: int = 100,
    ) -> list[CalendarEvent]:
        pass

    @abstractmethod
    def delete_event(
        self,
        identity: AuthenticatedIdentity | str,
        event_id: str,
    ) -> bool:
        pass


class LocalCalendarProvider(CalendarProvider):
    """Local SQLite-backed offline calendar provider."""

    def __init__(self, store: ProductivityStore):
        self.store = store

    def create_event(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        start_time: str,
        end_time: str,
        **kwargs: Any,
    ) -> CalendarEvent:
        return self.store.create_event(identity, title, start_time, end_time, **kwargs)

    def list_events(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        start_after: str | None = None,
        end_before: str | None = None,
        limit: int = 100,
    ) -> list[CalendarEvent]:
        return self.store.list_events(identity, start_after=start_after, end_before=end_before, limit=limit)

    def delete_event(
        self,
        identity: AuthenticatedIdentity | str,
        event_id: str,
    ) -> bool:
        return self.store.delete_event(identity, event_id)


class CalendarManager:
    """Manages schedules, conflict detection, and agenda aggregation."""

    def __init__(self, store: ProductivityStore | None = None, provider: CalendarProvider | None = None):
        self.store = store or ProductivityStore()
        self.provider = provider or LocalCalendarProvider(self.store)

    def schedule_event(
        self,
        identity: AuthenticatedIdentity | str,
        title: str,
        start_time: str,
        end_time: str,
        *,
        description: str = "",
        location: str = "",
        recurrence: str | None = None,
        check_conflicts: bool = True,
    ) -> tuple[CalendarEvent, list[CalendarEvent]]:
        """Schedule an event and return (event, conflicts)."""
        conflicts: list[CalendarEvent] = []
        if check_conflicts:
            conflicts = self.store.detect_conflicts(identity, start_time, end_time)

        evt = self.provider.create_event(
            identity,
            title,
            start_time,
            end_time,
            description=description,
            location=location,
            recurrence=recurrence,
        )
        return evt, conflicts

    def get_event(
        self,
        identity: AuthenticatedIdentity | str,
        event_id: str,
    ) -> CalendarEvent | None:
        return self.store.get_event(identity, event_id)

    def cancel_event(
        self,
        identity: AuthenticatedIdentity | str,
        event_id: str,
    ) -> bool:
        return self.provider.delete_event(identity, event_id)

    def get_daily_schedule(
        self,
        identity: AuthenticatedIdentity | str,
        date_str: str | None = None,
    ) -> list[CalendarEvent]:
        """Return all events scheduled for a specific date (YYYY-MM-DD), default today."""
        target_date = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_boundary = f"{target_date}T00:00:00"
        end_boundary = f"{target_date}T23:59:59"
        return self.provider.list_events(identity, start_after=start_boundary, end_before=end_boundary)

    def get_weekly_schedule(
        self,
        identity: AuthenticatedIdentity | str,
        start_date_str: str | None = None,
    ) -> list[CalendarEvent]:
        """Return all events for a 7-day window."""
        if start_date_str:
            try:
                base_dt = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
            except Exception:
                base_dt = datetime.now(timezone.utc)
        else:
            base_dt = datetime.now(timezone.utc)

        start_boundary = base_dt.strftime("%Y-%m-%dT00:00:00")
        end_boundary = (base_dt + timedelta(days=7)).strftime("%Y-%m-%dT23:59:59")
        return self.provider.list_events(identity, start_after=start_boundary, end_before=end_boundary)

    def find_conflicts(
        self,
        identity: AuthenticatedIdentity | str,
        start_time: str,
        end_time: str,
        exclude_event_id: str | None = None,
    ) -> list[CalendarEvent]:
        """Return any existing events overlapping the specified time window."""
        return self.store.detect_conflicts(identity, start_time, end_time, exclude_event_id=exclude_event_id)
