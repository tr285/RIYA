"""Offline evaluation datasets and accuracy/latency benchmarking for RIYA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import secrets
import time
from typing import Any, Callable

from core.ai.safety import strip_unsafe_control_chars
from core.identity.manager import IdentityManager
from core.identity.models import AuthenticatedIdentity


@dataclass(frozen=True)
class EvaluationCase:
    """Single offline benchmark case."""

    case_id: str
    dataset_name: str
    category: str
    prompt: str
    expected_keywords: tuple[str, ...] = ()
    forbidden_keywords: tuple[str, ...] = ()
    max_latency_ms: float | None = None


@dataclass(frozen=True)
class EvaluationCaseResult:
    """Outcome for one evaluated prompt."""

    case_id: str
    prompt: str
    response: str
    passed: bool
    latency_ms: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationReport:
    """Summary metrics for an offline evaluation run."""

    run_id: str
    dataset_name: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    avg_latency_ms: float
    results: tuple[EvaluationCaseResult, ...]
    created_at: str


class OfflineEvaluator:
    """Stores and executes offline evaluation datasets without network calls."""

    def __init__(
        self,
        identity_manager: IdentityManager,
        *,
        permission_manager: Any = None,
    ):
        self.identity_manager = identity_manager
        self.database = identity_manager.database
        self.permission_manager = permission_manager

    def _enforce_permission(self, identity: AuthenticatedIdentity, resource: str) -> None:
        if self.permission_manager is None:
            return
        res = self.permission_manager.evaluate(
            identity,
            "learning.manage",
            resource_or_target=resource,
            actor="offline_evaluator",
        )
        if res.blocked:
            raise PermissionError(res.reason)

    def add_case(
        self,
        identity: AuthenticatedIdentity,
        *,
        dataset_name: str,
        prompt: str,
        expected_keywords: list[str] | tuple[str, ...] = (),
        forbidden_keywords: list[str] | tuple[str, ...] = (),
        max_latency_ms: float | None = None,
        category: str = "general",
    ) -> EvaluationCase:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"learning:eval:add:{dataset_name}")
        ds_name = strip_unsafe_control_chars(str(dataset_name or ""), allow_newlines=False).strip()[:64]
        clean_prompt = strip_unsafe_control_chars(str(prompt or ""), allow_newlines=True).strip()[:2000]
        if not ds_name or not clean_prompt:
            raise ValueError("dataset_name and prompt are required for evaluation case.")

        case_id = f"eval_{secrets.token_hex(8)}"
        exp_kw = tuple(str(k).strip() for k in expected_keywords if str(k).strip())
        forb_kw = tuple(str(k).strip() for k in forbidden_keywords if str(k).strip())
        now_iso = datetime.now(timezone.utc).isoformat()

        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_datasets (
                    case_id, user_id, dataset_name, category, prompt,
                    expected_keywords, forbidden_keywords, max_latency_ms, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    validated.user_id,
                    ds_name,
                    strip_unsafe_control_chars(str(category or "general"), allow_newlines=False).strip()[:48],
                    clean_prompt,
                    json.dumps(list(exp_kw)),
                    json.dumps(list(forb_kw)),
                    float(max_latency_ms) if max_latency_ms is not None else None,
                    now_iso,
                ),
            )
            conn.commit()

        return EvaluationCase(
            case_id=case_id,
            dataset_name=ds_name,
            category=category,
            prompt=clean_prompt,
            expected_keywords=exp_kw,
            forbidden_keywords=forb_kw,
            max_latency_ms=max_latency_ms,
        )

    def list_cases(
        self,
        identity: AuthenticatedIdentity,
        *,
        dataset_name: str,
    ) -> list[EvaluationCase]:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"learning:eval:read:{dataset_name}")
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT case_id, dataset_name, category, prompt,
                       expected_keywords, forbidden_keywords, max_latency_ms
                FROM evaluation_datasets
                WHERE user_id = ? AND dataset_name = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (validated.user_id, dataset_name),
            ).fetchall()
        return [
            EvaluationCase(
                case_id=str(row["case_id"]),
                dataset_name=str(row["dataset_name"]),
                category=str(row["category"]),
                prompt=str(row["prompt"]),
                expected_keywords=tuple(json.loads(row["expected_keywords"] or "[]")),
                forbidden_keywords=tuple(json.loads(row["forbidden_keywords"] or "[]")),
                max_latency_ms=row["max_latency_ms"],
            )
            for row in rows
        ]

    def run_dataset(
        self,
        identity: AuthenticatedIdentity,
        *,
        dataset_name: str,
        responder: Callable[[str], str],
    ) -> EvaluationReport:
        """Execute stored offline evaluation cases via the supplied local responder callable."""
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, f"learning:eval:run:{dataset_name}")
        cases = self.list_cases(validated, dataset_name=dataset_name)
        if not cases:
            raise ValueError(f"No evaluation cases found in dataset '{dataset_name}'.")

        case_results: list[EvaluationCaseResult] = []
        for case in cases:
            t0 = time.perf_counter()
            reasons: list[str] = []
            response_text = ""
            try:
                response_text = str(responder(case.prompt) or "")
            except Exception as exc:
                reasons.append(f"Responder error: {exc}")
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

            lower_resp = response_text.lower()
            for kw in case.expected_keywords:
                if kw.lower() not in lower_resp:
                    reasons.append(f"Missing expected keyword: '{kw}'")
            for kw in case.forbidden_keywords:
                if kw.lower() in lower_resp:
                    reasons.append(f"Contains forbidden keyword: '{kw}'")
            if case.max_latency_ms is not None and latency_ms > case.max_latency_ms:
                reasons.append(
                    f"Latency {latency_ms}ms exceeded budget {case.max_latency_ms}ms"
                )

            case_results.append(
                EvaluationCaseResult(
                    case_id=case.case_id,
                    prompt=case.prompt,
                    response=response_text,
                    passed=(len(reasons) == 0),
                    latency_ms=latency_ms,
                    reasons=tuple(reasons),
                )
            )

        total = len(case_results)
        passed = sum(1 for r in case_results if r.passed)
        avg_lat = round(sum(r.latency_ms for r in case_results) / total, 2)
        pass_rate = round(passed / total, 4)
        run_id = f"run_{secrets.token_hex(8)}"
        now_iso = datetime.now(timezone.utc).isoformat()

        summary_payload = {
            "pass_rate": pass_rate,
            "cases": [
                {
                    "case_id": r.case_id,
                    "passed": r.passed,
                    "latency_ms": r.latency_ms,
                    "reasons": list(r.reasons),
                }
                for r in case_results
            ],
        }
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_runs (
                    run_id, user_id, dataset_name, total_cases,
                    passed_cases, avg_latency_ms, summary_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    validated.user_id,
                    dataset_name,
                    total,
                    passed,
                    avg_lat,
                    json.dumps(summary_payload),
                    now_iso,
                ),
            )
            conn.commit()

        return EvaluationReport(
            run_id=run_id,
            dataset_name=dataset_name,
            total_cases=total,
            passed_cases=passed,
            pass_rate=pass_rate,
            avg_latency_ms=avg_lat,
            results=tuple(case_results),
            created_at=now_iso,
        )

    def list_runs(
        self,
        identity: AuthenticatedIdentity,
        *,
        dataset_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        validated = self.identity_manager.validate_session(identity)
        self._enforce_permission(validated, "learning:eval:history")
        safe_limit = max(1, min(int(limit), 100))
        with self.database.connect() as conn:
            if dataset_name is not None:
                rows = conn.execute(
                    """
                    SELECT run_id, dataset_name, total_cases, passed_cases,
                           avg_latency_ms, summary_json, created_at
                    FROM evaluation_runs
                    WHERE user_id = ? AND dataset_name = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (validated.user_id, dataset_name, safe_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT run_id, dataset_name, total_cases, passed_cases,
                           avg_latency_ms, summary_json, created_at
                    FROM evaluation_runs
                    WHERE user_id = ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (validated.user_id, safe_limit),
                ).fetchall()
        return [
            {
                "run_id": str(row["run_id"]),
                "dataset_name": str(row["dataset_name"]),
                "total_cases": int(row["total_cases"]),
                "passed_cases": int(row["passed_cases"]),
                "avg_latency_ms": float(row["avg_latency_ms"]),
                "summary": json.loads(row["summary_json"] or "{}"),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
