"""First-launch detection, onboarding state persistence, and crash-safe recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from core.database.migrations import CURRENT_SCHEMA_VERSION, get_schema_version
from core.identity.models import AuthenticatedIdentity
from core.memory.database import DATABASE_PATH, MemoryDatabase


class InstallState(str, Enum):
    """Accurate installation and onboarding states detected at startup."""

    FRESH_INSTALL = "fresh_install"
    SETUP_IN_PROGRESS = "setup_in_progress"
    SETUP_COMPLETED = "setup_completed"
    EXISTING_V04 = "existing_v04"
    EXISTING_V05 = "existing_v05"
    NEEDS_RECOVERY = "needs_recovery"


ONBOARDING_STEPS: tuple[str, ...] = (
    "welcome",
    "account",
    "ai_engine",
    "voice",
    "permissions",
    "diagnostics",
    "complete",
)

_ALLOWED_CHECKPOINT_KEYS = frozenset(
    {
        "status",
        "current_step",
        "completed_steps",
        "selected_model_path",
        "selected_backend",
        "active_user_id",
        "last_error",
        "started_at",
        "updated_at",
        "completed_at",
    }
)
_MAX_CHECKPOINT_BYTES = 64 * 1024


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_checkpoint_path(database_path: str | Path = DATABASE_PATH) -> Path:
    db_path = Path(database_path).expanduser().resolve()
    return db_path.parent / "onboarding_checkpoint.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".onboarding_state.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return path


def _validate_step(step: str) -> str:
    cleaned = str(step or "").strip().lower()
    if cleaned not in ONBOARDING_STEPS:
        raise ValueError(f"Unsupported onboarding step: {step!r}")
    return cleaned


def _next_step_after(completed_steps: list[str] | tuple[str, ...]) -> str:
    done = set(completed_steps)
    for step in ONBOARDING_STEPS:
        if step not in done:
            return step
    return "complete"


@dataclass(frozen=True)
class OnboardingDetectionResult:
    install_state: InstallState
    schema_version: int
    database_accessible: bool
    database_error: str | None
    registered_users_count: int
    legacy_memories_count: int
    checkpoint_present: bool
    checkpoint_corrupted: bool
    checkpoint_error: str | None
    active_user_id: str | None
    resume_step: str
    completed_steps: tuple[str, ...]
    selected_model_path: str | None
    selected_backend: str
    needs_onboarding: bool
    can_resume: bool
    message: str
    recovery_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserOnboardingRecord:
    user_id: str
    status: str
    current_step: str
    completed_steps: tuple[str, ...]
    model_path: str | None
    ai_backend: str
    voice_configured: bool
    permissions_reviewed: bool
    health_checked: bool
    updated_at: str
    completed_at: str | None = None


def _load_checkpoint(
    checkpoint_path: Path,
) -> tuple[bool, bool, str | None, dict[str, Any] | None]:
    if not checkpoint_path.exists():
        return False, False, None, None
    try:
        if checkpoint_path.stat().st_size > _MAX_CHECKPOINT_BYTES:
            return (
                True,
                True,
                "Onboarding checkpoint file exceeds maximum allowed size.",
                None,
            )
        raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return True, True, "Onboarding checkpoint is not a JSON object.", None
        status = str(raw.get("status", "not_started"))
        if status not in {"not_started", "in_progress", "completed", "recovery_required"}:
            return True, True, f"Invalid checkpoint status: {status!r}.", None
        step = str(raw.get("current_step", "welcome"))
        if step not in ONBOARDING_STEPS:
            return True, True, f"Invalid checkpoint step: {step!r}.", None
        steps = raw.get("completed_steps", [])
        if not isinstance(steps, list) or not all(
            isinstance(s, str) and s in ONBOARDING_STEPS for s in steps
        ):
            return True, True, "Invalid completed_steps in onboarding checkpoint.", None
        filtered = {k: v for k, v in raw.items() if k in _ALLOWED_CHECKPOINT_KEYS}
        return True, False, None, filtered
    except Exception as exc:
        return True, True, f"Corrupted onboarding checkpoint JSON: {exc}", None


def detect_install_state(
    database_path: str | Path = DATABASE_PATH,
    *,
    checkpoint_path: str | Path | None = None,
) -> OnboardingDetectionResult:
    """Safely inspect DB and checkpoint files without mutating or deleting user data."""
    db_path = Path(database_path).expanduser().resolve()
    cp_path = (
        Path(checkpoint_path).expanduser().resolve()
        if checkpoint_path is not None
        else default_checkpoint_path(db_path)
    )

    cp_present, cp_corrupt, cp_err, cp_data = _load_checkpoint(cp_path)
    cp_status = str(cp_data.get("status")) if cp_data else None
    cp_step = str(cp_data.get("current_step", "welcome")) if cp_data else "welcome"
    cp_completed = tuple(cp_data.get("completed_steps", [])) if cp_data else ()
    cp_model = cp_data.get("selected_model_path") if cp_data else None
    cp_backend = str(cp_data.get("selected_backend", "auto") or "auto") if cp_data else "auto"
    cp_user_id = cp_data.get("active_user_id") if cp_data else None

    if not db_path.exists():
        if cp_corrupt:
            return OnboardingDetectionResult(
                install_state=InstallState.NEEDS_RECOVERY,
                schema_version=0,
                database_accessible=False,
                database_error="Database file does not exist yet and checkpoint is corrupted.",
                registered_users_count=0,
                legacy_memories_count=0,
                checkpoint_present=True,
                checkpoint_corrupted=True,
                checkpoint_error=cp_err,
                active_user_id=None,
                resume_step="welcome",
                completed_steps=(),
                selected_model_path=None,
                selected_backend="auto",
                needs_onboarding=True,
                can_resume=False,
                message="Onboarding checkpoint configuration is corrupted. Back it up safely and restart setup.",
                recovery_hints=(
                    "Use 'Backup & Reset Corrupt Checkpoint' to preserve a copy and start fresh setup.",
                ),
            )
        if cp_status == "completed" or (cp_status == "in_progress" and cp_user_id):
            return OnboardingDetectionResult(
                install_state=InstallState.NEEDS_RECOVERY,
                schema_version=0,
                database_accessible=False,
                database_error=f"Expected database not found at {db_path}.",
                registered_users_count=0,
                legacy_memories_count=0,
                checkpoint_present=cp_present,
                checkpoint_corrupted=False,
                checkpoint_error=None,
                active_user_id=cp_user_id,
                resume_step=cp_step,
                completed_steps=cp_completed,
                selected_model_path=cp_model,
                selected_backend=cp_backend,
                needs_onboarding=True,
                can_resume=False,
                message="Previous setup state exists, but the local database file is missing.",
                recovery_hints=(
                    "Restore your data/riya.db backup if available, or confirm resetting only the setup checkpoint.",
                ),
            )
        if cp_status == "in_progress":
            return OnboardingDetectionResult(
                install_state=InstallState.SETUP_IN_PROGRESS,
                schema_version=0,
                database_accessible=False,
                database_error=None,
                registered_users_count=0,
                legacy_memories_count=0,
                checkpoint_present=True,
                checkpoint_corrupted=False,
                checkpoint_error=None,
                active_user_id=None,
                resume_step=cp_step,
                completed_steps=cp_completed,
                selected_model_path=cp_model,
                selected_backend=cp_backend,
                needs_onboarding=True,
                can_resume=True,
                message=f"Incomplete setup detected. Resume from step '{cp_step}'.",
            )
        return OnboardingDetectionResult(
            install_state=InstallState.FRESH_INSTALL,
            schema_version=0,
            database_accessible=False,
            database_error=None,
            registered_users_count=0,
            legacy_memories_count=0,
            checkpoint_present=cp_present,
            checkpoint_corrupted=False,
            checkpoint_error=None,
            active_user_id=None,
            resume_step="welcome",
            completed_steps=(),
            selected_model_path=None,
            selected_backend="auto",
            needs_onboarding=True,
            can_resume=False,
            message="Fresh RIYA installation detected. Ready to begin guided setup.",
        )

    # Database exists: inspect strictly read-only first so corrupted/partial DBs are never wiped.
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        quick = conn.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]).lower() != "ok":
            raise sqlite3.DatabaseError(f"SQLite quick_check failed: {quick[0] if quick else 'unknown'}")

        tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        schema_ver = get_schema_version(conn)

        recovery_reasons: list[str] = []
        if "memories_v04_backup" in tables or "memories_v05_rollback" in tables:
            recovery_reasons.append(
                "An interrupted migration backup table exists in the local database."
            )
        if schema_ver > CURRENT_SCHEMA_VERSION:
            recovery_reasons.append(
                f"Database schema version {schema_ver} is newer than supported version {CURRENT_SCHEMA_VERSION}."
            )
        if schema_ver >= 2 and "users" not in tables:
            recovery_reasons.append("Schema version >= 2 is missing the 'users' table.")
        if schema_ver >= 3 and (
            "permission_grants" not in tables or "permission_audit_log" not in tables
        ):
            recovery_reasons.append(
                "Schema version >= 3 is missing permission/audit tables."
            )
        if schema_ver >= 4 and (
            "installation_setup_state" not in tables
            or "user_onboarding_state" not in tables
        ):
            recovery_reasons.append(
                "Schema version >= 4 is missing onboarding tables."
            )

        users_count = 0
        if "users" in tables:
            users_count = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

        legacy_memories = 0
        if "memories" in tables:
            cols = {
                str(r[1])
                for r in conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "legacy_unclaimed" in cols:
                legacy_memories = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE user_id IS NULL AND legacy_unclaimed = 1"
                    ).fetchone()[0]
                )
            elif "user_id" not in cols:
                legacy_memories = int(
                    conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                )

        db_setup_row = None
        if "installation_setup_state" in tables:
            db_setup_row = conn.execute(
                "SELECT * FROM installation_setup_state WHERE id = 1"
            ).fetchone()
    except sqlite3.DatabaseError as exc:
        return OnboardingDetectionResult(
            install_state=InstallState.NEEDS_RECOVERY,
            schema_version=0,
            database_accessible=False,
            database_error=str(exc),
            registered_users_count=0,
            legacy_memories_count=0,
            checkpoint_present=cp_present,
            checkpoint_corrupted=cp_corrupt,
            checkpoint_error=cp_err,
            active_user_id=cp_user_id,
            resume_step=cp_step,
            completed_steps=cp_completed,
            selected_model_path=cp_model,
            selected_backend=cp_backend,
            needs_onboarding=True,
            can_resume=False,
            message=f"Local database requires recovery: {exc}",
            recovery_hints=(
                "Do not delete data automatically. Inspect or restore a backup (e.g., data/riya.db.before_v05_*).",
            ),
        )
    finally:
        if conn is not None:
            conn.close()

    # Determine authoritative state from DB + checkpoint safely.
    db_status = str(db_setup_row["status"]) if db_setup_row is not None else None
    db_step = str(db_setup_row["current_step"]) if db_setup_row is not None else None
    db_completed: tuple[str, ...] = ()
    if db_setup_row is not None:
        try:
            parsed = json.loads(str(db_setup_row["completed_steps"] or "[]"))
            if isinstance(parsed, list):
                db_completed = tuple(str(s) for s in parsed if s in ONBOARDING_STEPS)
        except Exception:
            recovery_reasons.append("Onboarding completed_steps column is corrupted.")
    db_model = (
        str(db_setup_row["selected_model_path"])
        if db_setup_row is not None and db_setup_row["selected_model_path"]
        else cp_model
    )
    db_backend = (
        str(db_setup_row["selected_backend"] or "auto")
        if db_setup_row is not None
        else cp_backend
    )
    db_user_id = (
        str(db_setup_row["active_user_id"])
        if db_setup_row is not None and db_setup_row["active_user_id"]
        else cp_user_id
    )
    db_last_error = (
        str(db_setup_row["last_error"])
        if db_setup_row is not None and db_setup_row["last_error"]
        else (str(cp_data.get("last_error")) if cp_data and cp_data.get("last_error") else None)
    )

    if cp_corrupt:
        recovery_reasons.append(cp_err or "Corrupted onboarding checkpoint file.")
    if db_status == "recovery_required":
        recovery_reasons.append(db_last_error or "Previous setup ended with a recovery state.")

    eff_status = db_status or cp_status
    eff_step = db_step or cp_step
    eff_completed = db_completed or cp_completed

    if recovery_reasons:
        return OnboardingDetectionResult(
            install_state=InstallState.NEEDS_RECOVERY,
            schema_version=schema_ver,
            database_accessible=True,
            database_error=None,
            registered_users_count=users_count,
            legacy_memories_count=legacy_memories,
            checkpoint_present=cp_present,
            checkpoint_corrupted=cp_corrupt,
            checkpoint_error=cp_err,
            active_user_id=db_user_id,
            resume_step=eff_step if eff_step in ONBOARDING_STEPS else "welcome",
            completed_steps=eff_completed,
            selected_model_path=db_model,
            selected_backend=db_backend,
            needs_onboarding=True,
            can_resume=eff_status == "in_progress" and not cp_corrupt,
            message="Installation state requires user-guided recovery before continuing.",
            recovery_hints=tuple(recovery_reasons),
        )

    if users_count == 0:
        if eff_status == "completed":
            return OnboardingDetectionResult(
                install_state=InstallState.NEEDS_RECOVERY,
                schema_version=schema_ver,
                database_accessible=True,
                database_error=None,
                registered_users_count=0,
                legacy_memories_count=legacy_memories,
                checkpoint_present=cp_present,
                checkpoint_corrupted=False,
                checkpoint_error=None,
                active_user_id=db_user_id,
                resume_step="account",
                completed_steps=eff_completed,
                selected_model_path=db_model,
                selected_backend=db_backend,
                needs_onboarding=True,
                can_resume=True,
                message="Setup was marked completed, but no local user accounts exist in the database.",
                recovery_hints=(
                    "Create or restore a local user account cleanly without resetting memories.",
                ),
            )

        if eff_status == "in_progress":
            return OnboardingDetectionResult(
                install_state=InstallState.SETUP_IN_PROGRESS,
                schema_version=schema_ver,
                database_accessible=True,
                database_error=None,
                registered_users_count=0,
                legacy_memories_count=legacy_memories,
                checkpoint_present=cp_present,
                checkpoint_corrupted=False,
                checkpoint_error=None,
                active_user_id=db_user_id,
                resume_step=eff_step,
                completed_steps=eff_completed,
                selected_model_path=db_model,
                selected_backend=db_backend,
                needs_onboarding=True,
                can_resume=True,
                message=f"Setup was interrupted at '{eff_step}'. Resume safely from your last completed step.",
            )

        if legacy_memories > 0 or (schema_ver == 1 and "memories" in tables):
            return OnboardingDetectionResult(
                install_state=InstallState.EXISTING_V04,
                schema_version=schema_ver,
                database_accessible=True,
                database_error=None,
                registered_users_count=0,
                legacy_memories_count=legacy_memories,
                checkpoint_present=cp_present,
                checkpoint_corrupted=False,
                checkpoint_error=None,
                active_user_id=None,
                resume_step="welcome",
                completed_steps=(),
                selected_model_path=db_model,
                selected_backend=db_backend,
                needs_onboarding=True,
                can_resume=False,
                message=(
                    f"Existing RIYA v0.4 data detected ({legacy_memories} preserved memories). "
                    "Create a local account to continue safely."
                ),
            )

        return OnboardingDetectionResult(
            install_state=InstallState.FRESH_INSTALL,
            schema_version=schema_ver,
            database_accessible=True,
            database_error=None,
            registered_users_count=0,
            legacy_memories_count=0,
            checkpoint_present=cp_present,
            checkpoint_corrupted=False,
            checkpoint_error=None,
            active_user_id=None,
            resume_step="welcome",
            completed_steps=(),
            selected_model_path=db_model,
            selected_backend=db_backend,
            needs_onboarding=True,
            can_resume=False,
            message="Fresh RIYA installation detected. Ready to start setup.",
        )

    # users_count > 0: NEVER misclassify an existing account database as fresh install!
    if eff_status == "in_progress":
        return OnboardingDetectionResult(
            install_state=InstallState.SETUP_IN_PROGRESS,
            schema_version=schema_ver,
            database_accessible=True,
            database_error=None,
            registered_users_count=users_count,
            legacy_memories_count=legacy_memories,
            checkpoint_present=cp_present,
            checkpoint_corrupted=False,
            checkpoint_error=None,
            active_user_id=db_user_id,
            resume_step=eff_step,
            completed_steps=eff_completed,
            selected_model_path=db_model,
            selected_backend=db_backend,
            needs_onboarding=True,
            can_resume=True,
            message=f"Existing account preserved. Resume setup from step '{eff_step}'.",
        )

    if eff_status == "completed":
        return OnboardingDetectionResult(
            install_state=InstallState.SETUP_COMPLETED,
            schema_version=schema_ver,
            database_accessible=True,
            database_error=None,
            registered_users_count=users_count,
            legacy_memories_count=legacy_memories,
            checkpoint_present=cp_present,
            checkpoint_corrupted=False,
            checkpoint_error=None,
            active_user_id=db_user_id,
            resume_step="complete",
            completed_steps=eff_completed or ONBOARDING_STEPS,
            selected_model_path=db_model,
            selected_backend=db_backend,
            needs_onboarding=False,
            can_resume=False,
            message="RIYA setup is complete. Sign in with your local account.",
        )

    return OnboardingDetectionResult(
        install_state=InstallState.EXISTING_V05,
        schema_version=schema_ver,
        database_accessible=True,
        database_error=None,
        registered_users_count=users_count,
        legacy_memories_count=legacy_memories,
        checkpoint_present=cp_present,
        checkpoint_corrupted=False,
        checkpoint_error=None,
        active_user_id=db_user_id,
        resume_step="account",
        completed_steps=eff_completed,
        selected_model_path=db_model,
        selected_backend=db_backend,
        needs_onboarding=False,
        can_resume=False,
        message=(
            f"Existing RIYA v0.5 installation detected ({users_count} local account(s)). "
            "Sign in to open RIYA or review setup options."
        ),
    )


class OnboardingStateStore:
    """Manages persistent installation & per-user onboarding state in SQLite + checkpoint."""

    def __init__(
        self,
        database: MemoryDatabase | None = None,
        *,
        checkpoint_path: str | Path | None = None,
    ):
        self.database = database or MemoryDatabase()
        self.checkpoint_path = (
            Path(checkpoint_path).expanduser().resolve()
            if checkpoint_path is not None
            else default_checkpoint_path(self.database.database_path)
        )

    def detect(self) -> OnboardingDetectionResult:
        return detect_install_state(
            self.database.database_path,
            checkpoint_path=self.checkpoint_path,
        )

    def _write_checkpoint(self, payload: dict[str, Any]) -> None:
        safe = {k: v for k, v in payload.items() if k in _ALLOWED_CHECKPOINT_KEYS}
        _atomic_write_json(self.checkpoint_path, safe)

    @staticmethod
    def repair_corrupted_checkpoint(checkpoint_path: str | Path) -> Path | None:
        """Preserve corrupted checkpoint as a timestamped backup instead of discarding silently."""
        cp = Path(checkpoint_path).expanduser().resolve()
        if not cp.exists():
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = cp.with_name(f"{cp.name}.corrupt.{ts}")
        os.replace(cp, backup)
        return backup

    def begin_or_resume_setup(
        self,
        *,
        step: str = "welcome",
        active_user_id: str | None = None,
    ) -> OnboardingDetectionResult:
        safe_step = _validate_step(step)
        now_iso = _utc_now_iso()
        conn = self.database.connect()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT * FROM installation_setup_state WHERE id = 1"
                ).fetchone()
                if existing is None:
                    completed: list[str] = []
                    started_at = now_iso
                    model_path = None
                    backend = "auto"
                    curr = safe_step
                    user_id = active_user_id
                else:
                    completed = json.loads(str(existing["completed_steps"] or "[]"))
                    started_at = str(existing["started_at"] or now_iso)
                    model_path = existing["selected_model_path"]
                    backend = str(existing["selected_backend"] or "auto")
                    curr = str(existing["current_step"] or safe_step)
                    if existing["status"] == "not_started":
                        curr = safe_step
                    user_id = active_user_id or existing["active_user_id"]

                payload_steps = json.dumps(completed)
                conn.execute(
                    """
                    INSERT INTO installation_setup_state (
                        id, status, current_step, completed_steps,
                        selected_model_path, selected_backend, active_user_id,
                        last_error, started_at, updated_at, completed_at
                    )
                    VALUES (1, 'in_progress', ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        status = CASE
                            WHEN installation_setup_state.status = 'completed' THEN 'completed'
                            ELSE 'in_progress'
                        END,
                        current_step = excluded.current_step,
                        active_user_id = COALESCE(excluded.active_user_id, installation_setup_state.active_user_id),
                        last_error = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (curr, payload_steps, model_path, backend, user_id, started_at, now_iso),
                )
        finally:
            conn.close()

        row = self._read_installation_row()
        if row is not None:
            self._write_checkpoint(row)
        return self.detect()

    def record_step_progress(
        self,
        completed_step: str,
        *,
        next_step: str | None = None,
        identity: AuthenticatedIdentity | None = None,
        selected_model_path: str | None = None,
        selected_backend: str | None = None,
        voice_configured: bool | None = None,
        permissions_reviewed: bool | None = None,
        health_checked: bool | None = None,
    ) -> None:
        safe_done = _validate_step(completed_step)
        now_iso = _utc_now_iso()
        conn = self.database.connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT * FROM installation_setup_state WHERE id = 1"
                ).fetchone()
                existing_steps: list[str] = (
                    json.loads(str(row["completed_steps"] or "[]")) if row else []
                )
                if safe_done not in existing_steps:
                    existing_steps.append(safe_done)
                resolved_next = (
                    _validate_step(next_step)
                    if next_step is not None
                    else _next_step_after(existing_steps)
                )
                prior_status = str(row["status"]) if row else "in_progress"
                new_status = "completed" if prior_status == "completed" else "in_progress"
                eff_model = (
                    selected_model_path
                    if selected_model_path is not None
                    else (row["selected_model_path"] if row else None)
                )
                eff_backend = (
                    selected_backend
                    if selected_backend is not None
                    else (str(row["selected_backend"] or "auto") if row else "auto")
                )
                eff_uid = (
                    identity.user_id
                    if identity is not None
                    else (row["active_user_id"] if row else None)
                )
                started_at = str(row["started_at"] or now_iso) if row else now_iso
                completed_at = row["completed_at"] if row else None

                conn.execute(
                    """
                    INSERT INTO installation_setup_state (
                        id, status, current_step, completed_steps,
                        selected_model_path, selected_backend, active_user_id,
                        last_error, started_at, updated_at, completed_at
                    )
                    VALUES (1, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        current_step = excluded.current_step,
                        completed_steps = excluded.completed_steps,
                        selected_model_path = excluded.selected_model_path,
                        selected_backend = excluded.selected_backend,
                        active_user_id = excluded.active_user_id,
                        last_error = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        new_status,
                        resolved_next,
                        json.dumps(existing_steps),
                        eff_model,
                        eff_backend,
                        eff_uid,
                        started_at,
                        now_iso,
                        completed_at,
                    ),
                )

                if identity is not None:
                    urow = conn.execute(
                        "SELECT * FROM user_onboarding_state WHERE user_id = ?",
                        (identity.user_id,),
                    ).fetchone()
                    u_steps: list[str] = (
                        json.loads(str(urow["completed_steps"] or "[]")) if urow else []
                    )
                    if safe_done not in u_steps:
                        u_steps.append(safe_done)
                    u_status = (
                        "completed"
                        if (urow is not None and urow["status"] == "completed")
                        else "in_progress"
                    )
                    u_model = (
                        selected_model_path
                        if selected_model_path is not None
                        else (urow["model_path"] if urow else eff_model)
                    )
                    u_backend = (
                        selected_backend
                        if selected_backend is not None
                        else (str(urow["ai_backend"] or "auto") if urow else eff_backend)
                    )
                    u_voice = (
                        int(bool(voice_configured))
                        if voice_configured is not None
                        else (int(urow["voice_configured"]) if urow else 0)
                    )
                    u_perm = (
                        int(bool(permissions_reviewed))
                        if permissions_reviewed is not None
                        else (int(urow["permissions_reviewed"]) if urow else 0)
                    )
                    u_health = (
                        int(bool(health_checked))
                        if health_checked is not None
                        else (int(urow["health_checked"]) if urow else 0)
                    )
                    u_comp_at = urow["completed_at"] if urow else None
                    conn.execute(
                        """
                        INSERT INTO user_onboarding_state (
                            user_id, status, current_step, completed_steps,
                            model_path, ai_backend, voice_configured,
                            permissions_reviewed, health_checked, updated_at, completed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            status = excluded.status,
                            current_step = excluded.current_step,
                            completed_steps = excluded.completed_steps,
                            model_path = excluded.model_path,
                            ai_backend = excluded.ai_backend,
                            voice_configured = excluded.voice_configured,
                            permissions_reviewed = excluded.permissions_reviewed,
                            health_checked = excluded.health_checked,
                            updated_at = excluded.updated_at
                        """,
                        (
                            identity.user_id,
                            u_status,
                            resolved_next,
                            json.dumps(u_steps),
                            u_model,
                            u_backend,
                            u_voice,
                            u_perm,
                            u_health,
                            now_iso,
                            u_comp_at,
                        ),
                    )
        finally:
            conn.close()

        saved = self._read_installation_row()
        if saved is not None:
            self._write_checkpoint(saved)

    def mark_recovery_required(self, error_message: str) -> None:
        now_iso = _utc_now_iso()
        conn = self.database.connect()
        try:
            with conn:
                row = conn.execute(
                    "SELECT * FROM installation_setup_state WHERE id = 1"
                ).fetchone()
                curr_step = str(row["current_step"]) if row else "welcome"
                steps = str(row["completed_steps"]) if row else "[]"
                started = str(row["started_at"] or now_iso) if row else now_iso
                conn.execute(
                    """
                    INSERT INTO installation_setup_state (
                        id, status, current_step, completed_steps, last_error, started_at, updated_at
                    )
                    VALUES (1, 'recovery_required', ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = 'recovery_required',
                        last_error = excluded.last_error,
                        updated_at = excluded.updated_at
                    """,
                    (curr_step, steps, str(error_message)[:500], started, now_iso),
                )
        finally:
            conn.close()

        saved = self._read_installation_row()
        if saved is not None:
            self._write_checkpoint(saved)

    def mark_setup_completed(
        self,
        identity: AuthenticatedIdentity,
        *,
        selected_model_path: str | None = None,
        selected_backend: str = "auto",
    ) -> UserOnboardingRecord:
        """Validate DB schema and user workspace before persisting completion."""
        if not isinstance(identity, AuthenticatedIdentity):
            raise ValueError("AuthenticatedIdentity is required to complete onboarding.")
        workspace = Path(identity.workspace_dir).expanduser().resolve()
        if not workspace.is_dir() or not os.access(workspace, os.W_OK):
            raise RuntimeError(
                f"User workspace directory is not accessible: {workspace}"
            )

        conn = self.database.connect()
        now_iso = _utc_now_iso()
        try:
            if get_schema_version(conn) != CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    "Database schema version mismatch; cannot mark onboarding complete."
                )
            required_tables = {
                "users",
                "user_sessions",
                "permission_grants",
                "permission_audit_log",
                "installation_setup_state",
                "user_onboarding_state",
            }
            present = {
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing = required_tables - present
            if missing:
                raise RuntimeError(
                    f"Database missing required tables: {sorted(missing)}"
                )
            user_row = conn.execute(
                "SELECT is_active FROM users WHERE user_id = ?",
                (identity.user_id,),
            ).fetchone()
            if user_row is None or not bool(user_row["is_active"]):
                raise RuntimeError("Active user account record not found in database.")

            with conn:
                prev_inst = conn.execute(
                    "SELECT * FROM installation_setup_state WHERE id = 1"
                ).fetchone()
                eff_model = (
                    selected_model_path
                    if selected_model_path is not None
                    else (prev_inst["selected_model_path"] if prev_inst else None)
                )
                eff_backend = (
                    selected_backend
                    or (str(prev_inst["selected_backend"]) if prev_inst else "auto")
                    or "auto"
                )
                all_steps = json.dumps(list(ONBOARDING_STEPS))
                started = str(prev_inst["started_at"] or now_iso) if prev_inst else now_iso
                conn.execute(
                    """
                    INSERT INTO installation_setup_state (
                        id, status, current_step, completed_steps,
                        selected_model_path, selected_backend, active_user_id,
                        last_error, started_at, updated_at, completed_at
                    )
                    VALUES (1, 'completed', 'complete', ?, ?, ?, ?, NULL, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = 'completed',
                        current_step = 'complete',
                        completed_steps = excluded.completed_steps,
                        selected_model_path = COALESCE(excluded.selected_model_path, installation_setup_state.selected_model_path),
                        selected_backend = excluded.selected_backend,
                        active_user_id = excluded.active_user_id,
                        last_error = NULL,
                        updated_at = excluded.updated_at,
                        completed_at = excluded.completed_at
                    """,
                    (
                        all_steps,
                        eff_model,
                        eff_backend,
                        identity.user_id,
                        started,
                        now_iso,
                        now_iso,
                    ),
                )

                prev_user = conn.execute(
                    "SELECT * FROM user_onboarding_state WHERE user_id = ?",
                    (identity.user_id,),
                ).fetchone()
                u_model = (
                    selected_model_path
                    if selected_model_path is not None
                    else (prev_user["model_path"] if prev_user else eff_model)
                )
                u_voice = int(prev_user["voice_configured"]) if prev_user else 0
                u_perm = int(prev_user["permissions_reviewed"]) if prev_user else 0
                conn.execute(
                    """
                    INSERT INTO user_onboarding_state (
                        user_id, status, current_step, completed_steps,
                        model_path, ai_backend, voice_configured,
                        permissions_reviewed, health_checked, updated_at, completed_at
                    )
                    VALUES (?, 'completed', 'complete', ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        status = 'completed',
                        current_step = 'complete',
                        completed_steps = excluded.completed_steps,
                        model_path = COALESCE(excluded.model_path, user_onboarding_state.model_path),
                        ai_backend = excluded.ai_backend,
                        voice_configured = excluded.voice_configured,
                        permissions_reviewed = excluded.permissions_reviewed,
                        health_checked = 1,
                        updated_at = excluded.updated_at,
                        completed_at = excluded.completed_at
                    """,
                    (
                        identity.user_id,
                        all_steps,
                        u_model,
                        eff_backend,
                        u_voice,
                        u_perm,
                        now_iso,
                        now_iso,
                    ),
                )
        finally:
            conn.close()

        saved = self._read_installation_row()
        if saved is not None:
            self._write_checkpoint(saved)
        rec = self.get_user_state(identity.user_id)
        assert rec is not None
        return rec

    def get_user_state(self, user_id: str) -> UserOnboardingRecord | None:
        conn = self.database.connect()
        try:
            row = conn.execute(
                "SELECT * FROM user_onboarding_state WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                steps = tuple(
                    str(s)
                    for s in json.loads(str(row["completed_steps"] or "[]"))
                    if s in ONBOARDING_STEPS
                )
            except Exception:
                steps = ()
            return UserOnboardingRecord(
                user_id=str(row["user_id"]),
                status=str(row["status"]),
                current_step=str(row["current_step"]),
                completed_steps=steps,
                model_path=str(row["model_path"]) if row["model_path"] else None,
                ai_backend=str(row["ai_backend"] or "auto"),
                voice_configured=bool(row["voice_configured"]),
                permissions_reviewed=bool(row["permissions_reviewed"]),
                health_checked=bool(row["health_checked"]),
                updated_at=str(row["updated_at"]),
                completed_at=str(row["completed_at"]) if row["completed_at"] else None,
            )
        finally:
            conn.close()

    def get_effective_model_config(
        self,
        user_id: str | None = None,
    ) -> tuple[str | None, str]:
        conn = self.database.connect()
        try:
            if user_id:
                urow = conn.execute(
                    "SELECT model_path, ai_backend FROM user_onboarding_state WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if urow is not None and (urow["model_path"] or urow["ai_backend"]):
                    return (
                        str(urow["model_path"]) if urow["model_path"] else None,
                        str(urow["ai_backend"] or "auto"),
                    )
            irow = conn.execute(
                "SELECT selected_model_path, selected_backend FROM installation_setup_state WHERE id = 1"
            ).fetchone()
            if irow is not None:
                return (
                    str(irow["selected_model_path"]) if irow["selected_model_path"] else None,
                    str(irow["selected_backend"] or "auto"),
                )
            return None, "auto"
        finally:
            conn.close()

    def _read_installation_row(self) -> dict[str, Any] | None:
        conn = self.database.connect()
        try:
            row = conn.execute(
                "SELECT * FROM installation_setup_state WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            return {
                "status": str(row["status"]),
                "current_step": str(row["current_step"]),
                "completed_steps": json.loads(str(row["completed_steps"] or "[]")),
                "selected_model_path": row["selected_model_path"],
                "selected_backend": str(row["selected_backend"] or "auto"),
                "active_user_id": row["active_user_id"],
                "last_error": row["last_error"],
                "started_at": row["started_at"],
                "updated_at": str(row["updated_at"]),
                "completed_at": row["completed_at"],
            }
        finally:
            conn.close()
