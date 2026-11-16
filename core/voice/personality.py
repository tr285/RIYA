"""Expressive voice personality profiles and speech-chunk formatting."""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.ai.safety import strip_unsafe_control_chars


@dataclass(frozen=True)
class VoiceStyleProfile:
    """Delivery settings for a named conversational voice style.

    Note: Native macOS speech synthesis (``/usr/bin/say``) controls rate and
    textual phrasing pauses, not true neural acoustic emotion. Values here must
    never be represented as neural emotional synthesis.
    """

    name: str
    rate_multiplier: float
    description: str


STYLE_PROFILES: dict[str, VoiceStyleProfile] = {
    "friendly": VoiceStyleProfile(
        name="friendly",
        rate_multiplier=1.0,
        description="Warm, natural conversational pace.",
    ),
    "calm": VoiceStyleProfile(
        name="calm",
        rate_multiplier=0.90,
        description="Measured, unhurried delivery for focused listening.",
    ),
    "excited": VoiceStyleProfile(
        name="excited",
        rate_multiplier=1.08,
        description="Upbeat, brisk conversational cadence.",
    ),
    "empathetic": VoiceStyleProfile(
        name="empathetic",
        rate_multiplier=0.92,
        description="Gentle, supportive conversational pacing.",
    ),
    "professional": VoiceStyleProfile(
        name="professional",
        rate_multiplier=1.0,
        description="Clear, concise, neutral assistant delivery.",
    ),
}

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|~~)")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+|\n+")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:])\s+")
_CASUAL_OPENING_RE = re.compile(
    r"^(?:Hey there!|Hey!|Hi there!|Sure!|Awesome!|Great!)\s+",
    re.IGNORECASE,
)

_STYLE_PROMPT_INSTRUCTIONS: dict[str, str] = {
    "friendly": "Respond in a warm, natural conversational tone.",
    "calm": "Respond in a calm, measured, steady tone without exclamation marks.",
    "excited": "Respond in an upbeat, energetic tone while keeping facts exact.",
    "empathetic": "Respond in a gentle, supportive, empathetic tone without inventing facts.",
    "professional": "Respond in a clear, concise, formal tone without casual fillers.",
}


def style_prompt_instruction(style: str = "friendly") -> str:
    """Return a textual style guidance instruction for the chosen voice profile."""

    normalized = style.strip().lower() if isinstance(style, str) else "friendly"
    return _STYLE_PROMPT_INSTRUCTIONS.get(
        normalized,
        _STYLE_PROMPT_INSTRUCTIONS["friendly"],
    )


def apply_response_style_wording(text: str, style: str = "friendly") -> str:
    """Adapt spoken wording/cadence to the active style without altering facts.

    Security confirmation prompts, empty strings, and internal error diagnostics
    are preserved unchanged so meaning and safety notices remain exact.
    """

    if not isinstance(text, str) or not text:
        return ""

    lower = text.lower()
    if (
        "proceed?" in lower
        or "requires confirmation" in lower
        or "confirmation needed" in lower
        or lower.startswith("i encountered an internal error")
        or lower.startswith("i timed out")
    ):
        return text

    normalized_style = style.strip().lower() if isinstance(style, str) else "friendly"
    styled = text

    if normalized_style == "professional":
        styled = _CASUAL_OPENING_RE.sub("", styled).strip()
        styled = re.sub(r"!+", ".", styled)
    elif normalized_style == "calm":
        styled = re.sub(r"!+", ".", styled)
    elif normalized_style == "excited":
        if styled.startswith("Done."):
            styled = "Done!" + styled[5:]
        elif styled.startswith("Ready."):
            styled = "Ready!" + styled[6:]
        elif styled.endswith(".") and not styled.endswith("..."):
            styled = styled[:-1] + "!"
    elif normalized_style == "empathetic":
        if styled.startswith("Cannot "):
            styled = "I'm sorry, I cannot " + styled[len("Cannot "):]
        elif styled == "Action cancelled." or styled.startswith("Action cancelled."):
            styled = "No problem, action cancelled." + styled[len("Action cancelled."):]
        elif styled.startswith("I couldn't "):
            styled = "I'm sorry, I couldn't " + styled[len("I couldn't "):]

    return styled


def style_rate_wpm(base_rate_wpm: int, style: str) -> int:
    """Compute effective words-per-minute for a validated style profile."""

    profile = STYLE_PROFILES.get(style, STYLE_PROFILES["friendly"])
    effective = int(round(base_rate_wpm * profile.rate_multiplier))
    return max(90, min(360, effective))


def prepare_text_for_speech(
    text: str,
    *,
    style: str = "friendly",
    max_chars: int = 1500,
) -> str:
    """Convert markdown assistant replies into clean, safe, speakable text.

    Confirmation prompts (containing ``Proceed?``) are preserved verbatim after
    control-character sanitization so security details are never altered.
    """

    if not isinstance(text, str):
        return ""

    cleaned = strip_unsafe_control_chars(text, allow_newlines=True).strip()
    if not cleaned:
        return ""

    # Never rewrite or paraphrase security confirmation prompts.
    if "proceed?" in cleaned.lower():
        collapsed = " ".join(cleaned.split())
        if len(collapsed) > max_chars:
            return collapsed[: max_chars - 3].rstrip() + "..."
        return collapsed

    spoken = _CODE_BLOCK_RE.sub(" I have shown that code in the transcript. ", cleaned)
    spoken = _MARKDOWN_LINK_RE.sub(r"\1", spoken)
    spoken = _INLINE_CODE_RE.sub(r"\1", spoken)
    spoken = _HEADING_RE.sub("", spoken)
    spoken = _BULLET_RE.sub("", spoken)
    spoken = _EMPHASIS_RE.sub("", spoken)
    spoken = _WHITESPACE_RE.sub(" ", spoken)
    spoken = re.sub(r"\n{2,}", "\n", spoken).strip()
    spoken = apply_response_style_wording(spoken, style=style)

    if len(spoken) > max_chars:
        truncated = spoken[:max_chars].rsplit(" ", 1)[0].strip()
        spoken = (truncated or spoken[:max_chars]).rstrip(".,;:") + "..."

    return spoken


def split_into_speech_chunks(text: str, max_chunk_chars: int = 220) -> list[str]:
    """Split text on sentence/clause/word boundaries without splitting mid-word."""

    if not isinstance(text, str):
        return []
    cleaned = strip_unsafe_control_chars(text, allow_newlines=True).strip()
    if not cleaned:
        return []

    limit = max(40, int(max_chunk_chars))
    raw_sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(cleaned) if s.strip()]

    clauses: list[str] = []
    for sentence in raw_sentences:
        if len(sentence) <= limit:
            clauses.append(sentence)
            continue
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            if len(clause) <= limit:
                clauses.append(clause)
            else:
                # Word-level split as last resort — never cuts inside a word unless a single
                # token itself exceeds limit.
                current = ""
                for word in clause.split():
                    if not current:
                        current = word
                    elif len(current) + 1 + len(word) <= limit:
                        current = f"{current} {word}"
                    else:
                        clauses.append(current)
                        current = word
                if current:
                    clauses.append(current)

    # Merge tiny neighboring fragments up to limit for smoother cadence.
    chunks: list[str] = []
    buffer = ""
    for piece in clauses:
        if not buffer:
            buffer = piece
        elif len(buffer) + 1 + len(piece) <= limit:
            buffer = f"{buffer} {piece}"
        else:
            chunks.append(buffer)
            buffer = piece
    if buffer:
        chunks.append(buffer)

    return chunks
