"""Personal Notes and Knowledge Management System for RIYA (Phase 6.6).

Features:
- Markdown-capable local note storage.
- Categorization, multi-tag filtering, and full-text keyword search.
- Pinning high-priority notes and archiving finished ones.
- Entity linking (connecting notes directly to tasks and calendar events).
- Zero cloud leakage: 100% offline and user-isolated.
"""

from __future__ import annotations

import logging
from typing import Any

from core.identity.models import AuthenticatedIdentity
from core.productivity.models import PersonalNote
from core.productivity.store import ProductivityStore

logger = logging.getLogger("riya.productivity.notes")


class NotesManager:
    """Manages personal markdown notes, categorization, tagging, and entity links."""

    def __init__(self, store: ProductivityStore | None = None):
        self.store = store or ProductivityStore()

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
        """Create a new user note."""
        return self.store.create_note(
            identity,
            title,
            content,
            category=category,
            tags=tags,
            pinned=pinned,
            linked_task_id=linked_task_id,
            linked_event_id=linked_event_id,
        )

    def get_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
    ) -> PersonalNote | None:
        """Retrieve a specific note by ID."""
        return self.store.get_note(identity, note_id)

    def list_notes(
        self,
        identity: AuthenticatedIdentity | str,
        *,
        category: str | None = None,
        pinned_only: bool = False,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[PersonalNote]:
        """List notes matching optional category or pinned filters."""
        return self.store.list_notes(
            identity,
            category=category,
            pinned_only=pinned_only,
            include_archived=include_archived,
            limit=limit,
        )

    def search_notes(
        self,
        identity: AuthenticatedIdentity | str,
        query: str,
        *,
        limit: int = 50,
    ) -> list[PersonalNote]:
        """Search notes across title, markdown content, categories, and tags."""
        return self.store.search_notes(identity, query, limit=limit)

    def update_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
        **updates: Any,
    ) -> PersonalNote | None:
        """Update note attributes."""
        return self.store.update_note(identity, note_id, **updates)

    def toggle_pin(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
    ) -> PersonalNote | None:
        """Toggle pinned status for a note."""
        note = self.store.get_note(identity, note_id)
        if not note:
            return None
        return self.store.update_note(identity, note_id, pinned=not note.pinned)

    def archive_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
    ) -> PersonalNote | None:
        """Archive a note so it is hidden from default listings."""
        return self.store.update_note(identity, note_id, archived=True)

    def unarchive_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
    ) -> PersonalNote | None:
        """Unarchive a note."""
        return self.store.update_note(identity, note_id, archived=False)

    def delete_note(
        self,
        identity: AuthenticatedIdentity | str,
        note_id: str,
    ) -> bool:
        """Delete a note."""
        return self.store.delete_note(identity, note_id)
