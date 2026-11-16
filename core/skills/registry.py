"""SkillRegistry managing validation, permission enforcement, SQLite metadata & execution."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from core.identity.models import AuthenticatedIdentity
from core.security.permissions import PermissionManager
from core.skills.base import SkillContext, SkillDefinition, SkillExecutionResult
from core.skills.builtins import get_builtin_skills


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillRegistry:
    """Central registry for reusable modular RIYA skills."""

    def __init__(
        self,
        *,
        database: Any = None,
        load_builtins: bool = True,
    ):
        self._skills: dict[str, SkillDefinition] = {}
        self.database = database
        if load_builtins:
            for skill in get_builtin_skills():
                self.register(skill, sync_db=False)
        if self.database is not None:
            self.sync_to_database()

    def register(self, skill: SkillDefinition, *, sync_db: bool = True) -> None:
        if not skill.skill_id or not isinstance(skill.skill_id, str):
            raise ValueError("skill_id must be a non-empty string.")
        if not callable(skill.handler):
            raise ValueError(f"Skill '{skill.skill_id}' handler must be callable.")
        if skill.skill_id in self._skills:
            raise ValueError(f"Skill already registered: {skill.skill_id}")
        self._skills[skill.skill_id] = skill
        if sync_db and self.database is not None:
            self.sync_to_database()

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(str(skill_id or "").strip())

    def has(self, skill_id: str) -> bool:
        return str(skill_id or "").strip() in self._skills

    def list_skills(self) -> list[SkillDefinition]:
        return [self._skills[k] for k in sorted(self._skills)]

    def sync_to_database(self) -> None:
        if self.database is None:
            return
        now = _utc_now()
        conn = self.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for skill in self._skills.values():
                conn.execute(
                    """
                    INSERT INTO skills_registry (
                        skill_id, user_id, name, version, description,
                        required_permissions_json, risk_level, enabled, source,
                        proposal_id, created_at, updated_at
                    )
                    VALUES (?, NULL, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)
                    ON CONFLICT(skill_id) DO UPDATE SET
                        name = excluded.name,
                        version = excluded.version,
                        description = excluded.description,
                        required_permissions_json = excluded.required_permissions_json,
                        risk_level = excluded.risk_level,
                        updated_at = excluded.updated_at
                    """,
                    (
                        skill.skill_id,
                        skill.name,
                        skill.version,
                        skill.description,
                        json.dumps(list(skill.required_permissions)),
                        skill.risk_level.value,
                        skill.source,
                        now,
                        now,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(
        self,
        skill_id: str,
        *,
        inputs: dict[str, Any] | None = None,
        identity: AuthenticatedIdentity | None = None,
        tool_executor: Any,
        permission_manager: PermissionManager | None = None,
        task_store: Any = None,
        assistant: Any = None,
    ) -> SkillExecutionResult:
        """Validate inputs & required permissions, then execute the skill cleanly."""
        skill = self.get(skill_id)
        if skill is None:
            return SkillExecutionResult(
                skill_id=str(skill_id),
                version="0.0.0",
                success=False,
                status="failed",
                error=f"Unknown skill '{skill_id}'.",
            )

        try:
            validated_inputs = skill.validate_inputs(inputs)
        except ValueError as exc:
            return SkillExecutionResult(
                skill_id=skill.skill_id,
                version=skill.version,
                success=False,
                status="failed",
                error=str(exc),
            )

        # Enforce skills.manage + every declared required_permission BEFORE running handler.
        if permission_manager is not None and identity is not None:
            skill_eval = permission_manager.evaluate(
                identity,
                "skills.manage",
                resource_or_target=f"skill:{skill.skill_id}",
                actor="skill_registry",
            )
            if skill_eval.blocked:
                return SkillExecutionResult(
                    skill_id=skill.skill_id,
                    version=skill.version,
                    success=False,
                    status="blocked",
                    error=skill_eval.reason,
                )

            for req_perm in skill.required_permissions:
                perm_eval = permission_manager.evaluate(
                    identity,
                    req_perm,
                    resource_or_target="*",
                    actor="skill_registry",
                )
                if perm_eval.blocked:
                    return SkillExecutionResult(
                        skill_id=skill.skill_id,
                        version=skill.version,
                        success=False,
                        status="blocked",
                        error=f"Missing required permission '{req_perm}': {perm_eval.reason}",
                    )

        ctx = SkillContext(
            identity=identity,
            tool_executor=tool_executor,
            permission_manager=permission_manager,
            task_store=task_store,
            assistant=assistant,
        )

        try:
            raw_output = skill.handler(ctx, validated_inputs)
            validated_output = skill.validate_output(raw_output)
        except PermissionError as exc:
            return SkillExecutionResult(
                skill_id=skill.skill_id,
                version=skill.version,
                success=False,
                status="blocked",
                error=str(exc),
            )
        except Exception as exc:
            return SkillExecutionResult(
                skill_id=skill.skill_id,
                version=skill.version,
                success=False,
                status="failed",
                error=str(exc),
            )

        summary = str(validated_output.get("summary") or f"Completed skill '{skill.name}'.")
        return SkillExecutionResult(
            skill_id=skill.skill_id,
            version=skill.version,
            success=True,
            status="success",
            output=validated_output,
            summary=summary,
        )
