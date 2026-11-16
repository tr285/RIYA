"""Framework-independent ViewModel and Tkinter desktop UI for RIYA onboarding & sign-in."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

from core.config.settings import APP_NAME, APP_VERSION
from core.identity.models import AuthenticatedIdentity, UserProfile
from core.memory.manager import MemoryManager
from core.onboarding.ai_setup import AIEngineStatus, ModelVerificationResult
from core.onboarding.diagnostics import CheckSeverity, ReadinessReport
from core.onboarding.manager import (
    OnboardingCoordinator,
    OnboardingWelcomeInfo,
)
from core.onboarding.state import (
    ONBOARDING_STEPS,
    InstallState,
    OnboardingDetectionResult,
)
from core.onboarding.voice_setup import VoiceSubsystemStatus, VoiceTestResult
from core.security.permissions import (
    PERMISSION_SPECS,
    PermissionDecision,
    PermissionManager,
)
from core.voice.config import RESPONSE_STYLES, VoiceConfig
from core.voice.tts import LocalTTS


@dataclass(frozen=True)
class WizardStepDescriptor:
    step_id: str
    title: str
    subtitle: str


WIZARD_STEP_DESCRIPTORS: tuple[WizardStepDescriptor, ...] = (
    WizardStepDescriptor("welcome", "Welcome & Privacy", "Offline-first promise and honest boundaries"),
    WizardStepDescriptor("account", "Local Account", "Create or unlock your isolated local profile"),
    WizardStepDescriptor("ai_engine", "Local AI Engine", "Select & verify a local GGUF model"),
    WizardStepDescriptor("voice", "Voice & Audio", "Permission-gated microphone & speaker setup"),
    WizardStepDescriptor("permissions", "Trust & Permissions", "Review how RIYA asks before acting"),
    WizardStepDescriptor("diagnostics", "System Readiness", "Verify storage, AI, voice, and security"),
)


class OnboardingViewModel:
    """Headless state controller for RIYA's onboarding, recovery, and login flows."""

    def __init__(self, coordinator: OnboardingCoordinator | None = None):
        self.coordinator = coordinator or OnboardingCoordinator()
        self.current_step: str = "welcome"
        self.last_detection: OnboardingDetectionResult | None = None
        self.last_ai_status: AIEngineStatus | None = None
        self.last_voice_status: VoiceSubsystemStatus | None = None
        self.last_report: ReadinessReport | None = None
        self.last_error: str | None = None

    def initial_route(self) -> str:
        det = self.coordinator.detect_initial_state()
        self.last_detection = det
        if det.install_state == InstallState.NEEDS_RECOVERY:
            return "recovery"
        if det.needs_onboarding:
            self.current_step = (
                det.resume_step
                if det.resume_step in {s.step_id for s in WIZARD_STEP_DESCRIPTORS}
                else "welcome"
            )
            return "onboarding"
        return "login"

    def step_ids(self) -> tuple[str, ...]:
        return tuple(d.step_id for d in WIZARD_STEP_DESCRIPTORS)

    def step_index(self, step: str | None = None) -> int:
        target = step or self.current_step
        ids = self.step_ids()
        return ids.index(target) if target in ids else 0

    def go_to_step(self, step: str) -> str:
        ids = self.step_ids()
        if step not in ids:
            raise ValueError(f"Unsupported UI onboarding step: {step!r}")
        self.current_step = step
        return self.current_step

    def next_step(self) -> str:
        ids = self.step_ids()
        idx = self.step_index()
        if idx + 1 < len(ids):
            self.current_step = ids[idx + 1]
        return self.current_step

    def previous_step(self) -> str:
        ids = self.step_ids()
        idx = self.step_index()
        if idx > 0:
            self.current_step = ids[idx - 1]
        return self.current_step

    def welcome_info(self) -> OnboardingWelcomeInfo:
        info = self.coordinator.welcome_info()
        self.last_detection = info.detection
        return info

    def recover_installation(self) -> list[str]:
        notes: list[str] = []
        det = self.coordinator.detect_initial_state()
        if det.checkpoint_corrupted:
            backup = self.coordinator.recover_corrupted_checkpoint()
            if backup is not None:
                notes.append(f"Corrupted checkpoint backed up to {backup.name}.")
        self.coordinator.initialize_services()
        self.last_detection = self.coordinator.detect_initial_state()
        notes.append("Database and onboarding services initialized safely.")
        if self.last_detection.needs_onboarding:
            self.current_step = (
                self.last_detection.resume_step
                if self.last_detection.resume_step in self.step_ids()
                else "welcome"
            )
        return notes

    def begin_setup(self) -> OnboardingDetectionResult:
        res = self.coordinator.start_or_resume_setup(step=self.current_step)
        self.last_detection = res
        return res

    def list_profiles(self) -> list[UserProfile]:
        return self.coordinator.list_existing_profiles()

    def create_account(
        self,
        *,
        username: str,
        display_name: str | None = None,
        auth_method: str = "local_password",
        secret: str | None = None,
        confirm_secret: str | None = None,
        claim_legacy_memories: bool = False,
    ) -> tuple[AuthenticatedIdentity, int]:
        identity = self.coordinator.create_local_account(
            username=username,
            display_name=display_name,
            auth_method=auth_method,
            secret=secret,
            confirm_secret=confirm_secret,
        )
        claimed = 0
        if claim_legacy_memories:
            claimed = self.claim_legacy_memories()
        return identity, claimed

    def sign_in(
        self,
        username: str,
        *,
        secret: str | None = None,
        claim_legacy_memories: bool = False,
    ) -> tuple[AuthenticatedIdentity, int]:
        identity = self.coordinator.sign_in(username, secret=secret)
        claimed = 0
        if claim_legacy_memories:
            claimed = self.claim_legacy_memories()
        return identity, claimed

    def claim_legacy_memories(self) -> int:
        db, id_mgr, _, _ = self.coordinator.initialize_services()
        active = self.coordinator.active_identity
        if active is None:
            raise PermissionError("Sign in before claiming legacy v0.4 memories.")
        mm = MemoryManager(db, identity_manager=id_mgr)
        return mm.claim_legacy_memories(active, identity_manager=id_mgr)

    def logout(self) -> None:
        self.coordinator.logout_current_user()

    def inspect_ai(self) -> AIEngineStatus:
        status = self.coordinator.inspect_ai()
        self.last_ai_status = status
        return status

    def verify_ai(self, *, model_path: str | None, backend: str = "auto") -> ModelVerificationResult:
        return self.coordinator.configure_and_verify_ai(
            model_path=model_path,
            backend=backend,
        )

    def continue_without_ai(self) -> None:
        self.coordinator.continue_without_ai()

    def inspect_voice(self, *, config: VoiceConfig | None = None) -> VoiceSubsystemStatus:
        st = self.coordinator.inspect_voice(config=config)
        self.last_voice_status = st
        return st

    def load_voice_config(self) -> tuple[VoiceConfig, str | None]:
        return self.coordinator.load_current_voice_config()

    def test_microphone(self, *, config: VoiceConfig | None = None) -> VoiceTestResult:
        return self.coordinator.test_microphone(config=config)

    def test_speaker(self, *, config: VoiceConfig | None = None) -> VoiceTestResult:
        return self.coordinator.test_speaker(config=config)

    def save_voice(self, config: VoiceConfig) -> VoiceConfig:
        return self.coordinator.save_voice_setup(config)

    def skip_voice(self) -> None:
        self.coordinator.skip_voice_setup()

    def acknowledge_permissions(self) -> None:
        self.coordinator.acknowledge_permissions_reviewed()

    def grant_explicit_permission(
        self,
        permission_key: str,
        *,
        decision: str = "allow",
        lifetime: str = "session",
        ttl_seconds: float | None = 300.0,
        explicit_persistent_ack: bool = False,
    ) -> None:
        """Grant an explicit user-approved permission (e.g., to test voice or save voice settings)."""
        _, _, pm, _ = self.coordinator.initialize_services()
        active = self.coordinator.active_identity
        if active is None:
            raise PermissionError("Sign in first before configuring permissions.")
        pm.grant_permission(
            active,
            permission_key,
            decision=decision,
            lifetime=lifetime,
            resource_scope="*",
            ttl_seconds=ttl_seconds if lifetime == "time_limited" else None,
            reason="Explicit user approval during RIYA setup",
            actor="user_onboarding_ui",
            explicit_user_approval=explicit_persistent_ack,
        )

    def run_diagnostics(self) -> ReadinessReport:
        report = self.coordinator.run_diagnostics()
        self.last_report = report
        return report

    def finish_onboarding(self) -> ReadinessReport:
        report = self.coordinator.complete_onboarding()
        self.last_report = report
        return report


