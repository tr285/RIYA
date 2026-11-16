"""Comprehensive automated test suite for Phase 3 Desktop UI Modernization & Native macOS App."""

from __future__ import annotations

import plistlib
import queue
from pathlib import Path
import tkinter as tk
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.desktop.app import RIYADesktopApp, launch_desktop
from core.desktop.chat_view import ChatView
from core.desktop.diagnostics_view import DiagnosticsView
from core.desktop.markdown import MarkdownRenderer
from core.desktop.memories_view import MemoriesView
from core.desktop.permissions_view import PermissionsView
from core.desktop.settings_view import SettingsView
from core.desktop.sidebar import SidebarView
from core.desktop.tasks_view import TasksView
from core.desktop.theme import (
    DARK_THEME,
    LIGHT_THEME,
    FontSystem,
    ThemeManager,
    ThemePalette,
)
from core.voice.config import VoiceConfig
from core.voice.orchestrator import VoiceOrchestrator
from core.voice.session import TranscriptEntry
from core.voice.state import VoiceState
from core.voice.ui import VoiceUIController, VoiceVisualSnapshot


def _create_mock_orchestrator():
    orch = Mock(spec=VoiceOrchestrator)
    orch.mode = "text"
    orch.is_paused = False
    orch.conversation_mode = "continuous"
    orch.state = VoiceState.IDLE
    orch.config = VoiceConfig()
    orch.active_session = None
    orch.session = None
    orch.observers = []
    orch.add_observer = lambda cb: orch.observers.append(cb)
    orch.remove_observer = lambda cb: orch.observers.remove(cb) if cb in orch.observers else None
    orch.audio_level = 0.0
    orch.last_error = None
    orch.active_task_progress = None
    orch.streaming_text = ""
    orch.transcript = ()
    orch.can_interrupt = False

    # Assistant & Identity mock
    assistant = Mock()
    assistant.current_identity = Mock(username="testuser", display_name="Test User")
    assistant.ai_status.return_value = {"model_name": "Qwen3 4B Instruct", "backend": "metal", "loaded": True}
    assistant.ai_metrics_summary.return_value = {"total_inferences": 5, "avg_latency_ms": 22.4}
    assistant.last_tool_results = ()
    assistant.pending_action = None
    assistant.pending_plan = None
    orch.assistant = assistant

    # Memory mock
    memory_manager = Mock()
    memory_manager.list_memories.return_value = [
        {"category": "general", "key": "user_name", "value": "Tukaram", "created_at": "2026-11-15T12:00:00"}
    ]
    memory_manager.for_identity.return_value = memory_manager
    memory_manager.search.return_value = []
    assistant.memory = Mock(manager=memory_manager)

    # Permissions mock
    perm_mgr = Mock()
    perm_mgr.list_effective_permissions.return_value = []
    perm_mgr.audit_logger.get_recent_logs.return_value = []
    assistant.permission_manager = perm_mgr

    # Productivity mock
    assistant.productivity = Mock()
    assistant.productivity.get_productivity_summary.return_value = {
        "pending_tasks_count": 1,
        "active_reminders_count": 0,
        "daily_plan_summary": "Test Plan",
    }
    return orch


