"""Centralized CLI Command Dispatcher for RIYA.

Routes slash commands and system control actions BEFORE any AI conversation pipeline
processing, ensuring commands are never evaluated by Qwen3 as ordinary user prompts.
"""

from __future__ import annotations

import getpass
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable

from core.identity import (
    AuthenticatedIdentity,
    IdentityError,
    IdentityManager,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from core.security.permissions import (
    PERMISSION_SPECS,
    PermissionErrorBase,
    PermissionLifetime,
)
from core.voice.audio_input import list_input_devices
from core.voice.errors import (
    MicrophonePermissionError,
    MicrophoneUnavailableError,
    VoiceError,
    VoicePermissionError,
)


@dataclass
class DispatchResult:
    """Outcome of evaluating a user input through the CLI command dispatcher."""

    handled: bool
    is_command: bool
    should_exit: bool = False
    output: str = ""
    error: str | None = None


class CLICommandDispatcher:
    """Dispatches CLI input to voice, productivity, system, or AI handlers.

    Enforces strict command routing before ordinary AI message processing:
    - Slash commands (e.g. /voice, /pause, /resume, /interrupt, /text, /status)
      are executed by their respective subsystem handlers.
    - Commands validate arguments and are case-insensitive.
    - Unrecognized slash commands return helpful errors and NEVER reach Qwen3.
    - Only non-command natural language text is forwarded to AI conversation.
    """

    AVAILABLE_COMMANDS_HELP = (
        "Commands: /voice [ptt|continuous], /text, /pause, /resume, /interrupt, /status, "
        "/permissions, /tasks, /remind, /notes, /plan, /memories, /diagnostics, "
        "/ai-metrics, /summary, /feedback, /whoami, /create-user, /switch-user, /logout, or 'exit'."
    )

    def __init__(
        self,
        assistant: Any,
        coordinator: Any | None = None,
        identity_manager: IdentityManager | None = None,
        get_orchestrator: Callable[[], Any] | None = None,
        output_fn: Callable[[str], None] = print,
        input_fn: Callable[[], str] | None = None,
        confirm_fn: Callable[[str], bool] | None = None,
        prompt_create_user_fn: Callable[[IdentityManager], AuthenticatedIdentity] | None = None,
        authenticate_user_fn: Callable[..., AuthenticatedIdentity] | None = None,
    ):
        self.assistant = assistant
        self.coordinator = coordinator
        self.identity_manager = identity_manager
        self._get_orchestrator_fn = get_orchestrator
        self._orchestrator: Any | None = None
        self.output_fn = output_fn
        self._input_fn_override = input_fn
        self.confirm_fn = confirm_fn or self._default_confirm
        self.prompt_create_user_fn = prompt_create_user_fn
        self.authenticate_user_fn = authenticate_user_fn

    @property
    def input_fn(self) -> Callable[[], str]:
        if self._input_fn_override is not None:
            return self._input_fn_override
        import builtins
        return builtins.input

    @input_fn.setter
    def input_fn(self, fn: Callable[[], str]) -> None:
        self._input_fn_override = fn

    def _default_confirm(self, prompt_text: str) -> bool:
        try:
            self.output_fn(f"{prompt_text} Type 'yes' to confirm: ")
            ans = self.input_fn().strip().lower()
            return ans == "yes"
        except (EOFError, KeyboardInterrupt):
            return False

    def get_orchestrator(self) -> Any:
        """Resolve or initialize VoiceOrchestrator for the active user."""
        if self._orchestrator is not None:
            return self._orchestrator
        if self._get_orchestrator_fn is not None:
            self._orchestrator = self._get_orchestrator_fn()
            return self._orchestrator
        raise VoiceError("Voice orchestrator factory is not configured.")

    def get_orchestrator_safe(self) -> Any | None:
        """Retrieve VoiceOrchestrator if available without raising."""
        try:
            return self.get_orchestrator()
        except Exception:
            return None

    def is_command(self, raw_text: str) -> bool:
        """Check whether input is a slash command or exit command."""
        cleaned = raw_text.strip()
        if not cleaned:
            return False
        if cleaned.startswith("/"):
            return True
        if cleaned.lower() in {
            "exit",
            "quit",
            "stop",
            "activate voice",
            "turn on voice",
            "start voice",
            "voice mode",
            "enable voice",
            "switch to voice",
            "deactivate voice",
            "turn off voice",
            "stop voice",
            "disable voice",
            "text mode",
            "switch to text",
            "exit voice",
        }:
            return True
        return False

    def dispatch(self, raw_input: str) -> DispatchResult:
        """Route terminal input to appropriate command handler or AI pipeline."""
        user = raw_input.strip()
        if not user:
            return DispatchResult(handled=True, is_command=False)

        lower = user.lower()

        # Exit command
        if lower in {"exit", "quit", "stop"}:
            try:
                response = self.assistant.process(user)
            except Exception:
                response = "Goodbye."
            self.output_fn(f"RIYA: {response}")
            return DispatchResult(
                handled=True,
                is_command=True,
                should_exit=True,
                output=response,
            )

        # Natural language voice activation / deactivation shortcuts
        lower_user = user.lower().strip()
        if lower_user in {
            "activate voice",
            "turn on voice",
            "start voice",
            "voice mode",
            "enable voice",
            "switch to voice",
        }:
            return self._activate_continuous()

        if lower_user in {
            "deactivate voice",
            "turn off voice",
            "stop voice",
            "disable voice",
            "text mode",
            "switch to text",
            "exit voice",
        }:
            return self._handle_text_command([])

        # Slash commands MUST be handled as commands and NEVER forwarded to AI
        if user.startswith("/"):
            return self._dispatch_slash_command(user)

        # Ordinary natural-language conversation message
        orch = self.get_orchestrator_safe()
        if orch is not None and orch.mode == "voice":
            response = orch.send_text(user)
        else:
            response = self.assistant.process(user)

        self.output_fn(f"RIYA: {response}")
        return DispatchResult(handled=True, is_command=False, output=response)

    def _dispatch_slash_command(self, user: str) -> DispatchResult:
        """Parse and execute a validated slash command."""
        parts = user.split()
        cmd = parts[0].lower()
        args = parts[1:]

        # 1. Voice commands
        if cmd == "/voice":
            return self._handle_voice_command(args, user)

        # 2. Listening control
        if cmd == "/pause":
            return self._handle_pause_command(args)

        if cmd == "/resume":
            return self._handle_resume_command(args, user)

        if cmd == "/interrupt":
            return self._handle_interrupt_command(args)

        if cmd == "/text":
            return self._handle_text_command(args)

        # 3. Status & Diagnostics
        if cmd == "/status":
            return self._handle_status_command(args)

        if cmd == "/diagnostics":
            return self._handle_diagnostics_command(args)

        if cmd == "/ai-metrics":
            return self._handle_ai_metrics_command(args)

        if cmd == "/summary":
            return self._handle_summary_command(args)

        if cmd == "/feedback":
            return self._handle_feedback_command(args, user)

        if cmd == "/settings":
            return self._handle_settings_command(args)

        if cmd == "/onboarding":
            return self._handle_onboarding_command(args)

        # 4. Identity & Permissions
        if cmd == "/whoami":
            return self._handle_whoami_command(args)

        if cmd == "/create-user":
            return self._handle_create_user_command(args)

        if cmd == "/switch-user":
            return self._handle_switch_user_command(args)

        if cmd == "/logout":
            return self._handle_logout_command(args)

        if cmd == "/permissions":
            return self._handle_permissions_command(args, user)

        # 5. Productivity & Memory
        if cmd == "/memories":
            return self._handle_memories_command(args)

        if cmd in {"/tasks", "/task"}:
            return self._handle_task_command(cmd, args, user)

        if cmd in {"/remind", "/reminders"}:
            return self._handle_reminder_command(args, user)

        if cmd == "/calendar":
            return self._handle_calendar_command(args)

        if cmd in {"/notes", "/note"}:
            return self._handle_notes_command(args, user)

        if cmd == "/plan":
            return self._handle_plan_command(args)

        if cmd == "/cancel-task":
            return self._handle_cancel_task_command(args)

        if cmd in {"/skills", "/skill"}:
            return self._handle_skills_command(cmd, args, user)

        if cmd == "/help":
            self.output_fn(f"[RIYA {self.AVAILABLE_COMMANDS_HELP}]")
            return DispatchResult(handled=True, is_command=True, output=self.AVAILABLE_COMMANDS_HELP)

        # Unknown slash command: reject with useful error. NEVER send to Qwen3.
        err_msg = f"[Unknown command: '{cmd}'. {self.AVAILABLE_COMMANDS_HELP}]"
        self.output_fn(err_msg)
        return DispatchResult(handled=True, is_command=True, error=err_msg)

    # -----------------------------------------------------------------
    # VOICE COMMAND HANDLERS
    # -----------------------------------------------------------------

    def _handle_voice_command(self, args: list[str], raw_text: str) -> DispatchResult:
        """Route `/voice [ptt|continuous]` with argument validation."""
        if not args:
            msg = "[Usage: /voice [ptt|continuous]]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)

        mode_arg = args[0].strip().lower()

        if mode_arg in {"ptt", "push_to_talk"}:
            return self._activate_push_to_talk()

        if mode_arg in {"continuous", "cont"}:
            return self._activate_continuous()

        err = f"[Invalid voice mode: '{args[0]}'. Valid modes are: 'ptt' or 'continuous'.]"
        self.output_fn(err)
        return DispatchResult(handled=True, is_command=True, error=err)

    def _activate_push_to_talk(self) -> DispatchResult:
        """Activate push-to-talk session with permission check and real audio capture."""
        current = getattr(self.assistant, "current_identity", None)
        if current is None:
            msg = "[Please sign in first with /switch-user before activating voice mode.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)

        # 1. Check microphone permission via PermissionManager
        pm = getattr(self.assistant, "permission_manager", None)
        if pm is not None:
            eval_res = pm.evaluate(
                current,
                "voice.microphone",
                resource_or_target="voice:microphone",
                actor="voice",
            )
            if eval_res.blocked:
                msg = (
                    f"[Voice error: Permission 'voice.microphone' is not configured and blocked by default-deny. "
                    "Grant it with /permissions grant voice.microphone.]"
                )
                self.output_fn(msg)
                return DispatchResult(handled=True, is_command=True, error=msg)

        # 2. Check microphone device availability
        try:
            orch = self.get_orchestrator()
        except (IdentityError, PermissionError, VoiceError) as exc:
            user_msg = getattr(exc, "user_message", str(exc))
            err = f"[Voice error: {user_msg}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

        has_custom_mic = getattr(orch.session, "_mic_factory", None) is not None
        if not has_custom_mic:
            try:
                input_devs = list_input_devices()
            except Exception:
                input_devs = []
            if not input_devs:
                err = "[Voice error: No microphone input device is available.]"
                self.output_fn(err)
                return DispatchResult(handled=True, is_command=True, error=err)

        # 3. Start real audio capture / PTT recording
        try:
            orch.start_push_to_talk(background_workers=True)
        except (VoicePermissionError, PermissionError) as exc:
            user_msg = getattr(exc, "user_message", str(exc))
            err = f"[Voice error: {user_msg}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)
        except Exception as exc:
            user_msg = getattr(exc, "user_message", str(exc))
            err = f"[Voice error: {user_msg}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

        self.output_fn(
            f"[Push-to-Talk active: Microphone listening ({orch.conversation_mode}) - "
            "Speak now, then press Enter to stop and process (or type /cancel to abort):]"
        )

        # 4. Wait for user to finish speaking
        try:
            user_action = self.input_fn()
        except (EOFError, KeyboardInterrupt):
            orch.session.stop_recording()
            msg = "[Push-to-Talk cancelled.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)

        if user_action and user_action.strip().lower() in {"/cancel", "cancel"}:
            orch.session.stop_recording()
            msg = "[Push-to-Talk cancelled.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)

        # 5. Stop capture, transcribe with local faster-whisper, and process with assistant
        try:
            response = orch.stop_push_to_talk_and_transcribe()
        except Exception as exc:
            user_msg = getattr(exc, "user_message", str(exc))
            err = f"[Voice error: {user_msg}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

        if response:
            entries = orch.session.transcript_entries()
            last_user = next((e for e in reversed(entries) if e.role == "user" and e.modality == "voice"), None)
            if last_user and last_user.text:
                self.output_fn(f"You (voice): {last_user.text}")
            self.output_fn(f"RIYA: {response}")
            return DispatchResult(handled=True, is_command=True, output=response)
        else:
            msg = "[No speech detected in push-to-talk recording.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)

    def _activate_continuous(self) -> DispatchResult:
        """Activate continuous conversation session with Silero VAD listening."""
        current = getattr(self.assistant, "current_identity", None)
        if current is None:
            msg = "[Please sign in first with /switch-user before activating voice mode.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)

        # 1. Check microphone permission
        pm = getattr(self.assistant, "permission_manager", None)
        if pm is not None:
            eval_res = pm.evaluate(
                current,
                "voice.microphone",
                resource_or_target="voice:microphone",
                actor="voice",
            )
            if eval_res.blocked:
                msg = (
                    f"[Voice error: Permission 'voice.microphone' is not configured and blocked by default-deny. "
                    "Grant it with /permissions grant voice.microphone.]"
                )
                self.output_fn(msg)
                return DispatchResult(handled=True, is_command=True, error=msg)

        # 2. Check microphone device availability
        try:
            orch = self.get_orchestrator()
        except (IdentityError, PermissionError, VoiceError) as exc:
            user_msg = getattr(exc, "user_message", str(exc))
            err = f"[Voice error: {user_msg}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

        has_custom_mic = getattr(orch.session, "_mic_factory", None) is not None
        if not has_custom_mic:
            try:
                input_devs = list_input_devices()
            except Exception:
                input_devs = []
            if not input_devs:
                err = "[Voice error: No microphone input device is available.]"
                self.output_fn(err)
                return DispatchResult(handled=True, is_command=True, error=err)

        # 3. Start continuous mode
        try:
            orch.set_conversation_mode("continuous")
            orch.switch_mode("voice")
        except (VoicePermissionError, PermissionError) as exc:
            user_msg = getattr(exc, "user_message", str(exc))
            err = f"[Voice error: {user_msg}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)
        except Exception as exc:
            user_msg = getattr(exc, "user_message", str(exc))
            err = f"[Voice error: {user_msg}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

        msg = (
            f"[Voice mode ON ({orch.conversation_mode}) - Microphone active. "
            "Listening with Silero VAD. Speak naturally or type /pause, /interrupt, or /text.]"
        )
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_pause_command(self, args: list[str]) -> DispatchResult:
        """Pause voice listening without terminating the session."""
        orch = self.get_orchestrator_safe()
        if orch is not None and orch.mode == "voice":
            orch.pause_listening()
            msg = "[Voice paused - Listening paused]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)
        msg = "[Voice is not active]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_resume_command(self, args: list[str], raw_text: str) -> DispatchResult:
        """Resume voice listening or autonomous task execution."""
        if args:
            # Task resumption: /resume <task_id>
            tid = args[0].strip()
            try:
                res = self.assistant.resume_task(tid)
                msg = f"RIYA [{res.task_id} | {res.status.value}]: {self.assistant._format_task_result(res)}"
                self.output_fn(msg)
                return DispatchResult(handled=True, is_command=True, output=msg)
            except Exception as exc:
                err = f"[Resume error: {exc}]"
                self.output_fn(err)
                return DispatchResult(handled=True, is_command=True, error=err)

        orch = self.get_orchestrator_safe()
        if orch is not None and orch.mode == "voice":
            orch.resume_listening()
            msg = "[Voice resumed - Listening active]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)
        msg = "[Voice is not active]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_interrupt_command(self, args: list[str]) -> DispatchResult:
        """Interrupt active playback and thinking immediately."""
        orch = self.get_orchestrator_safe()
        interrupted = False
        if orch is not None:
            orch.interrupt("cli_interrupt")
            interrupted = True
        if hasattr(self.assistant, "cancel"):
            try:
                self.assistant.cancel()
                interrupted = True
            except Exception:
                pass

        msg = "[Playback and processing interrupted]" if interrupted else "[Nothing active to interrupt]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_text_command(self, args: list[str]) -> DispatchResult:
        """Switch cleanly to text mode, turning off the microphone."""
        orch = self.get_orchestrator_safe()
        if orch is not None:
            orch.switch_mode("text")
        msg = "[Text mode ON - Microphone off]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_status_command(self, args: list[str]) -> DispatchResult:
        """Report actual session, AI model, and microphone status."""
        current = getattr(self.assistant, "current_identity", None)
        user_label = f"@{current.username}" if current is not None else "signed_out"
        ai_st = self.assistant.ai_status() if hasattr(self.assistant, "ai_status") else {}
        ai_label = (
            f"ready ({ai_st.get('backend', 'local')})"
            if ai_st.get("loaded")
            else "limited (no AI model loaded)"
        )
        orch = self.get_orchestrator_safe()
        if orch is None:
            msg = f"[user={user_label} ai={ai_label} mode=text voice_state=idle mic=OFF]"
        else:
            paused_label = " PAUSED" if orch.is_paused else ""
            mic_status = (
                "LIVE"
                if (orch.session is not None and getattr(orch.session, "microphone_active", False) and not orch.is_paused)
                else ("PAUSED" if getattr(orch, "is_paused", False) else "OFF")
            )
            msg = (
                f"[user={user_label} ai={ai_label} mode={orch.mode} ({orch.conversation_mode}{paused_label}) "
                f"voice_state={orch.state.value} mic={mic_status}]"
            )
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    # -----------------------------------------------------------------
    # IDENTITY & SYSTEM COMMAND HANDLERS
    # -----------------------------------------------------------------

    def _handle_whoami_command(self, args: list[str]) -> DispatchResult:
        current = getattr(self.assistant, "current_identity", None)
        if current is None:
            msg = "[Signed out]"
        else:
            msg = (
                f"[user=@{current.username} display={current.display_name!r} "
                f"user_id={current.user_id} workspace={current.workspace_dir}]"
            )
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_create_user_command(self, args: list[str]) -> DispatchResult:
        if self.identity_manager is None or self.prompt_create_user_fn is None:
            msg = "[User creation unavailable in current environment.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)
        try:
            new_identity = self.prompt_create_user_fn(self.identity_manager)
            self.assistant.set_identity(new_identity)
            if self.coordinator is not None:
                self.coordinator.active_identity = new_identity
            orch = self.get_orchestrator_safe()
            if orch is not None:
                from core.voice.config import VoiceConfig

                cfg = VoiceConfig.load_for_user(self.identity_manager.users_root, new_identity.user_id)
                orch.switch_identity(new_identity, config=cfg)
            msg = f"[Created and switched to @{new_identity.username}]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)
        except (EOFError, KeyboardInterrupt, IdentityError, ValueError) as exc:
            err = f"[Account creation failed: {exc}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

    def _handle_switch_user_command(self, args: list[str]) -> DispatchResult:
        if self.identity_manager is None or self.authenticate_user_fn is None:
            msg = "[User switching unavailable in current environment.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)
        try:
            new_identity = self.authenticate_user_fn(self.identity_manager)
            previous = getattr(self.assistant, "current_identity", None)
            if previous is not None:
                self.identity_manager.logout(previous)
            self.assistant.set_identity(new_identity)
            if self.coordinator is not None:
                self.coordinator.active_identity = new_identity
            orch = self.get_orchestrator_safe()
            if orch is not None:
                from core.voice.config import VoiceConfig

                cfg = VoiceConfig.load_for_user(self.identity_manager.users_root, new_identity.user_id)
                orch.switch_identity(new_identity, config=cfg)
            msg = f"[Switched user to {new_identity.display_name} (@{new_identity.username})]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)
        except (EOFError, KeyboardInterrupt, IdentityError, ValueError) as exc:
            err = f"[Switch user failed: {exc}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

    def _handle_logout_command(self, args: list[str]) -> DispatchResult:
        self.assistant.logout()
        if self.coordinator is not None:
            self.coordinator.active_identity = None
        orch = self.get_orchestrator_safe()
        if orch is not None:
            orch.switch_identity(None)
        msg = "[Signed out. Use /switch-user to sign in or 'exit' to quit.]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_permissions_command(self, args: list[str], raw_text: str) -> DispatchResult:
        pm = getattr(self.assistant, "permission_manager", None)
        current = getattr(self.assistant, "current_identity", None)
        if pm is None or current is None:
            msg = "[Permissions unavailable: sign in first with /switch-user.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)

        try:
            tokens = shlex.split(raw_text)
        except ValueError as exc:
            err = f"[Invalid command syntax: {exc}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

        sub = tokens[1].lower() if len(tokens) > 1 else "list"
        cmd_args = tokens[2:]

        try:
            if sub == "list":
                cat_filter = cmd_args[0].lower() if cmd_args else None
                grants = pm.list_permissions(current, category=cat_filter)
                by_key: dict[str, list] = {}
                for g in grants:
                    by_key.setdefault(g.permission_key, []).append(g)

                self.output_fn(f"[Permissions for @{current.username}]")
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
                        self.output_fn(f"  - {spec.permission_key} [{spec.category}]: {default_lbl} — {spec.description}")
                    else:
                        for g in active:
                            exp = f" expires={g.expires_at}" if g.expires_at else ""
                            self.output_fn(
                                f"  - {spec.permission_key} [{spec.category}]: {g.decision.value.upper()} "
                                f"lifetime={g.lifetime.value} scope={g.resource_scope}{exp}"
                            )
                return DispatchResult(handled=True, is_command=True)

            if sub == "audit":
                limit = int(cmd_args[0]) if cmd_args else 25
                events = pm.audit_logger.list_audit_events(current, limit=limit)
                self.output_fn(f"[Recent Permission Audit Events ({len(events)})]")
                for ev in events:
                    self.output_fn(
                        f"  #{ev.audit_id} [{ev.created_at}] {ev.permission_category}/{ev.permission_key} "
                        f"({ev.resource_or_tool}) -> {ev.decision} ({ev.actor}): {ev.reason}"
                    )
                return DispatchResult(handled=True, is_command=True)

            if sub == "reset":
                category = cmd_args[0].lower() if cmd_args else None
                target_desc = f"category '{category}'" if category else "ALL permissions"
                if not self.confirm_fn(f"Reset {target_desc} for @{current.username}?"):
                    msg = "[Permission reset cancelled.]"
                    self.output_fn(msg)
                    return DispatchResult(handled=True, is_command=True, output=msg)
                count = pm.reset_permissions(current, category=category, actor="user_cli")
                msg = f"[Reset {count} permission rule(s).]"
                self.output_fn(msg)
                return DispatchResult(handled=True, is_command=True, output=msg)

            if sub in {"grant", "deny", "revoke"}:
                if not cmd_args:
                    msg = f"[Usage: /permissions {sub} <permission_key> [--scope S] [--lifetime L] [--ttl SEC] [--mode allow|confirm]]"
                    self.output_fn(msg)
                    return DispatchResult(handled=True, is_command=True, error=msg)
                perm_key = cmd_args[0]
                scope = "*"
                lifetime = "persistent" if sub == "deny" else "session"
                ttl_seconds: float | None = None
                mode = "allow"

                idx = 1
                while idx < len(cmd_args):
                    flag = cmd_args[idx]
                    if flag == "--scope" and idx + 1 < len(cmd_args):
                        scope = cmd_args[idx + 1]
                        idx += 2
                    elif flag == "--lifetime" and idx + 1 < len(cmd_args):
                        lifetime = cmd_args[idx + 1].lower()
                        idx += 2
                    elif flag == "--ttl" and idx + 1 < len(cmd_args):
                        ttl_seconds = float(cmd_args[idx + 1])
                        idx += 2
                    elif flag == "--mode" and idx + 1 < len(cmd_args):
                        mode = cmd_args[idx + 1].lower()
                        idx += 2
                    else:
                        err = f"[Unknown or incomplete option: {flag}]"
                        self.output_fn(err)
                        return DispatchResult(handled=True, is_command=True, error=err)

                if sub == "revoke":
                    count = pm.revoke_permission(
                        current,
                        perm_key,
                        resource_scope=None if scope == "*" else scope,
                        actor="user_cli",
                    )
                    msg = f"[Revoked {count} grant(s) for {perm_key}.]"
                    self.output_fn(msg)
                    return DispatchResult(handled=True, is_command=True, output=msg)

                if sub == "deny":
                    grant = pm.deny_permission(
                        current,
                        perm_key,
                        resource_scope=scope,
                        lifetime=lifetime,
                        ttl_seconds=ttl_seconds,
                        actor="user_cli",
                    )
                    msg = f"[Set DENY rule on {grant.permission_key} scope={grant.resource_scope} lifetime={grant.lifetime.value}.]"
                    self.output_fn(msg)
                    return DispatchResult(handled=True, is_command=True, output=msg)

                spec = PERMISSION_SPECS.get(perm_key)
                if spec is None:
                    err = f"[Unknown permission key: {perm_key}]"
                    self.output_fn(err)
                    return DispatchResult(handled=True, is_command=True, error=err)

                explicit_ok = True
                if lifetime == PermissionLifetime.PERSISTENT.value or spec.sensitive:
                    explicit_ok = self.confirm_fn(
                        f"Grant {mode.upper()} on {perm_key} (lifetime={lifetime}, scope={scope}) for @{current.username}?"
                    )
                    if not explicit_ok:
                        msg = "[Permission grant cancelled.]"
                        self.output_fn(msg)
                        return DispatchResult(handled=True, is_command=True, output=msg)

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
                msg = (
                    f"[Granted {grant.decision.value.upper()} on {grant.permission_key} "
                    f"lifetime={grant.lifetime.value} scope={grant.resource_scope}.]"
                )
                self.output_fn(msg)
                return DispatchResult(handled=True, is_command=True, output=msg)

            msg = (
                "[Commands: /permissions [list [cat]] | /permissions grant <key> [--scope S] "
                "[--lifetime one_time|session|time_limited|persistent] [--ttl SEC] [--mode allow|confirm] | "
                "/permissions deny <key> [--scope S] | /permissions revoke <key> | "
                "/permissions reset [cat] | /permissions audit [limit]]"
            )
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)
        except (ValueError, PermissionErrorBase, IdentityError) as exc:
            err = f"[Permission error: {exc}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

    # -----------------------------------------------------------------
    # PRODUCTIVITY, MEMORY & MISC HANDLERS
    # -----------------------------------------------------------------

    def _handle_ai_metrics_command(self, args: list[str]) -> DispatchResult:
        if hasattr(self.assistant, "ai_metrics"):
            msg = str(self.assistant.ai_metrics())
        else:
            msg = "[AI metrics unavailable.]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_summary_command(self, args: list[str]) -> DispatchResult:
        if hasattr(self.assistant, "summarize_conversation"):
            summary = self.assistant.summarize_conversation()
            msg = summary if summary else "[No conversation summary yet.]"
        else:
            msg = "[Summary unavailable.]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_feedback_command(self, args: list[str], raw_text: str) -> DispatchResult:
        current = getattr(self.assistant, "current_identity", None)
        if current is None:
            msg = "[Sign in first with /switch-user.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)
        parts = raw_text.split(maxsplit=2)
        if len(parts) < 2:
            msg = "[Usage: /feedback <positive|negative|correction> [correction or note]]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)
        rating = parts[1].strip().lower()
        corr = parts[2].strip() if len(parts) > 2 else None
        try:
            entry, profile = self.assistant.record_feedback(rating, correction_text=corr)
            style_info = (
                f"style={profile.response_style}, tone={profile.tone}, format={profile.formatting_preference}"
                if profile is not None
                else "saved"
            )
            msg = f"[Recorded {entry.rating} feedback ({entry.feedback_id}); adaptive profile: {style_info}]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)
        except Exception as exc:
            err = f"[Feedback error: {exc}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

    def _handle_settings_command(self, args: list[str]) -> DispatchResult:
        orch = self.get_orchestrator_safe()
        if orch is not None:
            msg = str(orch.config.to_dict())
        else:
            msg = "[Voice configuration unavailable.]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_onboarding_command(self, args: list[str]) -> DispatchResult:
        if self.coordinator is not None:
            det_now = self.coordinator.detect_initial_state()
            msg = (
                f"[Onboarding state={det_now.install_state.value} step={det_now.resume_step} "
                f"legacy_v04_memories={det_now.legacy_memories_count}: {det_now.message}]"
            )
        else:
            msg = "[Onboarding coordinator unavailable.]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_diagnostics_command(self, args: list[str]) -> DispatchResult:
        if self.coordinator is not None:
            report = self.coordinator.run_diagnostics(assistant=self.assistant)
            from core.main import _print_diagnostics_report

            _print_diagnostics_report(report)
            return DispatchResult(handled=True, is_command=True)
        msg = "[Diagnostics unavailable.]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, error=msg)

    def _handle_memories_command(self, args: list[str]) -> DispatchResult:
        current = getattr(self.assistant, "current_identity", None)
        if current is None:
            msg = "[Sign in first with /switch-user.]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)
        try:
            rows = self.assistant.memory.manager.for_identity(current).list_memories()
            self.output_fn(f"[Stored memories for @{current.username} ({len(rows)})]")
            for row in rows:
                self.output_fn(f"  - [{row.get('category', 'general')}] {row.get('key')} = {row.get('value')}")
            return DispatchResult(handled=True, is_command=True)
        except Exception as exc:
            err = f"[Memory access blocked/error: {exc}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)

    def _handle_task_command(self, cmd: str, args: list[str], raw_text: str) -> DispatchResult:
        if cmd == "/tasks" and not args:
            try:
                tasks = self.assistant.list_tasks() if hasattr(self.assistant, "list_tasks") else []
                self.output_fn(f"[Tasks for current user ({len(tasks)})]")
                for t in tasks:
                    self.output_fn(f"  - {t['task_id']} [{t['status']}] {t['goal']}")
                return DispatchResult(handled=True, is_command=True)
            except Exception as exc:
                err = f"[Tasks error: {exc}]"
                self.output_fn(err)
                return DispatchResult(handled=True, is_command=True, error=err)

        if cmd == "/task" and args and args[0].lower() not in {"add", "done", "list", "all", "pending"}:
            goal_text = raw_text[len(cmd):].strip()
            if hasattr(self.assistant, "run_autonomous_task"):
                res = self.assistant.run_autonomous_task(goal_text)
                msg = f"RIYA [{res.task_id} | {res.status.value}]: {self.assistant._format_task_result(res)}"
                self.output_fn(msg)
                return DispatchResult(handled=True, is_command=True, output=msg)

        from core.main import _handle_task_cli

        _handle_task_cli(self.assistant, raw_text)
        return DispatchResult(handled=True, is_command=True)

    def _handle_reminder_command(self, args: list[str], raw_text: str) -> DispatchResult:
        from core.main import _handle_reminder_cli

        _handle_reminder_cli(self.assistant, raw_text)
        return DispatchResult(handled=True, is_command=True)

    def _handle_calendar_command(self, args: list[str]) -> DispatchResult:
        from core.main import _handle_calendar_cli

        _handle_calendar_cli(self.assistant)
        return DispatchResult(handled=True, is_command=True)

    def _handle_notes_command(self, args: list[str], raw_text: str) -> DispatchResult:
        from core.main import _handle_notes_cli

        _handle_notes_cli(self.assistant, raw_text)
        return DispatchResult(handled=True, is_command=True)

    def _handle_plan_command(self, args: list[str]) -> DispatchResult:
        from core.main import _handle_plan_cli

        _handle_plan_cli(self.assistant)
        return DispatchResult(handled=True, is_command=True)

    def _handle_cancel_task_command(self, args: list[str]) -> DispatchResult:
        if not args:
            msg = "[Usage: /cancel-task <task_id>]"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, error=msg)
        tid = args[0].strip()
        ok = self.assistant.cancel_task(tid) if hasattr(self.assistant, "cancel_task") else False
        msg = f"[Task {tid} cancelled: {ok}]"
        self.output_fn(msg)
        return DispatchResult(handled=True, is_command=True, output=msg)

    def _handle_skills_command(self, cmd: str, args: list[str], raw_text: str) -> DispatchResult:
        if cmd == "/skills" and not args:
            skills = self.assistant.list_skills() if hasattr(self.assistant, "list_skills") else []
            self.output_fn(f"[Available RIYA Skills ({len(skills)})]")
            for s in skills:
                self.output_fn(f"  - {s.skill_id} (v{s.version}): {s.description}")
            return DispatchResult(handled=True, is_command=True)

        parts = raw_text.split(maxsplit=2)
        skill_id = parts[1].strip() if len(parts) > 1 else ""
        raw_json = parts[2].strip() if len(parts) > 2 else "{}"
        try:
            import json as _json

            parsed_inputs = _json.loads(raw_json) if raw_json else {}
            sres = self.assistant.run_skill(skill_id, inputs=parsed_inputs)
            msg = f"[Skill {skill_id} | {sres.status}]: {sres.summary or sres.error}"
            self.output_fn(msg)
            return DispatchResult(handled=True, is_command=True, output=msg)
        except Exception as exc:
            err = f"[Skill error: {exc}]"
            self.output_fn(err)
            return DispatchResult(handled=True, is_command=True, error=err)