class _DesktopOnboardingWindow:
    """Tkinter multi-step wizard + recovery + sign-in screen rendered inside a shared root."""

    def __init__(self, root: Any, view_model: OnboardingViewModel):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.ttk = ttk

        self.root = root
        self.vm = view_model
        self.result: str = "closed"  # "launch" | "closed"
        self._route: str = self.vm.initial_route()

        self.root.title(f"{APP_NAME} v{APP_VERSION} — Setup & Sign In")
        self.root.geometry("940x720")
        self.root.configure(bg="#11141a")
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self._container = tk.Frame(self.root, bg="#11141a")
        self._container.pack(fill=tk.BOTH, expand=True)

        self._selected_model_path: str = self.vm.coordinator.selected_model_path or ""
        self._selected_backend: str = self.vm.coordinator.selected_backend or "auto"
        self._ai_testing = False

        self.render()

    def _clear(self) -> None:
        for child in self._container.winfo_children():
            child.destroy()

    def _on_window_close(self) -> None:
        self.result = "closed"
        self.root.quit()

    def run(self) -> str:
        self.root.mainloop()
        return self.result

    def render(self) -> None:
        self._clear()
        if self._route == "recovery":
            self._render_recovery()
        elif self._route == "login":
            self._render_login()
        else:
            self._render_onboarding_step()

    def _render_header(self, title: str, subtitle: str, *, show_stepper: bool = False) -> None:
        tk = self.tk
        hdr = tk.Frame(self._container, bg="#181d26", padx=20, pady=14)
        hdr.pack(fill=tk.X)

        tk.Label(
            hdr,
            text=f"{APP_NAME} v{APP_VERSION} — {title}",
            fg="#f8fafc",
            bg="#181d26",
            font=("Helvetica", 15, "bold"),
            anchor="w",
        ).pack(fill=tk.X)
        tk.Label(
            hdr,
            text=subtitle,
            fg="#94a3b8",
            bg="#181d26",
            font=("Helvetica", 10),
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 0))

        if show_stepper:
            bar = tk.Frame(self._container, bg="#0f172a", padx=20, pady=8)
            bar.pack(fill=tk.X)
            cur = self.vm.current_step
            for idx, desc in enumerate(WIZARD_STEP_DESCRIPTORS, start=1):
                active = desc.step_id == cur
                fg = "#38bdf8" if active else "#94a3b8"
                weight = "bold" if active else "normal"
                tk.Label(
                    bar,
                    text=f"{idx}. {desc.title}",
                    fg=fg,
                    bg="#0f172a",
                    font=("Helvetica", 9, weight),
                    padx=8,
                ).pack(side=tk.LEFT)

    # ---- Recovery screen ---------------------------------------------------
    def _render_recovery(self) -> None:
        tk = self.tk
        ttk = self.ttk
        det = self.vm.coordinator.detect_initial_state()
        self._render_header(
            "Startup Recovery",
            "RIYA detected an issue during startup check and protected your existing data.",
        )

        body = tk.Frame(self._container, bg="#11141a", padx=24, pady=18)
        body.pack(fill=tk.BOTH, expand=True)

        box = tk.Text(
            body,
            bg="#0d1015",
            fg="#e2e8f0",
            font=("Helvetica", 11),
            relief=tk.FLAT,
            wrap=tk.WORD,
            padx=14,
            pady=12,
            height=16,
        )
        box.pack(fill=tk.BOTH, expand=True, pady=(0, 14))
        box.insert(tk.END, f"Detected State: {det.install_state.value}\n")
        box.insert(tk.END, f"Summary: {det.message}\n\n")
        if det.database_error:
            box.insert(tk.END, f"Database Error: {det.database_error}\n")
        if det.checkpoint_error:
            box.insert(tk.END, f"Checkpoint Error: {det.checkpoint_error}\n")
        if det.recovery_hints:
            box.insert(tk.END, "\nRecommended Actions:\n")
            for hint in det.recovery_hints:
                box.insert(tk.END, f"  • {hint}\n")
        box.configure(state=tk.DISABLED)

        msg_var = tk.StringVar(value="")
        tk.Label(body, textvariable=msg_var, fg="#34d399", bg="#11141a").pack(anchor="w", pady=(0, 8))

        btns = tk.Frame(body, bg="#11141a")
        btns.pack(fill=tk.X)

        def _repair():
            try:
                notes = self.vm.recover_installation()
                msg_var.set(" ".join(notes))
                self._route = "onboarding"
                self.render()
            except Exception as exc:
                self.messagebox.showerror("Recovery Failed", str(exc))

        ttk.Button(btns, text="Repair Checkpoint & Continue Safely", command=_repair).pack(side=tk.LEFT)
        ttk.Button(btns, text="Exit", command=self._on_window_close).pack(side=tk.RIGHT)

    # ---- Returning-user login screen --------------------------------------
    def _render_login(self) -> None:
        tk = self.tk
        ttk = self.ttk
        self.vm.coordinator.initialize_services()
        profiles = self.vm.list_profiles()
        self._render_header(
            "Sign In",
            "Unlock your local profile. Every profile's memories, files, voice config, and permissions stay isolated.",
        )

        body = tk.Frame(self._container, bg="#11141a", padx=28, pady=20)
        body.pack(fill=tk.BOTH, expand=True)

        if not profiles:
            self._route = "onboarding"
            self.vm.go_to_step("account")
            self.render()
            return

        card = tk.Frame(body, bg="#181d26", padx=20, pady=18)
        card.pack(fill=tk.X, pady=(8, 12))

        usernames = [p.username for p in profiles]
        by_name = {p.username: p for p in profiles}
        user_var = tk.StringVar(value=usernames[0])
        secret_var = tk.StringVar(value="")
        status_var = tk.StringVar(value="")

        tk.Label(card, text="Local Profile", fg="#cbd5e1", bg="#181d26", font=("Helvetica", 11, "bold")).pack(anchor="w")
        combo = ttk.Combobox(card, textvariable=user_var, values=usernames, state="readonly")
        combo.pack(fill=tk.X, pady=(4, 12))

        secret_lbl = tk.Label(card, text="Password / PIN", fg="#cbd5e1", bg="#181d26")
        secret_lbl.pack(anchor="w")
        secret_entry = tk.Entry(card, textvariable=secret_var, show="*", bg="#0d1015", fg="#f8fafc", insertbackground="#f8fafc")
        secret_entry.pack(fill=tk.X, pady=(4, 12))

        def _sync_auth_ui(*_args):
            prof = by_name.get(user_var.get())
            if prof and prof.auth_method == "local_unlock":
                secret_lbl.configure(text="Quick local unlock enabled (no password required)")
                secret_entry.configure(state=tk.DISABLED)
                secret_var.set("")
            else:
                secret_lbl.configure(text="Password / PIN")
                secret_entry.configure(state=tk.NORMAL)

        user_var.trace_add("write", _sync_auth_ui)
        _sync_auth_ui()

        tk.Label(card, textvariable=status_var, fg="#f87171", bg="#181d26").pack(anchor="w", pady=(0, 8))

        def _do_sign_in(_event=None):
            try:
                self.vm.sign_in(user_var.get(), secret=secret_var.get() or None)
                secret_var.set("")
                self.result = "launch"
                self.root.quit()
            except Exception as exc:
                status_var.set(str(exc))

        secret_entry.bind("<Return>", _do_sign_in)

        row = tk.Frame(card, bg="#181d26")
        row.pack(fill=tk.X)
        ttk.Button(row, text="Sign In & Open RIYA", command=_do_sign_in).pack(side=tk.LEFT)
        ttk.Button(
            row,
            text="Create New Profile",
            command=lambda: (
                setattr(self, "_route", "onboarding"),
                self.vm.go_to_step("account"),
                self.render(),
            ),
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(
            row,
            text="Re-run Setup Wizard",
            command=lambda: (
                setattr(self, "_route", "onboarding"),
                self.vm.go_to_step("welcome"),
                self.render(),
            ),
        ).pack(side=tk.RIGHT)

    # ---- Onboarding wizard dispatcher -------------------------------------
    def _render_onboarding_step(self) -> None:
        step = self.vm.current_step
        desc = next(
            (d for d in WIZARD_STEP_DESCRIPTORS if d.step_id == step),
            WIZARD_STEP_DESCRIPTORS[0],
        )
        self._render_header(desc.title, desc.subtitle, show_stepper=True)
        if step == "welcome":
            self._render_step_welcome()
        elif step == "account":
            self._render_step_account()
        elif step == "ai_engine":
            self._render_step_ai()
        elif step == "voice":
            self._render_step_voice()
        elif step == "permissions":
            self._render_step_permissions()
        else:
            self._render_step_diagnostics()

    def _render_step_welcome(self) -> None:
        tk = self.tk
        ttk = self.ttk
        info = self.vm.welcome_info()
        det = info.detection

        body = tk.Frame(self._container, bg="#11141a", padx=24, pady=18)
        body.pack(fill=tk.BOTH, expand=True)

        box = tk.Text(
            body,
            bg="#0d1015",
            fg="#e2e8f0",
            font=("Helvetica", 11),
            relief=tk.FLAT,
            wrap=tk.WORD,
            padx=14,
            pady=12,
        )
        box.pack(fill=tk.BOTH, expand=True, pady=(0, 14))
        box.insert(tk.END, f"{info.title}\n{info.tagline}\n\n")
        box.insert(tk.END, f"Detected Installation: {det.install_state.value}\n")
        box.insert(tk.END, f"{det.message}\n")
        if det.legacy_memories_count > 0:
            box.insert(
                tk.END,
                f"Preserved v0.4 memories detected: {det.legacy_memories_count} (never deleted).\n",
            )
        box.insert(tk.END, "\n" + info.privacy_details + "\n")
        box.configure(state=tk.DISABLED)

        btns = tk.Frame(body, bg="#11141a")
        btns.pack(fill=tk.X)

        def _begin():
            try:
                res = self.vm.begin_setup()
                if res.install_state == InstallState.SETUP_IN_PROGRESS and res.resume_step != "welcome":
                    self.vm.go_to_step(
                        res.resume_step if res.resume_step in self.vm.step_ids() else "account"
                    )
                else:
                    self.vm.go_to_step("account")
                self.render()
            except Exception as exc:
                self.messagebox.showerror("Initialization Error", str(exc))

        label = "Resume Setup" if det.can_resume else "Get Started"
        ttk.Button(btns, text=label, command=_begin).pack(side=tk.RIGHT)

    def _render_step_account(self) -> None:
        tk = self.tk
        ttk = self.ttk
        self.vm.coordinator.initialize_services()
        det = self.vm.coordinator.detect_initial_state()
        profiles = self.vm.list_profiles()

        body = tk.Frame(self._container, bg="#11141a", padx=24, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        active = self.vm.coordinator.active_identity
        if active is not None:
            tk.Label(
                body,
                text=f"Currently signed in as: {active.display_name} (@{active.username})",
                fg="#34d399",
                bg="#14332a",
                padx=12,
                pady=8,
                font=("Helvetica", 10, "bold"),
            ).pack(fill=tk.X, pady=(0, 12))

        mode_var = tk.StringVar(value="existing" if profiles and active is None else "create")
        card = tk.Frame(body, bg="#181d26", padx=18, pady=14)
        card.pack(fill=tk.X, pady=(0, 12))

        top_opts = tk.Frame(card, bg="#181d26")
        top_opts.pack(fill=tk.X, pady=(0, 10))
        tk.Radiobutton(
            top_opts,
            text="Create New Local Profile",
            variable=mode_var,
            value="create",
            bg="#181d26",
            fg="#f8fafc",
            selectcolor="#11141a",
        ).pack(side=tk.LEFT, padx=(0, 14))
        if profiles:
            tk.Radiobutton(
                top_opts,
                text="Sign In to Existing Profile",
                variable=mode_var,
                value="existing",
                bg="#181d26",
                fg="#f8fafc",
                selectcolor="#11141a",
            ).pack(side=tk.LEFT)

        form = tk.Frame(card, bg="#181d26")
        form.pack(fill=tk.X)

        username_var = tk.StringVar(value=profiles[0].username if profiles else "")
        display_var = tk.StringVar(value="")
        auth_var = tk.StringVar(value="local_password")
        secret_var = tk.StringVar(value="")
        confirm_var = tk.StringVar(value="")
        claim_var = tk.BooleanVar(value=False)  # Always opt-in!
        err_var = tk.StringVar(value="")

        def _rebuild_form(*_args):
            for w in form.winfo_children():
                w.destroy()
            if mode_var.get() == "existing" and profiles:
                tk.Label(form, text="Existing Username", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
                ttk.Combobox(
                    form,
                    textvariable=username_var,
                    values=[p.username for p in profiles],
                    state="readonly",
                ).pack(fill=tk.X, pady=(4, 10))
                tk.Label(form, text="Password / PIN (blank only for local_unlock)", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
                tk.Entry(form, textvariable=secret_var, show="*", bg="#0d1015", fg="#f8fafc", insertbackground="#f8fafc").pack(fill=tk.X, pady=(4, 10))
            else:
                if mode_var.get() == "create" and profiles and username_var.get() == profiles[0].username:
                    username_var.set("")
                tk.Label(form, text="Username (letters/digits/._-)", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
                tk.Entry(form, textvariable=username_var, bg="#0d1015", fg="#f8fafc", insertbackground="#f8fafc").pack(fill=tk.X, pady=(4, 8))

                tk.Label(form, text="Display Name (optional)", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
                tk.Entry(form, textvariable=display_var, bg="#0d1015", fg="#f8fafc", insertbackground="#f8fafc").pack(fill=tk.X, pady=(4, 8))

                tk.Label(form, text="Authentication Method", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
                ttk.Combobox(
                    form,
                    textvariable=auth_var,
                    values=["local_password", "local_pin", "local_unlock"],
                    state="readonly",
                ).pack(fill=tk.X, pady=(4, 8))

                tk.Label(form, text="Password / PIN (stored as salted scrypt hash)", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
                tk.Entry(form, textvariable=secret_var, show="*", bg="#0d1015", fg="#f8fafc", insertbackground="#f8fafc").pack(fill=tk.X, pady=(4, 8))

                tk.Label(form, text="Confirm Password / PIN", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
                tk.Entry(form, textvariable=confirm_var, show="*", bg="#0d1015", fg="#f8fafc", insertbackground="#f8fafc").pack(fill=tk.X, pady=(4, 8))

            if det.legacy_memories_count > 0:
                tk.Checkbutton(
                    form,
                    text=f"Claim {det.legacy_memories_count} preserved v0.4 memories into this profile",
                    variable=claim_var,
                    bg="#181d26",
                    fg="#fbbf24",
                    selectcolor="#11141a",
                ).pack(anchor="w", pady=(6, 4))

        mode_var.trace_add("write", _rebuild_form)
        _rebuild_form()

        tk.Label(body, textvariable=err_var, fg="#f87171", bg="#11141a").pack(anchor="w")

        nav = tk.Frame(body, bg="#11141a", pady=10)
        nav.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(nav, text="Back", command=lambda: (self.vm.go_to_step("welcome"), self.render())).pack(side=tk.LEFT)

        def _submit_and_next():
            try:
                if active is not None and not username_var.get().strip():
                    self.vm.go_to_step("ai_engine")
                    self.render()
                    return
                if mode_var.get() == "existing" and profiles:
                    self.vm.sign_in(
                        username_var.get().strip(),
                        secret=secret_var.get() or None,
                        claim_legacy_memories=bool(claim_var.get()),
                    )
                else:
                    self.vm.create_account(
                        username=username_var.get().strip(),
                        display_name=display_var.get().strip() or None,
                        auth_method=auth_var.get(),
                        secret=secret_var.get() or None,
                        confirm_secret=confirm_var.get() if auth_var.get() != "local_unlock" else None,
                        claim_legacy_memories=bool(claim_var.get()),
                    )
                secret_var.set("")
                confirm_var.set("")
                self.vm.go_to_step("ai_engine")
                self.render()
            except Exception as exc:
                err_var.set(str(exc))

        if active is not None:
            ttk.Button(
                nav,
                text="Continue as Current User",
                command=lambda: (self.vm.go_to_step("ai_engine"), self.render()),
            ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(nav, text="Authenticate & Continue", command=_submit_and_next).pack(side=tk.RIGHT)

    def _render_step_ai(self) -> None:
        tk = self.tk
        ttk = self.ttk
        st = self.vm.inspect_ai()

        body = tk.Frame(self._container, bg="#11141a", padx=24, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        info_box = tk.Text(
            body,
            bg="#0d1015",
            fg="#e2e8f0",
            font=("Helvetica", 10),
            relief=tk.FLAT,
            height=9,
            padx=12,
            pady=10,
        )
        info_box.pack(fill=tk.X, pady=(0, 12))
        ram_gb = st.total_ram_bytes / float(1024**3) if st.total_ram_bytes else 0.0
        disk_gb = st.free_disk_bytes / float(1024**3) if st.free_disk_bytes else 0.0
        info_box.insert(
            tk.END,
            f"Hardware: {st.os_info} ({st.architecture}) | RAM: {ram_gb:.1f} GB | Free Disk: {disk_gb:.1f} GB\n"
            f"Embedded llama.cpp runtime: {'Ready' if st.native_library_loadable else 'Unavailable'} ({st.hardware_acceleration})\n"
            f"Library path: {st.native_library_path}\n"
            f"Discovered GGUF models: {len(st.discovered_models)}\n",
        )
        if st.native_library_error:
            info_box.insert(tk.END, f"Runtime Error: {st.native_library_error}\n")
        for g in st.guidance:
            info_box.insert(tk.END, f"Guidance: {g}\n")
        info_box.configure(state=tk.DISABLED)

        cfg = tk.Frame(body, bg="#181d26", padx=16, pady=14)
        cfg.pack(fill=tk.X)

        discovered_paths = [m.path for m in st.discovered_models]
        default_path = self._selected_model_path or st.selected_model_path or (discovered_paths[0] if discovered_paths else "")
        model_var = tk.StringVar(value=default_path)
        backend_var = tk.StringVar(value=self._selected_backend or "auto")
        status_var = tk.StringVar(
            value="Verified with real local inference."
            if st.inference_verified
            else "Select a local .gguf model and click Verify Local Model, or continue in Limited Mode."
        )

        tk.Label(cfg, text="Local GGUF Model File", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
        row = tk.Frame(cfg, bg="#181d26")
        row.pack(fill=tk.X, pady=(4, 10))
        ttk.Combobox(row, textvariable=model_var, values=discovered_paths).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _browse():
            chosen = self.filedialog.askopenfilename(
                title="Select Local GGUF Model",
                filetypes=[("GGUF Model", "*.gguf"), ("All Files", "*.*")],
            )
            if chosen:
                model_var.set(chosen)

        ttk.Button(row, text="Browse...", command=_browse).pack(side=tk.RIGHT, padx=(8, 0))

        tk.Label(cfg, text="Inference Backend", fg="#cbd5e1", bg="#181d26").pack(anchor="w")
        ttk.Combobox(
            cfg,
            textvariable=backend_var,
            values=list(st.supported_backends) if st.supported_backends else ["auto", "cpu"],
            state="readonly",
        ).pack(fill=tk.X, pady=(4, 10))

        tk.Label(cfg, textvariable=status_var, fg="#93c5fd", bg="#181d26", wraplength=820, justify="left").pack(anchor="w")

        verify_btn = ttk.Button(cfg, text="Load & Verify Local Model")
        verify_btn.pack(anchor="w", pady=(10, 0))

        def _run_verify():
            if self._ai_testing:
                return
            path = model_var.get().strip()
            backend = backend_var.get().strip() or "auto"
            self._selected_model_path = path
            self._selected_backend = backend
            self._ai_testing = True
            verify_btn.configure(state=self.tk.DISABLED)
            status_var.set("Loading GGUF model and running real offline verification prompt...")

            def _worker():
                res = self.vm.verify_ai(model_path=path or None, backend=backend)

                def _done():
                    self._ai_testing = False
                    if verify_btn.winfo_exists():
                        verify_btn.configure(state=self.tk.NORMAL)
                    if res.success:
                        status_var.set(
                            f"Verified ({res.backend_used}): {res.output_preview[:140]}"
                        )
                    else:
                        status_var.set(f"Verification failed: {res.error_message}")

                self.root.after(0, _done)

            threading.Thread(target=_worker, daemon=True).start()

        verify_btn.configure(command=_run_verify)

        nav = tk.Frame(body, bg="#11141a", pady=10)
        nav.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(nav, text="Back", command=lambda: (self.vm.go_to_step("account"), self.render())).pack(side=tk.LEFT)
        ttk.Button(
            nav,
            text="Continue in Limited Mode",
            command=lambda: (self.vm.continue_without_ai(), self.vm.go_to_step("voice"), self.render()),
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            nav,
            text="Next: Voice Setup",
            command=lambda: (self.vm.go_to_step("voice"), self.render()),
        ).pack(side=tk.RIGHT)

    def _render_step_voice(self) -> None:
        tk = self.tk
        ttk = self.ttk
        cfg, err_msg = self.vm.load_voice_config()
        vst = self.vm.inspect_voice(config=cfg)

        body = tk.Frame(self._container, bg="#11141a", padx=24, pady=14)
        body.pack(fill=tk.BOTH, expand=True)

        summary = tk.Label(
            body,
            text=(
                f"Voice Privacy: Mic stays OFF by default. {vst.privacy_summary}\n"
                f"STT ({vst.stt_model}): {vst.stt_status_detail} | VAD: {vst.vad_backend} | "
                f"TTS: {'Ready' if vst.tts_available else 'Unavailable'}\n"
                f"Permissions — mic: {vst.mic_permission_decision}, speaker: {vst.speaker_permission_decision}, "
                f"config: {vst.voice_config_decision}"
                + (f"\nNote: {err_msg}" if err_msg else "")
            ),
            fg="#cbd5e1",
            bg="#181d26",
            padx=12,
            pady=10,
            justify="left",
            wraplength=840,
            anchor="w",
        )
        summary.pack(fill=tk.X, pady=(0, 10))

        form = tk.Frame(body, bg="#181d26", padx=16, pady=12)
        form.pack(fill=tk.X)

        in_devs = ["Default"] + [d.name for d in vst.input_devices]
        out_devs = ["Default"] + [d.name for d in vst.output_devices]
        lang_opts = list(vst.language_options)
        lang_labels = [lbl for _, lbl in lang_opts]
        lang_codes = [code for code, _ in lang_opts]
        voices = sorted({cfg.voice} | {v["name"] for v in LocalTTS.list_voices()})

        in_var = tk.StringVar(value=str(cfg.input_device or "Default"))
        out_var = tk.StringVar(value=str(cfg.output_device or "Default"))
        lang_idx = lang_codes.index(cfg.language) if cfg.language in lang_codes else 0
        lang_var = tk.StringVar(value=lang_labels[lang_idx])
        voice_var = tk.StringVar(value=cfg.voice)
        style_var = tk.StringVar(value=cfg.response_style)
        resp_var = tk.BooleanVar(value=cfg.voice_responses_enabled)
        test_msg_var = tk.StringVar(value="")

        def _row(label: str, builder):
            r = tk.Frame(form, bg="#181d26", pady=3)
            r.pack(fill=tk.X)
            tk.Label(r, text=label, fg="#cbd5e1", bg="#181d26", width=18, anchor="w").pack(side=tk.LEFT)
            builder(r).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        _row("Microphone Device", lambda p: ttk.Combobox(p, textvariable=in_var, values=in_devs, state="readonly"))
        _row("Speaker Device", lambda p: ttk.Combobox(p, textvariable=out_var, values=out_devs, state="readonly"))
        _row("Language", lambda p: ttk.Combobox(p, textvariable=lang_var, values=lang_labels, state="readonly"))
        _row("TTS Voice", lambda p: ttk.Combobox(p, textvariable=voice_var, values=voices, state="readonly"))
        _row("Response Style", lambda p: ttk.Combobox(p, textvariable=style_var, values=list(RESPONSE_STYLES), state="readonly"))

        tk.Checkbutton(
            form,
            text="Enable spoken voice responses when voice mode is turned on",
            variable=resp_var,
            bg="#181d26",
            fg="#f8fafc",
            selectcolor="#11141a",
        ).pack(anchor="w", pady=(6, 4))

        def _draft_config() -> VoiceConfig:
            sel_lang = lang_codes[lang_labels.index(lang_var.get())]
            return VoiceConfig(
                input_device=None if in_var.get() == "Default" else in_var.get(),
                output_device=None if out_var.get() == "Default" else out_var.get(),
                language=sel_lang,
                voice=voice_var.get(),
                response_style=style_var.get(),
                speech_rate_wpm=cfg.speech_rate_wpm,
                volume=cfg.volume,
                voice_responses_enabled=bool(resp_var.get()),
                stt_model=cfg.stt_model,
            )

        test_bar = tk.Frame(form, bg="#181d26", pady=6)
        test_bar.pack(fill=tk.X)

        def _ensure_perm_prompt(perm_key: str, label: str) -> bool:
            if not self.messagebox.askyesno(
                "Permission Required",
                f"Allow '{perm_key}' for this session to test {label}?",
            ):
                return False
            self.vm.grant_explicit_permission(perm_key, decision="allow", lifetime="session")
            return True

        def _test_mic():
            res = self.vm.test_microphone(config=_draft_config())
            if res.blocked_by_permission and _ensure_perm_prompt("voice.microphone", "your microphone"):
                res = self.vm.test_microphone(config=_draft_config())
            test_msg_var.set(res.message)

        def _test_spk():
            res = self.vm.test_speaker(config=_draft_config())
            if res.blocked_by_permission and _ensure_perm_prompt("voice.speaker", "your speaker"):
                res = self.vm.test_speaker(config=_draft_config())
            test_msg_var.set(res.message)

        ttk.Button(test_bar, text="Test Microphone", command=_test_mic).pack(side=tk.LEFT)
        ttk.Button(test_bar, text="Test Speaker", command=_test_spk).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(body, textvariable=test_msg_var, fg="#34d399", bg="#11141a", wraplength=840, justify="left").pack(anchor="w", pady=(6, 0))

        nav = tk.Frame(body, bg="#11141a", pady=10)
        nav.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(nav, text="Back", command=lambda: (self.vm.go_to_step("ai_engine"), self.render())).pack(side=tk.LEFT)

        def _save_and_next():
            try:
                draft = _draft_config()
                try:
                    self.vm.save_voice(draft)
                except PermissionError:
                    if not _ensure_perm_prompt("voice.config", "saving voice settings"):
                        return
                    if (draft.input_device is not None or draft.output_device is not None):
                        try:
                            self.vm.save_voice(draft)
                        except PermissionError:
                            if not _ensure_perm_prompt("voice.device", "selecting audio devices"):
                                return
                            self.vm.save_voice(draft)
                    else:
                        self.vm.save_voice(draft)
                self.vm.go_to_step("permissions")
                self.render()
            except Exception as exc:
                test_msg_var.set(f"Save failed: {exc}")

        ttk.Button(
            nav,
            text="Skip Voice Setup",
            command=lambda: (self.vm.skip_voice(), self.vm.go_to_step("permissions"), self.render()),
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(nav, text="Save & Continue", command=_save_and_next).pack(side=tk.RIGHT)

    def _render_step_permissions(self) -> None:
        tk = self.tk
        ttk = self.ttk
        body = tk.Frame(self._container, bg="#11141a", padx=24, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        box = tk.Text(
            body,
            bg="#0d1015",
            fg="#e2e8f0",
            font=("Helvetica", 10),
            relief=tk.FLAT,
            wrap=tk.WORD,
            padx=12,
            pady=10,
            height=13,
        )
        box.pack(fill=tk.X, pady=(0, 10))
        box.insert(tk.END, self.vm.coordinator.permission_guide_text() + "\n\n")
        box.insert(tk.END, "Registered Sensitive Capabilities (never auto-granted):\n")
        for spec in PERMISSION_SPECS.values():
            tier = (
                "protected-default-deny"
                if spec.protected_by_default
                else ("sensitive-confirm" if spec.sensitive else "standard")
            )
            box.insert(
                tk.END,
                f"  • {spec.permission_key} ({tier}) — {spec.description}\n",
            )
        box.configure(state=tk.DISABLED)

        # Optional per-capability explicit rule grant (persistent checkbox NEVER preselected)
        opt_card = tk.Frame(body, bg="#181d26", padx=14, pady=10)
        opt_card.pack(fill=tk.X)
        tk.Label(
            opt_card,
            text="Optional: Pre-configure a permission now (all capabilities start ungranted by default)",
            fg="#cbd5e1",
            bg="#181d26",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w")

        ctrl_row = tk.Frame(opt_card, bg="#181d26", pady=6)
        ctrl_row.pack(fill=tk.X)
        keys = [s.permission_key for s in PERMISSION_SPECS.values()]
        key_var = tk.StringVar(value=keys[0] if keys else "")
        dec_var = tk.StringVar(value="confirm")
        life_var = tk.StringVar(value="session")
        persist_ack_var = tk.BooleanVar(value=False)  # Security: MUST start unchecked
        perm_msg = tk.StringVar(value="")

        ttk.Combobox(ctrl_row, textvariable=key_var, values=keys, state="readonly", width=26).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Combobox(ctrl_row, textvariable=dec_var, values=["allow", "confirm", "deny"], state="readonly", width=10).pack(side=tk.LEFT, padx=6)
        ttk.Combobox(ctrl_row, textvariable=life_var, values=["one_time", "session", "time_limited", "persistent"], state="readonly", width=14).pack(side=tk.LEFT, padx=6)

        tk.Checkbutton(
            opt_card,
            text="I explicitly confirm saving a persistent rule across restarts (required for persistent)",
            variable=persist_ack_var,
            bg="#181d26",
            fg="#fbbf24",
            selectcolor="#11141a",
        ).pack(anchor="w")

        def _apply_perm():
            try:
                self.vm.grant_explicit_permission(
                    key_var.get(),
                    decision=dec_var.get(),
                    lifetime=life_var.get(),
                    explicit_persistent_ack=bool(persist_ack_var.get()),
                )
                persist_ack_var.set(False)
                perm_msg.set(f"Saved {dec_var.get()} ({life_var.get()}) for {key_var.get()}.")
            except Exception as exc:
                perm_msg.set(f"Blocked: {exc}")

        ttk.Button(ctrl_row, text="Apply Rule", command=_apply_perm).pack(side=tk.RIGHT)
        tk.Label(opt_card, textvariable=perm_msg, fg="#34d399", bg="#181d26").pack(anchor="w")

        nav = tk.Frame(body, bg="#11141a", pady=10)
        nav.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(nav, text="Back", command=lambda: (self.vm.go_to_step("voice"), self.render())).pack(side=tk.LEFT)
        ttk.Button(
            nav,
            text="I Understand — Run Readiness Checks",
            command=lambda: (self.vm.acknowledge_permissions(), self.vm.go_to_step("diagnostics"), self.render()),
        ).pack(side=tk.RIGHT)

    def _render_step_diagnostics(self) -> None:
        tk = self.tk
        ttk = self.ttk
        report = self.vm.run_diagnostics()

        body = tk.Frame(self._container, bg="#11141a", padx=24, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        box = tk.Text(
            body,
            bg="#0d1015",
            fg="#e2e8f0",
            font=("Helvetica", 10),
            relief=tk.FLAT,
            wrap=tk.WORD,
            padx=12,
            pady=10,
        )
        box.pack(fill=tk.BOTH, expand=True, pady=(0, 12))
        box.tag_configure("passed", foreground="#34d399")
        box.tag_configure("warning", foreground="#fbbf24")
        box.tag_configure("failed", foreground="#f87171")
        box.tag_configure("not_configured", foreground="#94a3b8")

        for chk in report.checks:
            box.insert(
                tk.END,
                f"[{chk.status.value.upper()}] {chk.name} ({chk.category}): {chk.summary}\n",
                (chk.status.value,),
            )
            box.insert(tk.END, f"    {chk.detail}\n")
            if chk.recovery_action:
                box.insert(tk.END, f"    Action: {chk.recovery_action}\n")
        if report.degraded_capabilities:
            box.insert(
                tk.END,
                f"\nDegraded Optional Capabilities: {', '.join(report.degraded_capabilities)}\n",
                ("warning",),
            )
        box.configure(state=tk.DISABLED)

        err_var = tk.StringVar(value="")
        tk.Label(body, textvariable=err_var, fg="#f87171", bg="#11141a").pack(anchor="w")

        nav = tk.Frame(body, bg="#11141a", pady=10)
        nav.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(nav, text="Back", command=lambda: (self.vm.go_to_step("permissions"), self.render())).pack(side=tk.LEFT)
        ttk.Button(nav, text="Re-run Checks", command=self.render).pack(side=tk.LEFT, padx=(8, 0))

        def _finish():
            try:
                self.vm.finish_onboarding()
                self.result = "launch"
                self.root.quit()
            except Exception as exc:
                err_var.set(str(exc))

        finish_btn = ttk.Button(nav, text="Finish & Launch RIYA", command=_finish)
        if not report.mandatory_passed:
            finish_btn.configure(state=self.tk.DISABLED)
            err_var.set("Resolve mandatory FAILED or NOT_CONFIGURED checks (such as Account) before finishing.")
        finish_btn.pack(side=tk.RIGHT)


def launch_desktop_app(
    coordinator: OnboardingCoordinator | None = None,
) -> None:
    """Launch RIYA's complete desktop flow (Onboarding/Recovery/Login -> Chat & Voice UI)."""
    import tkinter as tk
    from core.voice.ui import launch_voice_gui

    coord = coordinator or OnboardingCoordinator()
    root = tk.Tk()
    try:
        while True:
            for child in root.winfo_children():
                child.destroy()

            vm = OnboardingViewModel(coord)
            wizard = _DesktopOnboardingWindow(root, vm)
            outcome = wizard.run()
            if outcome != "launch" or coord.active_identity is None:
                break

            assistant, orchestrator = coord.build_runtime_session()
            for child in root.winfo_children():
                child.destroy()

            from core.desktop.app import launch_desktop

            exit_reason = launch_desktop(
                orchestrator,
                coordinator=coord,
                on_logout=coord.logout_current_user,
                root_window=root,
            )
            try:
                assistant.close()
            except Exception:
                pass
            if exit_reason != "logout":
                break
    finally:
        try:
            root.destroy()
        except Exception:
            pass
