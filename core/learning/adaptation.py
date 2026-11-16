"""Controlled, inspectable prompt/configuration adaptation (no weight modification)."""

from __future__ import annotations

from dataclasses import dataclass

from core.ai.safety import MAX_GENERATION_TOKENS, sanitize_untrusted_text
from core.learning.preferences import AdaptiveProfile


@dataclass(frozen=True)
class AdaptationSettings:
    """Resolved prompt-level and generation-parameter adaptation for one turn."""

    system_guidance: str
    suggested_max_tokens: int


class PromptAdapter:
    """Translates a user's AdaptiveProfile into safe system-level style guidance and token hints."""

    _STYLE_HINTS = {
        "concise": "Keep answers concise and to the point.",
        "balanced": "Give clear, well-proportioned answers.",
        "detailed": "Provide thorough explanations with helpful context.",
    }
    _TONE_HINTS = {
        "helpful": "Use a calm, helpful tone.",
        "direct": "Use a direct, matter-of-fact tone.",
        "friendly": "Use a warm, friendly tone.",
        "technical": "Use accurate technical terminology where appropriate.",
    }
    _FORMAT_HINTS = {
        "plain": "",
        "bullets": "Prefer clean bullet points when listing multiple items.",
        "step_by_step": "Present multi-part guidance as numbered steps.",
    }
    _STYLE_TOKENS = {
        "concise": 160,
        "balanced": 256,
        "detailed": 512,
    }

    @classmethod
    def adapt(
        cls,
        profile: AdaptiveProfile | None,
        *,
        base_max_tokens: int = 256,
    ) -> AdaptationSettings:
        if profile is None:
            return AdaptationSettings(
                system_guidance="",
                suggested_max_tokens=max(64, min(int(base_max_tokens), MAX_GENERATION_TOKENS)),
            )

        parts: list[str] = []
        style_hint = cls._STYLE_HINTS.get(profile.response_style, "")
        if style_hint:
            parts.append(style_hint)
        tone_hint = cls._TONE_HINTS.get(profile.tone, "")
        if tone_hint:
            parts.append(tone_hint)
        fmt_hint = cls._FORMAT_HINTS.get(profile.formatting_preference, "")
        if fmt_hint:
            parts.append(fmt_hint)

        if profile.custom_guidance:
            safe_custom = sanitize_untrusted_text(
                profile.custom_guidance,
                max_chars=200,
                allow_newlines=False,
                neutralize_roles=True,
            ).strip()
            if safe_custom:
                parts.append(f"Additional user preference: {safe_custom}")

        guidance = " ".join(parts).strip()[:400]
        target_tokens = cls._STYLE_TOKENS.get(profile.response_style, base_max_tokens)
        bounded_tokens = max(64, min(max(base_max_tokens, target_tokens), MAX_GENERATION_TOKENS))
        return AdaptationSettings(
            system_guidance=guidance,
            suggested_max_tokens=bounded_tokens,
        )
