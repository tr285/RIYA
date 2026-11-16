"""Modern macOS theme definitions and dynamic theme coordinator for RIYA Desktop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

import sys

ThemeMode = Literal["dark", "light"]

_IS_LINUX = sys.platform.startswith("linux")
_IS_WINDOWS = sys.platform == "win32"

_SYSTEM_FONT = "Ubuntu" if _IS_LINUX else ("Segoe UI" if _IS_WINDOWS else "-apple-system")
_MONO_FONT = "DejaVu Sans Mono" if _IS_LINUX else ("Consolas" if _IS_WINDOWS else "Menlo")


@dataclass(frozen=True)
class ThemePalette:
    """Design system color tokens tailored for macOS Aqua and Dark appearances."""

    name: ThemeMode

    # Backgrounds
    bg_base: str
    bg_sidebar: str
    bg_surface: str
    bg_surface_elevated: str
    bg_card: str
    bg_input: str

    # Borders & Dividers
    border: str
    border_subtle: str
    border_focus: str

    # Typography
    text_primary: str
    text_secondary: str
    text_muted: str
    text_inverse: str

    # Accents
    accent: str
    accent_hover: str
    accent_subtle: str

    # Chat Bubbles
    user_bubble_bg: str
    user_bubble_fg: str
    assistant_bubble_bg: str
    assistant_bubble_fg: str
    assistant_bubble_border: str

    # Code Blocks
    code_bg: str
    code_fg: str
    code_border: str
    code_header_bg: str

    # Status & Alerts
    status_success_bg: str
    status_success_fg: str
    status_warning_bg: str
    status_warning_fg: str
    status_error_bg: str
    status_error_fg: str
    status_info_bg: str
    status_info_fg: str

    # Voice / Waveform
    waveform_active: str
    waveform_idle: str
    waveform_mic_live: str
    waveform_mic_paused: str
    waveform_mic_off: str


DARK_THEME = ThemePalette(
    name="dark",
    bg_base="#0d1117",
    bg_sidebar="#161b22",
    bg_surface="#161b22",
    bg_surface_elevated="#21262d",
    bg_card="#1c2128",
    bg_input="#0d1117",
    border="#30363d",
    border_subtle="#21262d",
    border_focus="#58a6ff",
    text_primary="#f0f6fc",
    text_secondary="#8b949e",
    text_muted="#6e7681",
    text_inverse="#0d1117",
    accent="#2f81f7",
    accent_hover="#388bfd",
    accent_subtle="#1f6feb33",
    user_bubble_bg="#1f6feb",
    user_bubble_fg="#ffffff",
    assistant_bubble_bg="#161b22",
    assistant_bubble_fg="#f0f6fc",
    assistant_bubble_border="#30363d",
    code_bg="#090d13",
    code_fg="#e6edf3",
    code_border="#30363d",
    code_header_bg="#161b22",
    status_success_bg="#172b1d",
    status_success_fg="#3fb950",
    status_warning_bg="#342813",
    status_warning_fg="#d29922",
    status_error_bg="#351a1a",
    status_error_fg="#f85149",
    status_info_bg="#14263b",
    status_info_fg="#58a6ff",
    waveform_active="#38bdf8",
    waveform_idle="#475569",
    waveform_mic_live="#22c55e",
    waveform_mic_paused="#eab308",
    waveform_mic_off="#64748b",
)

LIGHT_THEME = ThemePalette(
    name="light",
    bg_base="#f6f8fa",
    bg_sidebar="#ffffff",
    bg_surface="#ffffff",
    bg_surface_elevated="#f0f2f5",
    bg_card="#ffffff",
    bg_input="#ffffff",
    border="#d0d7de",
    border_subtle="#eaeef2",
    border_focus="#0969da",
    text_primary="#1f2328",
    text_secondary="#656d76",
    text_muted="#8c959f",
    text_inverse="#ffffff",
    accent="#0969da",
    accent_hover="#0852ab",
    accent_subtle="#0969da1a",
    user_bubble_bg="#0969da",
    user_bubble_fg="#ffffff",
    assistant_bubble_bg="#ffffff",
    assistant_bubble_fg="#1f2328",
    assistant_bubble_border="#d0d7de",
    code_bg="#f6f8fa",
    code_fg="#24292f",
    code_border="#d0d7de",
    code_header_bg="#eaeef2",
    status_success_bg="#dafbe1",
    status_success_fg="#1a7f37",
    status_warning_bg="#fff8c5",
    status_warning_fg="#9a6700",
    status_error_bg="#ffebe9",
    status_error_fg="#cf222e",
    status_info_bg="#ddf4ff",
    status_info_fg="#0969da",
    waveform_active="#0284c7",
    waveform_idle="#94a3b8",
    waveform_mic_live="#16a34a",
    waveform_mic_paused="#ca8a04",
    waveform_mic_off="#94a3b8",
)


class FontSystem:
    """Cross-platform typography hierarchy tailored for macOS, Linux, and Windows."""

    SYSTEM_FAMILY = _SYSTEM_FONT
    MONOSPACE_FAMILY = _MONO_FONT

    APP_TITLE = (_SYSTEM_FONT, 17, "bold")
    SECTION_HEADER = (_SYSTEM_FONT, 14, "bold")
    ITEM_TITLE = (_SYSTEM_FONT, 12, "bold")
    BODY = (_SYSTEM_FONT, 12)
    BODY_BOLD = (_SYSTEM_FONT, 12, "bold")
    CAPTION = (_SYSTEM_FONT, 10)
    CAPTION_BOLD = (_SYSTEM_FONT, 10, "bold")
    MONOSPACE = (_MONO_FONT, 11)
    MONOSPACE_SMALL = (_MONO_FONT, 10)

    @classmethod
    def get_system_font_family(cls) -> str:
        return cls.SYSTEM_FAMILY

    @classmethod
    def get_monospace_font_family(cls) -> str:
        return cls.MONOSPACE_FAMILY


class ThemeManager:
    """Central theme coordinator allowing dynamic switching between Dark and Light mode."""

    def __init__(self, initial_mode: ThemeMode = "dark"):
        self._mode: ThemeMode = initial_mode
        self._theme: ThemePalette = DARK_THEME if initial_mode == "dark" else LIGHT_THEME
        self._listeners: list[Callable[[ThemePalette], None]] = []

    @property
    def current(self) -> ThemePalette:
        return self._theme

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    def toggle(self) -> ThemePalette:
        new_mode: ThemeMode = "light" if self._mode == "dark" else "dark"
        return self.set_mode(new_mode)

    def set_mode(self, mode: ThemeMode) -> ThemePalette:
        self._mode = mode
        self._theme = DARK_THEME if mode == "dark" else LIGHT_THEME
        for callback in self._listeners:
            try:
                callback(self._theme)
            except Exception:
                pass
        return self._theme

    def add_listener(self, callback: Callable[[ThemePalette], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[ThemePalette], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

