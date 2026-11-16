"""Modern native macOS Desktop UI package for RIYA."""

from __future__ import annotations

from core.desktop.app import RIYADesktopApp, launch_desktop
from core.desktop.chat_view import ChatView
from core.desktop.diagnostics_view import DiagnosticsView
from core.desktop.markdown import MarkdownRenderer
from core.desktop.memories_view import MemoriesView
from core.desktop.permissions_view import PermissionsView
from core.desktop.settings_view import SettingsView
from core.desktop.sidebar import SidebarView
from core.desktop.theme import (
    DARK_THEME,
    LIGHT_THEME,
    FontSystem,
    ThemeManager,
    ThemePalette,
)

__all__ = [
    "DARK_THEME",
    "LIGHT_THEME",
    "ChatView",
    "DiagnosticsView",
    "FontSystem",
    "MarkdownRenderer",
    "MemoriesView",
    "PermissionsView",
    "RIYADesktopApp",
    "SettingsView",
    "SidebarView",
    "ThemeManager",
    "ThemePalette",
    "launch_desktop",
]

