"""Controlled, user-authorized self-improvement workflow with rollback support."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
from typing import Any

from core.ai.safety import strip_unsafe_control_chars
from core.config.settings import PROJECT_ROOT
from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity
from core.security.confirmation import ConfirmationManager
from core.security.permissions import PermissionManager
from core.self_improve.analyzer import (
    analyze_proposed_code,
    resolve_safe_repo_target,
)
from core.self_improve.verifier import run_isolated_verification


@dataclass(frozen=True)
class SelfImprovementProposal:
    """Persisted code modification proposal with verification & authorization state."""

    proposal_id: str
    user_id: str | None
    title: str
    problem_description: str
    target_files: tuple[str, ...]
    patch_diff: str
    diff_sha256: str
    static_analysis_passed: bool
    tests_passed: bool
    security_review_passed: bool
    risk_level: str
    status: str
    rollback_snapshot_dir: str | None
    review_report: dict[str, Any]
    created_at: str
    updated_at: str
    applied_at: str | None = None


class SelfImprovementManager:
    """
    Safe code-modification pipeline:
    propose -> static analysis & security review -> isolated sandbox tests
    -> user inspection -> explicit permission + confirmation -> atomic apply -> rollback.
    """

    def __init__(
        self,
        identity_manager: IdentityManager,
        *,
        permission_manager: PermissionManager | None = None,
        project_root: Path = PROJECT_ROOT,
        snapshots_root: Path | None = None,
    ):
        self.identity_manager = identity_manager
        self.database = identity_manager.database
        self.permission_manager = permission_manager
        self.project_root = Path(project_root).expanduser().resolve()
        self.snapshots_root = (
            Path(snapshots_root).expanduser().resolve()
            if snapshots_root is not None
            else (self.project_root / "data" / "self_improve_snapshots").resolve()
        )
        # Keep staged candidate sources in-memory / DB review_report (never mutating live repo on propose).
        self._staged_payloads: dict[str, dict[str, str]] = {}

    def _enforce_permission(
        self,
        identity: AuthenticatedIdentity,
        permission_key: str,
        resource: str,
        *,
        consume: bool = False,
    ) -> None:
        if self.permission_manager is None:
            raise PermissionError(
                "PermissionManager is required for self-improvement actions."
            )
        if consume:
            res = self.permission_manager.consume_if_authorized(
                identity,
                permission_key,
                resource_or_target=resource,
                actor="self_improvement_manager",
            )
        else:
            res = self.permission_manager.evaluate(
                identity,
                permission_key,
                resource_or_target=resource,
                actor="self_improvement_manager",
            )
        if res.blocked or (consume and not res.allowed):
            raise PermissionError(res.reason)

    @staticmethod
    def _unified_diff(rel_path: str, old_text: str, new_text: str) -> str:
        lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
        )
        return "".join(lines)

    def _row_to_proposal(self, row) -> SelfImprovementProposal:
        return SelfImprovementProposal(
            proposal_id=str(row["proposal_id"]),
            user_id=row["user_id"],
            title=str(row["title"]),
            problem_description=str(row["problem_description"]),
            target_files=tuple(json.loads(row["target_files"] or "[]")),
            patch_diff=str(row["patch_diff"]),
            diff_sha256=str(row["diff_sha256"]),
            static_analysis_passed=bool(row["static_analysis_passed"]),
            tests_passed=bool(row["tests_passed"]),
            security_review_passed=bool(row["security_review_passed"]),
            risk_level=str(row["risk_level"]),
            status=str(row["status"]),
            rollback_snapshot_dir=row["rollback_snapshot_dir"],
            review_report=json.loads(row["review_report"] or "{}"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            applied_at=row["applied_at"],
        )

    def get_proposal(self, proposal_id: str) -> SelfImprovementProposal:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM self_improvement_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Unknown self-improvement proposal: {proposal_id}")
        return self._row_to_proposal(row)

    def list_proposals(
        self,
        identity: AuthenticatedIdentity,
        *,
        limit: int = 25,
    ) -> list[SelfImprovementProposal]:
        validated = self.identity_manager.validate_session(identity)
        safe_limit = max(1, min(int(limit), 100))
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM self_improvement_proposals
                WHERE user_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (validated.user_id, safe_limit),
            ).fetchall()
        return [self._row_to_proposal(r) for r in rows]

    def propose_modification(
        self,
        identity: AuthenticatedIdentity,
        *,
        title: str,
        problem_description: str,
        target_file: str,
        proposed_content: str,
    ) -> SelfImprovementProposal:
        """Create a draft code modification proposal without touching any live workspace file."""
        validated = self.identity_manager.validate_session(identity)
        target_abs, rel_path = resolve_safe_repo_target(self.project_root, target_file)
        self._enforce_permission(validated, "self_improve.propose", rel_path, consume=False)

        clean_title = strip_unsafe_control_chars(str(title or ""), allow_newlines=False).strip()[:160]
        clean_problem = strip_unsafe_control_chars(
            str(problem_description or ""), allow_newlines=True
        ).strip()[:2000]
        if not clean_title or not clean_problem:
            raise ValueError("Proposal title and problem_description are required.")

        original_text = target_abs.read_text(encoding="utf-8") if target_abs.is_file() else ""
        diff_text = self._unified_diff(rel_path, original_text, proposed_content)
        if not diff_text.strip():
            raise ValueError("Proposed content produces an empty diff.")

        diff_hash = sha256(diff_text.encode("utf-8")).hexdigest()
        original_hash = sha256(original_text.encode("utf-8")).hexdigest()
        proposal_id = f"prop_{secrets.token_hex(8)}"
        now_iso = datetime.now(timezone.utc).isoformat()

        report = {
            "target_file": rel_path,
            "base_exists": target_abs.is_file(),
            "base_sha256": original_hash,
            "findings": [],
        }

        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO self_improvement_proposals (
                    proposal_id, user_id, title, problem_description,
                    target_files, patch_diff, diff_sha256,
                    static_analysis_passed, tests_passed, security_review_passed,
                    risk_level, status, rollback_snapshot_dir, review_report,
                    created_at, updated_at, applied_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 'high', 'draft', NULL, ?, ?, ?, NULL)
                """,
                (
                    proposal_id,
                    validated.user_id,
                    clean_title,
                    clean_problem,
                    json.dumps([rel_path]),
                    diff_text,
                    diff_hash,
                    json.dumps(report),
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()

        self._staged_payloads[proposal_id] = {
            "rel_path": rel_path,
            "proposed_content": proposed_content,
            "base_sha256": original_hash,
        }
        return self.get_proposal(proposal_id)

    def verify_proposal(
        self,
        identity: AuthenticatedIdentity,
        proposal_id: str,
        *,
        test_files: tuple[str, ...] = (),
        timeout_seconds: float = 45.0,
    ) -> SelfImprovementProposal:
        """Run static AST analysis, security inspection, and isolated sandbox tests."""
        validated = self.identity_manager.validate_session(identity)
        proposal = self.get_proposal(proposal_id)
        if proposal.user_id != validated.user_id:
            raise PermissionError("Proposal belongs to a different user.")
        rel_path = proposal.target_files[0]
        self._enforce_permission(validated, "self_improve.propose", rel_path, consume=False)

        staged = self._staged_payloads.get(proposal_id)
        if staged is None:
            raise RuntimeError(
                "Staged proposal content is missing from active session memory; recreate proposal."
            )
        proposed_content = staged["proposed_content"]

        static_report = analyze_proposed_code(rel_path, proposed_content)
        if not static_report.passed:
            review_payload = {
                **proposal.review_report,
                "findings": list(static_report.findings),
                "sandbox": {"ran": False, "reason": "Blocked by static/security check."},
            }
            self._update_verification_state(
                proposal_id,
                static_ok=static_report.syntax_ok,
                tests_ok=False,
                security_ok=static_report.security_ok,
                risk_level=static_report.risk_level,
                status="failed",
                review_report=review_payload,
            )
            return self.get_proposal(proposal_id)

        sandbox = run_isolated_verification(
            self.project_root,
            rel_path,
            proposed_content,
            test_files=test_files,
            timeout_seconds=timeout_seconds,
        )
        new_status = "awaiting_authorization" if (static_report.passed and sandbox.passed) else "failed"
        review_payload = {
            **proposal.review_report,
            "findings": list(static_report.findings),
            "sandbox": {
                "ran": True,
                "compile_passed": sandbox.compile_passed,
                "tests_passed": sandbox.tests_passed,
                "exit_code": sandbox.exit_code,
                "test_files": list(sandbox.test_files),
                "stdout_tail": sandbox.stdout[-1200:],
                "stderr_tail": sandbox.stderr[-1200:],
            },
        }
        self._update_verification_state(
            proposal_id,
            static_ok=static_report.syntax_ok,
            tests_ok=sandbox.passed,
            security_ok=static_report.security_ok,
            risk_level=static_report.risk_level,
            status=new_status,
            review_report=review_payload,
        )
        return self.get_proposal(proposal_id)

    def _update_verification_state(
        self,
        proposal_id: str,
        *,
        static_ok: bool,
        tests_ok: bool,
        security_ok: bool,
        risk_level: str,
        status: str,
        review_report: dict[str, Any],
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE self_improvement_proposals
                SET static_analysis_passed = ?,
                    tests_passed = ?,
                    security_review_passed = ?,
                    risk_level = ?,
                    status = ?,
                    review_report = ?,
                    updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    1 if static_ok else 0,
                    1 if tests_ok else 0,
                    1 if security_ok else 0,
                    risk_level,
                    status,
                    json.dumps(review_report),
                    now_iso,
                    proposal_id,
                ),
            )
            conn.commit()

    def present_review(
        self,
        identity: AuthenticatedIdentity,
        proposal_id: str,
    ) -> dict[str, Any]:
        """Return structured diff & verification summary for explicit user review."""
        validated = self.identity_manager.validate_session(identity)
        proposal = self.get_proposal(proposal_id)
        if proposal.user_id != validated.user_id:
            raise PermissionError("Proposal belongs to a different user.")
        return {
            "proposal_id": proposal.proposal_id,
            "title": proposal.title,
            "problem_description": proposal.problem_description,
            "target_files": list(proposal.target_files),
            "risk_level": proposal.risk_level,
            "status": proposal.status,
            "static_analysis_passed": proposal.static_analysis_passed,
            "tests_passed": proposal.tests_passed,
            "security_review_passed": proposal.security_review_passed,
            "diff_sha256": proposal.diff_sha256,
            "diff": proposal.patch_diff,
            "review_report": proposal.review_report,
        }

    def request_apply_confirmation(
        self,
        identity: AuthenticatedIdentity,
        proposal_id: str,
        confirmation_manager: ConfirmationManager,
    ) -> str:
        """Issue a bound one-time confirmation token after checking proposal readiness & permissions."""
        validated = self.identity_manager.validate_session(identity)
        proposal = self.get_proposal(proposal_id)
        if proposal.user_id != validated.user_id:
            raise PermissionError("Proposal belongs to a different user.")
        if proposal.status != "awaiting_authorization" or not (
            proposal.static_analysis_passed
            and proposal.tests_passed
            and proposal.security_review_passed
        ):
            raise PermissionError(
                "Proposal has not passed static analysis, security review, and sandbox tests."
            )
        rel_path = proposal.target_files[0]
        self._enforce_permission(validated, "self_improve.apply", rel_path, consume=False)
        return confirmation_manager.issue(
            "self_improve_apply",
            {"proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256},
            user_id=validated.user_id,
            session_id=validated.session_id,
            permission_key="self_improve.apply",
        )

    def authorize_and_apply(
        self,
        identity: AuthenticatedIdentity,
        proposal_id: str,
        *,
        confirmation_token: str | None,
        confirmation_manager: ConfirmationManager,
    ) -> SelfImprovementProposal:
        """Apply a verified patch ONLY with explicit user permission + valid one-time confirmation."""
        validated = self.identity_manager.validate_session(identity)
        proposal = self.get_proposal(proposal_id)
        if proposal.user_id != validated.user_id:
            raise PermissionError("Proposal belongs to a different user.")

        if proposal.status != "awaiting_authorization" or not (
            proposal.static_analysis_passed
            and proposal.tests_passed
            and proposal.security_review_passed
        ):
            raise PermissionError(
                "Cannot apply unverified or failed self-improvement proposal."
            )

        rel_path = proposal.target_files[0]
        target_abs, verified_rel = resolve_safe_repo_target(self.project_root, rel_path)
        if verified_rel != rel_path:
            raise PermissionError("Target path mismatch.")

        # Re-check permission first, then verify and consume one-time confirmation bound to exact diff hash.
        self._enforce_permission(validated, "self_improve.apply", rel_path, consume=False)
        confirmed, reason = confirmation_manager.consume(
            confirmation_token,
            "self_improve_apply",
            {"proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256},
            user_id=validated.user_id,
            session_id=validated.session_id,
        )
        if not confirmed:
            raise PermissionError(f"Explicit user authorization required: {reason}")

        # Consume permission grant only after confirmation succeeds.
        self._enforce_permission(validated, "self_improve.apply", rel_path, consume=True)

        staged = self._staged_payloads.get(proposal_id)
        if staged is None or staged.get("rel_path") != rel_path:
            raise RuntimeError("Staged patch content missing from memory.")

        current_text = target_abs.read_text(encoding="utf-8") if target_abs.is_file() else ""
        current_sha = sha256(current_text.encode("utf-8")).hexdigest()
        expected_base_sha = staged.get("base_sha256")
        if current_sha != expected_base_sha:
            raise RuntimeError(
                f"Target file '{rel_path}' changed since proposal creation; refusing stale apply."
            )

        snap_dir = (self.snapshots_root / proposal_id).resolve()
        snap_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(snap_dir, 0o700)
        except OSError:
            pass

        manifest = {
            "proposal_id": proposal_id,
            "rel_path": rel_path,
            "existed_before": target_abs.is_file(),
            "base_sha256": current_sha,
        }
        (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if target_abs.is_file():
            (snap_dir / "original_content.bak").write_text(current_text, encoding="utf-8")

        target_abs.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_abs.with_name(f".{target_abs.name}.{secrets.token_hex(6)}.tmp")
        tmp_path.write_text(staged["proposed_content"], encoding="utf-8")
        os.replace(tmp_path, target_abs)

        now_iso = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE self_improvement_proposals
                SET status = 'applied',
                    rollback_snapshot_dir = ?,
                    updated_at = ?,
                    applied_at = ?
                WHERE proposal_id = ?
                """,
                (str(snap_dir), now_iso, now_iso, proposal_id),
            )
            conn.commit()

        return self.get_proposal(proposal_id)

    def request_rollback_confirmation(
        self,
        identity: AuthenticatedIdentity,
        proposal_id: str,
        confirmation_manager: ConfirmationManager,
    ) -> str:
        validated = self.identity_manager.validate_session(identity)
        proposal = self.get_proposal(proposal_id)
        if proposal.user_id != validated.user_id:
            raise PermissionError("Proposal belongs to a different user.")
        if proposal.status != "applied":
            raise ValueError("Only applied proposals can be rolled back.")
        rel_path = proposal.target_files[0]
        self._enforce_permission(validated, "self_improve.apply", rel_path, consume=False)
        return confirmation_manager.issue(
            "self_improve_rollback",
            {"proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256},
            user_id=validated.user_id,
            session_id=validated.session_id,
            permission_key="self_improve.apply",
        )

    def rollback_proposal(
        self,
        identity: AuthenticatedIdentity,
        proposal_id: str,
        *,
        confirmation_token: str | None,
        confirmation_manager: ConfirmationManager,
    ) -> SelfImprovementProposal:
        """Restore the exact pre-patch file state from atomic snapshot."""
        validated = self.identity_manager.validate_session(identity)
        proposal = self.get_proposal(proposal_id)
        if proposal.user_id != validated.user_id:
            raise PermissionError("Proposal belongs to a different user.")
        if proposal.status != "applied" or not proposal.rollback_snapshot_dir:
            raise ValueError("No applied snapshot available for rollback.")

        rel_path = proposal.target_files[0]
        target_abs, _ = resolve_safe_repo_target(self.project_root, rel_path)
        self._enforce_permission(validated, "self_improve.apply", rel_path, consume=False)

        confirmed, reason = confirmation_manager.consume(
            confirmation_token,
            "self_improve_rollback",
            {"proposal_id": proposal.proposal_id, "diff_sha256": proposal.diff_sha256},
            user_id=validated.user_id,
            session_id=validated.session_id,
        )
        if not confirmed:
            raise PermissionError(f"Explicit user authorization required for rollback: {reason}")
        self._enforce_permission(validated, "self_improve.apply", rel_path, consume=True)

        snap_dir = Path(proposal.rollback_snapshot_dir).resolve()
        manifest = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("rel_path") != rel_path:
            raise RuntimeError("Rollback snapshot manifest mismatch.")

        if manifest.get("existed_before", False):
            original_content = (snap_dir / "original_content.bak").read_text(encoding="utf-8")
            tmp_path = target_abs.with_name(f".{target_abs.name}.{secrets.token_hex(6)}.rollback.tmp")
            tmp_path.write_text(original_content, encoding="utf-8")
            os.replace(tmp_path, target_abs)
        elif target_abs.exists():
            target_abs.unlink()

        now_iso = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE self_improvement_proposals
                SET status = 'rolled_back',
                    updated_at = ?
                WHERE proposal_id = ?
                """,
                (now_iso, proposal_id),
            )
            conn.commit()
        return self.get_proposal(proposal_id)

    def reject_proposal(
        self,
        identity: AuthenticatedIdentity,
        proposal_id: str,
        *,
        reason: str = "",
    ) -> SelfImprovementProposal:
        validated = self.identity_manager.validate_session(identity)
        proposal = self.get_proposal(proposal_id)
        if proposal.user_id != validated.user_id:
            raise PermissionError("Proposal belongs to a different user.")
        now_iso = datetime.now(timezone.utc).isoformat()
        report = dict(proposal.review_report)
        if reason:
            report["rejection_reason"] = str(reason)[:500]
        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE self_improvement_proposals
                SET status = 'rejected',
                    review_report = ?,
                    updated_at = ?
                WHERE proposal_id = ?
                """,
                (json.dumps(report), now_iso, proposal_id),
            )
            conn.commit()
        self._staged_payloads.pop(proposal_id, None)
        return self.get_proposal(proposal_id)