class TestDesktopUIModernization:
    """Automated unit and integration tests for RIYA macOS Desktop UI."""

    def test_theme_manager_palette_and_toggle(self):
        """Verify theme manager palette tokens, contrast, and dynamic toggle."""
        mgr = ThemeManager("dark")
        assert mgr.mode == "dark"
        assert mgr.current.name == "dark"
        assert mgr.current.bg_base == DARK_THEME.bg_base

        notified = []
        mgr.add_listener(lambda th: notified.append(th.name))

        # Toggle to light
        new_th = mgr.toggle()
        assert new_th.name == "light"
        assert mgr.mode == "light"
        assert len(notified) == 1
        assert notified[0] == "light"

        # Toggle back to dark
        mgr.toggle()
        assert mgr.mode == "dark"
        assert len(notified) == 2
        assert notified[1] == "dark"

    def test_markdown_renderer_headings_codeblocks_lists(self):
        """Verify rich markdown parser properly handles headings, code blocks, and inline styles."""
        root = tk.Tk()
        root.withdraw()
        try:
            text_widget = tk.Text(root)
            MarkdownRenderer.configure_tags(text_widget, DARK_THEME)

            sample_md = (
                "# Main Title\n\n"
                "This is **bold** and *italic* with `inline code`.\n\n"
                "- Item 1\n"
                "- Item 2\n\n"
                "> Blockquote text\n\n"
                "```python\n"
                "def hello():\n"
                "    return 'world'\n"
                "```\n"
            )

            MarkdownRenderer.render(text_widget, sample_md, DARK_THEME)
            content = text_widget.get("1.0", tk.END)

            assert "Main Title" in content
            assert "bold" in content
            assert "inline code" in content
            assert "Item 1" in content
            assert "Blockquote text" in content
            assert "def hello():" in content
            assert "PYTHON" in content  # Code block language header
        finally:
            root.destroy()

    def test_desktop_app_startup_and_components_initialization(self):
        """Verify master RIYADesktopApp initializes all 6 views and macOS native menu bar."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            app = RIYADesktopApp(
                orch,
                root_window=root,
                initial_theme="dark",
            )
            assert "RIYA" in root.title()
            assert app._current_view_name == "chat"
            assert "chat" in app._views
            assert "memories" in app._views
            assert "tasks" in app._views
            assert "permissions" in app._views
            assert "diagnostics" in app._views
            assert "settings" in app._views

            # Verify sidebar present
            assert hasattr(app, "sidebar")
            assert bool(app.sidebar.winfo_manager())
        finally:
            app.on_close()

    def test_view_switching_and_sidebar_sync(self):
        """Verify switching views displays the target panel and updates sidebar selection."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            app = RIYADesktopApp(orch, root_window=root)

            # Switch to memories
            app.switch_view("memories")
            assert app._current_view_name == "memories"
            assert bool(app._memories_view.winfo_manager())
            assert not bool(app._chat_view.winfo_manager())

            # Switch to settings
            app.switch_view("settings")
            assert app._current_view_name == "settings"
            assert bool(app._settings_view.winfo_manager())

            # Switch back to chat
            app.switch_view("chat")
            assert app._current_view_name == "chat"
            assert bool(app._chat_view.winfo_manager())
        finally:
            app.on_close()

    def test_chat_view_rendering_snapshots_and_streaming(self):
        """Verify chat view updates status chips, message bubbles, and live streaming text."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            chat = ChatView(
                root,
                controller=ctrl,
                theme=DARK_THEME,
                on_theme_toggle=lambda: None,
                on_refresh_requested=lambda: None,
            )
            chat.pack(fill=tk.BOTH, expand=True)

            snap = VoiceVisualSnapshot(
                mode="text",
                voice_state=VoiceState.IDLE,
                status_label="Ready (Text mode)",
                microphone_active=False,
                assistant_speaking=False,
                audio_level=0.0,
                waveform_bars=(0.1,) * 24,
                transcript=(
                    TranscriptEntry("1", "user", "Hello RIYA!", "text", 1700000000.0),
                    TranscriptEntry("2", "assistant", "Hello! How can I assist you?", "text", 1700000001.0),
                ),
                error_message=None,
                can_interrupt=False,
                config=VoiceConfig(),
                model_status_label="AI: Qwen3 Metal",
                streaming_text="Generating response...",
            )

            chat.render_snapshot(snap)
            content = chat._transcript.get("1.0", tk.END)

            assert "Hello RIYA!" in content
            assert "Hello! How can I assist you?" in content
            assert "Generating response..." in content
            assert chat._mic_badge.cget("text") == "MIC OFF"
        finally:
            root.destroy()

    def test_confirmation_banner_in_chat_view(self):
        """Verify confirmation banner displays when approval is required and hides when cleared."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            chat = ChatView(
                root,
                controller=ctrl,
                theme=DARK_THEME,
                on_theme_toggle=lambda: None,
                on_refresh_requested=lambda: None,
            )
            chat.pack(fill=tk.BOTH, expand=True)

            # Snapshot with pending confirmation
            snap_confirm = VoiceVisualSnapshot(
                mode="text",
                voice_state=VoiceState.IDLE,
                status_label="Pending Approval",
                microphone_active=False,
                assistant_speaking=False,
                audio_level=0.0,
                waveform_bars=(0.0,) * 24,
                transcript=(),
                error_message=None,
                can_interrupt=False,
                config=VoiceConfig(),
                pending_confirmation={"tool": "write_file", "parameters": {"path": "/tmp/test.txt"}},
            )
            chat.render_snapshot(snap_confirm)
            assert bool(chat._confirm_bar.winfo_manager())
            assert "write_file" in chat._confirm_label.cget("text")

            # Snapshot without confirmation
            snap_clean = VoiceVisualSnapshot(
                mode="text",
                voice_state=VoiceState.IDLE,
                status_label="Ready",
                microphone_active=False,
                assistant_speaking=False,
                audio_level=0.0,
                waveform_bars=(0.0,) * 24,
                transcript=(),
                error_message=None,
                can_interrupt=False,
                config=VoiceConfig(),
                pending_confirmation=None,
            )
            chat.render_snapshot(snap_clean)
            assert not bool(chat._confirm_bar.winfo_manager())
        finally:
            root.destroy()

    def test_memories_view_operations_and_search(self):
        """Verify memory explorer loads memories and filters by search query."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            mem_view = MemoriesView(root, controller=ctrl, theme=DARK_THEME)
            mem_view.pack(fill=tk.BOTH, expand=True)

            children = mem_view._tree.get_children()
            assert len(children) >= 1
            vals = mem_view._tree.item(children[0], "values")
            assert vals[0] == "general"
            assert vals[1] == "user_name"
            assert vals[2] == "Tukaram"

            # Search filter
            mem_view._search_var.set("Tukaram")
            mem_view._on_search()
            assert len(mem_view._tree.get_children()) >= 1

            mem_view._search_var.set("nonexistent_term")
            mem_view._on_search()
            assert len(mem_view._tree.get_children()) == 0
        finally:
            root.destroy()

    def test_settings_view_all_10_tabs_present(self):
        """Verify preferences view constructs all 10 required configuration sections."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            settings_view = SettingsView(
                root,
                controller=ctrl,
                theme=DARK_THEME,
                on_theme_change=lambda _m: None,
            )
            settings_view.pack(fill=tk.BOTH, expand=True)

            tab_count = settings_view._nb.index("end")
            assert tab_count == 10

            tab_names = [settings_view._nb.tab(i, "text").strip() for i in range(tab_count)]
            expected_tabs = [
                "General",
                "Appearance",
                "AI Models",
                "Voice",
                "Memory",
                "Privacy",
                "Permissions",
                "Devices",
                "Storage",
                "About RIYA",
            ]
            for exp in expected_tabs:
                assert exp in tab_names
        finally:
            root.destroy()

    def test_packaging_bundle_and_dmg_validation(self):
        """Verify built RIYA.app bundle structure, Info.plist entries, and DMG file."""
        repo_root = Path(__file__).resolve().parents[1]
        app_path = repo_root / "dist" / "RIYA.app"
        dmg_path = repo_root / "dist" / "RIYA-v1.0.0-AppleSilicon.dmg"

        assert app_path.exists(), f"RIYA.app missing at {app_path}"
        assert (app_path / "Contents" / "MacOS" / "RIYA").exists()
        assert (app_path / "Contents" / "Info.plist").exists()
        assert (app_path / "Contents" / "Resources" / "AppIcon.icns").exists()

        # Parse Info.plist
        with (app_path / "Contents" / "Info.plist").open("rb") as f:
            plist = plistlib.load(f)

        assert plist["CFBundleName"] == "RIYA"
        assert plist["CFBundleIdentifier"] == "com.riya.assistant"
        assert plist["CFBundleShortVersionString"] == "1.0.0"
        assert "microphone" in plist["NSMicrophoneUsageDescription"].lower()

        # Check DMG
        assert dmg_path.exists(), f"RIYA DMG missing at {dmg_path}"
        assert dmg_path.stat().st_size > 50_000  # Non-trivial disk image size

    def test_graceful_shutdown_and_resource_cleanup(self):
        """Verify on_close shuts down voice stream, cleans observers, and exits cleanly."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            app = RIYADesktopApp(orch, root_window=root)
            assert len(orch.observers) >= 1

            app.on_close()
            assert app.exit_action["reason"] == "closed"
        finally:
            try:
                root.destroy()
            except Exception:
                pass

    def test_chat_input_sending_and_voice_controls(self):
        """Verify text entry submission and voice control events trigger controller methods."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            ctrl.submit_text = Mock()
            ctrl.toggle_voice_mode = Mock()
            ctrl.pause = Mock()
            ctrl.resume = Mock()
            ctrl.interrupt = Mock()
            ctrl.start_push_to_talk = Mock()
            ctrl.stop_push_to_talk = Mock()

            chat = ChatView(
                root,
                controller=ctrl,
                theme=DARK_THEME,
                on_theme_toggle=lambda: None,
                on_refresh_requested=lambda: None,
            )
            chat.pack(fill=tk.BOTH, expand=True)

            # Test text sending
            chat._entry.insert(0, "Test prompt")
            chat._on_send()
            assert chat._entry.get() == ""

            # Test voice toggle
            chat._on_toggle_voice_mode()
            ctrl.toggle_voice_mode.assert_called_once()

            # Test PTT press and release
            chat._on_ptt_press()
            ctrl.start_push_to_talk.assert_called_once()
            chat._on_ptt_release()
            ctrl.stop_push_to_talk.assert_called_once()

            # Test interrupt
            chat._on_interrupt()
            ctrl.interrupt.assert_called_once()
        finally:
            root.destroy()

    def test_tasks_view_submission_and_cancellation(self):
        """Verify task view starts autonomous tasks and cancels active tasks."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            ctrl.submit_task = Mock()
            ctrl.cancel_task = Mock()

            tasks_view = TasksView(root, controller=ctrl, theme=DARK_THEME)
            tasks_view.pack(fill=tk.BOTH, expand=True)

            tasks_view._task_entry.insert(0, "Organize files")
            tasks_view._on_start_task()
            assert tasks_view._task_entry.get() == ""

            tasks_view._on_cancel()
            ctrl.cancel_task.assert_called_once()
        finally:
            root.destroy()

    def test_permissions_view_grant_rule_execution(self):
        """Verify permissions view saves configured capability rules."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            ctrl.grant_permission = Mock()

            perms_view = PermissionsView(root, controller=ctrl, theme=DARK_THEME)
            perms_view.pack(fill=tk.BOTH, expand=True)

            perms_view._key_var.set("voice_input")
            perms_view._dec_var.set("allow")
            perms_view._life_var.set("session")
            with patch("tkinter.messagebox.showinfo"), patch("tkinter.messagebox.showwarning"):
                perms_view._on_apply_rule()

            ctrl.grant_permission.assert_called_once_with(
                "voice_input",
                decision="allow",
                lifetime="session",
                explicit_persistent_ack=False,
            )
        finally:
            root.destroy()

    def test_diagnostics_view_renders_readiness_checks(self):
        """Verify diagnostics view renders platform and subsystem readiness checks."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            ctrl = VoiceUIController(orch)
            diag_view = DiagnosticsView(root, controller=ctrl, theme=DARK_THEME)
            diag_view.pack(fill=tk.BOTH, expand=True)

            content = diag_view._text.get("1.0", tk.END)
            assert "READINESS REPORT" in content
            assert "Platform OS" in content
            assert "Audio Subsystem" in content
            assert "Local Database" in content
        finally:
            root.destroy()

    def test_launch_desktop_convenience_function(self):
        """Verify top-level launch_desktop function initializes correctly without blocking."""
        orch = _create_mock_orchestrator()
        root = tk.Tk()
        root.withdraw()
        try:
            status = launch_desktop(orch, root_window=root, run_mainloop=False)
            assert status == "ready"
        finally:
            root.destroy()
