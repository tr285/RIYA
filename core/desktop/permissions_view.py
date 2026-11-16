"""Permissions, Trust & Audit Center view for RIYA Desktop."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from core.desktop.theme import FontSystem, ThemePalette
from core.security.permissions import PERMISSION_SPECS
from core.voice.ui import VoiceUIController


class PermissionsView(tk.Frame):
    """Trust and authorization management: view capabilities, grant/deny rules, and review audit history."""

    def __init__(
        self,
        master: Any,
        controller: VoiceUIController,
        theme: ThemePalette,
        **kwargs: Any,
    ):
        super().__init__(master, bg=theme.bg_base, **kwargs)
        self._controller = controller
        self._theme = theme

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        th = self._theme

        # Header
        header = tk.Frame(self, bg=th.bg_surface, padx=20, pady=14)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="🛡️  Permissions, Trust & Security Audit",
            font=FontSystem.SECTION_HEADER,
            fg=th.text_primary,
            bg=th.bg_surface,
        ).pack(anchor="w")

        tk.Label(
            header,
            text="Control how RIYA asks before acting. High-impact operations default to explicit confirmation.",
            font=FontSystem.CAPTION,
            fg=th.text_muted,
            bg=th.bg_surface,
        ).pack(anchor="w", pady=(2, 0))

        tk.Frame(self, bg=th.border, height=1).pack(fill=tk.X)

        # Pre-configure Rule Card
        opt_card = tk.Frame(self, bg=th.bg_surface, padx=16, pady=12)
        opt_card.pack(fill=tk.X, padx=16, pady=12)

        tk.Label(
            opt_card,
            text="Configure a Capability Rule:",
            font=FontSystem.ITEM_TITLE,
            fg=th.text_primary,
            bg=th.bg_surface,
        ).pack(anchor="w")

        ctrl_row = tk.Frame(opt_card, bg=th.bg_surface, pady=6)
        ctrl_row.pack(fill=tk.X)

        keys = [s.permission_key for s in PERMISSION_SPECS.values()]
        self._key_var = tk.StringVar(value=keys[0] if keys else "")
        self._dec_var = tk.StringVar(value="confirm")
        self._life_var = tk.StringVar(value="session")
        self._persist_ack_var = tk.BooleanVar(value=False)

        ttk.Combobox(ctrl_row, textvariable=self._key_var, values=keys, state="readonly", width=24).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Combobox(ctrl_row, textvariable=self._dec_var, values=["allow", "confirm", "deny"], state="readonly", width=10).pack(side=tk.LEFT, padx=6)
        ttk.Combobox(ctrl_row, textvariable=self._life_var, values=["one_time", "session", "time_limited", "persistent"], state="readonly", width=14).pack(side=tk.LEFT, padx=6)

        apply_btn = tk.Button(
            ctrl_row,
            text="Save Rule",
            font=FontSystem.CAPTION_BOLD,
            bg=th.accent,
            fg="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._on_apply_rule,
        )
        apply_btn.pack(side=tk.RIGHT)

        tk.Checkbutton(
            opt_card,
            text="I explicitly confirm saving a persistent rule across restarts (required for 'persistent')",
            variable=self._persist_ack_var,
            bg=th.bg_surface,
            fg=th.status_warning_fg,
            selectcolor=th.bg_base,
            font=FontSystem.CAPTION,
        ).pack(anchor="w", pady=(4, 0))

        # Notebook: Active Grants vs Audit Log
        nb_frame = tk.Frame(self, bg=th.bg_base)
        nb_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        self._nb = ttk.Notebook(nb_frame)
        self._nb.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Active Grants
        self._grants_tab = tk.Frame(self._nb, bg=th.bg_surface, padx=12, pady=10)
        self._nb.add(self._grants_tab, text="  Active Permission Rules  ")

        self._grants_tree = ttk.Treeview(
            self._grants_tab,
            columns=("cap", "decision", "lifetime", "reason"),
            show="headings",
            selectmode="browse",
        )
        self._grants_tree.heading("cap", text="Capability")
        self._grants_tree.heading("decision", text="Decision")
        self._grants_tree.heading("lifetime", text="Lifetime")
        self._grants_tree.heading("reason", text="Reason / Scope")
        self._grants_tree.column("cap", width=160)
        self._grants_tree.column("decision", width=90)
        self._grants_tree.column("lifetime", width=100)
        self._grants_tree.column("reason", width=340)

        grants_scroll = tk.Scrollbar(self._grants_tab, orient=tk.VERTICAL, command=self._grants_tree.yview)
        self._grants_tree.configure(yscrollcommand=grants_scroll.set)
        self._grants_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        grants_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Tab 2: Security Audit History
        self._audit_tab = tk.Frame(self._nb, bg=th.bg_surface, padx=12, pady=10)
        self._nb.add(self._audit_tab, text="  Security Audit Log  ")

        self._audit_tree = ttk.Treeview(
            self._audit_tab,
            columns=("time", "cap", "decision", "details"),
            show="headings",
            selectmode="browse",
        )
        self._audit_tree.heading("time", text="Timestamp")
        self._audit_tree.heading("cap", text="Capability")
        self._audit_tree.heading("decision", text="Outcome")
        self._audit_tree.heading("details", text="Evaluation Context")
        self._audit_tree.column("time", width=130)
        self._audit_tree.column("cap", width=140)
        self._audit_tree.column("decision", width=90)
        self._audit_tree.column("details", width=330)

        audit_scroll = tk.Scrollbar(self._audit_tab, orient=tk.VERTICAL, command=self._audit_tree.yview)
        self._audit_tree.configure(yscrollcommand=audit_scroll.set)
        self._audit_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        audit_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh(self) -> None:
        """Populate permissions table and audit records."""
        # Refresh grants
        try:
            grants = self._controller.list_permissions()
            self._grants_tree.delete(*self._grants_tree.get_children())
            for g in grants:
                self._grants_tree.insert(
                    "",
                    tk.END,
                    values=(
                        str(g.get("permission_key", "")),
                        str(g.get("decision", "")).upper(),
                        str(g.get("lifetime", "")),
                        str(g.get("reason", "")),
                    ),
                )
        except Exception:
            pass

        # Refresh audit
        try:
            logs = self._controller.audit_history(limit=50)
            self._audit_tree.delete(*self._audit_tree.get_children())
            for l in logs:
                ts = str(getattr(l, "timestamp", ""))[:19]
                cap = str(getattr(l, "permission_key", ""))
                dec = str(getattr(l, "decision", "")).upper()
                det = str(getattr(l, "reason", ""))
                self._audit_tree.insert("", tk.END, values=(ts, cap, dec, det))
        except Exception:
            pass

    def _on_apply_rule(self) -> None:
        key = self._key_var.get()
        dec = self._dec_var.get()
        life = self._life_var.get()
        ack = self._persist_ack_var.get()

        if life == "persistent" and not ack:
            messagebox.showwarning(
                "Persistent Rule Confirmation",
                "Saving a rule that persists across restarts requires checking the explicit confirmation box.",
                parent=self,
            )
            return

        try:
            self._controller.grant_permission(
                key,
                decision=dec,
                lifetime=life,
                explicit_persistent_ack=ack,
            )
            self._persist_ack_var.set(False)
            messagebox.showinfo("Rule Saved", f"Successfully saved rule for '{key}'.", parent=self)
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Error", f"Could not save rule: {exc}", parent=self)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.configure(bg=theme.bg_base)
        self.refresh()

