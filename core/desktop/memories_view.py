"""Modern memory explorer and management panel for RIYA Desktop."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from core.desktop.theme import FontSystem, ThemePalette
from core.voice.ui import VoiceUIController


class MemoriesView(tk.Frame):
    """View, search, add, and forget persistent user memories with account isolation."""

    CATEGORIES = (
        "all",
        "general",
        "user_profile",
        "preferences",
        "personal_instructions",
        "important_facts",
        "work",
    )

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
        self._items: list[dict[str, Any]] = []

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        th = self._theme

        # --- Top Header ---
        header = tk.Frame(self, bg=th.bg_surface, padx=20, pady=14)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="🧠  Memory & Personalization",
            font=FontSystem.SECTION_HEADER,
            fg=th.text_primary,
            bg=th.bg_surface,
        ).pack(anchor="w")

        tk.Label(
            header,
            text="RIYA remembers facts and preferences locally to personalize your experience without cloud telemetry.",
            font=FontSystem.CAPTION,
            fg=th.text_muted,
            bg=th.bg_surface,
        ).pack(anchor="w", pady=(2, 0))

        # Divider
        tk.Frame(self, bg=th.border, height=1).pack(fill=tk.X)

        # --- Privacy Assurance Banner ---
        priv_banner = tk.Frame(self, bg=th.status_info_bg, padx=16, pady=8)
        priv_banner.pack(fill=tk.X, padx=16, pady=(12, 4))
        tk.Label(
            priv_banner,
            text="🔒 100% Offline & Isolated: Memories are stored exclusively in your local database and never shared across user profiles.",
            font=FontSystem.CAPTION_BOLD,
            fg=th.status_info_fg,
            bg=th.status_info_bg,
        ).pack(anchor="w")

        # --- Search & Filter Bar ---
        ctrl_bar = tk.Frame(self, bg=th.bg_base, padx=16, pady=8)
        ctrl_bar.pack(fill=tk.X)

        tk.Label(ctrl_bar, text="Search:", font=FontSystem.BODY_BOLD, fg=th.text_primary, bg=th.bg_base).pack(side=tk.LEFT, padx=(0, 6))

        self._search_var = tk.StringVar()
        self._search_entry = tk.Entry(
            ctrl_bar,
            textvariable=self._search_var,
            font=FontSystem.BODY,
            bg=th.bg_input,
            fg=th.text_primary,
            insertbackground=th.text_primary,
            relief=tk.SOLID,
            bd=1,
            highlightthickness=1,
            highlightcolor=th.accent,
            highlightbackground=th.border,
            width=28,
        )
        self._search_entry.pack(side=tk.LEFT, ipady=4, padx=(0, 10))
        self._search_entry.bind("<KeyRelease>", lambda _e: self._on_search())

        tk.Label(ctrl_bar, text="Category:", font=FontSystem.BODY, fg=th.text_secondary, bg=th.bg_base).pack(side=tk.LEFT, padx=(6, 4))
        self._cat_var = tk.StringVar(value="all")
        self._cat_cb = ttk.Combobox(
            ctrl_bar,
            textvariable=self._cat_var,
            values=self.CATEGORIES,
            state="readonly",
            width=14,
        )
        self._cat_cb.pack(side=tk.LEFT, padx=(0, 12))
        self._cat_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_search())

        # Action: Add Memory Button
        add_btn = tk.Button(
            ctrl_bar,
            text="+ Remember Fact",
            font=FontSystem.CAPTION_BOLD,
            bg=th.accent,
            fg="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._on_add_dialog,
        )
        add_btn.pack(side=tk.RIGHT)

        # --- Memories Table / List ---
        table_frame = tk.Frame(self, bg=th.bg_surface, padx=1, pady=1)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=6)

        self._tree = ttk.Treeview(
            table_frame,
            columns=("category", "key", "value", "created_at"),
            show="headings",
            selectmode="browse",
        )
        self._tree.heading("category", text="Category")
        self._tree.heading("key", text="Key / Topic")
        self._tree.heading("value", text="Memory Content")
        self._tree.heading("created_at", text="Saved Date")

        self._tree.column("category", width=120, anchor="w")
        self._tree.column("key", width=160, anchor="w")
        self._tree.column("value", width=340, anchor="w")
        self._tree.column("created_at", width=120, anchor="w")

        tree_scroll = tk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._on_select_item)

        # --- Selected Item Details & Actions Footer ---
        footer = tk.Frame(self, bg=th.bg_surface, padx=16, pady=12)
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        self._detail_label = tk.Label(
            footer,
            text="Select a memory item above to review details or delete.",
            font=FontSystem.CAPTION,
            fg=th.text_secondary,
            bg=th.bg_surface,
            anchor="w",
        )
        self._detail_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._forget_btn = tk.Button(
            footer,
            text="Forget Selected Memory",
            font=FontSystem.CAPTION_BOLD,
            bg=th.bg_surface_elevated,
            fg=th.status_error_fg,
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            state=tk.DISABLED,
            command=self._on_forget_selected,
        )
        self._forget_btn.pack(side=tk.RIGHT)

    def refresh(self) -> None:
        """Fetch memories from the controller and populate the table."""
        try:
            res = self._controller.list_memories()
            self._items = list(res) if isinstance(res, (list, tuple)) else []
        except Exception:
            self._items = []
        self._populate_tree(self._items)

    def _populate_tree(self, items: list[dict[str, Any]]) -> None:
        self._tree.delete(*self._tree.get_children())
        for item in items:
            cat = str(item.get("category", "general"))
            key = str(item.get("key", ""))
            val = str(item.get("value", ""))
            dt = str(item.get("created_at", ""))[:19]
            self._tree.insert("", tk.END, values=(cat, key, val, dt))

        count = len(items)
        self._detail_label.configure(
            text=f"Total memories stored: {count}. Select an item to manage."
        )
        self._forget_btn.configure(state=tk.DISABLED)

    def _on_search(self) -> None:
        query = self._search_var.get().strip().lower()
        cat = self._cat_var.get()
        filtered = []
        for it in self._items:
            item_cat = str(it.get("category", "")).lower()
            if cat != "all" and item_cat != cat.lower():
                continue
            if query:
                k = str(it.get("key", "")).lower()
                v = str(it.get("value", "")).lower()
                if query not in k and query not in v and query not in item_cat:
                    continue
            filtered.append(it)
        self._populate_tree(filtered)

    def _on_select_item(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            self._forget_btn.configure(state=tk.DISABLED)
            return
        vals = self._tree.item(sel[0], "values")
        if vals:
            self._detail_label.configure(
                text=f"[{vals[0]}] {vals[1]} = '{vals[2]}'"
            )
            self._forget_btn.configure(state=tk.NORMAL)

    def _on_forget_selected(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        vals = self._tree.item(sel[0], "values")
        if not vals:
            return

        cat, key = vals[0], vals[1]
        confirm = messagebox.askyesno(
            "Confirm Forget",
            f"Are you sure you want RIYA to forget:\n\n[{cat}] {key}?",
            parent=self,
        )
        if confirm:
            try:
                self._controller.forget_fact(cat, key)
                messagebox.showinfo("Memory Removed", f"Successfully forgot '{key}'.", parent=self)
                self.refresh()
            except Exception as exc:
                messagebox.showerror("Error", f"Failed to forget memory: {exc}", parent=self)

    def _on_add_dialog(self) -> None:
        """Modal dialog to store a new memory."""
        th = self._theme
        dlg = tk.Toplevel(self)
        dlg.title("Remember New Fact")
        dlg.geometry("440x320")
        dlg.configure(bg=th.bg_surface)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text="Remember New Fact", font=FontSystem.ITEM_TITLE, fg=th.text_primary, bg=th.bg_surface).pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(dlg, text="Store an explicit preference or fact for future recall.", font=FontSystem.CAPTION, fg=th.text_muted, bg=th.bg_surface).pack(anchor="w", padx=16, pady=(0, 12))

        form = tk.Frame(dlg, bg=th.bg_surface, padx=16)
        form.pack(fill=tk.X)

        tk.Label(form, text="Category:", font=FontSystem.CAPTION_BOLD, fg=th.text_secondary, bg=th.bg_surface).pack(anchor="w")
        cat_var = tk.StringVar(value="general")
        ttk.Combobox(form, textvariable=cat_var, values=self.CATEGORIES[1:], state="readonly").pack(fill=tk.X, pady=(2, 8))

        tk.Label(form, text="Key / Title:", font=FontSystem.CAPTION_BOLD, fg=th.text_secondary, bg=th.bg_surface).pack(anchor="w")
        key_entry = tk.Entry(form, font=FontSystem.BODY, bg=th.bg_input, fg=th.text_primary, relief=tk.SOLID, bd=1)
        key_entry.pack(fill=tk.X, ipady=4, pady=(2, 8))

        tk.Label(form, text="Memory Content (Value):", font=FontSystem.CAPTION_BOLD, fg=th.text_secondary, bg=th.bg_surface).pack(anchor="w")
        val_entry = tk.Entry(form, font=FontSystem.BODY, bg=th.bg_input, fg=th.text_primary, relief=tk.SOLID, bd=1)
        val_entry.pack(fill=tk.X, ipady=4, pady=(2, 12))

        btn_row = tk.Frame(dlg, bg=th.bg_surface, padx=16, pady=8)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)

        def _save():
            k = key_entry.get().strip()
            v = val_entry.get().strip()
            c = cat_var.get().strip()
            if not k or not v:
                messagebox.showwarning("Missing Fields", "Key and Value are required.", parent=dlg)
                return
            try:
                self._controller.remember_fact(c, k, v)
                dlg.destroy()
                self.refresh()
            except Exception as exc:
                messagebox.showerror("Error", f"Could not save memory: {exc}", parent=dlg)

        tk.Button(btn_row, text="Cancel", font=FontSystem.CAPTION, bg=th.bg_surface_elevated, fg=th.text_primary, relief=tk.FLAT, bd=0, padx=10, pady=5, command=dlg.destroy).pack(side=tk.LEFT)
        tk.Button(btn_row, text="Save Memory", font=FontSystem.CAPTION_BOLD, bg=th.accent, fg="#ffffff", relief=tk.FLAT, bd=0, padx=12, pady=5, command=_save).pack(side=tk.RIGHT)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        th = theme
        self.configure(bg=th.bg_base)
        self.refresh()

