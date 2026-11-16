"""User-isolated feedback and correction storage for RIYA adaptive learning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import secrets
from typing import Any

from core.ai.safety import strip_unsafe_control_chars
from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity


VALID_RATINGS = frozenset({"positive", "negative", "correction"})


@dataclass(frozen=True)
class FeedbackEntry:
    """Single user feedback or response-correction record."""

    feedback_id: str
    user_id: str
    session_id: str | None
    prompt: str
    response: str
    rating: str
    correction_text: str | None
    tags: tuple[str, ...]
    created_at: str


class FeedbackStore:
    """Stores positive/negative feedback and response corrections isolated per user."""

    def __init__(
        self,
        identity_manager: IdentityManager,
        *,
        permission_manager: Any = None,
    ):
        self.identity_manager = identity_manager
        self.database = identity_manager.database
        self.permission_manager = permission_manager

    def _enforce_permission(self, identity: AuthenticatedIdentity, resource: str) -> None:
        if self.permission_manager is None:
            return
        res = self.permission_manager.evaluate(
            identity,
            "learning.manage",
            resource_or_target=resource,
            actor="feedback_store",
        )
        if res.blocked:
            raise PermissionError(res.reason)

    def record_feedback(
        self,
        identity: AuthenticatedIdentity,
        *,
        prompt: str,
        response: str,
        rating: str,
        correction_text: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        session_id: str | None = None,
    ) -> FeedbackEntry:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"learning:feedback:{rating}")

        normalized_rating = str(rating or "").strip().lower()
        if normalized_rating not in VALID_RATINGS:
            raise ValueError(
                f"Invalid feedback rating '{rating}'. Expected 'positive', 'negative', or 'correction'."
            )

        clean_prompt = strip_unsafe_control_chars(str(prompt or ""), allow_newlines=True).strip()[:4000]
        clean_response = strip_unsafe_control_chars(str(response or ""), allow_newlines=True).strip()[:4000]
        if not clean_prompt:
            raise ValueError("Feedback prompt cannot be empty.")

        clean_correction: str | None = None
        if correction_text is not None:
            stripped = strip_unsafe_control_chars(str(correction_text), allow_newlines=True).strip()[:4000]
            clean_correction = stripped or None
        if normalized_rating == "correction" and not clean_correction:
            raise ValueError("correction_text is required when rating is 'correction'.")

        clean_tags = tuple(
            strip_unsafe_control_chars(str(t), allow_newlines=False).strip().lower()[:40]
            for t in (tags or ())
            if str(t).strip()
        )[:12]

        feedback_id = f"fb_{secrets.token_hex(10)}"
        effective_session = session_id or validated.session_id
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_feedback (
                    feedback_id, user_id, session_id, prompt, response,
                    rating, correction_text, tags, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    validated.user_id,
                    effective_session,
                    clean_prompt,
                    clean_response,
                    normalized_rating,
                    clean_correction,
                    json.dumps(list(clean_tags)),
                    now_iso,
                ),
            )
            conn.commit()

        return FeedbackEntry(
            feedback_id=feedback_id,
            user_id=validated.user_id,
            session_id=effective_session,
            prompt=clean_prompt,
            response=clean_response,
            rating=normalized_rating,
            correction_text=clean_correction,
            tags=clean_tags,
            created_at=now_iso,
        )

    def list_feedback(
        self,
        identity: AuthenticatedIdentity,
        *,
        rating: str | None = None,
        limit: int = 50,
    ) -> list[FeedbackEntry]:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "learning:feedback:read")
        safe_limit = max(1, min(int(limit), 500))

        with self.database.connect() as conn:
            if rating is not None:
                norm_rating = str(rating).strip().lower()
                if norm_rating not in VALID_RATINGS:
                    raise ValueError(f"Invalid rating filter: {rating!r}")
                rows = conn.execute(
                    """
                    SELECT feedback_id, user_id, session_id, prompt, response,
                           rating, correction_text, tags, created_at
                    FROM user_feedback
                    WHERE user_id = ? AND rating = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (validated.user_id, norm_rating, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT feedback_id, user_id, session_id, prompt, response,
                           rating, correction_text, tags, created_at
                    FROM user_feedback
                    WHERE user_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (validated.user_id, safe_limit),
                ).fetchall()

        results: list[FeedbackEntry] = []
        for row in rows:
            try:
                tags = tuple(json.loads(row["tags"] or "[]"))
            except Exception:
                tags = ()
            results.append(
                FeedbackEntry(
                    feedback_id=str(row["feedback_id"]),
                    user_id=str(row["user_id"]),
                    session_id=row["session_id"],
                    prompt=str(row["prompt"]),
                    response=str(row["response"]),
                    rating=str(row["rating"]),
                    correction_text=row["correction_text"],
                    tags=tags,
                    created_at=str(row["created_at"]),
                )
            )
        return results

    def feedback_stats(self, identity: AuthenticatedIdentity) -> dict[str, int]:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "learning:feedback:stats")
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT rating, COUNT(*) AS cnt
                FROM user_feedback
                WHERE user_id = ?
                GROUP BY rating
                """,
                (validated.user_id,),
            ).fetchall()
        counts = {"positive": 0, "negative": 0, "correction": 0, "total": 0}
        for row in rows:
            r = str(row["rating"])
            c = int(row["cnt"])
            if r in counts:
                counts[r] = c
            counts["total"] += c
        return counts

    def export_corrections_jsonl(self, identity: AuthenticatedIdentity) -> str:
        """Export user feedback & corrections as offline JSONL for evaluation/review."""
        entries = self.list_feedback(identity, limit=500)
        lines = [
            json.dumps(
                {
                    "feedback_id": e.feedback_id,
                    "prompt": e.prompt,
                    "response": e.response,
                    "rating": e.rating,
                    "correction_text": e.correction_text,
                    "tags": list(e.tags),
                    "created_at": e.created_at,
                },
                ensure_ascii=False,
            )
            for e in entries
        ]
        return "\n".join(lines)
