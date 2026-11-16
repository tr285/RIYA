"""User-isolated persistent SQLite store for autonomous tasks, checkpoints, approvals & patterns."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any
from uuid import uuid4

from core.identity.errors import InvalidIdentityError, MissingIdentityError
from core.identity.models import AuthenticatedIdentity
from core.tasks.models import StepStatus, TaskPlan, TaskStatus, TaskStep

_SENSITIVE_KEY_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "confirmation_token",
    "api_key",
    "apikey",
    "auth",
    "cookie",
    "credential",
    "private_key",
    "bearer",
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9._-]+|sk-[A-Za-z0-9]{12,}|ghp_[A-Za-z0-9]{12,}|AIza[0-9A-Za-z_-]{20,})\b"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_sensitive_data(value: Any) -> Any:
    """Recursively strip credentials/tokens before saving task metadata into SQLite."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            lowered = key_str.lower()
            if any(tok in lowered for tok in _SENSITIVE_KEY_TOKENS):
                sanitized[key_str] = "[REDACTED]"
            else:
                sanitized[key_str] = redact_sensitive_data(v)
        return sanitized
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("[REDACTED_SECRET]", value)
    return value


def _safe_json(value: Any) -> str:
    return json.dumps(redact_sensitive_data(value), ensure_ascii=False, default=str)


