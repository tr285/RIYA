"""Modern Autonomous Tasks, Skills & Productivity panel for RIYA Desktop."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from core.desktop.theme import FontSystem, ThemePalette
from core.voice.ui import VoiceUIController


class TasksView(tk.Frame):
    """Monitor autonomous tasks, run specialized skills, and view productivity summaries."""

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

        # --- Top Header ---
        header = tk.Frame(self, bg=th.bg_surface, padx=20, pady=14)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="⚡  Autonomous Tasks & Skills",
            font=FontSystem.SECTION_HEADER,
            fg=th.text_primary,
            bg=th.bg_surface,
        ).pack(anchor="w")

        tk.Label(
            header,
            text="Execute multi-step plans, manage background automations, and trigger verified system skills.",
            font=FontSystem.CAPTION,
            fg=th.text_muted,
            bg=th.bg_surface,
        ).pack(anchor="w", pady=(2, 0))

        tk.Frame(self, bg=th.border, height=1).pack(fill=tk.X)

        # --- Productivity Summary Card ---
        self._prod_card = tk.Frame(self, bg=th.bg_surface, padx=16, pady=12)
        self._prod_card.pack(fill=tk.X, padx=16, pady=12)

        tk.Label(
            self._prod_card,
            text="DAILY PRODUCTIVITY OVERVIEW",
            font=FontSystem.CAPTION_BOLD,
            fg=th.text_muted,
            bg=th.bg_surface,
        ).pack(anchor="w")

        self._prod_text = tk.Label(
            self._prod_card,
            text="Loading summary...",
            font=FontSystem.BODY,
            fg=th.text_primary,
            bg=th.bg_surface,
            justify="left",
            anchor="w",
        )
        self._prod_text.pack(fill=tk.X, pady=(4, 0))

        # --- Task Input Row ---
        task_input_frame = tk.Frame(self, bg=th.bg_surface, padx=16, pady=12)
        task_input_frame.pack(fill=tk.X, padx=16, pady=(0, 12))

        tk.Label(
            task_input_frame,
            text="Run an Autonomous Multi-Step Task:",
            font=FontSystem.ITEM_TITLE,
            fg=th.text_primary,
            bg=th.bg_surface,
        ).pack(anchor="w")

        input_row = tk.Frame(task_input_frame, bg=th.bg_surface)
        input_row.pack(fill=tk.X, pady=(6, 0))

        self._task_entry = tk.Entry(
            input_row,
            font=FontSystem.BODY,
            bg=th.bg_input,
            fg=th.text_primary,
            insertbackground=th.text_primary,
            relief=tk.SOLID,
            bd=1,
            highlightthickness=1,
            highlightcolor=th.accent,
            highlightbackground=th.border,
        )
        self._task_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))
        self._task_entry.bind("<Return>", lambda _e: self._on_start_task())

        self._start_task_btn = tk.Button(
            input_row,
            text="Start Task ⚡",
            font=FontSystem.CAPTION_BOLD,
            bg=th.accent,
            fg="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            cursor="hand2",
            command=self._on_start_task,
        )
        self._start_task_btn.pack(side=tk.RIGHT)

        # --- Skills and History Panes (Notebook tabs) ---
        notebook_frame = tk.Frame(self, bg=th.bg_base)
        notebook_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        self._notebook = ttk.Notebook(notebook_frame)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Available Skills
        self._skills_tab = tk.Frame(self._notebook, bg=th.bg_surface, padx=12, pady=10)
        self._notebook.add(self._skills_tab, text="  Built-in Skills  ")

        self._skills_tree = ttk.Treeview(
            self._skills_tab,
            columns=("name", "tier", "description"),
            show="headings",
            selectmode="browse",
        )
        self._skills_tree.heading("name", text="Skill Name")
        self._skills_tree.heading("tier", text="Risk Tier")
        self._skills_tree.heading("description", text="Capability & Actions")
        self._skills_tree.column("name", width=140, anchor="w")
        self._skills_tree.column("tier", width=90, anchor="w")
        self._skills_tree.column("description", width=450, anchor="w")

        skills_scroll = tk.Scrollbar(self._skills_tab, orient=tk.VERTICAL, command=self._skills_tree.yview)
        self._skills_tree.configure(yscrollcommand=skills_scroll.set)
        self._skills_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        skills_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Tab 2: Task Execution Progress & History
        self._history_tab = tk.Frame(self._notebook, bg=th.bg_surface, padx=12, pady=10)
        self._notebook.add(self._history_tab, text="  Execution History  ")

        self._hist_tree = ttk.Treeview(
            self._history_tab,
            columns=("time", "task", "status"),
            show="headings",
            selectmode="browse",
        )
        self._hist_tree.heading("time", text="Timestamp")
        self._hist_tree.heading("task", text="Task Description")
        self._hist_tree.heading("status", text="Status")
        self._hist_tree.column("time", width=120, anchor="w")
        self._hist_tree.column("task", width=460, anchor="w")
        self._hist_tree.column("status", width=100, anchor="w")

        hist_scroll = tk.Scrollbar(self._history_tab, orient=tk.VERTICAL, command=self._hist_tree.yview)
        self._hist_tree.configure(yscrollcommand=hist_scroll.set)
        self._hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hist_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Footer Actions
        footer = tk.Frame(self, bg=th.bg_surface, padx=16, pady=8)
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        self._cancel_btn = tk.Button(
            footer,
            text="Cancel Active Task",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.status_error_fg,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self._on_cancel,
        )
        self._cancel_btn.pack(side=tk.LEFT)

        refresh_btn = tk.Button(
            footer,
            text="Refresh Status",
            font=FontSystem.CAPTION,
            bg=th.bg_surface_elevated,
            fg=th.text_primary,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.refresh,
        )
        refresh_btn.pack(side=tk.RIGHT)

    def refresh(self) -> None:
        """Fetch latest skills, tasks, and productivity details."""
        try:
            prod = self._controller.get_productivity_summary()
            tasks_cnt = prod.get("pending_tasks_count", 0)
            remind_cnt = prod.get("active_reminders_count", 0)
            plan = prod.get("daily_plan_summary") or "No plan active."
            self._prod_text.configure(
                text=f"Tasks: {tasks_cnt} pending  |  Reminders: {remind_cnt} active\nPlan: {plan}"
            )
        except Exception:
            self._prod_text.configure(text="Productivity engine: Idle")

        # Refresh skills
        try:
            skills = self._controller.list_skills()
            self._skills_tree.delete(*self._skills_tree.get_children())
            for s in skills:
                name = str(s.get("name", ""))
                tier = str(s.get("risk_tier", "low")).upper()
                desc = str(s.get("description", ""))
                self._skills_tree.insert("", tk.END, values=(name, tier, desc))
        except Exception:
            pass

        # Refresh history
        try:
            hist = self._controller.task_progress_history()
            self._hist_tree.delete(*self._hist_tree.get_children())
            for h in hist[-25:]:
                ts = str(h.get("timestamp", ""))[:19]
                desc = str(h.get("step_description", ""))
                st = str(h.get("status", "")).upper()
                self._hist_tree.insert("", tk.END, values=(ts, desc, st))
        except Exception:
            pass

    def _on_start_task(self) -> None:
        req = self._task_entry.get().strip()
        if not req:
            return
        self._task_entry.delete(0, tk.END)
        self._start_task_btn.configure(state=tk.DISABLED)

        def _worker():
            try:
                self._controller.submit_task(req)
            finally:
                try:
                    self._start_task_btn.configure(state=tk.NORMAL)
                    self.refresh()
                except Exception:
                    pass

        threading.Thread(target=_worker, name="riya-task-runner", daemon=True).start()

    def _on_cancel(self) -> None:
        self._controller.cancel_task()
        self.refresh()

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.configure(bg=theme.bg_base)
        self.refresh()

