"""Strict structured-output parsing and validation for untrusted LLM responses."""

from __future__ import annotations

import json
from typing import Any

from core.ai.safety import MAX_MODEL_RESPONSE_CHARS, strip_unsafe_control_chars


MAX_RESPONSE_CHARS = MAX_MODEL_RESPONSE_CHARS
MAX_JSON_DEPTH = 6
MAX_REASON_CHARS = 512
MAX_DESCRIPTION_CHARS = 256


def _strip_single_markdown_fence(text: str) -> str:
    """Unwrap a single outer markdown code fence only if the full response is fenced."""

    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```")):
        return stripped

    if stripped.count("```") != 2:
        return stripped

    first_newline = stripped.find("\n")
    if first_newline == -1:
        return stripped

    opening_line = stripped[3:first_newline].strip().lower()
    if opening_line not in ("", "json"):
        return stripped

    inner = stripped[first_newline + 1 : -3].strip()
    return inner


def _validate_json_depth(text: str, max_depth: int) -> None:
    """Ensure bracket/brace nesting depth stays bounded before JSON decoding."""

    depth = 0
    in_string = False
    escaped = False

    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if depth > max_depth:
                raise ValueError(
                    f"Structured JSON exceeded maximum nesting depth ({max_depth})."
                )
        elif char in "}]":
            depth -= 1
            if depth < 0:
                raise ValueError("Malformed JSON brackets in model output.")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject JSON objects with duplicate keys instead of silently overwriting."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key is not allowed: {key!r}")
        result[key] = value
    return result


def _reject_invalid_constant(value: str) -> Any:
    """Reject non-standard JSON numeric constants like NaN and Infinity."""

    raise ValueError(f"Invalid non-standard JSON constant: {value!r}")


def parse_strict_json_object(
    raw_response: Any,
    *,
    max_chars: int = MAX_MODEL_RESPONSE_CHARS,
    max_depth: int = MAX_JSON_DEPTH,
) -> dict[str, Any]:
    """Parse one complete, bounded JSON object without duplicates or trailing data."""

    if not isinstance(raw_response, str):
        raise ValueError("Model response must be a text string.")

    cleaned = strip_unsafe_control_chars(raw_response, allow_newlines=True).strip()

    if not cleaned:
        raise ValueError("RIYA returned an empty structured response.")

    if len(cleaned) > max_chars:
        raise ValueError(
            f"RIYA structured response exceeded maximum size ({max_chars} chars)."
        )

    candidate = _strip_single_markdown_fence(cleaned)

    if not candidate.startswith("{") or not candidate.endswith("}"):
        raise ValueError(
            "RIYA structured response must contain only a single JSON object."
        )

    _validate_json_depth(candidate, max_depth)

    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_invalid_constant,
    )

    try:
        parsed, end_index = decoder.raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"RIYA returned invalid structured JSON: {exc}") from exc

    if end_index != len(candidate):
        raise ValueError(
            "RIYA returned ambiguous or duplicate JSON data in a single response."
        )

    if not isinstance(parsed, dict):
        raise ValueError("Structured response must be a JSON object.")

    return parsed
