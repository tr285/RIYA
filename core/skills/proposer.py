"""Generate and sandbox-verify proposed new skills from repeated user workflows."""

from __future__ import annotations

import json
import re
from typing import Any

from core.identity.models import AuthenticatedIdentity
from core.self_improve.manager import SelfImprovementManager, SelfImprovementProposal
from core.tasks.store import TaskMemoryStore


def _safe_slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip().lower()).strip("_")
    return cleaned[:48] or "custom_workflow"


class WorkflowSkillProposer:
    """Detects repeated multi-step workflows and stages verified skill code proposals."""

    def __init__(
        self,
        *,
        task_store: TaskMemoryStore,
        self_improve_manager: SelfImprovementManager,
    ):
        self.task_store = task_store
        self.self_improve = self_improve_manager

    def find_candidates(
        self,
        identity: AuthenticatedIdentity,
        *,
        min_count: int = 2,
    ) -> list[dict[str, Any]]:
        return self.task_store.get_repeated_workflows(identity, min_count=min_count)

    @staticmethod
    def build_skill_module_source(
        skill_id: str,
        description: str,
        actions: list[str],
    ) -> str:
        """Render a safe, pure-Python reusable workflow skill module."""
        steps_literal = json.dumps(actions)
        return (
            '"""Auto-proposed reusable workflow skill (requires explicit user approval before activation)."""\n\n'
            "from __future__ import annotations\n\n"
            "from core.security.manager import RiskLevel\n"
            "from core.skills.base import SkillContext, SkillDefinition\n\n\n"
            f"WORKFLOW_ACTIONS: tuple[str, ...] = tuple({steps_literal})\n\n\n"
            f"def _run_{skill_id}(ctx: SkillContext, inputs: dict) -> dict:\n"
            "    note = str(inputs.get('note') or '').strip()\n"
            "    return {\n"
            f"        'skill_id': {skill_id!r},\n"
            "        'planned_actions': list(WORKFLOW_ACTIONS),\n"
            "        'note': note,\n"
            "        'summary': f'Prepared workflow with {len(WORKFLOW_ACTIONS)} step(s): ' + ', '.join(WORKFLOW_ACTIONS),\n"
            "    }\n\n\n"
            "def build_skill() -> SkillDefinition:\n"
            "    return SkillDefinition(\n"
            f"        skill_id={skill_id!r},\n"
            f"        name={skill_id.replace('_', ' ').title()!r},\n"
            "        version='1.0.0',\n"
            f"        description={description!r},\n"
            "        required_permissions=(),\n"
            "        input_schema={'note': {'type': str, 'required': False, 'default': ''}},\n"
            "        output_schema={'skill_id': 'str', 'planned_actions': 'list', 'note': 'str', 'summary': 'str'},\n"
            f"        handler=_run_{skill_id},\n"
            "        risk_level=RiskLevel.LOW,\n"
            "        source='user_approved',\n"
            "    )\n"
        )

    def propose_from_pattern(
        self,
        identity: AuthenticatedIdentity,
        pattern: dict[str, Any],
        *,
        verify: bool = True,
    ) -> SelfImprovementProposal:
        """Stage (and optionally sandbox-verify) a skill proposal without auto-applying."""
        actions = json.loads(str(pattern.get("actions_json") or "[]"))
        slug = _safe_slug(pattern.get("suggested_skill_name") or "workflow_skill")
        skill_id = f"proposed_{slug}"
        target_rel = f"core/skills/{skill_id}.py"
        description = (
            f"Repeated workflow ({pattern.get('occurrence_count', 2)}x): "
            + " -> ".join(actions)
        )[:180]
        source_code = self.build_skill_module_source(skill_id, description, actions)

        proposal = self.self_improve.propose_modification(
            identity,
            title=f"Propose reusable skill '{skill_id}'",
            problem_description=(
                f"Detected repeated workflow pattern '{pattern.get('signature', '')}' "
                f"({pattern.get('occurrence_count', 2)} runs) for goal: {pattern.get('goal_sample', '')}"
            ),
            target_file=target_rel,
            proposed_content=source_code,
        )

        # Link proposal_id back onto workflow_patterns without modifying live skills directory yet.
        conn = self.task_store.database.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE workflow_patterns
                SET proposal_id = ?
                WHERE pattern_id = ? AND user_id = ?
                """,
                (proposal.proposal_id, pattern["pattern_id"], identity.user_id),
            )
            conn.commit()
        finally:
            conn.close()

        if verify:
            proposal = self.self_improve.verify_proposal(identity, proposal.proposal_id)
        return proposal
