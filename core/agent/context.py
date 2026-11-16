"""Conversation context, rolling summarization, reference resolution, and history persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Any

from core.ai.safety import sanitize_untrusted_text, strip_unsafe_control_chars
from core.identity.models import AuthenticatedIdentity


ALLOWED_CONVERSATION_ROLES = frozenset({"user", "assistant"})

_STOPWORDS = frozenset(
    {
        "the", "and", "for", "that", "this", "with", "from", "have", "what", "when",
        "where", "which", "would", "could", "should", "about", "into", "your", "you",
        "are", "was", "were", "can", "will", "just", "please", "riya", "help", "tell",
        "more", "some", "how", "why", "who", "did", "does", "not", "yes", "okay", "sure",
        "there", "their", "then", "than", "also", "been", "being", "them", "they", "its",
    }
)

_FOLLOW_UP_CUES_RE = re.compile(
    r"\b(?:it|its|that|this|them|those|the\s+previous\s+one|the\s+last\s+one|"
    r"same\s+one|earlier\s+one|tell\s+me\s+more|explain\s+more|elaborate|"
    r"go\s+deeper|what\s+about\s+it|why\s+did\s+that)\b",
    re.IGNORECASE,
)

_BARE_INCOMPLETE_VERBS = frozenset(
    {
        "open",
        "close",
        "create",
        "create folder",
        "delete",
        "remove",
        "remember",
        "save",
        "store",
        "forget",
        "recall",
        "read",
        "write",
        "find",
        "search",
        "rename",
        "edit",
    }
)

_ANAPHORIC_ONLY_RE = re.compile(
    r"^(?:explain\s+it|summarize\s+it|summarize\s+that|read\s+it|read\s+that|"
    r"open\s+it|delete\s+it|tell\s+me\s+more(?:\s+about\s+(?:it|that))?|"
    r"elaborate(?:\s+on\s+(?:it|that))?|why\??|how\??|what\s+about\s+(?:it|that)\??)$",
    re.IGNORECASE,
)

_PATH_OR_FILE_RE = re.compile(
    r"(?:~|/)[A-Za-z0-9._/\-]+|\b[A-Za-z0-9_\-]+\.(?:py|txt|md|json|yaml|yml|db|csv|log|gguf)\b"
)
_QUOTED_RE = re.compile(r"['\"]([^'\"]{2,80})['\"]")
_PROPER_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9_\-]{2,}(?:\s+[A-Z][a-zA-Z0-9_\-]{2,})*)\b")


def extract_entities_and_topics(text: str) -> tuple[list[str], list[str]]:
    """Extract lightweight entities (paths, quoted phrases, proper nouns) and topic keywords."""
    if not isinstance(text, str) or not text.strip():
        return [], []

    entities: list[str] = []
    seen_ent: set[str] = set()

    for match in _PATH_OR_FILE_RE.findall(text):
        cleaned = match.strip(".,:;!?")
        if cleaned and cleaned not in seen_ent:
            seen_ent.add(cleaned)
            entities.append(cleaned)

    for match in _QUOTED_RE.findall(text):
        cleaned = match.strip()
        if cleaned and cleaned not in seen_ent:
            seen_ent.add(cleaned)
            entities.append(cleaned)

    for match in _PROPER_PHRASE_RE.findall(text):
        if match.lower() not in {"user", "riya", "what", "when", "where", "how", "why", "who", "please", "remember"}:
            if match not in seen_ent:
                seen_ent.add(match)
                entities.append(match)

    tokens = re.findall(r"[A-Za-z0-9_]{3,}", text.lower())
    topics: list[str] = []
    seen_topics: set[str] = set()
    for tok in tokens:
        if tok in _STOPWORDS or tok.isdigit():
            continue
        if tok not in seen_topics:
            seen_topics.add(tok)
            topics.append(tok)

    return entities[:12], topics[:12]


@dataclass
class ConversationMessage:
    """Single message in RIYA's conversation memory."""

    role: str
    content: str
    timestamp: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TurnAnalysis:
    """Result of pre-turn context & intent completeness analysis."""

    raw_input: str
    is_follow_up: bool
    resolved_subject: str | None
    resolved_prompt: str
    is_incomplete: bool
    clarification_prompt: str | None = None
    active_topics: tuple[str, ...] = ()
    recent_entities: tuple[str, ...] = ()


