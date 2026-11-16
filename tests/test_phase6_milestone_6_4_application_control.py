"""Comprehensive unit, integration, and security tests for RIYA Phase 6 Milestone 6.4.

Intelligent Application Control & Desktop Automation:
- Application search with relevance scoring
- Application details and bundle metadata inspection
- Structured running application process enumeration and resource telemetry
- Safe application termination with protected process and self-termination guards
- Safe browser URL validation and launch
- Cross-platform adapter compliance (macOS, Windows, Linux)
- Permission, confirmation, and security enforcement
- ToolExecutor verification and DesktopController integration
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch
import pytest

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
from core.device.linux import LinuxPlatform
from core.device.macos import MacOSPlatform
from core.device.platform import PlatformOperationResult
from core.device.windows import WindowsPlatform
from core.security.manager import RiskLevel, SecurityManager
from core.security.permissions import (
    PERMISSION_SPECS,
    PermissionGrant,
    PermissionManager,
    resolve_permissions_for_tool,
)
from core.tools.system.macos import (
    _PROTECTED_PROCESSES,
    _is_protected_process,
    _is_riya_own_process,
    get_application_details,
    get_application_resources,
    get_running_applications_structured,
    open_browser_url,
    search_applications,
    terminate_application,
    validate_url,
)


# =====================================================================
# 1. APPLICATION SEARCH TESTS
# =====================================================================

class TestApplicationSearch:
    def test_search_applications_empty_query(self):
        assert search_applications("") == []
        assert search_applications("   ") == []

    def test_search_applications_query_too_long(self):
        with pytest.raises(ValueError, match="maximum length"):
            search_applications("a" * 129)

    def test_search_applications_with_mocked_bundles(self, tmp_path):
        # Create a mock application directory structure
        app_dir = tmp_path / "Applications"
        app_dir.mkdir()

        # App 1: Safari.app
        safari = app_dir / "Safari.app"
        safari.mkdir()
        (safari / "Contents").mkdir()
        info_plist_safari = safari / "Contents" / "Info.plist"
        import plistlib
        with info_plist_safari.open("wb") as fp:
            plistlib.dump({
                "CFBundleName": "Safari",
                "CFBundleIdentifier": "com.apple.Safari",
                "CFBundleShortVersionString": "17.0",
                "CFBundleExecutable": "Safari",
            }, fp)

        # App 2: Visual Studio Code.app
        vscode = app_dir / "Visual Studio Code.app"
        vscode.mkdir()
        (vscode / "Contents").mkdir()
        info_plist_vscode = vscode / "Contents" / "Info.plist"
        with info_plist_vscode.open("wb") as fp:
            plistlib.dump({
                "CFBundleName": "Code",
                "CFBundleDisplayName": "Visual Studio Code",
                "CFBundleIdentifier": "com.microsoft.VSCode",
                "CFBundleShortVersionString": "1.85.0",
                "CFBundleExecutable": "Electron",
            }, fp)

        # App 3: Terminal.app
        term = app_dir / "Terminal.app"
        term.mkdir()
        (term / "Contents").mkdir()
        info_plist_term = term / "Contents" / "Info.plist"
        with info_plist_term.open("wb") as fp:
            plistlib.dump({
                "CFBundleName": "Terminal",
                "CFBundleIdentifier": "com.apple.Terminal",
                "CFBundleShortVersionString": "2.14",
                "CFBundleExecutable": "Terminal",
            }, fp)

        # Search for exact match
        results = search_applications("safari", search_dirs=[app_dir])
        assert len(results) == 1
        assert results[0]["name"] == "Safari"

        # Search for display name substring
        results = search_applications("visual", search_dirs=[app_dir])
        assert len(results) == 1
        assert results[0]["name"] == "Visual Studio Code"

        # Search for bundle ID substring
        results = search_applications("com.microsoft", search_dirs=[app_dir])
        assert len(results) == 1
        assert results[0]["name"] == "Visual Studio Code"

        # Search matching multiple apps
        results = search_applications("apple", search_dirs=[app_dir])
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert "Safari" in names
        assert "Terminal" in names

    def test_desktop_search_apps_json_output(self, tmp_path):
        res = desktop_search_apps("non_existent_app_xyz_12345")
        data = json.loads(res)
        assert data["query"] == "non_existent_app_xyz_12345"
        assert data["count"] == 0
        assert data["applications"] == []


# =====================================================================
# 2. APPLICATION DETAILS TESTS
# =====================================================================

class TestApplicationDetails:
    def test_get_application_details_not_found(self, tmp_path):
        res = get_application_details("NonExistentFakeApp999", search_dirs=[tmp_path])
        assert res["found"] is False
        assert "not found" in res["error"].lower()

    def test_get_application_details_found(self, tmp_path):
        app_dir = tmp_path / "Applications"
        app_dir.mkdir()
        calc = app_dir / "Calculator.app"
        calc.mkdir()
        (calc / "Contents").mkdir()
        (calc / "Contents" / "Resources").mkdir()
        (calc / "Contents" / "Resources" / "icon.png").write_bytes(b"\x89PNGfake" * 50)

        import plistlib
        with (calc / "Contents" / "Info.plist").open("wb") as fp:
            plistlib.dump({
                "CFBundleName": "Calculator",
                "CFBundleIdentifier": "com.apple.calculator",
                "CFBundleShortVersionString": "10.15",
                "CFBundleExecutable": "Calculator",
            }, fp)

        res = get_application_details("Calculator", search_dirs=[app_dir])
        assert res["found"] is True
        assert res["name"] == "Calculator"
        assert res["bundle_id"] == "com.apple.calculator"
        assert res["version"] == "10.15"
        assert res["bundle_file_count"] >= 2
        assert res["bundle_size_bytes"] > 0
        assert res["bundle_size_mb"] >= 0.0

    def test_desktop_app_details_json_wrapper(self, tmp_path):
        res = desktop_app_details("NonExistentApp")
        data = json.loads(res)
        assert data["found"] is False


# =====================================================================
# 3. RUNNING APPLICATIONS TELEMETRY TESTS
# =====================================================================

class TestRunningApplicationsTelemetry:
    def test_get_running_applications_structured(self):
        with patch("core.tools.system.macos.list_running_applications", return_value=["Finder", "Python"]):
            apps = get_running_applications_structured()
            assert len(apps) == 2
            names = [a["name"] for a in apps]
            assert "Finder" in names
            assert "Python" in names

    def test_desktop_running_apps_detailed_json(self):
        with patch("core.tools.system.macos.list_running_applications", return_value=["Finder"]):
            res = desktop_running_apps_detailed()
            data = json.loads(res)
            assert "running_applications" in data
            assert data["count"] >= 1


# =====================================================================
# 4. APPLICATION RESOURCE MONITORING TESTS
# =====================================================================

class TestApplicationResources:
    def test_get_application_resources_not_running(self):
        res = get_application_resources("AppDefinitelyNotRunning999")
        assert res["status"] == "not_running"
        assert res["processes"] == []

    def test_get_application_resources_running(self):
        # Mock psutil
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 12345,
            "name": "TestApp",
            "memory_info": MagicMock(rss=50 * 1024 * 1024),
            "cpu_percent": 3.5,
            "status": "running",
            "create_time": 1000.0,
        }

        with patch("psutil.process_iter", return_value=[mock_proc]):
            res = get_application_resources("TestApp")
            assert res["status"] == "success"
            assert res["process_count"] == 1
            assert res["processes"][0]["pid"] == 12345
            assert res["processes"][0]["rss_mb"] == 50.0
            assert res["processes"][0]["cpu_percent"] == 3.5

    def test_desktop_app_resources_json(self):
        res = desktop_app_resources("NonRunningAppX")
        data = json.loads(res)
        assert data["status"] in {"not_running", "unavailable"}


# =====================================================================
# 5. APPLICATION TERMINATION & SAFETY TESTS
# =====================================================================

class TestApplicationTerminationSafety:
    def test_protected_processes_list(self):
        assert "launchd" in _PROTECTED_PROCESSES
        assert "kernel_task" in _PROTECTED_PROCESSES
        assert "WindowServer" in _PROTECTED_PROCESSES
        assert "loginwindow" in _PROTECTED_PROCESSES
        assert "Dock" in _PROTECTED_PROCESSES
        assert "Finder" in _PROTECTED_PROCESSES
        assert _is_protected_process("launchd")
        assert _is_protected_process("WindowServer")
        assert _is_protected_process("Finder")
        assert not _is_protected_process("Safari")
        assert not _is_protected_process("Calculator")

    def test_refuse_terminate_protected_process(self):
        for proc in ("launchd", "WindowServer", "Finder", "Dock", "kernel_task"):
            res = terminate_application(proc)
            assert res["status"] == "denied"
            assert res["terminated"] is False
            assert res["protected"] is True
            assert "protected system process" in res["error"]

    def test_refuse_terminate_riya_self(self):
        own_pid = os.getpid()
        assert _is_riya_own_process(own_pid) is True
        assert _is_riya_own_process(9999999) is False

        mock_proc = MagicMock()
        mock_proc.info = {"pid": own_pid, "name": "RIYA"}

        with patch("psutil.process_iter", return_value=[mock_proc]):
            res = terminate_application("RIYA")
            assert res["status"] == "denied"
            assert res["terminated"] is False
            assert "cannot terminate its own process" in res["error"]

    def test_terminate_not_running_application(self):
        with patch("core.tools.system.macos.list_running_applications", return_value=["Finder", "Safari"]):
            res = terminate_application("Calculator")
            assert res["status"] == "not_running"
            assert res["terminated"] is False
            assert "not currently running" in res["error"]

    def test_terminate_running_application_success(self):
        with patch("core.tools.system.macos.list_running_applications", return_value=["Finder", "Calculator"]):
            mock_result = subprocess.CompletedProcess(
                args=["/usr/bin/osascript"],
                returncode=0,
                stdout="CLOSED\n",
                stderr="",
            )
            with patch("core.tools.system.macos._run_osascript", return_value=mock_result):
                res = terminate_application("Calculator")
                assert res["status"] == "success"
                assert res["terminated"] is True
                assert res["verified"] is True
                assert "gracefully terminated" in res["message"]

    def test_terminate_running_application_still_running(self):
        with patch("core.tools.system.macos.list_running_applications", return_value=["Finder", "TextEdit"]):
            mock_result = subprocess.CompletedProcess(
                args=["/usr/bin/osascript"],
                returncode=0,
                stdout="STILL_RUNNING\n",
                stderr="",
            )
            with patch("core.tools.system.macos._run_osascript", return_value=mock_result):
                res = terminate_application("TextEdit")
                assert res["status"] == "failed"
                assert res["terminated"] is False
                assert "unsaved data" in res["error"]

    def test_desktop_terminate_app_json(self):
        res = desktop_terminate_app("launchd")
        data = json.loads(res)
        assert data["status"] == "denied"
        assert data["protected"] is True


# =====================================================================
# 6. SAFE URL VALIDATION & OPENING TESTS
# =====================================================================

class TestUrlValidationAndLaunch:
    def test_validate_url_valid_http(self):
        res = validate_url("http://localhost:8080/dashboard")
        assert res["valid"] is True
        assert res["scheme"] == "http"
        assert res["host"] == "localhost"

    def test_validate_url_valid_https(self):
        res = validate_url("https://docs.python.org/3/library/os.html")
        assert res["valid"] is True
        assert res["scheme"] == "https"
        assert res["host"] == "docs.python.org"

    def test_validate_url_reject_malicious_schemes(self):
        bad_schemes = [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "applescript://run",
            "blob:http://example.com/uuid",
            "ftp://example.com",
            "ssh://user@host",
        ]
        for url in bad_schemes:
            res = validate_url(url)
            assert res["valid"] is False
            assert "not permitted" in res["error"]

    def test_validate_url_reject_shell_injection_characters(self):
        dangerous_urls = [
            "https://example.com; rm -rf /",
            "https://example.com | cat /etc/passwd",
            "https://example.com `id`",
            "https://example.com $(whoami)",
            "https://example.com & echo 1",
            "https://example.com/test\x00evil",
            "https://example.com'<script>",
            'https://example.com"<script>',
        ]
        for url in dangerous_urls:
            res = validate_url(url)
            assert res["valid"] is False
            assert "forbidden" in res["error"].lower() or "null" in res["error"].lower()

    def test_validate_url_length_limit(self):
        long_url = "https://example.com/" + "a" * 2050
        res = validate_url(long_url)
        assert res["valid"] is False
        assert "exceeds maximum length" in res["error"]

    def test_validate_url_empty_or_non_string(self):
        assert validate_url("")["valid"] is False
        assert validate_url(123)["valid"] is False
        assert validate_url(None)["valid"] is False

    def test_open_browser_url_success(self):
        mock_proc = subprocess.CompletedProcess(
            args=["/usr/bin/open"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch("core.tools.system.macos._run_executable", return_value=mock_proc) as mock_exec:
            res = open_browser_url("https://example.com", browser="Safari")
            assert res["status"] == "success"
            assert res["opened"] is True
            mock_exec.assert_called_once()
            called_args = mock_exec.call_args[0]
            assert called_args[0] == "/usr/bin/open"
            assert called_args[1] == ["-a", "Safari", "https://example.com"]

    def test_open_browser_url_rejects_invalid_browser_name(self):
        res = open_browser_url("https://example.com", browser="Safari; rm -rf /")
        assert res["status"] == "failed"
        assert res["opened"] is False
        assert "Invalid browser name" in res["error"]

    def test_open_browser_url_rejects_bad_url(self):
        res = open_browser_url("javascript:stealCookies()")
        assert res["status"] == "failed"
        assert res["opened"] is False

    def test_desktop_open_url_json_output(self):
        res = desktop_open_url("javascript:bad()")
        data = json.loads(res)
        assert data["status"] == "failed"
        assert data["opened"] is False


# =====================================================================
# 7. CROSS-PLATFORM ADAPTER TESTS (macOS, Windows, Linux)
# =====================================================================

class TestCrossPlatformAdapters:
    def test_macos_platform_capabilities(self):
        platform = MacOSPlatform()
        caps = platform.capabilities()
        assert caps.supports("app_search") is True
        assert caps.supports("app_details") is True
        assert caps.supports("app_status") is True
        assert caps.supports("app_resources") is True
        assert caps.supports("app_terminate") is True
        assert caps.supports("url_open") is True
        assert caps.supports("unrestricted_shell") is False

    def test_macos_application_adapter_operations(self):
        platform = MacOSPlatform()
        app_adapter = platform.application_adapter

        # search_applications
        search_res = app_adapter.search_applications("safari")
        assert isinstance(search_res, PlatformOperationResult)
        assert search_res.capability == "app_search"

        # get_application_details
        details_res = app_adapter.get_application_details("NonExistentAppXYZ")
        assert isinstance(details_res, PlatformOperationResult)
        assert details_res.capability == "app_details"

        # get_running_applications_structured
        status_res = app_adapter.get_running_applications_structured()
        assert isinstance(status_res, PlatformOperationResult)
        assert status_res.capability == "app_status"

        # get_application_resources
        res_res = app_adapter.get_application_resources("NonExistentAppXYZ")
        assert isinstance(res_res, PlatformOperationResult)
        assert res_res.capability == "app_resources"

        # terminate_application (protected)
        term_res = app_adapter.terminate_application("launchd")
        assert isinstance(term_res, PlatformOperationResult)
        assert term_res.status == "denied"
        assert term_res.capability == "app_terminate"

        # open_url (bad url)
        url_res = app_adapter.open_url("file:///etc/passwd")
        assert isinstance(url_res, PlatformOperationResult)
        assert url_res.status == "failed"
        assert url_res.capability == "url_open"

    def test_windows_platform_honest_unsupported(self):
        platform = WindowsPlatform()
        app_adapter = platform.application_adapter

        for method, args in [
            (app_adapter.search_applications, ("query",)),
            (app_adapter.get_application_details, ("app",)),
            (app_adapter.get_running_applications_structured, ()),
            (app_adapter.terminate_application, ("app",)),
            (app_adapter.get_application_resources, ("app",)),
            (app_adapter.open_url, ("https://example.com",)),
        ]:
            res = method(*args)
            assert isinstance(res, PlatformOperationResult)
            assert res.status == "unsupported"
            assert res.supported is False
            assert "Windows native OS integration is not yet implemented" in res.message

    def test_linux_platform_honest_unsupported(self):
        platform = LinuxPlatform()
        app_adapter = platform.application_adapter

        for method, args in [
            (app_adapter.search_applications, ("query",)),
            (app_adapter.get_application_details, ("app",)),
            (app_adapter.get_running_applications_structured, ()),
            (app_adapter.terminate_application, ("app",)),
            (app_adapter.get_application_resources, ("app",)),
            (app_adapter.open_url, ("https://example.com",)),
        ]:
            res = method(*args)
            assert isinstance(res, PlatformOperationResult)
            assert res.status == "unsupported"
            assert res.supported is False
            assert "Linux native OS integration is not yet implemented" in res.message


# =====================================================================
# 8. SECURITY, PERMISSIONS & CONFIRMATION TESTS
# =====================================================================

class TestSecurityAndPermissions:
    def test_permission_specs_present(self):
        expected_perms = [
            "application.discover",
            "application.search",
            "application.status",
            "application.launch",
            "application.switch",
            "application.terminate",
            "application.force_terminate",
            "application.window.read",
            "application.resource.read",
            "application.open_url",
        ]
        for perm in expected_perms:
            assert perm in PERMISSION_SPECS, f"Missing permission spec: {perm}"

    def test_resolve_permissions_for_tools(self):
        # desktop_terminate_app
        req = resolve_permissions_for_tool("desktop_terminate_app", {"app_name": "Safari"})
        assert req.permission_key == "system.close_app"
        assert req.target_resource == "app:safari"

        # desktop_open_url
        req = resolve_permissions_for_tool("desktop_open_url", {"url": "https://python.org/doc"})
        assert req.permission_key == "browser.interact"
        assert req.target_resource == "origin:python.org"

        # desktop_search_apps
        req = resolve_permissions_for_tool("desktop_search_apps", {"query": "safari"})
        assert req.permission_key == "desktop.automate"

        # desktop_app_details
        req = resolve_permissions_for_tool("desktop_app_details", {"app_name": "Notes"})
        assert req.permission_key == "desktop.automate"
        assert req.target_resource == "app:notes"

        # desktop_app_resources
        req = resolve_permissions_for_tool("desktop_app_resources", {"app_name": "Terminal"})
        assert req.permission_key == "desktop.automate"
        assert req.target_resource == "app:terminal"

    def test_security_manager_risk_and_confirmation(self):
        sm = SecurityManager()

        # Safe read-only actions
        for tool in ("desktop_search_apps", "desktop_app_details", "desktop_running_apps_detailed", "desktop_app_resources"):
            decision = sm.check(tool)
            assert decision.allowed is True
            assert decision.requires_confirmation is False
            assert decision.risk_level == RiskLevel.LOW

        # Actions requiring confirmation
        for tool in ("desktop_terminate_app", "desktop_open_url"):
            decision = sm.check(tool)
            assert decision.allowed is True
            assert decision.requires_confirmation is True
            assert decision.risk_level == RiskLevel.MEDIUM


# =====================================================================
# 9. DESKTOP CONTROLLER INTEGRATION TESTS
# =====================================================================

class TestDesktopController:
    def test_controller_delegates_to_platform(self):
        mock_platform = MagicMock()
        mock_platform.search_applications.return_value = [{"name": "MockApp"}]
        mock_platform.get_application_details.return_value = {"found": True, "name": "MockApp"}
        mock_platform.get_running_applications_structured.return_value = [{"name": "MockApp", "pid": 100}]
        mock_platform.get_application_resources.return_value = {"status": "success"}
        mock_platform.terminate_application.return_value = {"status": "success"}
        mock_platform.open_url.return_value = {"status": "success"}

        controller = DesktopController(platform=mock_platform)

        # search_apps
        res = json.loads(controller.search_apps("test"))
        assert res["count"] == 1
        mock_platform.search_applications.assert_called_with("test")

        # app_details
        res = json.loads(controller.app_details("MockApp"))
        assert res["found"] is True
        mock_platform.get_application_details.assert_called_with("MockApp")

        # running_apps_detailed
        res = json.loads(controller.running_apps_detailed())
        assert res["count"] == 1
        mock_platform.get_running_applications_structured.assert_called_once()

        # app_resources
        res = json.loads(controller.app_resources("MockApp"))
        assert res["status"] == "success"
        mock_platform.get_application_resources.assert_called_with("MockApp")

        # terminate_app
        res = json.loads(controller.terminate_app("MockApp"))
        assert res["status"] == "success"
        mock_platform.terminate_application.assert_called_with("MockApp")

        # open_url
        res = json.loads(controller.open_url("https://example.com"))
        assert res["status"] == "success"
        mock_platform.open_url.assert_called_with("https://example.com", browser=None)


# =====================================================================
# 10. TOOL EXECUTOR VERIFICATION INTEGRATION
# =====================================================================

class TestToolExecutorVerification:
    def test_tool_executor_verifies_terminate_app(self):
        from core.agent.tool_executor import ToolExecutor
        from core.agent.tool_registry import ToolRegistry
        te = ToolExecutor(ToolRegistry())

        # Missing app_name
        ver = te.verify("desktop_terminate_app", {}, "{}")
        assert ver["success"] is False
        assert "missing" in ver["reason"].lower()

        # Termination failed in payload
        ver = te.verify(
            "desktop_terminate_app",
            {"app_name": "Calculator"},
            json.dumps({"status": "failed", "error": "Application refused to quit."}),
        )
        assert ver["success"] is False

        # Termination success and confirmed closed via pgrep
        with patch.object(te, "_verify_application_closed", return_value={"success": True, "reason": "Closed"}):
            ver = te.verify(
                "desktop_terminate_app",
                {"app_name": "Calculator"},
                json.dumps({"status": "success"}),
            )
            assert ver["success"] is True

    def test_tool_executor_verifies_open_url(self):
        from core.agent.tool_executor import ToolExecutor
        from core.agent.tool_registry import ToolRegistry
        te = ToolExecutor(ToolRegistry())

        # Success
        ver = te.verify(
            "desktop_open_url",
            {"url": "https://python.org"},
            json.dumps({"status": "success"}),
        )
        assert ver["success"] is True

        # Failure
        ver = te.verify(
            "desktop_open_url",
            {"url": "javascript:bad()"},
            json.dumps({"status": "failed", "error": "Scheme not allowed"}),
        )
        assert ver["success"] is False
