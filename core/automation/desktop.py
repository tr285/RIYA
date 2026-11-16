"""Desktop interaction & workflow automation tools layered cleanly over PlatformInterface."""

from __future__ import annotations

import json
from typing import Any

from core.device import get_platform_adapter


def _get_platform():
    return get_platform_adapter()


_DEFAULT_PLATFORM = get_platform_adapter()


def desktop_list_apps(filter_text: str = "") -> str:
    """List installed desktop applications (optionally filtered)."""
    apps = _DEFAULT_PLATFORM.list_installed_applications()
    needle = str(filter_text or "").strip().lower()
    if needle:
        apps = [a for a in apps if needle in a.lower()]
    return json.dumps({"installed_apps": apps[:80], "count": len(apps)}, ensure_ascii=False)


def desktop_running_apps() -> str:
    """List currently running non-background desktop applications."""
    apps = _DEFAULT_PLATFORM.list_running_applications()
    return json.dumps({"running_apps": apps[:80], "count": len(apps)}, ensure_ascii=False)


def desktop_app_state(app_name: str) -> str:
    """Inspect whether an application is not_running, running, or frontmost."""
    return _DEFAULT_PLATFORM.get_application_state(app_name)


def desktop_focus_app(app_name: str) -> str:
    """Focus a running desktop application (requires user confirmation & permission)."""
    return _DEFAULT_PLATFORM.focus_application(app_name)


def desktop_search_apps(query: str) -> str:
    """Search installed applications with fuzzy and substring matching."""
    matches = _DEFAULT_PLATFORM.search_applications(query)
    return json.dumps({"query": query, "applications": matches[:50], "count": len(matches)}, ensure_ascii=False)


def desktop_app_details(app_name: str) -> str:
    """Get detailed installation, version, and bundle metadata for an application."""
    details = _DEFAULT_PLATFORM.get_application_details(app_name)
    return json.dumps(details, ensure_ascii=False)


def desktop_running_apps_detailed() -> str:
    """List currently running applications with process IDs and memory telemetry."""
    apps = _DEFAULT_PLATFORM.get_running_applications_structured()
    return json.dumps({"running_applications": apps[:80], "count": len(apps)}, ensure_ascii=False)


def desktop_app_resources(app_name: str) -> str:
    """Inspect CPU and memory usage of a running application."""
    res = _DEFAULT_PLATFORM.get_application_resources(app_name)
    return json.dumps(res, ensure_ascii=False)


def desktop_terminate_app(app_name: str) -> str:
    """Gracefully terminate a running application (requires user confirmation & permission)."""
    res = _DEFAULT_PLATFORM.terminate_application(app_name)
    return json.dumps(res, ensure_ascii=False)


def desktop_open_url(url: str, browser: str | None = None) -> str:
    """Open a validated URL in a web browser (requires user confirmation & permission)."""
    res = _DEFAULT_PLATFORM.open_url(url, browser=browser)
    return json.dumps(res, ensure_ascii=False)


class DesktopController:
    """Coordinates desktop app inspection and confirmed focus/launch actions via ToolExecutor."""

    def __init__(self, tool_executor=None, platform=None):
        self.tool_executor = tool_executor
        self.platform = platform or _DEFAULT_PLATFORM

    def discover_apps(self, *, user_context=None) -> str:
        if self.tool_executor is not None:
            return self.tool_executor.execute("desktop_list_apps", user_context=user_context)
        apps = self.platform.list_installed_applications()
        return json.dumps({"installed_apps": apps[:80], "count": len(apps)}, ensure_ascii=False)

    def running_apps(self, *, user_context=None) -> str:
        if self.tool_executor is not None:
            return self.tool_executor.execute("desktop_running_apps", user_context=user_context)
        apps = self.platform.list_running_applications()
        return json.dumps({"running_apps": apps[:80], "count": len(apps)}, ensure_ascii=False)

    def app_state(self, app_name: str, *, user_context=None) -> Any:
        if self.tool_executor is not None:
            return self.tool_executor.execute(
                "desktop_app_state",
                user_context=user_context,
                app_name=app_name,
            )
        return self.platform.get_application_state(app_name)

    def search_apps(self, query: str, *, user_context=None) -> str:
        if self.tool_executor is not None:
            return self.tool_executor.execute("desktop_search_apps", user_context=user_context, query=query)
        matches = self.platform.search_applications(query)
        return json.dumps({"query": query, "applications": matches[:50], "count": len(matches)}, ensure_ascii=False)

    def app_details(self, app_name: str, *, user_context=None) -> str:
        if self.tool_executor is not None:
            return self.tool_executor.execute("desktop_app_details", user_context=user_context, app_name=app_name)
        details = self.platform.get_application_details(app_name)
        return json.dumps(details, ensure_ascii=False)

    def running_apps_detailed(self, *, user_context=None) -> str:
        if self.tool_executor is not None:
            return self.tool_executor.execute("desktop_running_apps_detailed", user_context=user_context)
        apps = self.platform.get_running_applications_structured()
        return json.dumps({"running_applications": apps[:80], "count": len(apps)}, ensure_ascii=False)

    def app_resources(self, app_name: str, *, user_context=None) -> str:
        if self.tool_executor is not None:
            return self.tool_executor.execute("desktop_app_resources", user_context=user_context, app_name=app_name)
        res = self.platform.get_application_resources(app_name)
        return json.dumps(res, ensure_ascii=False)

    def terminate_app(self, app_name: str, *, user_context=None) -> str:
        if self.tool_executor is not None:
            return self.tool_executor.execute("desktop_terminate_app", user_context=user_context, app_name=app_name)
        res = self.platform.terminate_application(app_name)
        return json.dumps(res, ensure_ascii=False)

    def open_url(self, url: str, browser: str | None = None, *, user_context=None) -> str:
        if self.tool_executor is not None:
            params = {"url": url}
            if browser:
                params["browser"] = browser
            return self.tool_executor.execute("desktop_open_url", user_context=user_context, **params)
        res = self.platform.open_url(url, browser=browser)
        return json.dumps(res, ensure_ascii=False)
