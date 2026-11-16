"""Collapsible modern macOS sidebar with navigation, session history, and user profile."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

from core.config.settings import APP_NAME, APP_VERSION
from core.desktop.theme import FontSystem, ThemePalette


class SidebarView(tk.Frame):
    """Modern macOS navigation sidebar with active item highlighting and theme support."""

    NAV_ITEMS = (
        ("chat", "💬  Conversation"),
        ("memories", "🧠  Memories"),
        ("tasks", "⚡  Skills & Tasks"),
        ("permissions", "🛡️  Permissions & Trust"),
        ("diagnostics", "📊  System Status"),
        ("settings", "⚙️  Preferences"),
    )

    def __init__(
        self,
        master: Any,
        theme: ThemePalette,
        on_nav_change: Callable[[str], None],
        on_new_chat: Callable[[], None],
        on_logout: Callable[[], None],
        current_user_fn: Callable[[], str],
        **kwargs: Any,
    ):
        super().__init__(master, bg=theme.bg_sidebar, width=240, **kwargs)
        self.pack_propagate(False)

        self._theme = theme
        self._on_nav_change = on_nav_change
        self._on_new_chat = on_new_chat
        self._on_logout = on_logout
        self._current_user_fn = current_user_fn
        self._active_tab = "chat"

        self._nav_buttons: dict[str, tk.Button] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        th = self._theme

        # --- Top Header: App Branding ---
        header = tk.Frame(self, bg=th.bg_sidebar, padx=16, pady=16)
        header.pack(fill=tk.X)

        title_row = tk.Frame(header, bg=th.bg_sidebar)
        title_row.pack(fill=tk.X)

        tk.Label(
            title_row,
            text=APP_NAME,
            font=FontSystem.APP_TITLE,
            fg=th.text_primary,
            bg=th.bg_sidebar,
        ).pack(side=tk.LEFT)

        tk.Label(
            title_row,
            text=f"v{APP_VERSION}",
            font=FontSystem.CAPTION_BOLD,
            fg=th.accent,
            bg=th.bg_surface_elevated,
            padx=6,
            pady=2,
        ).pack(side=tk.RIGHT)

        tk.Label(
            header,
            text="Universal Personal AI Assistant",
            font=FontSystem.CAPTION,
            fg=th.text_muted,
            bg=th.bg_sidebar,
            anchor="w",
        ).pack(fill=tk.X, pady=(2, 12))

        # --- Action Button: New Conversation ---
        self._new_chat_btn = tk.Button(
            header,
            text="+  New Conversation",
            font=FontSystem.ITEM_TITLE,
            bg=th.accent,
            fg="#ffffff",
            activebackground=th.accent_hover,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            command=self._on_new_chat,
        )
        self._new_chat_btn.pack(fill=tk.X)

        # --- Divider ---
        tk.Frame(self, bg=th.border_subtle, height=1).pack(fill=tk.X, padx=12, pady=8)

        # --- Primary Navigation List ---
        nav_container = tk.Frame(self, bg=th.bg_sidebar, padx=10)
        nav_container.pack(fill=tk.X)

        for tab_id, label in self.NAV_ITEMS:
            btn = tk.Button(
                nav_container,
                text=label,
                font=FontSystem.BODY,
                anchor="w",
                relief=tk.FLAT,
                bd=0,
                padx=10,
                pady=7,
                cursor="hand2",
                command=lambda t=tab_id: self.select_tab(t),
            )
            btn.pack(fill=tk.X, pady=2)
            self._nav_buttons[tab_id] = btn

        # --- Divider ---
        tk.Frame(self, bg=th.border_subtle, height=1).pack(fill=tk.X, padx=12, pady=10)

        # --- Middle Scrollable Container: Recent Turns / Quick History ---
        recent_header = tk.Frame(self, bg=th.bg_sidebar, padx=16)
        recent_header.pack(fill=tk.X)
        tk.Label(
            recent_header,
            text="RECENT SESSIONS",
            font=FontSystem.CAPTION_BOLD,
            fg=th.text_muted,
            bg=th.bg_sidebar,
            anchor="w",
        ).pack(fill=tk.X)

        self._recent_box = tk.Listbox(
            self,
            bg=th.bg_sidebar,
            fg=th.text_secondary,
            selectbackground=th.bg_surface_elevated,
            selectforeground=th.text_primary,
            font=FontSystem.CAPTION,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            activestyle="none",
        )
        self._recent_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        # --- Bottom User Profile Card ---
        self._profile_card = tk.Frame(self, bg=th.bg_surface_elevated, padx=12, pady=10)
        self._profile_card.pack(fill=tk.X, side=tk.BOTTOM, padx=8, pady=8)

        prof_top = tk.Frame(self._profile_card, bg=th.bg_surface_elevated)
        prof_top.pack(fill=tk.X)

        self._user_label = tk.Label(
            prof_top,
            text=self._current_user_fn(),
            font=FontSystem.ITEM_TITLE,
            fg=th.text_primary,
            bg=th.bg_surface_elevated,
            anchor="w",
        )
        self._user_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        logout_btn = tk.Button(
            prof_top,
            text="Sign Out",
            font=FontSystem.CAPTION,
            bg=th.bg_card,
            fg=th.status_error_fg,
            activebackground=th.status_error_bg,
            activeforeground=th.status_error_fg,
            relief=tk.FLAT,
            bd=0,
            padx=6,
            pady=2,
            cursor="hand2",
            command=self._on_logout,
        )
        logout_btn.pack(side=tk.RIGHT)

        status_row = tk.Frame(self._profile_card, bg=th.bg_surface_elevated)
        status_row.pack(fill=tk.X, pady=(4, 0))

        # Green indicator dot + offline-first assurance
        tk.Label(
            status_row,
            text="●",
            font=("Helvetica", 9),
            fg=th.status_success_fg,
            bg=th.bg_surface_elevated,
        ).pack(side=tk.LEFT)

        tk.Label(
            status_row,
            text=" Offline-First (Local Private)",
            font=FontSystem.CAPTION,
            fg=th.text_muted,
            bg=th.bg_surface_elevated,
        ).pack(side=tk.LEFT)

        self._refresh_styles()

    def select_tab(self, tab_id: str, notify: bool = True) -> None:
        self._active_tab = tab_id
        self._refresh_styles()
        if notify:
            self._on_nav_change(tab_id)

    def update_history_list(self, items: list[str]) -> None:
        """Populate the recent turns list."""
        self._recent_box.delete(0, tk.END)
        for item in items[:15]:
            snippet = item if len(item) <= 28 else item[:25] + "..."
            self._recent_box.insert(tk.END, f"  💬 {snippet}")

    def update_user_label(self, label: str) -> None:
        self._user_label.configure(text=label)

    def apply_theme(self, theme: ThemePalette) -> None:
        """Update colors dynamically when theme changes."""
        self._theme = theme
        th = theme
        self.configure(bg=th.bg_sidebar)

        for child in self.winfo_children():
            self._retheme_widget(child, th)

        self._new_chat_btn.configure(
            bg=th.accent,
            fg="#ffffff",
            activebackground=th.accent_hover,
        )
        self._recent_box.configure(
            bg=th.bg_sidebar,
            fg=th.text_secondary,
            selectbackground=th.bg_surface_elevated,
            selectforeground=th.text_primary,
        )
        self._profile_card.configure(bg=th.bg_surface_elevated)
        self._refresh_styles()

    def _retheme_widget(self, widget: Any, th: ThemePalette) -> None:
        try:
            widget.configure(bg=th.bg_sidebar)
        except Exception:
            pass
        for sub in getattr(widget, "winfo_children", lambda: [])():
            self._retheme_widget(sub, th)

    def _refresh_styles(self) -> None:
        th = self._theme
        for tab_id, btn in self._nav_buttons.items():
            if tab_id == self._active_tab:
                btn.configure(
                    bg=th.bg_surface_elevated,
                    fg=th.accent,
                    activebackground=th.bg_surface_elevated,
                    activeforeground=th.accent,
                    font=FontSystem.BODY_BOLD,
                )
            else:
                btn.configure(
                    bg=th.bg_sidebar,
                    fg=th.text_secondary,
                    activebackground=th.bg_surface_elevated,
                    activeforeground=th.text_primary,
                    font=FontSystem.BODY,
                )

