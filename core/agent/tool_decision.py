import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolDecision:
    """A structured decision made by RIYA's AI."""

    tool: str | None
    parameters: dict[str, Any]
    reason: str = ""


class ToolDecisionEngine:
    """Convert natural-language requests into safe tool decisions."""

    def __init__(self, ai_runtime, tool_registry):
        self.ai = ai_runtime
        self.tools = tool_registry

    def _tool_descriptions(self) -> str:
        """Build the tool list sent to the local model."""

        descriptions = []

        for tool in self.tools.list_tools():
            descriptions.append(
                f"- {tool.name}: {tool.description}"
            )

        return "\n".join(descriptions)

    def decide(self, request: str) -> ToolDecision:
        """Ask the local AI which registered tool should handle a request."""

        prompt = f"""
You are RIYA's tool decision engine.

Your job is ONLY to decide whether the user's request
should use one of the available tools.

Available tools:
{self._tool_descriptions()}

User request:
{request}

Return ONLY valid JSON in this exact format:

{{
  "tool": "tool_name_or_null",
  "parameters": {{}},
  "reason": "short explanation"
}}

Rules:
- Use only tools from the available tool list.
- Never invent a tool.
- Never invent parameters that the tool does not need.
- If no tool is appropriate, use null for "tool".
- Do not execute anything.
- Do not include markdown.
"""

        response = self.ai.generate(prompt)

        return self._parse_response(response)

    def _parse_response(self, response: str) -> ToolDecision:
        """Extract, parse, and validate the first JSON object."""

        response = response.strip()

        start = response.find("{")

        if start == -1:
            raise ValueError(
                f"RIYA returned no JSON tool decision: {response}"
            )

        depth = 0
        end = None

        for index in range(start, len(response)):
            if response[index] == "{":
                depth += 1

            elif response[index] == "}":
                depth -= 1

                if depth == 0:
                    end = index + 1
                    break

        if end is None:
            raise ValueError(
                f"RIYA returned incomplete JSON: {response}"
            )

        json_text = response[start:end]

        try:
            data = json.loads(json_text)

        except json.JSONDecodeError as exc:
            raise ValueError(
                f"RIYA returned invalid tool JSON: {json_text}"
            ) from exc

        tool_name = data.get("tool")
        parameters = data.get("parameters", {})
        reason = data.get("reason", "")

        if tool_name is not None and not self.tools.has(tool_name):
            raise ValueError(
                f"RIYA selected an unavailable tool: {tool_name}"
            )

        if not isinstance(parameters, dict):
            raise ValueError(
                "RIYA tool parameters must be an object."
            )

        # Normalize common parameter naming variations.
        if tool_name == "set_volume":
            if "level" not in parameters and "volume" in parameters:
                parameters["level"] = parameters.pop("volume")

        return ToolDecision(
            tool=tool_name,
            parameters=parameters,
            reason=reason,
        )