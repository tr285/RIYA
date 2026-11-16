"""RIYA interactive entry point supporting multi-user local authentication, setup, CLI, and desktop UI."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import shlex
import sys

from core.agent.assistant import RIYAAssistant
from core.command_dispatcher import CLICommandDispatcher
from core.config.settings import APP_NAME, APP_VERSION, DEVELOPER_NAME
from core.identity import (
    AuthenticatedIdentity,
    IdentityError,
    IdentityManager,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from core.memory.database import DATABASE_PATH, MemoryDatabase
from core.memory.manager import MemoryManager
from core.onboarding.manager import OnboardingCoordinator
from core.onboarding.state import InstallState
from core.security.permissions import (
    PERMISSION_SPECS,
    PermissionErrorBase,
    PermissionLifetime,
)


def _prompt_create_user(identity_manager: IdentityManager) -> AuthenticatedIdentity:
    while True:
        username = input("Choose username (2-32 chars): ").strip()
        display_name = input("Display name (optional): ").strip() or username
        method = (
            input("Auth method [local_password/local_pin/local_unlock] (default: local_password): ")
            .strip()
            .lower()
            or "local_password"
        )
        password: str | None = None
        if method in {"local_password", "local_pin"}:
            secret = getpass.getpass(f"Enter {method.replace('local_', '')}: ")
            confirm = getpass.getpass("Confirm credential: ")
            if secret != confirm:
                print("Credentials did not match. Please try again.")
                continue
            password = secret
        try:
            profile = identity_manager.create_user(
                username,
                password=password,
                display_name=display_name,
                auth_method=method,
            )
            return identity_manager.authenticate(
                profile.username,
                password=password,
                auth_method=method,
            )
        except (ValueError, UserAlreadyExistsError, IdentityError) as exc:
            print(f"Account creation error: {exc}")


def _authenticate_cli_user(
    identity_manager: IdentityManager,
    preferred_username: str | None = None,
) -> AuthenticatedIdentity:
    users = identity_manager.list_users()
    if not users:
        print("No local users found. Create your first RIYA user profile:")
        return _prompt_create_user(identity_manager)

    if preferred_username:
        profile = identity_manager.get_profile_by_username(preferred_username)
        password = (
            None
            if profile.auth_method == "local_unlock"
            else getpass.getpass(f"Password/PIN for {profile.username}: ")
        )
        return identity_manager.authenticate(
            profile.username,
            password=password,
            auth_method=profile.auth_method,
        )

    print("Available RIYA accounts:")
    for idx, profile in enumerate(users, start=1):
        print(f"  {idx}. {profile.display_name} (@{profile.username}, method={profile.auth_method})")

    while True:
        choice = input("Enter username (or 'new' to create an account): ").strip()
        if choice.lower() == "new":
            return _prompt_create_user(identity_manager)
        try:
            profile = identity_manager.get_profile_by_username(choice)
        except (ValueError, UserNotFoundError) as exc:
            print(f"Account error: {exc}")
            continue

        password = (
            None
            if profile.auth_method == "local_unlock"
            else getpass.getpass(f"Password/PIN for {profile.username}: ")
        )
        try:
            return identity_manager.authenticate(
                profile.username,
                password=password,
                auth_method=profile.auth_method,
            )
        except IdentityError as exc:
            print(f"Authentication failed: {exc}")


def _print_diagnostics_report(report) -> None:
    print("=" * 60)
    print(f"{APP_NAME} System Readiness & Health Check ({report.checked_at})")
    print("=" * 60)
    for chk in report.checks:
        print(f"  [{chk.status.value.upper():14s}] {chk.name} ({chk.category}): {chk.summary}")
        print(f"      {chk.detail}")
        if chk.recovery_action:
            print(f"      Recovery: {chk.recovery_action}")
    if report.degraded_capabilities:
        print(f"  Degraded optional capabilities: {', '.join(report.degraded_capabilities)}")
    print(
        f"Summary: mandatory_passed={report.mandatory_passed} | can_enter_app={report.can_enter_app}"
    )


def _run_cli_setup(coordinator: OnboardingCoordinator) -> AuthenticatedIdentity | None:
    welcome = coordinator.welcome_info()
    det = welcome.detection
    print("=" * 60)
    print(f"{welcome.title} — First-Time Setup & Onboarding")
    print(welcome.tagline)
    print("=" * 60)
    print(welcome.privacy_details)
    print(f"\nDetected state: {det.install_state.value} — {det.message}")

    if det.checkpoint_corrupted:
        ans = input("Corrupted onboarding checkpoint detected. Back up & repair it? [Y/n]: ").strip().lower()
        if ans in {"", "y", "yes"}:
            backup = coordinator.recover_corrupted_checkpoint()
            print(f"Checkpoint backed up to: {backup}")
        else:
            print("Setup paused without modifying files.")
            return None

    _, identity_manager, _, _ = coordinator.initialize_services()
    coordinator.start_or_resume_setup(step=det.resume_step if det.can_resume else "welcome")

    # Account step
    print("\n--- Step 1: Local Account Setup ---")
    if coordinator.active_identity is None:
        identity = _authenticate_cli_user(identity_manager)
        coordinator.active_identity = identity
    else:
        identity = coordinator.active_identity
        print(f"Using active account: {identity.display_name} (@{identity.username})")

    det_after = coordinator.detect_initial_state()
    if det_after.legacy_memories_count > 0:
        claim_ans = (
            input(
                f"Found {det_after.legacy_memories_count} preserved v0.4 memories. "
                "Import them into this profile? [y/N]: "
            )
            .strip()
            .lower()
        )
        if claim_ans in {"y", "yes"}:
            db, _, _, _ = coordinator.initialize_services()
            claimed = MemoryManager(db, identity_manager=identity_manager).claim_legacy_memories(
                identity,
                identity_manager=identity_manager,
            )
            print(f"Imported {claimed} preserved v0.4 memories into @{identity.username}.")

    # AI Engine step
    print("\n--- Step 2: Local AI Engine Setup (Offline GGUF) ---")
    ai_status = coordinator.inspect_ai()
    ram_gb = ai_status.total_ram_bytes / float(1024**3) if ai_status.total_ram_bytes else 0.0
    disk_gb = ai_status.free_disk_bytes / float(1024**3) if ai_status.free_disk_bytes else 0.0
    print(
        f"Hardware: {ai_status.architecture} | RAM: {ram_gb:.1f} GB | Free Disk: {disk_gb:.1f} GB | "
        f"Native llama.cpp: {'Ready' if ai_status.native_library_loadable else 'Unavailable'}"
    )
    if ai_status.discovered_models:
        print("Discovered local GGUF models:")
        for idx, m in enumerate(ai_status.discovered_models, start=1):
            state_lbl = "valid" if m.valid_gguf else f"invalid: {m.error}"
            print(f"  {idx}. {m.name} ({m.size_gb:.2f} GB, {state_lbl}) — {m.path}")
        choice = input(
            "Select model number, enter full .gguf path, or type 'skip' for Limited Mode [1]: "
        ).strip()
        if choice.lower() == "skip":
            coordinator.continue_without_ai()
        else:
            chosen_path: str | None = None
            if not choice:
                chosen_path = ai_status.discovered_models[0].path
            elif choice.isdigit() and 1 <= int(choice) <= len(ai_status.discovered_models):
                chosen_path = ai_status.discovered_models[int(choice) - 1].path
            else:
                chosen_path = choice
            backend = input("Backend [auto/metal/cpu] (default auto): ").strip().lower() or "auto"
            print("Verifying model with real offline inference...")
            ver = coordinator.configure_and_verify_ai(model_path=chosen_path, backend=backend)
            if ver.success:
                print(f"AI verified ({ver.backend_used}): {ver.output_preview[:120]}")
            else:
                print(f"AI verification failed: {ver.error_message}")
                cont = input("Continue in Limited Mode (deterministic tools + memory still work)? [Y/n]: ").strip().lower()
                if cont in {"", "y", "yes"}:
                    coordinator.continue_without_ai()
    else:
        print("No local .gguf models discovered in ./models.")
        custom = input("Enter path to a local .gguf file, or type 'skip' / Enter for Limited Mode: ").strip()
        if custom and custom.lower() != "skip":
            ver = coordinator.configure_and_verify_ai(model_path=custom, backend="auto")
            if ver.success:
                print(f"AI verified ({ver.backend_used}): {ver.output_preview[:120]}")
            else:
                print(f"AI verification failed: {ver.error_message}")
                coordinator.continue_without_ai()
        else:
            coordinator.continue_without_ai()

    # Voice setup step (permission-gated)
    print("\n--- Step 3: Voice & Audio Setup ---")
    cfg, cfg_err = coordinator.load_current_voice_config()
    if cfg_err:
        print(f"Note: {cfg_err}")
    vst = coordinator.inspect_voice(config=cfg)
    print(f"Input devices: {len(vst.input_devices)} | Output devices: {len(vst.output_devices)}")
    print(f"STT ({vst.stt_model}): {vst.stt_status_detail} | VAD: {vst.vad_backend}")
    print(f"Languages: {', '.join(lbl for _, lbl in vst.language_options)}")
    if input("Save default voice configuration now? [y/N]: ").strip().lower() in {"y", "yes"}:
        try:
            coordinator.save_voice_setup(cfg)
            print("Voice settings saved.")
        except PermissionError as exc:
            print(f"Voice config requires explicit permission ({exc}). Skipped without auto-granting.")
            coordinator.skip_voice_setup()
    else:
        coordinator.skip_voice_setup()

    # Permissions onboarding step (never auto-grants)
    print("\n--- Step 4: Permissions & Privacy Model ---")
    print(coordinator.permission_guide_text())
    input("Press Enter after reviewing RIYA's permission rules (no permissions are auto-granted)...")
    coordinator.acknowledge_permissions_reviewed()

    # Diagnostics & completion
    print("\n--- Step 5: System Readiness Check ---")
    report = coordinator.run_diagnostics()
    _print_diagnostics_report(report)
    if report.mandatory_passed:
        coordinator.complete_onboarding()
        print("Setup completed successfully.")
    else:
        print("Mandatory checks must pass before marking onboarding complete.")
    return identity


def main_ui(argv: list[str] | None = None) -> None:
    """Launch RIYA's desktop UI directly (used by `riya-ui` and macOS app launcher)."""
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Desktop UI")
    parser.add_argument("--version", "-v", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--database", type=str, default=None, help="Optional SQLite database path")
    args = parser.parse_args(argv)

    from core.onboarding.ui import launch_desktop_app

    db_path = Path(args.database).expanduser().resolve() if args.database else DATABASE_PATH
    coordinator = OnboardingCoordinator(database_path=db_path)
    launch_desktop_app(coordinator=coordinator)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Offline Assistant")
    parser.add_argument("--version", "-v", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--voice", action="store_true", help="Start directly in voice conversation mode")
    parser.add_argument("--ui", action="store_true", help="Launch desktop conversation UI")
    parser.add_argument("--setup", action="store_true", help="Run interactive first-time setup wizard")
    parser.add_argument("--diagnostics", action="store_true", help="Run system readiness diagnostics and exit")
    parser.add_argument("--user", type=str, default=None, help="Authenticate as a specific local username")
    parser.add_argument("--database", type=str, default=None, help="Optional SQLite database path")
    args = parser.parse_args(argv)

    db_path = Path(args.database).expanduser().resolve() if args.database else DATABASE_PATH
    coordinator = OnboardingCoordinator(database_path=db_path)

    if args.ui:
        from core.onboarding.ui import launch_desktop_app

        launch_desktop_app(coordinator=coordinator)
        return

    if args.diagnostics:
        report = coordinator.run_diagnostics()
        _print_diagnostics_report(report)
        return

    database, identity_manager, permission_manager, state_store = coordinator.initialize_services()

    if args.setup:
        try:
            identity = _run_cli_setup(coordinator)
        except (EOFError, KeyboardInterrupt):
            print("\nSetup cancelled.")
            return
        if identity is None:
            return
    else:
        det = coordinator.detect_initial_state()
        if det.checkpoint_corrupted:
            print(f"[Notice: {det.checkpoint_error} Run '--setup' or '--diagnostics' to inspect recovery.]")
        try:
            identity = _authenticate_cli_user(identity_manager, preferred_username=args.user)
        except (EOFError, KeyboardInterrupt):
            print("\nAuthentication cancelled.")
            return
        coordinator.active_identity = identity

    model_path, ai_backend = state_store.get_effective_model_config(identity.user_id)
    assistant = RIYAAssistant(
        identity_manager=identity_manager,
        default_identity=identity,
        require_identity=True,
        permission_manager=permission_manager,
        model_path=model_path,
        ai_backend=ai_backend,
        allow_degraded_ai=True,
    )

    print("=" * 50)
    print(f"{APP_NAME} {APP_VERSION}")
    print(f"Developer: {DEVELOPER_NAME}")
    print(f"Signed in as: {identity.display_name} (@{identity.username})")
    print("=" * 50)
    print(
        "RIYA is ready. Commands: /voice [ptt|continuous], /text, /pause, /resume, /interrupt, /status, /ai-metrics, /summary, "
        "/feedback, /diagnostics, /onboarding, /memories, /settings, /permissions, "
        "/whoami, /create-user, /switch-user, /logout, or 'exit'.\n"
    )

    orchestrator = None

    def _get_orchestrator():
        nonlocal orchestrator
        current = assistant.current_identity
        if current is None:
            raise IdentityError("Please sign in first with /switch-user.")
        if orchestrator is None:
            from core.voice.config import VoiceConfig
            from core.voice.orchestrator import VoiceOrchestrator
            from core.voice.session import VoiceSession

            cfg = VoiceConfig.load_for_user(identity_manager.users_root, current.user_id)
            session = VoiceSession(
                cfg,
                user_id=current.user_id,
                auth_session_id=current.session_id,
            )
            orchestrator = VoiceOrchestrator(
                assistant,
                config=cfg,
                session=session,
                identity_manager=identity_manager,
                permission_manager=assistant.permission_manager,
                identity=current,
            )
        return orchestrator

    dispatcher = CLICommandDispatcher(
        assistant=assistant,
        coordinator=coordinator,
        identity_manager=identity_manager,
        get_orchestrator=_get_orchestrator,
        confirm_fn=_confirm_cli_prompt,
        prompt_create_user_fn=_prompt_create_user,
        authenticate_user_fn=_authenticate_cli_user,
    )

    try:
        if args.voice:
            orch = _get_orchestrator()
            orch.switch_mode("voice")
            print("[Voice mode ON - Microphone active. Type /text or /interrupt anytime.]")

        while True:
            try:
                user = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user:
                continue

            res = dispatcher.dispatch(user)
            if res.should_exit:
                break
    finally:
        orch = dispatcher.get_orchestrator_safe()
        if orch is not None:
            orch.close()
        assistant.logout()
        assistant.close()


def _confirm_cli_prompt(prompt_text: str) -> bool:
    ans = input(f"{prompt_text} Type 'yes' to confirm: ").strip().lower()
    return ans == "yes"


def _handle_task_cli(assistant: RIYAAssistant, raw_command: str) -> None:
    current = assistant.current_identity
    if current is None or not hasattr(assistant, "productivity"):
        print("[Productivity tools unavailable: sign in first with /switch-user.]")
        return
    tokens = raw_command.strip().split(maxsplit=2)
    if len(tokens) == 1 or tokens[1].lower() in {"list", "all", "pending"}:
        status = None if len(tokens) > 1 and tokens[1].lower() == "all" else "pending"
        tasks = assistant.productivity.list_tasks(current, status=status)
        if not tasks:
            print(f"[No {status or ''} tasks found.]")
            return
        print(f"[Tasks for @{current.username} ({len(tasks)})]")
        for t in tasks:
            due = f" (due: {t.due_date})" if t.due_date else ""
            status_icon = "✅" if t.status == "completed" else ("⏳" if getattr(t, "is_overdue", False) else "📋")
            print(f"  {status_icon} [{t.priority.upper()}] {t.title} [id: {t.task_id}]{due}")
        return

    sub = tokens[1].lower()
    if sub == "add" and len(tokens) > 2:
        title = tokens[2].strip()
        task = assistant.productivity.create_task(current, title=title)
        print(f"[Created task: '{task.title}' (id: {task.task_id})]")
        return
    elif sub == "done" and len(tokens) > 2:
        task_id = tokens[2].strip()
        updated = assistant.productivity.complete_task(current, task_id=task_id)
        if updated:
            print(f"[Marked completed: '{updated.title}']")
        else:
            print(f"[Task '{task_id}' not found.]")
        return
    print("[Usage: /tasks | /task add <title> | /task done <task_id>]")


def _handle_reminder_cli(assistant: RIYAAssistant, raw_command: str) -> None:
    current = assistant.current_identity
    if current is None or not hasattr(assistant, "productivity"):
        print("[Productivity tools unavailable: sign in first with /switch-user.]")
        return
    tokens = raw_command.strip().split()
    if len(tokens) == 1 or tokens[1].lower() == "list":
        reminders = assistant.productivity.list_reminders(current, status="pending")
        if not reminders:
            print("[No pending reminders.]")
            return
        print(f"[Active Reminders for @{current.username} ({len(reminders)})]")
        for r in reminders:
            rec = f" (repeat: {r.recurring_rule})" if r.recurring_rule else ""
            print(f"  ⏰ {r.title} at {r.trigger_at} [id: {r.reminder_id}]{rec}")
        return
    print("[Usage: /reminders | /remind list]")


def _handle_calendar_cli(assistant: RIYAAssistant) -> None:
    current = assistant.current_identity
    if current is None or not hasattr(assistant, "productivity"):
        print("[Productivity tools unavailable: sign in first with /switch-user.]")
        return
    events = assistant.productivity.get_daily_schedule(current)
    if not events:
        print("[No calendar events scheduled for today.]")
        return
    print(f"[Today's Schedule for @{current.username} ({len(events)})]")
    for e in events:
        loc = f" at {e.location}" if e.location else ""
        print(f"  📅 {e.start_time[11:16]} - {e.end_time[11:16]}: {e.title}{loc} [id: {e.event_id}]")


def _handle_notes_cli(assistant: RIYAAssistant, raw_command: str) -> None:
    current = assistant.current_identity
    if current is None or not hasattr(assistant, "productivity"):
        print("[Productivity tools unavailable: sign in first with /switch-user.]")
        return
    parts = raw_command.strip().split(maxsplit=2)
    if len(parts) >= 3 and parts[1].lower() == "search":
        query = parts[2].strip()
        notes = assistant.productivity.search_notes(current, query=query)
        if not notes:
            print(f"[No notes matching '{query}'.]")
            return
        print(f"[Notes matching '{query}' ({len(notes)})]")
        for n in notes:
            tags = f" #{' #'.join(n.tags)}" if n.tags else ""
            print(f"  📝 {n.title} [{n.category}]{tags} [id: {n.note_id}]")
        return
    notes = assistant.productivity.list_notes(current)
    if not notes:
        print("[No notes saved yet.]")
        return
    print(f"[Saved Notes for @{current.username} ({len(notes)})]")
    for n in notes:
        pin = " 📌" if n.pinned else ""
        tags = f" #{' #'.join(n.tags)}" if n.tags else ""
        print(f"  📝 {n.title}{pin} [{n.category}]{tags} [id: {n.note_id}]")


def _handle_plan_cli(assistant: RIYAAssistant) -> None:
    current = assistant.current_identity
    if current is None or not hasattr(assistant, "productivity"):
        print("[Productivity tools unavailable: sign in first with /switch-user.]")
        return
    plan = assistant.productivity.get_daily_plan(current)
    print(f"[Daily Plan — {plan.date}]")
    if plan.events_today:
        print("  Calendar Events:")
        for ev in plan.events_today:
            print(f"    - {ev.start_time[11:16]}-{ev.end_time[11:16]}: {ev.title}")
    if plan.tasks_due_today:
        print("  Tasks Due Today:")
        for t in plan.tasks_due_today:
            print(f"    - [{t.priority.upper()}] {t.title}")
    if plan.overdue_tasks:
        print(f"  ⚠️ Overdue Tasks ({len(plan.overdue_tasks)}):")
        for t in plan.overdue_tasks:
            print(f"    - [{t.priority.upper()}] {t.title} (due: {t.due_date})")
    if plan.reminders_today:
        print("  Active Reminders:")
        for r in plan.reminders_today:
            print(f"    - {r.title} at {r.trigger_at[11:16]}")


def _handle_permissions_cli(assistant: RIYAAssistant, raw_command: str) -> None:
    pm = getattr(assistant, "permission_manager", None)
    current = assistant.current_identity
    if pm is None or current is None:
        print("[Permissions unavailable: sign in first with /switch-user.]")
        return

    try:
        tokens = shlex.split(raw_command)
    except ValueError as exc:
        print(f"[Invalid command syntax: {exc}]")
        return

    sub = tokens[1].lower() if len(tokens) > 1 else "list"
    args = tokens[2:]

    try:
        if sub == "list":
            cat_filter = args[0].lower() if args else None
            grants = pm.list_permissions(current, category=cat_filter)
            by_key: dict[str, list] = {}
            for g in grants:
                by_key.setdefault(g.permission_key, []).append(g)

            print(f"[Permissions for @{current.username}]")
            for spec in pm.list_catalog():
                if cat_filter and spec.category != cat_filter:
                    continue
                active = by_key.get(spec.permission_key, [])
                if not active:
                    default_lbl = (
                        "NOT_CONFIGURED (default-deny)"
                        if (pm.strict_default_deny or spec.protected_by_default)
                        else ("NOT_CONFIGURED (confirm)" if spec.sensitive else "NOT_CONFIGURED (sandbox-allow)")
                    )
                    print(f"  - {spec.permission_key} [{spec.category}]: {default_lbl} — {spec.description}")
                else:
                    for g in active:
                        exp = f" expires={g.expires_at}" if g.expires_at else ""
                        print(
                            f"  - {spec.permission_key} [{spec.category}]: {g.decision.value.upper()} "
                            f"lifetime={g.lifetime.value} scope={g.resource_scope}{exp}"
                        )
            return

        if sub == "audit":
            limit = int(args[0]) if args else 25
            events = pm.audit_logger.list_audit_events(current, limit=limit)
            print(f"[Recent Permission Audit Events ({len(events)})]")
            for ev in events:
                print(
                    f"  #{ev.audit_id} [{ev.created_at}] {ev.permission_category}/{ev.permission_key} "
                    f"({ev.resource_or_tool}) -> {ev.decision} ({ev.actor}): {ev.reason}"
                )
            return

        if sub == "reset":
            category = args[0].lower() if args else None
            target_desc = f"category '{category}'" if category else "ALL permissions"
            if not _confirm_cli_prompt(f"Reset {target_desc} for @{current.username}?"):
                print("[Permission reset cancelled.]")
                return
            count = pm.reset_permissions(current, category=category, actor="user_cli")
            print(f"[Reset {count} permission rule(s).]")
            return

        if sub in {"grant", "deny", "revoke"}:
            if not args:
                print(f"[Usage: /permissions {sub} <permission_key> [--scope S] [--lifetime L] [--ttl SEC] [--mode allow|confirm]]")
                return
            perm_key = args[0]
            scope = "*"
            lifetime = "persistent" if sub == "deny" else "session"
            ttl_seconds: float | None = None
            mode = "allow"

            idx = 1
            while idx < len(args):
                flag = args[idx]
                if flag == "--scope" and idx + 1 < len(args):
                    scope = args[idx + 1]
                    idx += 2
                elif flag == "--lifetime" and idx + 1 < len(args):
                    lifetime = args[idx + 1].lower()
                    idx += 2
                elif flag == "--ttl" and idx + 1 < len(args):
                    ttl_seconds = float(args[idx + 1])
                    idx += 2
                elif flag == "--mode" and idx + 1 < len(args):
                    mode = args[idx + 1].lower()
                    idx += 2
                else:
                    print(f"[Unknown or incomplete option: {flag}]")
                    return

            if sub == "revoke":
                count = pm.revoke_permission(
                    current,
                    perm_key,
                    resource_scope=None if scope == "*" else scope,
                    actor="user_cli",
                )
                print(f"[Revoked {count} grant(s) for {perm_key}.]")
                return

            if sub == "deny":
                grant = pm.deny_permission(
                    current,
                    perm_key,
                    resource_scope=scope,
                    lifetime=lifetime,
                    ttl_seconds=ttl_seconds,
                    actor="user_cli",
                )
                print(f"[Set DENY rule on {grant.permission_key} scope={grant.resource_scope} lifetime={grant.lifetime.value}.]")
                return

            spec = PERMISSION_SPECS.get(perm_key)
            if spec is None:
                print(f"[Unknown permission key: {perm_key}]")
                return

            explicit_ok = True
            if lifetime == PermissionLifetime.PERSISTENT.value or spec.sensitive:
                explicit_ok = _confirm_cli_prompt(
                    f"Grant {mode.upper()} on {perm_key} (lifetime={lifetime}, scope={scope}) for @{current.username}?"
                )
                if not explicit_ok:
                    print("[Permission grant cancelled.]")
                    return

            grant = pm.grant_permission(
                current,
                perm_key,
                decision=mode,
                lifetime=lifetime,
                resource_scope=scope,
                ttl_seconds=ttl_seconds,
                actor="user_cli",
                explicit_user_approval=explicit_ok,
            )
            print(
                f"[Granted {grant.decision.value.upper()} on {grant.permission_key} "
                f"lifetime={grant.lifetime.value} scope={grant.resource_scope}.]"
            )
            return

        print(
            "[Commands: /permissions [list [cat]] | /permissions grant <key> [--scope S] "
            "[--lifetime one_time|session|time_limited|persistent] [--ttl SEC] [--mode allow|confirm] | "
            "/permissions deny <key> [--scope S] | /permissions revoke <key> | "
            "/permissions reset [cat] | /permissions audit [limit]]"
        )
    except (ValueError, PermissionErrorBase, IdentityError) as exc:
        print(f"[Permission error: {exc}]")


if __name__ == "__main__":
    main(sys.argv[1:])
