"""Controlled preference learning from feedback and explicit user style directives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.ai.safety import strip_unsafe_control_chars
from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity
from core.learning.feedback import FeedbackEntry, FeedbackStore


VALID_RESPONSE_STYLES = frozenset({"concise", "balanced", "detailed"})
VALID_TONES = frozenset({"helpful", "direct", "friendly", "technical"})
VALID_FORMATTING = frozenset({"plain", "bullets", "step_by_step"})


@dataclass(frozen=True)
class AdaptiveProfile:
    """User-isolated adaptive response configuration (no weight training)."""

    user_id: str
    response_style: str = "balanced"
    tone: str = "helpful"
    formatting_preference: str = "plain"
    custom_guidance: str = ""
    positive_count: int = 0
    negative_count: int = 0
    correction_count: int = 0
    updated_at: str = ""


class PreferenceLearner:
    """Learns explicit user communication preferences from feedback/corrections without altering security."""

    def __init__(
        self,
        identity_manager: IdentityManager,
        *,
        permission_manager: Any = None,
        feedback_store: FeedbackStore | None = None,
    ):
        self.identity_manager = identity_manager
        self.database = identity_manager.database
        self.permission_manager = permission_manager
        self.feedback_store = feedback_store or FeedbackStore(
            identity_manager,
            permission_manager=permission_manager,
        )

    def _enforce_permission(self, identity: AuthenticatedIdentity, resource: str) -> None:
        if self.permission_manager is None:
            return
        res = self.permission_manager.evaluate(
            identity,
            "learning.manage",
            resource_or_target=resource,
            actor="preference_learner",
        )
        if res.blocked:
            raise PermissionError(res.reason)

    def get_profile(self, identity: AuthenticatedIdentity) -> AdaptiveProfile:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "learning:profile:read")
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, response_style, tone, formatting_preference,
                       custom_guidance, positive_count, negative_count,
                       correction_count, updated_at
                FROM adaptive_profiles
                WHERE user_id = ?
                """,
                (validated.user_id,),
            ).fetchone()
        if row is None:
            return AdaptiveProfile(
                user_id=validated.user_id,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        return AdaptiveProfile(
            user_id=str(row["user_id"]),
            response_style=str(row["response_style"]),
            tone=str(row["tone"]),
            formatting_preference=str(row["formatting_preference"]),
            custom_guidance=str(row["custom_guidance"] or ""),
            positive_count=int(row["positive_count"]),
            negative_count=int(row["negative_count"]),
            correction_count=int(row["correction_count"]),
            updated_at=str(row["updated_at"]),
        )

    def update_profile(
        self,
        identity: AuthenticatedIdentity,
        *,
        response_style: str | None = None,
        tone: str | None = None,
        formatting_preference: str | None = None,
        custom_guidance: str | None = None,
    ) -> AdaptiveProfile:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "learning:profile:write")
        current = self.get_profile(validated)

        new_style = (
            str(response_style).strip().lower()
            if response_style is not None
            else current.response_style
        )
        if new_style not in VALID_RESPONSE_STYLES:
            raise ValueError(f"Unsupported response_style: {new_style!r}")

        new_tone = str(tone).strip().lower() if tone is not None else current.tone
        if new_tone not in VALID_TONES:
            raise ValueError(f"Unsupported tone: {new_tone!r}")

        new_fmt = (
            str(formatting_preference).strip().lower()
            if formatting_preference is not None
            else current.formatting_preference
        )
        if new_fmt not in VALID_FORMATTING:
            raise ValueError(f"Unsupported formatting_preference: {new_fmt!r}")

        new_guidance = current.custom_guidance
        if custom_guidance is not None:
            new_guidance = strip_unsafe_control_chars(str(custom_guidance), allow_newlines=False).strip()[:300]

        now_iso = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO adaptive_profiles (
                    user_id, response_style, tone, formatting_preference,
                    custom_guidance, positive_count, negative_count,
                    correction_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    response_style = excluded.response_style,
                    tone = excluded.tone,
                    formatting_preference = excluded.formatting_preference,
                    custom_guidance = excluded.custom_guidance,
                    updated_at = excluded.updated_at
                """,
                (
                    validated.user_id,
                    new_style,
                    new_tone,
                    new_fmt,
                    new_guidance,
                    current.positive_count,
                    current.negative_count,
                    current.correction_count,
                    now_iso,
                ),
            )
            conn.commit()
        return self.get_profile(validated)

    def learn_from_feedback(
        self,
        identity: AuthenticatedIdentity,
        entry: FeedbackEntry,
    ) -> AdaptiveProfile:
        """Update bounded communication preferences cleanly from recorded feedback/correction."""
        validated = self.identity_manager.validate_session(identity)
        if entry.user_id != validated.user_id:
            raise PermissionError("Cannot apply feedback belonging to a different user.")
        self._enforce_permission(validated, "learning:profile:learn")

        current = self.get_profile(validated)
        pos = current.positive_count + (1 if entry.rating == "positive" else 0)
        neg = current.negative_count + (1 if entry.rating == "negative" else 0)
        corr = current.correction_count + (1 if entry.rating == "correction" else 0)

        new_style = current.response_style
        new_tone = current.tone
        new_fmt = current.formatting_preference
        new_guidance = current.custom_guidance

        signal_text = " ".join(
            [
                entry.correction_text or "",
                " ".join(entry.tags),
            ]
        ).lower()

        if any(tok in signal_text for tok in ("too long", "shorter", "concise", "brief")):
            new_style = "concise"
        elif any(tok in signal_text for tok in ("too short", "more detail", "detailed", "explain thoroughly")):
            new_style = "detailed"

        if any(tok in signal_text for tok in ("bullet", "bullets", "list format")):
            new_fmt = "bullets"
        elif any(tok in signal_text for tok in ("step by step", "step-by-step", "steps")):
            new_fmt = "step_by_step"
        elif "plain" in signal_text:
            new_fmt = "plain"

        if "technical" in signal_text:
            new_tone = "technical"
        elif "direct" in signal_text:
            new_tone = "direct"
        elif "friendly" in signal_text:
            new_tone = "friendly"

        if entry.rating == "correction" and entry.correction_text:
            cleaned_corr = strip_unsafe_control_chars(entry.correction_text, allow_newlines=False).strip()[:240]
            if cleaned_corr:
                new_guidance = cleaned_corr

        now_iso = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO adaptive_profiles (
                    user_id, response_style, tone, formatting_preference,
                    custom_guidance, positive_count, negative_count,
                    correction_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    response_style = excluded.response_style,
                    tone = excluded.tone,
                    formatting_preference = excluded.formatting_preference,
                    custom_guidance = excluded.custom_guidance,
                    positive_count = excluded.positive_count,
                    negative_count = excluded.negative_count,
                    correction_count = excluded.correction_count,
                    updated_at = excluded.updated_at
                """,
                (
                    validated.user_id,
                    new_style,
                    new_tone,
                    new_fmt,
                    new_guidance,
                    pos,
                    neg,
                    corr,
                    now_iso,
                ),
            )
            conn.commit()
        return self.get_profile(validated)

    def reset_profile(self, identity: AuthenticatedIdentity) -> None:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "learning:profile:reset")
        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM adaptive_profiles WHERE user_id = ?",
                (validated.user_id,),
            )
            conn.commit()
