"""Modern native macOS Desktop Application window orchestrator for RIYA."""

from __future__ import annotations

import queue
import sys
import tkinter as tk
from typing import Any, Callable

from core.config.settings import APP_NAME, APP_VERSION
from core.desktop.chat_view import ChatView
from core.desktop.diagnostics_view import DiagnosticsView
from core.desktop.memories_view import MemoriesView
from core.desktop.permissions_view import PermissionsView
from core.desktop.settings_view import SettingsView
from core.desktop.sidebar import SidebarView
from core.desktop.theme import DARK_THEME, LIGHT_THEME, ThemeManager, ThemePalette
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.ui import VoiceUIController


class RIYADesktopApp:
    """Master native macOS desktop application for RIYA."""

    def __init__(
        self,
        orchestrator: VoiceOrchestrator,
        *,
        coordinator: Any | None = None,
        on_logout: Callable[[], None] | None = None,
        root_window: tk.Tk | None = None,
        initial_theme: str = "dark",
    ):
        self.orchestrator = orchestrator
        self.coordinator = coordinator
        self.on_logout = on_logout
        self.controller = VoiceUIController(orchestrator)

        self.theme_manager = ThemeManager("dark" if initial_theme == "dark" else "light")
        self.theme_manager.add_listener(self._on_theme_changed)

        self._owns_root = root_window is None
        self.root = root_window or tk.Tk()
        self.exit_action = {"reason": "closed"}

        self._ui_events: queue.Queue[str] = queue.Queue()
        self._observer_cb = lambda: self._ui_events.put("refresh")
        self.orchestrator.add_observer(self._observer_cb)

        self._current_view_name = "chat"
        self._views: dict[str, tk.Frame] = {}

        self._setup_window()
        self._setup_menus()
        self._setup_keybindings()
        self._build_layout()
        self._start_event_loop()

    def _setup_window(self) -> None:
        th = self.theme_manager.current
        self.root.title(f"{APP_NAME} v{APP_VERSION} — Universal Personal AI Assistant")
        self.root.geometry("1120x780")
        self.root.minsize(920, 640)
        self.root.configure(bg=th.bg_base)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_menus(self) -> None:
        """Create macOS-native menu bar."""
        menubar = tk.Menu(self.root)

        # 1. Application Menu (RIYA)
        app_menu = tk.Menu(menubar, tearoff=0)
        app_menu.add_command(label=f"About {APP_NAME}", command=lambda: self.switch_view("settings"))
        app_menu.add_separator()
        app_menu.add_command(label="Preferences...", accelerator="Cmd+,", command=lambda: self.switch_view("settings"))
        app_menu.add_separator()
        app_menu.add_command(label="Sign Out", command=self._handle_logout)
        app_menu.add_command(label=f"Quit {APP_NAME}", accelerator="Cmd+Q", command=self.on_close)
        menubar.add_cascade(label=APP_NAME, menu=app_menu)

        # 2. File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New Conversation", accelerator="Cmd+N", command=self._handle_new_chat)
        file_menu.add_command(label="Clear History", accelerator="Cmd+K", command=lambda: self._chat_view._on_clear_transcript())
        file_menu.add_separator()
        file_menu.add_command(label="Close Window", accelerator="Cmd+W", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        # 3. View Menu
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Conversation", accelerator="Cmd+1", command=lambda: self.switch_view("chat"))
        view_menu.add_command(label="Memories", accelerator="Cmd+2", command=lambda: self.switch_view("memories"))
        view_menu.add_command(label="Skills & Tasks", accelerator="Cmd+3", command=lambda: self.switch_view("tasks"))
        view_menu.add_command(label="Permissions", accelerator="Cmd+4", command=lambda: self.switch_view("permissions"))
        view_menu.add_command(label="System Status", accelerator="Cmd+5", command=lambda: self.switch_view("diagnostics"))
        view_menu.add_separator()
        view_menu.add_command(label="Toggle Theme", accelerator="Cmd+T", command=self._toggle_theme)
        menubar.add_cascade(label="View", menu=view_menu)

        # 4. Voice Menu
        voice_menu = tk.Menu(menubar, tearoff=0)
        voice_menu.add_command(label="Toggle Voice Mode", accelerator="Cmd+Shift+V", command=lambda: self.controller.toggle_voice_mode())
        voice_menu.add_command(label="Pause / Resume Voice", accelerator="Cmd+Shift+P", command=lambda: self.controller.pause() if not self.orchestrator.is_paused else self.controller.resume())
        voice_menu.add_command(label="Interrupt Speech", accelerator="Cmd+.", command=lambda: self.controller.interrupt())
        menubar.add_cascade(label="Voice", menu=voice_menu)

        self.root.config(menu=menubar)

    def _setup_keybindings(self) -> None:
        """Bind native keyboard shortcuts."""
        mod = "Command" if sys.platform == "darwin" else "Control"
        self.root.bind(f"<{mod}-n>", lambda _e: self._handle_new_chat())
        self.root.bind(f"<{mod}-comma>", lambda _e: self.switch_view("settings"))
        self.root.bind(f"<{mod}-t>", lambda _e: self._toggle_theme())
        self.root.bind(f"<{mod}-q>", lambda _e: self.on_close())
        self.root.bind(f"<{mod}-w>", lambda _e: self.on_close())
        self.root.bind(f"<{mod}-period>", lambda _e: self.controller.interrupt())
        self.root.bind(f"<{mod}-1>", lambda _e: self.switch_view("chat"))
        self.root.bind(f"<{mod}-2>", lambda _e: self.switch_view("memories"))
        self.root.bind(f"<{mod}-3>", lambda _e: self.switch_view("tasks"))
        self.root.bind(f"<{mod}-4>", lambda _e: self.switch_view("permissions"))
        self.root.bind(f"<{mod}-5>", lambda _e: self.switch_view("diagnostics"))

    def _build_layout(self) -> None:
        th = self.theme_manager.current

        self._main_container = tk.Frame(self.root, bg=th.bg_base)
        self._main_container.pack(fill=tk.BOTH, expand=True)

        # Sidebar on the left
        self.sidebar = SidebarView(
            self._main_container,
            theme=th,
            on_nav_change=self.switch_view,
            on_new_chat=self._handle_new_chat,
            on_logout=self._handle_logout,
            current_user_fn=self.controller.current_user_label,
        )
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)

        # Vertical Divider
        self._sidebar_divider = tk.Frame(self._main_container, bg=th.border, width=1)
        self._sidebar_divider.pack(side=tk.LEFT, fill=tk.Y)

        # Content viewport on the right
        self._content_viewport = tk.Frame(self._main_container, bg=th.bg_base)
        self._content_viewport.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Instantiate primary views
        self._chat_view = ChatView(
            self._content_viewport,
            controller=self.controller,
            theme=th,
            on_theme_toggle=self._toggle_theme,
            on_refresh_requested=self.refresh_ui,
        )
        self._views["chat"] = self._chat_view

        self._memories_view = MemoriesView(
            self._content_viewport,
            controller=self.controller,
            theme=th,
        )
        self._views["memories"] = self._memories_view

        from core.desktop.tasks_view import TasksView
        self._tasks_view = TasksView(
            self._content_viewport,
            controller=self.controller,
            theme=th,
        )
        self._views["tasks"] = self._tasks_view

        self._permissions_view = PermissionsView(
            self._content_viewport,
            controller=self.controller,
            theme=th,
        )
        self._views["permissions"] = self._permissions_view

        self._diagnostics_view = DiagnosticsView(
            self._content_viewport,
            controller=self.controller,
            theme=th,
            coordinator=self.coordinator,
        )
        self._views["diagnostics"] = self._diagnostics_view

        self._settings_view = SettingsView(
            self._content_viewport,
            controller=self.controller,
            theme=th,
            on_theme_change=self.theme_manager.set_mode,
        )
        self._views["settings"] = self._settings_view

        # Show initial chat view
        self._chat_view.pack(fill=tk.BOTH, expand=True)

        # Populate sidebar recent history
        self._update_sidebar_history()

    def switch_view(self, view_name: str) -> None:
        """Switch the main viewport to the specified tab."""
        if view_name not in self._views:
            return

        # Hide current view
        if self._current_view_name in self._views:
            self._views[self._current_view_name].pack_forget()

        # Show target view
        target = self._views[view_name]
        target.pack(fill=tk.BOTH, expand=True)
        self._current_view_name = view_name

        # Refresh target view if applicable
        if hasattr(target, "refresh"):
            target.refresh()

        self.sidebar.select_tab(view_name, notify=False)

    def _toggle_theme(self) -> None:
        self.theme_manager.toggle()

    def _on_theme_changed(self, theme: ThemePalette) -> None:
        """Propagate theme palette updates to all child views."""
        th = theme
        self.root.configure(bg=th.bg_base)
        self._main_container.configure(bg=th.bg_base)
        self._sidebar_divider.configure(bg=th.border)
        self._content_viewport.configure(bg=th.bg_base)

        self.sidebar.apply_theme(th)
        for view in self._views.values():
            if hasattr(view, "apply_theme"):
                view.apply_theme(th)

    def _handle_new_chat(self) -> None:
        self.switch_view("chat")
        self._chat_view._on_clear_transcript()

    def _handle_logout(self) -> None:
        self.exit_action["reason"] = "logout"
        if self.on_logout:
            self.on_logout()
        self.root.quit()

    def _update_sidebar_history(self) -> None:
        try:
            hist = self.controller.conversation_history(limit=15)
            snippets = [
                h.get("user_input", "") for h in hist if h.get("user_input")
            ]
            self.sidebar.update_history_list(snippets)
        except Exception:
            pass

    def refresh_ui(self) -> None:
        """Refresh snapshots, waveforms, and conversation status."""
        try:
            snap = self.controller.snapshot()
            if self._current_view_name == "chat":
                self._chat_view.render_snapshot(snap)
            self.sidebar.update_user_label(self.controller.current_user_label())
        except Exception:
            pass

    def _start_event_loop(self) -> None:
        """Periodic 50ms tick to animate audio visualizer and process orchestrator events."""
        def _tick():
            # Check for queued background events
            while not self._ui_events.empty():
                try:
                    self._ui_events.get_nowait()
                    self.refresh_ui()
                    self._update_sidebar_history()
                except queue.Empty:
                    break

            # Fast waveform animation if in voice mode or speaking
            if self.orchestrator.mode == "voice" or self.orchestrator.state.value != "idle":
                self.refresh_ui()

            try:
                self.root.after(50, _tick)
            except Exception:
                pass

        self.root.after(50, _tick)

    def on_close(self) -> None:
        """Graceful shutdown releasing audio and neural network resources."""
        self.exit_action["reason"] = "closed"
        try:
            self.orchestrator.remove_observer(self._observer_cb)
        except Exception:
            pass
        try:
            self.controller.stop_voice()
        except Exception:
            pass
        try:
            self.root.quit()
            if self._owns_root:
                self.root.destroy()
        except Exception:
            pass

    def run(self) -> str:
        """Start the Tkinter event loop and return exit action."""
        self.refresh_ui()
        self.root.mainloop()
        return self.exit_action.get("reason", "closed")


def launch_desktop(
    orchestrator: VoiceOrchestrator,
    *,
    coordinator: Any | None = None,
    on_logout: Callable[[], None] | None = None,
    root_window: tk.Tk | None = None,
    run_mainloop: bool = True,
    initial_theme: str = "dark",
) -> str:
    """Launch RIYA's modern desktop interface.
    
    Returns "logout" when exited via Logout, "closed" on window close,
    or "ready" when run_mainloop=False.
    """
    app = RIYADesktopApp(
        orchestrator,
        coordinator=coordinator,
        on_logout=on_logout,
        root_window=root_window,
        initial_theme=initial_theme,
    )
    if run_mainloop:
        return app.run()
    return "ready"