def _hash_parameters(parameters: dict[str, Any]) -> str:
    payload = json.dumps(
        redact_sensitive_data(parameters),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


class TaskMemoryStore:
    """Per-user isolated SQLite persistence for task state, checkpoints, and recovery."""

    def __init__(self, identity_manager: Any):
        self.identity_manager = identity_manager
        self.database = identity_manager.database

    def _validate_identity(self, identity: Any) -> AuthenticatedIdentity:
        if identity is None:
            raise MissingIdentityError("Authenticated identity is required for task storage.")
        if not isinstance(identity, AuthenticatedIdentity):
            raise InvalidIdentityError("Invalid identity; expected AuthenticatedIdentity.")
        return self.identity_manager.validate_session(identity)

    def save_task(self, identity: AuthenticatedIdentity, plan: TaskPlan) -> TaskPlan:
        """Persist or update a TaskPlan and all its steps atomically for the active user."""
        validated = self._validate_identity(identity)
        now = _utc_now()
        plan_payload = _safe_json(
            {
                "intent": plan.intent,
                "clarification_needed": plan.clarification_needed,
                "metadata": plan.metadata,
            }
        )
        completed_at = now if plan.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        } else None

        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT user_id, created_at FROM tasks WHERE task_id = ?",
                (plan.task_id,),
            ).fetchone()
            if existing is not None and str(existing["user_id"]) != validated.user_id:
                raise PermissionError(
                    "Access denied: cannot modify a task owned by another user."
                )

            created_at = str(existing["created_at"]) if existing is not None else now
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, user_id, goal, plan_json, status, current_step_index,
                    requires_confirmation, clarification_prompt, final_summary,
                    error_message, idempotency_key, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    goal = excluded.goal,
                    plan_json = excluded.plan_json,
                    status = excluded.status,
                    current_step_index = excluded.current_step_index,
                    clarification_prompt = excluded.clarification_prompt,
                    updated_at = excluded.updated_at,
                    completed_at = COALESCE(excluded.completed_at, tasks.completed_at)
                """,
                (
                    plan.task_id,
                    validated.user_id,
                    plan.goal,
                    plan_payload,
                    plan.status.value,
                    plan.current_step_index,
                    1 if plan.status == TaskStatus.AWAITING_CONFIRMATION else 0,
                    plan.clarification_prompt,
                    plan.idempotency_key,
                    created_at,
                    now,
                    completed_at,
                ),
            )

            for idx, step in enumerate(plan.steps):
                self._upsert_step_row(conn, validated.user_id, plan.task_id, idx, step, now)

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return plan

    @staticmethod
    def _upsert_step_row(
        conn,
        user_id: str,
        task_id: str,
        step_order: int,
        step: TaskStep,
        now: str,
    ) -> None:
        step_row_id = f"{task_id}:{step.step_id}"
        conn.execute(
            """
            INSERT INTO task_steps (
                step_row_id, task_id, user_id, step_id, step_order, action,
                parameters_json, description, depends_on_json, status, attempts,
                max_retries, idempotency_key, result_json, verification_json,
                error_message, started_at, completed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, step_id) DO UPDATE SET
                step_order = excluded.step_order,
                action = excluded.action,
                parameters_json = excluded.parameters_json,
                description = excluded.description,
                depends_on_json = excluded.depends_on_json,
                status = excluded.status,
                attempts = excluded.attempts,
                max_retries = excluded.max_retries,
                result_json = excluded.result_json,
                verification_json = excluded.verification_json,
                error_message = excluded.error_message,
                started_at = COALESCE(excluded.started_at, task_steps.started_at),
                completed_at = COALESCE(excluded.completed_at, task_steps.completed_at),
                updated_at = excluded.updated_at
            """,
            (
                step_row_id,
                task_id,
                user_id,
                step.step_id,
                step_order,
                step.action,
                _safe_json(step.parameters),
                step.description,
                _safe_json(step.depends_on),
                step.status.value,
                step.attempts,
                step.max_retries,
                step.idempotency_key,
                _safe_json(step.result or {}),
                _safe_json(step.verification or {}),
                step.error,
                step.started_at,
                step.completed_at,
                now,
            ),
        )

    def update_task_state(
        self,
        identity: AuthenticatedIdentity,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        current_step_index: int | None = None,
        requires_confirmation: bool | None = None,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        validated = self._validate_identity(identity)
        now = _utc_now()
        updates = ["updated_at = ?"]
        params: list[Any] = [now]

        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
            if status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                updates.append("completed_at = ?")
                params.append(now)
        if current_step_index is not None:
            updates.append("current_step_index = ?")
            params.append(int(current_step_index))
        if requires_confirmation is not None:
            updates.append("requires_confirmation = ?")
            params.append(1 if requires_confirmation else 0)
        if summary is not None:
            updates.append("final_summary = ?")
            params.append(str(_SECRET_VALUE_RE.sub("[REDACTED_SECRET]", summary)))
        if error is not None:
            updates.append("error_message = ?")
            params.append(str(_SECRET_VALUE_RE.sub("[REDACTED_SECRET]", error)))

        params.extend([task_id, validated.user_id])
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ? AND user_id = ?",
                tuple(params),
            )
            if cur.rowcount == 0:
                existing = conn.execute(
                    "SELECT user_id FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if existing is not None:
                    raise PermissionError("Access denied: cannot update another user's task.")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_step(
        self,
        identity: AuthenticatedIdentity,
        task_id: str,
        step_order: int,
        step: TaskStep,
    ) -> None:
        validated = self._validate_identity(identity)
        now = _utc_now()
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT user_id FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown task_id: {task_id}")
            if str(row["user_id"]) != validated.user_id:
                raise PermissionError("Access denied: cannot modify another user's task step.")
            self._upsert_step_row(conn, validated.user_id, task_id, step_order, step, now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_checkpoint(
        self,
        identity: AuthenticatedIdentity,
        task_id: str,
        step_id: str,
        state: dict[str, Any],
    ) -> str:
        validated = self._validate_identity(identity)
        checkpoint_id = f"chk_{uuid4().hex[:16]}"
        now = _utc_now()
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT user_id FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None or str(row["user_id"]) != validated.user_id:
                raise PermissionError("Access denied saving task checkpoint.")
            conn.execute(
                """
                INSERT INTO task_checkpoints (
                    checkpoint_id, task_id, user_id, step_id, state_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (checkpoint_id, task_id, validated.user_id, step_id, _safe_json(state), now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return checkpoint_id

    def record_approval(
        self,
        identity: AuthenticatedIdentity,
        task_id: str,
        step_id: str,
        tool_name: str,
        risk_level: str,
        decision: str,
        reason: str,
        parameters: dict[str, Any] | None = None,
    ) -> str:
        """Record user approval/denial audit metadata (never storing raw confirmation tokens)."""
        validated = self._validate_identity(identity)
        approval_id = f"apr_{uuid4().hex[:16]}"
        now = _utc_now()
        param_hash = _hash_parameters(parameters or {})
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT user_id FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None or str(row["user_id"]) != validated.user_id:
                raise PermissionError("Access denied recording approval for task.")
            conn.execute(
                """
                INSERT INTO task_approvals (
                    approval_id, task_id, user_id, step_id, tool_name, risk_level,
                    decision, reason, parameters_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    task_id,
                    validated.user_id,
                    step_id,
                    tool_name,
                    risk_level,
                    decision,
                    str(_SECRET_VALUE_RE.sub("[REDACTED_SECRET]", reason)),
                    param_hash,
                    now,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return approval_id

    def get_task(self, identity: AuthenticatedIdentity, task_id: str) -> dict[str, Any] | None:
        validated = self._validate_identity(identity)
        conn = self.database.connect()
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ? AND user_id = ?",
                (task_id, validated.user_id),
            ).fetchone()
            if row is None:
                return None

            step_rows = conn.execute(
                "SELECT * FROM task_steps WHERE task_id = ? AND user_id = ? ORDER BY step_order ASC",
                (task_id, validated.user_id),
            ).fetchall()
            checkpoints = conn.execute(
                "SELECT * FROM task_checkpoints WHERE task_id = ? AND user_id = ? ORDER BY created_at ASC",
                (task_id, validated.user_id),
            ).fetchall()
            approvals = conn.execute(
                "SELECT * FROM task_approvals WHERE task_id = ? AND user_id = ? ORDER BY created_at ASC",
                (task_id, validated.user_id),
            ).fetchall()
        finally:
            conn.close()

        meta = json.loads(str(row["plan_json"] or "{}"))
        steps: list[TaskStep] = []
        for srow in step_rows:
            steps.append(
                TaskStep(
                    step_id=str(srow["step_id"]),
                    action=str(srow["action"]),
                    parameters=json.loads(str(srow["parameters_json"] or "{}")),
                    description=str(srow["description"] or ""),
                    depends_on=json.loads(str(srow["depends_on_json"] or "[]")),
                    status=StepStatus(str(srow["status"])),
                    attempts=int(srow["attempts"] or 0),
                    max_retries=int(srow["max_retries"] or 0),
                    idempotency_key=str(srow["idempotency_key"] or ""),
                    result=json.loads(str(srow["result_json"] or "{}")) or None,
                    verification=json.loads(str(srow["verification_json"] or "{}")) or None,
                    error=str(srow["error_message"] or ""),
                    started_at=srow["started_at"],
                    completed_at=srow["completed_at"],
                )
            )

        plan = TaskPlan(
            task_id=str(row["task_id"]),
            goal=str(row["goal"]),
            steps=steps,
            intent=str(meta.get("intent") or "task"),
            status=TaskStatus(str(row["status"])),
            clarification_needed=bool(meta.get("clarification_needed", False)),
            clarification_prompt=str(row["clarification_prompt"] or ""),
            current_step_index=int(row["current_step_index"] or 0),
            idempotency_key=str(row["idempotency_key"] or ""),
            metadata=dict(meta.get("metadata") or {}),
        )
        return {
            "plan": plan,
            "final_summary": str(row["final_summary"] or ""),
            "error_message": str(row["error_message"] or ""),
            "requires_confirmation": bool(row["requires_confirmation"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "completed_at": row["completed_at"],
            "checkpoints": [
                {
                    "checkpoint_id": str(c["checkpoint_id"]),
                    "step_id": str(c["step_id"]),
                    "state": json.loads(str(c["state_json"] or "{}")),
                    "created_at": str(c["created_at"]),
                }
                for c in checkpoints
            ],
            "approvals": [dict(a) for a in approvals],
        }

    def list_tasks(
        self,
        identity: AuthenticatedIdentity,
        *,
        status: TaskStatus | str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        validated = self._validate_identity(identity)
        limit = max(1, min(int(limit), 200))
        clauses = ["user_id = ?"]
        params: list[Any] = [validated.user_id]
        if status is not None:
            st_val = status.value if isinstance(status, TaskStatus) else str(status)
            clauses.append("status = ?")
            params.append(st_val)
        params.append(limit)

        conn = self.database.connect()
        try:
            rows = conn.execute(
                f"""
                SELECT task_id, goal, status, current_step_index, requires_confirmation,
                       final_summary, error_message, created_at, updated_at, completed_at
                FROM tasks
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_recoverable_tasks(self, identity: AuthenticatedIdentity) -> list[dict[str, Any]]:
        validated = self._validate_identity(identity)
        conn = self.database.connect()
        try:
            rows = conn.execute(
                """
                SELECT task_id, goal, status, current_step_index, requires_confirmation,
                       created_at, updated_at
                FROM tasks
                WHERE user_id = ?
                  AND status IN ('pending', 'running', 'paused', 'awaiting_confirmation')
                ORDER BY updated_at DESC
                """,
                (validated.user_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_approvals(
        self,
        identity: AuthenticatedIdentity,
        *,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        validated = self._validate_identity(identity)
        clauses = ["user_id = ?"]
        params: list[Any] = [validated.user_id]
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        params.append(max(1, min(int(limit), 200)))
        conn = self.database.connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM task_approvals WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def clear_history(
        self,
        identity: AuthenticatedIdentity,
        *,
        include_active: bool = False,
    ) -> int:
        validated = self._validate_identity(identity)
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if include_active:
                cur = conn.execute(
                    "DELETE FROM tasks WHERE user_id = ?",
                    (validated.user_id,),
                )
            else:
                cur = conn.execute(
                    """
                    DELETE FROM tasks
                    WHERE user_id = ?
                      AND status IN ('completed', 'failed', 'cancelled')
                    """,
                    (validated.user_id,),
                )
            conn.commit()
            return int(cur.rowcount)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_workflow_pattern(
        self,
        identity: AuthenticatedIdentity,
        goal: str,
        actions: list[str],
    ) -> int:
        """Record an executed multi-step action pattern for skill learning."""
        validated = self._validate_identity(identity)
        if not actions:
            return 0
        cleaned_actions = [str(a).strip() for a in actions if str(a).strip()]
        if len(cleaned_actions) < 2:
            return 0
        signature = "->".join(cleaned_actions)
        now = _utc_now()
        pattern_id = f"pat_{uuid4().hex[:16]}"
        suggested_name = f"workflow_{cleaned_actions[0]}_{cleaned_actions[-1]}"

        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO workflow_patterns (
                    pattern_id, user_id, signature, goal_sample, actions_json,
                    occurrence_count, suggested_skill_name, proposal_id, last_seen_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)
                ON CONFLICT(user_id, signature) DO UPDATE SET
                    occurrence_count = workflow_patterns.occurrence_count + 1,
                    goal_sample = excluded.goal_sample,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    pattern_id,
                    validated.user_id,
                    signature,
                    goal[:240],
                    _safe_json(cleaned_actions),
                    suggested_name[:64],
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT occurrence_count FROM workflow_patterns WHERE user_id = ? AND signature = ?",
                (validated.user_id, signature),
            ).fetchone()
            conn.commit()
            return int(row["occurrence_count"]) if row is not None else 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_repeated_workflows(
        self,
        identity: AuthenticatedIdentity,
        *,
        min_count: int = 2,
    ) -> list[dict[str, Any]]:
        validated = self._validate_identity(identity)
        conn = self.database.connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM workflow_patterns
                WHERE user_id = ? AND occurrence_count >= ?
                ORDER BY occurrence_count DESC, last_seen_at DESC
                """,
                (validated.user_id, int(min_count)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
