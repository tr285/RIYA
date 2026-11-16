"""High-level AgentCoordinator connecting planning, execution, replanning, recovery & workflow tracking."""

from __future__ import annotations

import threading
from typing import Any

from core.identity.models import AuthenticatedIdentity
from core.tasks.executor import ProgressCallback, TaskExecutor
from core.tasks.models import StepStatus, TaskPlan, TaskResult, TaskStatus
from core.tasks.store import TaskMemoryStore


class AgentCoordinator:
    """Orchestrates task decomposition, DAG execution, replanning on failure & recovery."""

    def __init__(
        self,
        *,
        planner: Any,
        task_executor: TaskExecutor,
        task_store: TaskMemoryStore | None = None,
        skill_registry: Any = None,
    ):
        self.planner = planner
        self.task_executor = task_executor
        self.task_store = task_store
        self.skill_registry = skill_registry
        self._active_cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def plan_task(
        self,
        identity: AuthenticatedIdentity | None,
        request: str,
        *,
        conversation_context: str | None = None,
    ) -> TaskPlan:
        """Use AgentPlanner to build a TaskPlan (supporting legacy Plan fallback)."""
        if hasattr(self.planner, "decompose_task"):
            task_plan = self.planner.decompose_task(
                request,
                conversation_context=conversation_context,
            )
        else:
            legacy = self.planner.create_plan(request, conversation_context=conversation_context)
            task_plan = TaskPlan.from_legacy_plan(legacy)

        if self.task_store is not None and identity is not None and (
            task_plan.steps or task_plan.clarification_needed
        ):
            self.task_store.save_task(identity, task_plan)
        return task_plan

    def run_plan(
        self,
        identity: AuthenticatedIdentity | None,
        plan: TaskPlan,
        *,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
        allow_replan: bool = True,
        conversation_context: str | None = None,
    ) -> TaskResult:
        """Execute a TaskPlan with bounded replanning on non-security failure and pattern logging."""
        ev = cancel_event or threading.Event()
        with self._lock:
            self._active_cancel_events[plan.task_id] = ev

        try:
            result = self.task_executor.execute_plan(
                plan,
                identity=identity,
                cancel_event=ev,
                progress_callback=progress_callback,
            )

            # Replan once after a recoverable non-security failure if planner supports replanning.
            if (
                result.status == TaskStatus.FAILED
                and allow_replan
                and not ev.is_set()
                and hasattr(self.planner, "replan")
            ):
                failed_step = next(
                    (s for s in plan.steps if s.status == StepStatus.FAILED),
                    None,
                )
                is_security_block = bool(
                    failed_step is not None
                    and isinstance(failed_step.result, dict)
                    and (
                        failed_step.result.get("status") == "authorization_failed"
                        or failed_step.result.get("blocked")
                    )
                )
                if failed_step is not None and not is_security_block:
                    try:
                        revised = self.planner.replan(
                            plan,
                            failed_step,
                            result.error,
                            conversation_context=conversation_context,
                        )
                    except Exception:
                        revised = None
                    if revised is not None and len(revised.steps) > sum(
                        1 for s in plan.steps if s.status == StepStatus.COMPLETED
                    ):
                        if self.task_store is not None and identity is not None:
                            self.task_store.save_task(identity, revised)
                        retry_res = self.task_executor.execute_plan(
                            revised,
                            identity=identity,
                            cancel_event=ev,
                            progress_callback=progress_callback,
                        )
                        retry_res.replanned = True
                        result = retry_res

            if (
                result.status == TaskStatus.COMPLETED
                and self.task_store is not None
                and identity is not None
                and len(plan.steps) >= 2
            ):
                try:
                    self.task_store.record_workflow_pattern(
                        identity,
                        plan.goal,
                        [s.action for s in plan.steps if s.status == StepStatus.COMPLETED],
                    )
                except Exception:
                    pass
            return result
        finally:
            with self._lock:
                self._active_cancel_events.pop(plan.task_id, None)

    def run_request(
        self,
        identity: AuthenticatedIdentity | None,
        request: str,
        *,
        conversation_context: str | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
        allow_replan: bool = True,
    ) -> TaskResult:
        plan = self.plan_task(
            identity,
            request,
            conversation_context=conversation_context,
        )
        if plan.clarification_needed:
            return TaskResult(
                task_id=plan.task_id,
                goal=plan.goal,
                status=TaskStatus.PAUSED,
                steps_completed=0,
                steps_total=len(plan.steps),
                clarification_prompt=plan.clarification_prompt,
                summary=plan.clarification_prompt,
                verified=False,
            )
        if not plan.steps:
            return TaskResult(
                task_id=plan.task_id,
                goal=plan.goal,
                status=TaskStatus.COMPLETED,
                steps_completed=0,
                steps_total=0,
                summary="",
                verified=True,
            )
        return self.run_plan(
            identity,
            plan,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
            allow_replan=allow_replan,
            conversation_context=conversation_context,
        )

    def resume_task(
        self,
        identity: AuthenticatedIdentity,
        task_id: str,
        *,
        confirmation_token: str | None = None,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> TaskResult:
        """Resume a saved/interrupted task idempotently from its persisted SQLite checkpoint."""
        if self.task_store is None:
            raise RuntimeError("TaskMemoryStore is required for task recovery.")
        record = self.task_store.get_task(identity, task_id)
        if record is None:
            raise KeyError(f"Task '{task_id}' was not found for this user.")
        plan: TaskPlan = record["plan"]
        if plan.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            return TaskResult(
                task_id=plan.task_id,
                goal=plan.goal,
                status=plan.status,
                steps_completed=sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
                steps_total=len(plan.steps),
                step_results=self.task_executor._build_step_results(plan),
                summary=record.get("final_summary") or f"Task already {plan.status.value}.",
                verified=(plan.status == TaskStatus.COMPLETED),
            )

        # Reset non-completed steps (e.g. 'running' or 'awaiting_confirmation' or 'failed')
        # cleanly back to 'pending' while keeping 'completed' steps untouched for idempotency!
        target_confirm_step_id: str | None = None
        for step in plan.steps:
            if step.status == StepStatus.AWAITING_CONFIRMATION and target_confirm_step_id is None:
                target_confirm_step_id = step.step_id
            if step.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
                step.status = StepStatus.PENDING

        ev = cancel_event or threading.Event()
        with self._lock:
            self._active_cancel_events[plan.task_id] = ev
        try:
            result = self.task_executor.execute_plan(
                plan,
                identity=identity,
                confirmation_token=confirmation_token,
                confirm_step_id=target_confirm_step_id,
                cancel_event=ev,
                progress_callback=progress_callback,
            )
            if (
                result.status == TaskStatus.COMPLETED
                and self.task_store is not None
                and len(plan.steps) >= 2
            ):
                try:
                    self.task_store.record_workflow_pattern(
                        identity,
                        plan.goal,
                        [s.action for s in plan.steps if s.status == StepStatus.COMPLETED],
                    )
                except Exception:
                    pass
            return result
        finally:
            with self._lock:
                self._active_cancel_events.pop(plan.task_id, None)

    def cancel_task(
        self,
        identity: AuthenticatedIdentity | None,
        task_id: str,
        *,
        confirmation_token: str | None = None,
    ) -> bool:
        """Signal cancellation on an active task and persist cancelled status."""
        if confirmation_token is not None:
            self.task_executor.tool_executor.cancel_confirmation(confirmation_token)
        with self._lock:
            ev = self._active_cancel_events.get(task_id)
            if ev is not None:
                ev.set()
        if self.task_store is not None and identity is not None:
            record = self.task_store.get_task(identity, task_id)
            if record is None:
                return False
            self.task_store.update_task_state(
                identity,
                task_id,
                status=TaskStatus.CANCELLED,
                requires_confirmation=False,
                error="Cancelled by user.",
            )
            return True
        return ev is not None

    def cancel_all_active(self) -> int:
        """Signal cancellation for all currently running tasks (e.g. on voice interruption)."""
        with self._lock:
            events = list(self._active_cancel_events.values())
        for ev in events:
            ev.set()
        return len(events)
