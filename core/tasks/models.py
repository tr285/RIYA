"""Structured data models for autonomous multi-step planning and task execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import threading
from typing import Any
from uuid import uuid4

from core.agent.planner import Plan, PlanStep
from core.identity.models import AuthenticatedIdentity


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


def compute_step_idempotency_key(
    step_id: str,
    action: str,
    parameters: dict[str, Any],
) -> str:
    payload = json.dumps(
        {
            "step_id": step_id,
            "action": action,
            "parameters": parameters,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass
class TaskStep:
    """One executable step inside an autonomous TaskPlan."""

    step_id: str
    action: str
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    max_retries: int = 0
    retry_delay_seconds: float = 0.0
    timeout_seconds: float | None = None
    verification_rule: str = ""
    idempotency_key: str = ""
    result: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    error: str = ""
    started_at: str | None = None
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = compute_step_idempotency_key(
                self.step_id,
                self.action,
                self.parameters,
            )

    def to_plan_step(self) -> PlanStep:
        return PlanStep(
            action=self.action,
            parameters=dict(self.parameters),
            description=self.description or self.action,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "parameters": dict(self.parameters),
            "description": self.description,
            "depends_on": list(self.depends_on),
            "status": self.status.value,
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
            "timeout_seconds": self.timeout_seconds,
            "verification_rule": self.verification_rule,
            "idempotency_key": self.idempotency_key,
            "result": dict(self.result) if isinstance(self.result, dict) else self.result,
            "verification": dict(self.verification)
            if isinstance(self.verification, dict)
            else self.verification,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskStep":
        return cls(
            step_id=str(data["step_id"]),
            action=str(data["action"]),
            parameters=dict(data.get("parameters") or {}),
            description=str(data.get("description") or ""),
            depends_on=[str(d) for d in (data.get("depends_on") or [])],
            status=StepStatus(str(data.get("status") or StepStatus.PENDING.value)),
            attempts=int(data.get("attempts") or 0),
            max_retries=int(data.get("max_retries") or 0),
            retry_delay_seconds=float(data.get("retry_delay_seconds") or 0.0),
            timeout_seconds=(
                float(data["timeout_seconds"])
                if data.get("timeout_seconds") is not None
                else None
            ),
            verification_rule=str(data.get("verification_rule") or ""),
            idempotency_key=str(data.get("idempotency_key") or ""),
            result=data.get("result"),
            verification=data.get("verification"),
            error=str(data.get("error") or ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class TaskPlan:
    """DAG-aware multi-step autonomous task plan."""

    goal: str
    steps: list[TaskStep] = field(default_factory=list)
    task_id: str = field(default_factory=lambda: f"tsk_{uuid4().hex[:16]}")
    intent: str = "task"
    status: TaskStatus = TaskStatus.PENDING
    clarification_needed: bool = False
    clarification_prompt: str = ""
    current_step_index: int = 0
    idempotency_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            payload = json.dumps(
                {
                    "goal": self.goal,
                    "steps": [(s.action, s.parameters) for s in self.steps],
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=str,
            )
            self.idempotency_key = sha256(payload.encode("utf-8")).hexdigest()[:24]

    def to_legacy_plan(self) -> Plan:
        return Plan(
            goal=self.goal,
            steps=[step.to_plan_step() for step in self.steps],
        )

    @classmethod
    def from_legacy_plan(
        cls,
        plan: Plan,
        *,
        task_id: str | None = None,
        sequential_dependencies: bool = True,
    ) -> "TaskPlan":
        steps: list[TaskStep] = []
        prev_id: str | None = None
        for idx, step in enumerate(plan.steps, start=1):
            sid = f"step_{idx}"
            deps = [prev_id] if (sequential_dependencies and prev_id is not None) else []
            steps.append(
                TaskStep(
                    step_id=sid,
                    action=step.action,
                    parameters=dict(step.parameters),
                    description=step.description or step.action,
                    depends_on=deps,
                )
            )
            prev_id = sid
        return cls(
            task_id=task_id or f"tsk_{uuid4().hex[:16]}",
            goal=plan.goal,
            steps=steps,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "intent": self.intent,
            "status": self.status.value,
            "clarification_needed": self.clarification_needed,
            "clarification_prompt": self.clarification_prompt,
            "current_step_index": self.current_step_index,
            "idempotency_key": self.idempotency_key,
            "metadata": dict(self.metadata),
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskPlan":
        return cls(
            task_id=str(data.get("task_id") or f"tsk_{uuid4().hex[:16]}"),
            goal=str(data.get("goal") or ""),
            intent=str(data.get("intent") or "task"),
            status=TaskStatus(str(data.get("status") or TaskStatus.PENDING.value)),
            clarification_needed=bool(data.get("clarification_needed", False)),
            clarification_prompt=str(data.get("clarification_prompt") or ""),
            current_step_index=int(data.get("current_step_index") or 0),
            idempotency_key=str(data.get("idempotency_key") or ""),
            metadata=dict(data.get("metadata") or {}),
            steps=[TaskStep.from_dict(s) for s in (data.get("steps") or [])],
        )


@dataclass
class TaskExecutionContext:
    """Runtime execution state for one multi-step task."""

    task_id: str
    user_id: str | None = None
    session_id: str | None = None
    identity: AuthenticatedIdentity | None = None
    step_outputs: dict[str, Any] = field(default_factory=dict)
    completed_step_ids: set[str] = field(default_factory=set)
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()


# Task-layer alias for Milestone 5.1 specification compatibility; identity scope
# remains `core.identity.context.ExecutionContext`.
ExecutionContext = TaskExecutionContext


@dataclass
class TaskResult:
    """Structured outcome returned by TaskExecutor and AgentCoordinator."""

    task_id: str
    goal: str
    status: TaskStatus
    steps_completed: int
    steps_total: int
    step_results: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    requires_confirmation: bool = False
    confirmation_payload: dict[str, Any] | None = None
    clarification_prompt: str = ""
    verified: bool = False
    replanned: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status.value,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "step_results": list(self.step_results),
            "summary": self.summary,
            "error": self.error,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_payload": self.confirmation_payload,
            "clarification_prompt": self.clarification_prompt,
            "verified": self.verified,
            "replanned": self.replanned,
            "duration_ms": self.duration_ms,
        }
