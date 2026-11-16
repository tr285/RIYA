"""Multi-step TaskExecutor with DAG dependencies, retries, checkpoints, idempotency & verification."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable

from core.agent.tool_executor import ToolExecutor
from core.identity.models import AuthenticatedIdentity
from core.tasks.models import (
    StepStatus,
    TaskExecutionContext,
    TaskPlan,
    TaskResult,
    TaskStatus,
    TaskStep,
)
from core.tasks.store import TaskMemoryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


ProgressCallback = Callable[[dict[str, Any]], None]


class TaskExecutor:
    """Executes structured TaskPlans safely through RIYA's central ToolExecutor."""

    def __init__(
        self,
        tool_executor: ToolExecutor,
        *,
        task_store: TaskMemoryStore | None = None,
    ):
        self.tool_executor = tool_executor
        self.task_store = task_store

    @staticmethod
    def _dependencies_satisfied(step: TaskStep, steps_by_id: dict[str, TaskStep]) -> bool:
        for dep_id in step.depends_on:
            dep = steps_by_id.get(dep_id)
            if dep is None or dep.status != StepStatus.COMPLETED:
                return False
        return True

    @staticmethod
    def _has_failed_dependency(step: TaskStep, steps_by_id: dict[str, TaskStep]) -> str | None:
        for dep_id in step.depends_on:
            dep = steps_by_id.get(dep_id)
            if dep is None:
                return f"Missing dependency step '{dep_id}'."
            if dep.status in {StepStatus.FAILED, StepStatus.CANCELLED}:
                return f"Dependency '{dep_id}' did not complete ({dep.status.value})."
        return None

    @staticmethod
    def _emit_progress(
        callback: ProgressCallback | None,
        event: str,
        plan: TaskPlan,
        *,
        step: TaskStep | None = None,
        message: str = "",
    ) -> None:
        if callback is None:
            return
        completed_count = sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED)
        payload: dict[str, Any] = {
            "event": event,
            "task_id": plan.task_id,
            "goal": plan.goal,
            "status": plan.status.value,
            "completed_steps": completed_count,
            "total_steps": len(plan.steps),
            "message": message,
        }
        if step is not None:
            payload["step_id"] = step.step_id
            payload["action"] = step.action
            payload["description"] = step.description
            payload["step_status"] = step.status.value
        try:
            callback(payload)
        except Exception:
            pass

    def _persist_plan(
        self,
        identity: AuthenticatedIdentity | None,
        plan: TaskPlan,
    ) -> None:
        if self.task_store is not None and identity is not None:
            self.task_store.save_task(identity, plan)

    def _persist_step(
        self,
        identity: AuthenticatedIdentity | None,
        plan: TaskPlan,
        step_order: int,
        step: TaskStep,
    ) -> None:
        if self.task_store is not None and identity is not None:
            self.task_store.save_step(identity, plan.task_id, step_order, step)
            self.task_store.save_checkpoint(
                identity,
                plan.task_id,
                step.step_id,
                {
                    "task_status": plan.status.value,
                    "current_step_index": plan.current_step_index,
                    "step_status": step.status.value,
                    "attempts": step.attempts,
                    "completed_steps": [
                        s.step_id for s in plan.steps if s.status == StepStatus.COMPLETED
                    ],
                },
            )

    def _build_step_results(self, plan: TaskPlan) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for s in plan.steps:
            if s.result is not None or s.status in {
                StepStatus.COMPLETED,
                StepStatus.FAILED,
                StepStatus.AWAITING_CONFIRMATION,
            }:
                results.append(
                    {
                        "step_id": s.step_id,
                        "action": s.action,
                        "description": s.description,
                        "status": s.status.value,
                        "result": s.result,
                        "error": s.error,
                    }
                )
        return results

    def _execute_single_step(
        self,
        plan: TaskPlan,
        step: TaskStep,
        step_order: int,
        *,
        identity: AuthenticatedIdentity | None,
        confirmation_token: str | None,
        ctx: TaskExecutionContext,
    ) -> tuple[str, dict[str, Any]]:
        """Execute one TaskStep with bounded retry support for non-security transient failures."""
        max_attempts = 1 + max(0, int(step.max_retries))
        last_result: dict[str, Any] = {}
        token_for_call = confirmation_token

        while True:
            if ctx.is_cancelled():
                if token_for_call is not None:
                    self.tool_executor.cancel_confirmation(token_for_call)
                step.status = StepStatus.CANCELLED
                step.error = "Task cancelled by user."
                self._persist_step(identity, plan, step_order, step)
                return "cancelled", {
                    "status": "failed",
                    "tool": step.action,
                    "error": step.error,
                }

            step.status = StepStatus.RUNNING
            step.attempts += 1
            if not step.started_at:
                step.started_at = _utc_now()
            self._persist_step(identity, plan, step_order, step)

            used_token = token_for_call is not None
            try:
                raw_result = self.tool_executor.execute(
                    step.action,
                    confirmation_token=token_for_call,
                    user_context=identity,
                    **copy.deepcopy(step.parameters),
                )
            except PermissionError as exc:
                last_result = {
                    "status": "authorization_failed",
                    "tool": step.action,
                    "parameters": copy.deepcopy(step.parameters),
                    "reason": str(exc),
                }
                step.status = StepStatus.FAILED
                step.error = str(exc)
                step.result = last_result
                self._persist_step(identity, plan, step_order, step)
                return "authorization_failed", last_result
            except ValueError as exc:
                last_result = {
                    "status": "failed",
                    "tool": step.action,
                    "parameters": copy.deepcopy(step.parameters),
                    "error": str(exc),
                }
                step.status = StepStatus.FAILED
                step.error = str(exc)
                step.result = last_result
                self._persist_step(identity, plan, step_order, step)
                return "failed", last_result
            finally:
                # Never reuse a confirmation token across retry attempts.
                token_for_call = None

            last_result = raw_result if isinstance(raw_result, dict) else {"status": "success", "result": raw_result}
            status = str(last_result.get("status") or "success")

            if status == "confirmation_required":
                step.status = StepStatus.AWAITING_CONFIRMATION
                step.result = last_result
                if self.task_store is not None and identity is not None:
                    risk = self.tool_executor.security.get_risk_level(step.action).value
                    self.task_store.record_approval(
                        identity,
                        plan.task_id,
                        step.step_id,
                        step.action,
                        risk,
                        "requested",
                        str(last_result.get("reason") or "Confirmation required."),
                        step.parameters,
                    )
                self._persist_step(identity, plan, step_order, step)
                return "confirmation_required", last_result

            if status == "success":
                if self.task_store is not None and identity is not None and used_token:
                    risk = self.tool_executor.security.get_risk_level(step.action).value
                    self.task_store.record_approval(
                        identity,
                        plan.task_id,
                        step.step_id,
                        step.action,
                        risk,
                        "approved",
                        "One-time confirmation token consumed.",
                        step.parameters,
                    )
                step.status = StepStatus.COMPLETED
                step.result = last_result
                step.verification = last_result.get("verification")
                step.error = ""
                step.completed_at = _utc_now()
                ctx.step_outputs[step.step_id] = last_result.get("result")
                ctx.completed_step_ids.add(step.step_id)
                self._persist_step(identity, plan, step_order, step)
                return "success", last_result

            # Do not retry authorization failures or security blocks.
            if status in {"authorization_failed", "verification_failed"} or last_result.get("blocked"):
                step.status = StepStatus.FAILED
                step.result = last_result
                step.verification = last_result.get("verification")
                step.error = str(
                    last_result.get("reason")
                    or last_result.get("error")
                    or f"Step exited with status '{status}'."
                )
                self._persist_step(identity, plan, step_order, step)
                return status, last_result

            # Retry transient failures if attempts remain.
            if step.attempts < max_attempts:
                ctx.attempt_history.append(
                    {
                        "step_id": step.step_id,
                        "attempt": step.attempts,
                        "error": last_result.get("error", ""),
                    }
                )
                if step.retry_delay_seconds > 0:
                    time.sleep(min(float(step.retry_delay_seconds), 2.0))
                continue

            step.status = StepStatus.FAILED
            step.result = last_result
            step.error = str(last_result.get("error") or last_result.get("reason") or "Tool failed.")
            self._persist_step(identity, plan, step_order, step)
            return "failed", last_result

    def execute_plan(
        self,
        plan: TaskPlan,
        *,
        identity: AuthenticatedIdentity | None = None,
        confirmation_token: str | None = None,
        confirm_step_id: str | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> TaskResult:
        """Execute a DAG-ordered TaskPlan with idempotency, checkpoints, confirmation & verification."""
        started = time.perf_counter()
        ctx = TaskExecutionContext(
            task_id=plan.task_id,
            user_id=identity.user_id if identity is not None else None,
            session_id=identity.session_id if identity is not None else None,
            identity=identity,
            cancel_event=cancel_event or threading.Event(),
        )

        if plan.clarification_needed:
            plan.status = TaskStatus.PAUSED
            self._persist_plan(identity, plan)
            return TaskResult(
                task_id=plan.task_id,
                goal=plan.goal,
                status=TaskStatus.PAUSED,
                steps_completed=0,
                steps_total=len(plan.steps),
                clarification_prompt=plan.clarification_prompt,
                summary=plan.clarification_prompt,
                verified=False,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )

        plan.status = TaskStatus.RUNNING
        self._persist_plan(identity, plan)
        self._emit_progress(progress_callback, "task_started", plan, message=f"Starting task: {plan.goal}")

        steps_by_id = {step.step_id: step for step in plan.steps}
        step_orders = {step.step_id: idx for idx, step in enumerate(plan.steps)}

        # Pre-populate context outputs for already completed steps (idempotent resume!).
        for step in plan.steps:
            if step.status == StepStatus.COMPLETED:
                ctx.completed_step_ids.add(step.step_id)
                if isinstance(step.result, dict) and "result" in step.result:
                    ctx.step_outputs[step.step_id] = step.result.get("result")

        remaining = [s for s in plan.steps if s.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}]
        token_used = False

        while remaining:
            if ctx.is_cancelled():
                if confirmation_token and not token_used:
                    self.tool_executor.cancel_confirmation(confirmation_token)
                for rem in remaining:
                    rem.status = StepStatus.CANCELLED
                    rem.error = "Task cancelled."
                plan.status = TaskStatus.CANCELLED
                self._persist_plan(identity, plan)
                if self.task_store is not None and identity is not None:
                    self.task_store.update_task_state(
                        identity,
                        plan.task_id,
                        status=TaskStatus.CANCELLED,
                        error="Cancelled by user.",
                    )
                self._emit_progress(progress_callback, "task_cancelled", plan, message="Task cancelled.")
                return TaskResult(
                    task_id=plan.task_id,
                    goal=plan.goal,
                    status=TaskStatus.CANCELLED,
                    steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                    steps_total=len(plan.steps),
                    step_results=self._build_step_results(plan),
                    summary="Task cancelled before completion.",
                    error="Cancelled by user.",
                    verified=False,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                )

            ready_step: TaskStep | None = None
            dep_error: str | None = None
            for candidate in remaining:
                err = self._has_failed_dependency(candidate, steps_by_id)
                if err is not None:
                    dep_error = err
                    ready_step = candidate
                    break
                if self._dependencies_satisfied(candidate, steps_by_id):
                    ready_step = candidate
                    break

            if ready_step is None:
                plan.status = TaskStatus.FAILED
                msg = "Task cannot proceed because step dependencies cannot be satisfied (cycle or blocked dependency)."
                self._persist_plan(identity, plan)
                if self.task_store is not None and identity is not None:
                    self.task_store.update_task_state(
                        identity, plan.task_id, status=TaskStatus.FAILED, error=msg
                    )
                return TaskResult(
                    task_id=plan.task_id,
                    goal=plan.goal,
                    status=TaskStatus.FAILED,
                    steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                    steps_total=len(plan.steps),
                    step_results=self._build_step_results(plan),
                    summary=msg,
                    error=msg,
                    verified=False,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                )

            if dep_error is not None:
                ready_step.status = StepStatus.FAILED
                ready_step.error = dep_error
                plan.status = TaskStatus.FAILED
                self._persist_step(identity, plan, step_orders[ready_step.step_id], ready_step)
                if self.task_store is not None and identity is not None:
                    self.task_store.update_task_state(
                        identity, plan.task_id, status=TaskStatus.FAILED, error=dep_error
                    )
                return TaskResult(
                    task_id=plan.task_id,
                    goal=plan.goal,
                    status=TaskStatus.FAILED,
                    steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                    steps_total=len(plan.steps),
                    step_results=self._build_step_results(plan),
                    summary=dep_error,
                    error=dep_error,
                    verified=False,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                )

            plan.current_step_index = step_orders[ready_step.step_id]
            self._emit_progress(
                progress_callback,
                "step_started",
                plan,
                step=ready_step,
                message=f"Running step {ready_step.step_id}: {ready_step.description or ready_step.action}",
            )

            step_token: str | None = None
            if (
                confirmation_token is not None
                and not token_used
                and (confirm_step_id is None or confirm_step_id == ready_step.step_id)
            ):
                step_token = confirmation_token
                token_used = True

            outcome, payload = self._execute_single_step(
                plan,
                ready_step,
                step_orders[ready_step.step_id],
                identity=identity,
                confirmation_token=step_token,
                ctx=ctx,
            )

            if outcome == "cancelled":
                plan.status = TaskStatus.CANCELLED
                self._persist_plan(identity, plan)
                return TaskResult(
                    task_id=plan.task_id,
                    goal=plan.goal,
                    status=TaskStatus.CANCELLED,
                    steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                    steps_total=len(plan.steps),
                    step_results=self._build_step_results(plan),
                    summary="Task cancelled during step execution.",
                    error="Cancelled by user.",
                    verified=False,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                )

            if outcome == "confirmation_required":
                plan.status = TaskStatus.AWAITING_CONFIRMATION
                self._persist_plan(identity, plan)
                if self.task_store is not None and identity is not None:
                    self.task_store.update_task_state(
                        identity,
                        plan.task_id,
                        status=TaskStatus.AWAITING_CONFIRMATION,
                        current_step_index=step_orders[ready_step.step_id],
                        requires_confirmation=True,
                    )
                self._emit_progress(
                    progress_callback,
                    "confirmation_required",
                    plan,
                    step=ready_step,
                    message=str(payload.get("reason") or "Confirmation required."),
                )
                return TaskResult(
                    task_id=plan.task_id,
                    goal=plan.goal,
                    status=TaskStatus.AWAITING_CONFIRMATION,
                    steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                    steps_total=len(plan.steps),
                    step_results=self._build_step_results(plan),
                    summary=str(payload.get("reason") or "This step requires user confirmation."),
                    requires_confirmation=True,
                    confirmation_payload={
                        "task_id": plan.task_id,
                        "step_id": ready_step.step_id,
                        **payload,
                    },
                    verified=False,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                )

            if outcome != "success":
                plan.status = TaskStatus.FAILED
                err_msg = str(
                    payload.get("reason")
                    or payload.get("error")
                    or ready_step.error
                    or f"Step '{ready_step.action}' failed."
                )
                self._persist_plan(identity, plan)
                if self.task_store is not None and identity is not None:
                    self.task_store.update_task_state(
                        identity,
                        plan.task_id,
                        status=TaskStatus.FAILED,
                        current_step_index=step_orders[ready_step.step_id],
                        error=err_msg,
                    )
                self._emit_progress(
                    progress_callback,
                    "step_failed",
                    plan,
                    step=ready_step,
                    message=err_msg,
                )
                return TaskResult(
                    task_id=plan.task_id,
                    goal=plan.goal,
                    status=TaskStatus.FAILED,
                    steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                    steps_total=len(plan.steps),
                    step_results=self._build_step_results(plan),
                    summary=f"Step '{ready_step.description or ready_step.action}' failed: {err_msg}",
                    error=err_msg,
                    verified=False,
                    duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
                )

            self._emit_progress(
                progress_callback,
                "step_completed",
                plan,
                step=ready_step,
                message=f"Completed {ready_step.description or ready_step.action}.",
            )
            remaining = [
                s for s in plan.steps if s.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}
            ]

        # All steps reached terminal success -> verify outcomes cleanly before marking COMPLETED!
        verified = all(
            (s.status == StepStatus.SKIPPED)
            or (
                s.status == StepStatus.COMPLETED
                and isinstance(s.result, dict)
                and s.result.get("status") == "success"
                and (
                    not isinstance(s.result.get("verification"), dict)
                    or bool(s.result["verification"].get("success", False))
                )
            )
            for s in plan.steps
        )
        if not verified:
            plan.status = TaskStatus.FAILED
            err_msg = "Task post-execution verification failed."
            if self.task_store is not None and identity is not None:
                self.task_store.update_task_state(
                    identity, plan.task_id, status=TaskStatus.FAILED, error=err_msg
                )
            return TaskResult(
                task_id=plan.task_id,
                goal=plan.goal,
                status=TaskStatus.FAILED,
                steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                steps_total=len(plan.steps),
                step_results=self._build_step_results(plan),
                summary=err_msg,
                error=err_msg,
                verified=False,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
            )

        plan.status = TaskStatus.COMPLETED
        summary_lines = [
            str(s.result.get("result")).strip()
            for s in plan.steps
            if s.status == StepStatus.COMPLETED
            and isinstance(s.result, dict)
            and s.result.get("result") is not None
            and str(s.result.get("result")).strip()
        ]
        final_summary = (
            "\n".join(summary_lines)
            if summary_lines
            else f"Completed '{plan.goal}' ({len(plan.steps)} step(s))."
        )
        self._persist_plan(identity, plan)
        if self.task_store is not None and identity is not None:
            self.task_store.update_task_state(
                identity,
                plan.task_id,
                status=TaskStatus.COMPLETED,
                current_step_index=len(plan.steps),
                requires_confirmation=False,
                summary=final_summary,
            )
        self._emit_progress(progress_callback, "task_completed", plan, message="All task steps verified complete.")
        return TaskResult(
            task_id=plan.task_id,
            goal=plan.goal,
            status=TaskStatus.COMPLETED,
            steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
            steps_total=len(plan.steps),
            step_results=self._build_step_results(plan),
            summary=final_summary,
            verified=True,
            duration_ms=round((time.perf_counter() - started) * 1000.0, 2),
        )
