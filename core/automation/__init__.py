"""Desktop automation package for RIYA."""

from core.automation.desktop import (
    DesktopController,
    desktop_app_details,
    desktop_app_resources,
    desktop_app_state,
    desktop_focus_app,
    desktop_list_apps,
    desktop_open_url,
    desktop_running_apps,
    desktop_running_apps_detailed,
    desktop_search_apps,
    desktop_terminate_app,
)

__all__ = [
    "DesktopController",
    "desktop_app_details",
    "desktop_app_resources",
    "desktop_app_state",
    "desktop_focus_app",
    "desktop_list_apps",
    "desktop_open_url",
    "desktop_running_apps",
    "desktop_running_apps_detailed",
    "desktop_search_apps",
    "desktop_terminate_app",
]