class ConversationContext:
    """
    Bounded conversation context with rolling summarization,
    follow-up reference tracking, and multi-user SQLite sync support.
    """

    def __init__(
        self,
        max_messages: int = 12,
        max_message_chars: int = 1000,
        max_total_chars: int = 4000,
        *,
        max_summary_chars: int = 900,
    ):
        self.max_messages = max(2, int(max_messages))
        self.max_message_chars = max(100, int(max_message_chars))
        self.max_total_chars = max(100, int(max_total_chars))
        self.max_summary_chars = max(200, int(max_summary_chars))
        self._messages: list[ConversationMessage] = []
        self._rolling_summary: str = ""
        self._recent_entities: list[str] = []
        self._active_topics: list[str] = []
        self._last_subject: str | None = None
        self._evicted_turn_count: int = 0

    def add_user(self, content: str) -> None:
        self._add("user", content)

    def add_assistant(self, content: str) -> None:
        self._add("assistant", content)

    @property
    def rolling_summary(self) -> str:
        return self._rolling_summary

    @property
    def active_topics(self) -> tuple[str, ...]:
        return tuple(self._active_topics)

    @property
    def recent_entities(self) -> tuple[str, ...]:
        return tuple(self._recent_entities)

    @property
    def last_subject(self) -> str | None:
        return self._last_subject

    def set_last_subject(self, subject: str | None, *, entities: list[str] | None = None) -> None:
        if subject and isinstance(subject, str):
            cleaned = strip_unsafe_control_chars(subject, allow_newlines=False).strip()
            if cleaned:
                self._last_subject = cleaned[:160]
        if entities:
            for item in entities:
                self._record_entity(item)

    def _record_entity(self, entity: str) -> None:
        cleaned = strip_unsafe_control_chars(str(entity), allow_newlines=False).strip()
        if not cleaned:
            return
        if cleaned in self._recent_entities:
            self._recent_entities.remove(cleaned)
        self._recent_entities.append(cleaned)
        if len(self._recent_entities) > 16:
            self._recent_entities = self._recent_entities[-16:]

    def _record_topic(self, topic: str) -> None:
        cleaned = strip_unsafe_control_chars(str(topic), allow_newlines=False).strip().lower()
        if not cleaned:
            return
        if cleaned in self._active_topics:
            self._active_topics.remove(cleaned)
        self._active_topics.append(cleaned)
        if len(self._active_topics) > 16:
            self._active_topics = self._active_topics[-16:]

    def _sanitize_content(self, content: str) -> str:
        if not isinstance(content, str):
            return ""
        cleaned = strip_unsafe_control_chars(
            content,
            allow_newlines=True,
        ).strip()
        if len(cleaned) > self.max_message_chars:
            if self.max_message_chars > 3:
                cleaned = cleaned[: self.max_message_chars - 3].rstrip() + "..."
            else:
                cleaned = cleaned[: self.max_message_chars]
        return cleaned

    def _absorb_evicted_message(self, msg: ConversationMessage) -> None:
        self._evicted_turn_count += 1
        label = "User" if msg.role == "user" else "RIYA"
        snippet = " ".join(msg.content.split())
        if len(snippet) > 140:
            snippet = snippet[:137].rstrip() + "..."
        entry = f"{label}: {snippet}"
        if not self._rolling_summary:
            self._rolling_summary = entry[: self.max_summary_chars]
            return

        combined = f"{self._rolling_summary} | {entry}"
        if len(combined) > self.max_summary_chars:
            # Keep entities/topics anchor plus most recent summary chunks.
            parts = [p.strip() for p in combined.split("|") if p.strip()]
            kept: list[str] = []
            used = 0
            for part in reversed(parts):
                extra = len(part) + (3 if kept else 0)
                if used + extra > self.max_summary_chars:
                    break
                kept.append(part)
                used += extra
            kept.reverse()
            combined = " | ".join(kept) if kept else combined[-self.max_summary_chars :]
        self._rolling_summary = combined

    def _add(self, role: str, content: str) -> None:
        if role not in ALLOWED_CONVERSATION_ROLES:
            raise ValueError(f"Unsupported conversation role: {role!r}")

        cleaned = self._sanitize_content(content)
        if not cleaned:
            return

        entities, topics = extract_entities_and_topics(cleaned)
        for ent in entities:
            self._record_entity(ent)
        for top in topics:
            self._record_topic(top)
        if role == "user":
            if entities:
                self._last_subject = entities[-1]
            elif topics:
                self._last_subject = " ".join(topics[:4])

        self._messages.append(
            ConversationMessage(
                role=role,
                content=cleaned,
            )
        )

        while len(self._messages) > self.max_messages:
            evicted = self._messages.pop(0)
            self._absorb_evicted_message(evicted)

        while self._messages and sum(len(m.content) for m in self._messages) > self.max_total_chars:
            evicted = self._messages.pop(0)
            self._absorb_evicted_message(evicted)

    def messages(self) -> list[ConversationMessage]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()
        self._rolling_summary = ""
        self._recent_entities.clear()
        self._active_topics.clear()
        self._last_subject = None
        self._evicted_turn_count = 0

    def __len__(self) -> int:
        return len(self._messages)

    def analyze_turn(self, user_input: str) -> TurnAnalysis:
        """Analyze whether a turn has incomplete context or follow-up references."""
        raw = strip_unsafe_control_chars(str(user_input or ""), allow_newlines=True).strip()
        normalized = " ".join(raw.lower().split()).rstrip("?.!:")
        has_history = bool(self._messages or self._rolling_summary or self._last_subject)

        if not raw:
            return TurnAnalysis(
                raw_input="",
                is_follow_up=False,
                resolved_subject=None,
                resolved_prompt="",
                is_incomplete=True,
                clarification_prompt="Could you please share what you'd like help with?",
                active_topics=self.active_topics,
                recent_entities=self.recent_entities,
            )

        # Bare action verb with no target object (e.g., "open", "remember", "delete", "read").
        if normalized in _BARE_INCOMPLETE_VERBS:
            return TurnAnalysis(
                raw_input=raw,
                is_follow_up=False,
                resolved_subject=self._last_subject,
                resolved_prompt=raw,
                is_incomplete=True,
                clarification_prompt=(
                    f"What would you like me to {normalized}? "
                    "Please provide a name, file path, or specific details."
                ),
                active_topics=self.active_topics,
                recent_entities=self.recent_entities,
            )

        is_anaphoric_only = bool(_ANAPHORIC_ONLY_RE.match(normalized))
        has_follow_up_cue = bool(_FOLLOW_UP_CUES_RE.search(raw))

        # Reference to previous context when no context exists at all.
        if is_anaphoric_only and not has_history:
            return TurnAnalysis(
                raw_input=raw,
                is_follow_up=True,
                resolved_subject=None,
                resolved_prompt=raw,
                is_incomplete=True,
                clarification_prompt=(
                    "We haven't started discussing a specific topic or item yet. "
                    "What are you referring to?"
                ),
                active_topics=self.active_topics,
                recent_entities=self.recent_entities,
            )

        subject = self._last_subject or (self._recent_entities[-1] if self._recent_entities else None)
        if subject is None and self._messages:
            for msg in reversed(self._messages):
                if msg.role == "user":
                    subject = msg.content[:120]
                    break

        is_follow_up = has_history and (has_follow_up_cue or is_anaphoric_only)
        resolved_prompt = raw
        if is_follow_up and subject:
            resolved_prompt = (
                f"{raw}\n"
                f"[Resolved recent reference/subject: {sanitize_untrusted_text(subject, max_chars=160, allow_newlines=False)}]"
            )

        return TurnAnalysis(
            raw_input=raw,
            is_follow_up=is_follow_up,
            resolved_subject=subject if is_follow_up else None,
            resolved_prompt=resolved_prompt,
            is_incomplete=False,
            clarification_prompt=None,
            active_topics=self.active_topics,
            recent_entities=self.recent_entities,
        )

    def summarize(self, ai_runtime: Any = None) -> str:
        """Return a concise offline summary combining evicted history and current turns."""
        parts: list[str] = []
        if self._rolling_summary:
            parts.append(f"Earlier: {self._rolling_summary}")
        if self._messages:
            highlights: list[str] = []
            for msg in self._messages[-6:]:
                lbl = "User" if msg.role == "user" else "RIYA"
                short = " ".join(msg.content.split())
                if len(short) > 120:
                    short = short[:117].rstrip() + "..."
                highlights.append(f"{lbl}: {short}")
            parts.append(" | ".join(highlights))

        extractive = "\n".join(parts).strip()
        if not extractive:
            return ""

        if ai_runtime is not None and getattr(ai_runtime, "is_loaded", False):
            try:
                safe_src = sanitize_untrusted_text(extractive, max_chars=1800, allow_newlines=True)
                prompt = (
                    "Summarize this conversation transcript in 2 concise factual sentences without "
                    "inventing details.\n<transcript>\n"
                    f"{safe_src}\n</transcript>"
                )
                generated = ai_runtime.generate(prompt)
                if isinstance(generated, str) and generated.strip():
                    return strip_unsafe_control_chars(generated, allow_newlines=False).strip()[:600]
            except Exception:
                pass
        return extractive[:900]

    def format_for_prompt(self) -> str:
        if not self._messages and not self._rolling_summary:
            return ""

        lines: list[str] = []
        total = 0

        if self._rolling_summary:
            summary_line = sanitize_untrusted_text(
                f"Earlier summary: {self._rolling_summary}",
                max_chars=min(self.max_summary_chars + 20, self.max_total_chars // 2),
                allow_newlines=False,
            )
            if summary_line:
                lines.append(summary_line)
                total += len(summary_line)

        if self._last_subject:
            subj_line = sanitize_untrusted_text(
                f"Active topic/reference: {self._last_subject}",
                max_chars=180,
                allow_newlines=False,
            )
            if subj_line and total + len(subj_line) < self.max_total_chars:
                lines.append(subj_line)
                total += len(subj_line)

        msg_lines: list[str] = []
        for message in reversed(self._messages):
            label = "User" if message.role == "user" else "RIYA"
            sanitized = sanitize_untrusted_text(
                message.content,
                max_chars=self.max_message_chars,
                allow_newlines=True,
                neutralize_roles=True,
            )
            entry = f"{label}: {sanitized}"
            if total + len(entry) > self.max_total_chars:
                break
            msg_lines.append(entry)
            total += len(entry) + 1

        msg_lines.reverse()
        lines.extend(msg_lines)
        return "\n".join(lines)

    def hydrate_from_history(
        self,
        messages: list[dict[str, Any]],
        *,
        summary_record: dict[str, Any] | None = None,
    ) -> None:
        """Restore persisted per-user turns and rolling summary into this context."""
        self.clear()
        if isinstance(summary_record, dict):
            summary = str(summary_record.get("summary") or "").strip()
            if summary:
                self._rolling_summary = summary[: self.max_summary_chars]
            for topic in summary_record.get("topics") or []:
                self._record_topic(str(topic))
            entities = summary_record.get("entities") or {}
            if isinstance(entities, dict):
                for ent in entities.get("recent", []) or []:
                    self._record_entity(str(ent))
                if entities.get("last_subject"):
                    self._last_subject = str(entities["last_subject"])[:160]

        for row in messages:
            role = str(row.get("role", "")).strip().lower()
            content = str(row.get("content", "")).strip()
            if role in ALLOWED_CONVERSATION_ROLES and content:
                self._add(role, content)


class ConversationSummaryStore:
    """SQLite-backed per-user conversation summaries strictly isolated by user_id."""

    def __init__(self, identity_manager, *, permission_manager: Any = None):
        self.identity_manager = identity_manager
        self.database = identity_manager.database
        self.permission_manager = permission_manager

    def _enforce_permission(self, identity: AuthenticatedIdentity, resource: str) -> None:
        if self.permission_manager is None:
            return
        res = self.permission_manager.evaluate(
            identity,
            "personal.history",
            resource_or_target=resource,
            actor="conversation_summary_store",
        )
        if res.blocked:
            raise PermissionError(res.reason)

    def save_summary(
        self,
        identity: AuthenticatedIdentity,
        context: ConversationContext,
        *,
        session_id: str | None = None,
    ) -> None:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "history:summary:write")
        effective_session = session_id or validated.session_id
        summary_text = context.rolling_summary or context.summarize()
        topics_json = json.dumps(list(context.active_topics))
        entities_json = json.dumps(
            {
                "recent": list(context.recent_entities),
                "last_subject": context.last_subject,
            }
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_summaries (
                    user_id, session_id, summary, topics, entities, turn_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, session_id)
                DO UPDATE SET
                    summary = excluded.summary,
                    topics = excluded.topics,
                    entities = excluded.entities,
                    turn_count = excluded.turn_count,
                    updated_at = excluded.updated_at
                """,
                (
                    validated.user_id,
                    effective_session,
                    summary_text,
                    topics_json,
                    entities_json,
                    len(context),
                    now_iso,
                ),
            )
            conn.commit()

    def get_summary(
        self,
        identity: AuthenticatedIdentity,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "history:summary:read")
        with self.database.connect() as conn:
            if session_id is not None:
                row = conn.execute(
                    """
                    SELECT session_id, summary, topics, entities, turn_count, updated_at
                    FROM conversation_summaries
                    WHERE user_id = ? AND session_id = ?
                    """,
                    (validated.user_id, session_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT session_id, summary, topics, entities, turn_count, updated_at
                    FROM conversation_summaries
                    WHERE user_id = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (validated.user_id,),
                ).fetchone()
        if row is None:
            return None
        return {
            "session_id": str(row["session_id"]),
            "summary": str(row["summary"]),
            "topics": json.loads(row["topics"] or "[]"),
            "entities": json.loads(row["entities"] or "{}"),
            "turn_count": int(row["turn_count"]),
            "updated_at": str(row["updated_at"]),
        }
