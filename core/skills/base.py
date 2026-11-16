"""Modular reusable Skill interfaces, contracts, and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.identity.models import AuthenticatedIdentity
from core.security.manager import RiskLevel


@dataclass
class SkillContext:
    """Trusted runtime services passed into a Skill handler."""

    identity: AuthenticatedIdentity | None
    tool_executor: Any
    permission_manager: Any = None
    task_store: Any = None
    assistant: Any = None

    def run_tool(
        self,
        tool_name: str,
        *,
        confirmation_token: str | None = None,
        **parameters,
    ) -> dict[str, Any]:
        """Execute a tool strictly through RIYA's central ToolExecutor."""
        return self.tool_executor.execute(
            tool_name,
            confirmation_token=confirmation_token,
            user_context=self.identity,
            **parameters,
        )


@dataclass(frozen=True)
class SkillDefinition:
    """Formal contract for a reusable modular RIYA skill."""

    skill_id: str
    name: str
    version: str
    description: str
    required_permissions: tuple[str, ...]
    input_schema: dict[str, dict[str, Any]]
    output_schema: dict[str, str]
    handler: Callable[[SkillContext, dict[str, Any]], dict[str, Any]]
    risk_level: RiskLevel = RiskLevel.LOW
    documentation: str = ""
    source: str = "builtin"

    def validate_inputs(self, inputs: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(inputs or {})
        for field_name, spec in self.input_schema.items():
            required = bool(spec.get("required", False))
            expected_type = spec.get("type", str)
            if field_name not in payload or payload[field_name] is None:
                if required:
                    raise ValueError(
                        f"Skill '{self.skill_id}' missing required input parameter '{field_name}'."
                    )
                if "default" in spec:
                    payload[field_name] = spec["default"]
                continue
            val = payload[field_name]
            if not isinstance(val, expected_type):
                raise ValueError(
                    f"Skill '{self.skill_id}' parameter '{field_name}' expected {expected_type.__name__}."
                )
            if isinstance(val, str) and not spec.get("allow_empty", True) and not val.strip():
                raise ValueError(
                    f"Skill '{self.skill_id}' parameter '{field_name}' cannot be empty."
                )
        unknown = set(payload) - set(self.input_schema)
        if unknown:
            raise ValueError(
                f"Skill '{self.skill_id}' received unexpected input(s): {sorted(unknown)}"
            )
        return payload

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(output, dict):
            raise ValueError(f"Skill '{self.skill_id}' must return a dict output.")
        missing = [k for k in self.output_schema if k not in output]
        if missing:
            raise ValueError(
                f"Skill '{self.skill_id}' output missed required field(s): {missing}"
            )
        return output


@dataclass
class SkillExecutionResult:
    """Structured result from invoking a skill through SkillRegistry."""

    skill_id: str
    version: str
    success: bool
    status: str  # "success" | "blocked" | "confirmation_required" | "failed"
    output: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: str = ""
    requires_confirmation: bool = False
    confirmation_payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "success": self.success,
            "status": self.status,
            "output": dict(self.output),
            "summary": self.summary,
            "error": self.error,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_payload": self.confirmation_payload,
        }
