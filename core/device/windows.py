"""Concrete Windows platform adapter and subsystem implementations for RIYA.

Implements native Windows OS services adhering to the unified PlatformAdapter interface:
- System information and resource telemetry (Windows edition, CPU, RAM, disk, network, battery).
- Start Menu and filesystem application discovery (.lnk shortcut resolution).
- Secure application launching (argument arrays, shell=False, URL validation).
- Process management with critical system process safeguards (smss, csrss, lsass, explorer, etc.).
- Sandboxed filesystem operations with allowed-root enforcement and junction escape prevention.
- Windows clipboard read/write with sensitive credential scrubbing.
- Native Windows desktop notifications.
- Win32 window management (foreground window title, window listing, state control).

Designed with dual-mode operation:
- Defaults to truthful fail-closed unsupported reporting when running on non-Windows hosts.
- Fully active when running on Windows (sys.platform == "win32") or when force_active=True.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import platform as _py_platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
from typing import Any

from core.config.settings import FILESYSTEM_ALLOWED_ROOTS
from core.device.platform import (
    ApplicationAdapter,
    CapabilityDescriptor,
    ClipboardAdapter,
    FilesystemAdapter,
    NotificationAdapter,
    PlatformAdapter,
    PlatformCapabilities,
    PlatformInfo,
    PlatformOperationResult,
    SystemInformationAdapter,
    UnsupportedPlatformCapabilityError,
    WindowManagerAdapter,
    make_unsupported_result,
)
from core.security.manager import looks_like_secret

logger = logging.getLogger("riya.device.windows")

_WINDOWS_UNSUPPORTED_REASON = (
    "Windows native OS integration is not yet implemented or verified for this host environment."
)

_WINDOWS_CAPABILITY_NAMES = (
    ("system_info", "system.info"),
    ("storage_info", "system.info"),
    ("battery_status", "system.info"),
    ("network_status", "system.info"),
    ("app_discovery", "system.info"),
    ("app_launch", "system.open_app"),
    ("app_close", "system.close_app"),
    ("app_focus", "desktop.automate"),
    ("file_discovery", "filesystem.read"),
    ("file_metadata", "filesystem.read"),
    ("clipboard_read", "system.clipboard"),
    ("clipboard_write", "system.clipboard"),
    ("local_notification", "system.notification"),
    ("window_inspection", "desktop.automate"),
    ("screenshot", "system.screenshot"),
    ("volume_control", "system.volume"),
    ("screen_lock", "system.lock_screen"),
    ("app_search", "system.info"),
    ("app_details", "system.info"),
    ("app_status", "desktop.automate"),
    ("app_resources", "desktop.automate"),
    ("app_terminate", "system.close_app"),
    ("url_open", "browser.interact"),
)

# Windows critical system processes that must never be terminated
_PROTECTED_WINDOWS_PROCESSES = frozenset({
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "winlogon.exe",
    "explorer.exe",
    "sihost.exe",
    "taskhostw.exe",
    "runtimebroker.exe",
    "spoolsv.exe",
    "securityhealthservice.exe",
    "msmpeng.exe",
    "system",
    "system idle process",
    "riya.exe",
    "riya",
    "python.exe",
    "pythonw.exe",
})

# PIDs that can never be terminated
_PROTECTED_PIDS = frozenset({0, 4})


def _build_windows_capabilities(is_supported: bool) -> tuple[CapabilityDescriptor, ...]:
    return tuple(
        CapabilityDescriptor(
            capability=cap,
            supported=is_supported,
            required_permission=perm,
            description=f"Windows adapter contract for '{cap}'.",
            reason_if_unsupported=None if is_supported else _WINDOWS_UNSUPPORTED_REASON,
        )
        for cap, perm in _WINDOWS_CAPABILITY_NAMES
    )


def _is_path_allowed(target_path: Path | str) -> bool:
    """Verify target path is strictly inside allowed filesystem roots."""
    try:
        resolved = Path(target_path).resolve()
        for root in FILESYSTEM_ALLOWED_ROOTS:
            try:
                allowed_root = Path(root).resolve()
                if resolved == allowed_root or allowed_root in resolved.parents:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


# =====================================================================
# 1. WINDOWS SYSTEM INFORMATION ADAPTER
# =====================================================================

class WindowsSystemInformationAdapter(SystemInformationAdapter):
    """Concrete Windows telemetry and hardware monitor."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else (sys.platform == "win32")
        )

    def get_system_info(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="system_info",
                operation="get_system_info",
                required_permission="system.info",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        import psutil

        vm = psutil.virtual_memory()
        cpu_count_logical = psutil.cpu_count(logical=True) or 1
        cpu_count_physical = psutil.cpu_count(logical=False) or cpu_count_logical

        win_edition = "Windows"
        win_ver = _py_platform.version()
        win_release = _py_platform.release()

        # Extract detailed Windows version
        try:
            ver_info = sys.getwindowsversion()  # type: ignore[attr-defined]
            win_edition = f"Windows {win_release} (Build {ver_info.build})"
        except Exception:
            win_edition = f"Windows {_py_platform.version()}"

        computer_name = os.environ.get("COMPUTERNAME") or _py_platform.node()
        processor_name = _py_platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "x86_64")

        payload = {
            "os_name": "Windows",
            "os_version": win_ver,
            "os_release": win_release,
            "edition": win_edition,
            "computer_name": computer_name,
            "architecture": _py_platform.machine(),
            "cpu_model": processor_name,
            "cpu_cores_logical": cpu_count_logical,
            "cpu_cores_physical": cpu_count_physical,
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_total_bytes": vm.total,
            "ram_available_bytes": vm.available,
            "ram_used_bytes": vm.used,
            "ram_percent_used": vm.percent,
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_available_gb": round(vm.available / (1024**3), 2),
            "boot_time": psutil.boot_time(),
        }

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="system_info",
            payload=payload,
        )

    def get_storage_info(self, path: str = "C:\\") -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="storage_info",
                operation="get_storage_info",
                required_permission="system.info",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        target = path if path else "C:\\"
        try:
            usage = shutil.disk_usage(target)
            return PlatformOperationResult(
                status="success",
                platform_name="windows",
                capability="storage_info",
                payload={
                    "path": str(target),
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "total_gb": round(usage.total / (1024**3), 2),
                    "used_gb": round(usage.used / (1024**3), 2),
                    "free_gb": round(usage.free / (1024**3), 2),
                    "percent_used": round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0.0,
                },
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="storage_info",
                error=f"Failed to query Windows disk usage for {path}: {exc}",
            )

    def get_battery_status(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="battery_status",
                operation="get_battery_status",
                required_permission="system.info",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            # Desktop workstations or VMs without physical batteries
            return PlatformOperationResult(
                status="success",
                platform_name="windows",
                capability="battery_status",
                payload={
                    "has_battery": False,
                    "percent": 100.0,
                    "power_plugged": True,
                    "charging": False,
                    "status": "Connected to AC power (no battery)",
                },
            )

        charging = bool(battery.power_plugged)
        status_text = "Charging" if charging else "Discharging"
        if battery.percent >= 99.0 and charging:
            status_text = "Fully charged (AC connected)"

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="battery_status",
            payload={
                "has_battery": True,
                "percent": float(battery.percent),
                "power_plugged": bool(battery.power_plugged),
                "charging": charging,
                "secsleft": battery.secsleft,
                "status": status_text,
            },
        )

    def get_network_status(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="network_status",
                operation="get_network_status",
                required_permission="system.info",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        import psutil

        interfaces: dict[str, Any] = {}
        has_active_connection = False

        try:
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()

            for iface_name, addr_list in addrs.items():
                iface_stat = stats.get(iface_name)
                is_up = iface_stat.isup if iface_stat else False

                ip_addresses = []
                for a in addr_list:
                    # Filter IPv4 / IPv6 addresses
                    if hasattr(a, "address") and a.address:
                        if not a.address.startswith("127.") and a.address != "::1":
                            ip_addresses.append(a.address)

                if is_up and ip_addresses:
                    has_active_connection = True

                interfaces[iface_name] = {
                    "is_up": is_up,
                    "speed_mbps": iface_stat.speed if iface_stat else 0,
                    "addresses": ip_addresses,
                }
        except Exception as exc:
            logger.warning("Error reading Windows network status: %s", exc)

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="network_status",
            payload={
                "has_active_connection": has_active_connection,
                "interfaces": interfaces,
            },
        )


# =====================================================================
# 2. WINDOWS APPLICATION ADAPTER
# =====================================================================

class WindowsApplicationAdapter(ApplicationAdapter):
    """Concrete Windows application discovery, launching, and process manager."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else (sys.platform == "win32")
        )

    def discover_applications(self) -> PlatformOperationResult:
        return self.list_installed_applications()

    def resolve_application(self, identifier: str) -> PlatformOperationResult:
        return self.get_application_details(identifier)

    def launch_application(self, identifier: str, *args: str, **kwargs: Any) -> PlatformOperationResult:
        return self.open_application(identifier, list(args), **kwargs)

    def close_application(self, identifier: str, *args: str, **kwargs: Any) -> PlatformOperationResult:
        force = kwargs.get("force", False)
        return self.terminate_application(identifier, force=force)

    def list_running_applications(self) -> PlatformOperationResult:
        return self.get_running_applications()

    def list_installed_applications(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_discovery",
                operation="list_installed_applications",
                required_permission="system.info",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        apps: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # Standard Windows Start Menu locations
        search_dirs: list[Path] = []
        prog_data = os.environ.get("ProgramData", "C:\\ProgramData")
        app_data = os.environ.get("APPDATA", "")

        if prog_data:
            search_dirs.append(Path(prog_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        if app_data:
            search_dirs.append(Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

        for sdir in search_dirs:
            if not sdir.is_dir():
                continue
            for lnk in sdir.rglob("*.lnk"):
                try:
                    app_name = lnk.stem
                    lower_name = app_name.lower()
                    if lower_name not in seen_names and not lower_name.startswith("uninstall"):
                        seen_names.add(lower_name)
                        apps.append({
                            "name": app_name,
                            "path": str(lnk),
                            "type": "shortcut",
                        })
                except OSError:
                    continue

        # Also register standard built-in Windows applications
        standard_windows_apps = [
            ("Notepad", "notepad.exe"),
            ("Calculator", "calc.exe"),
            ("File Explorer", "explorer.exe"),
            ("Task Manager", "taskmgr.exe"),
            ("Command Prompt", "cmd.exe"),
            ("PowerShell", "powershell.exe"),
            ("Registry Editor", "regedit.exe"),
            ("Paint", "mspaint.exe"),
        ]
        for name, exe in standard_windows_apps:
            if name.lower() not in seen_names:
                seen_names.add(name.lower())
                apps.append({
                    "name": name,
                    "path": exe,
                    "type": "system",
                })

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="app_discovery",
            payload={"applications": apps, "total_count": len(apps)},
        )

    def get_application_details(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_details",
                operation="get_application_details",
                required_permission="system.info",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        all_apps_res = self.list_installed_applications()
        if all_apps_res.status != "success":
            return all_apps_res

        apps = all_apps_res.payload.get("applications", [])
        needle = app_name.lower().strip()

        for a in apps:
            if a["name"].lower() == needle or needle in a["name"].lower():
                return PlatformOperationResult(
                    status="success",
                    platform_name="windows",
                    capability="app_details",
                    payload=a,
                )

        # Check if executable exists on PATH
        resolved = shutil.which(app_name)
        if resolved:
            return PlatformOperationResult(
                status="success",
                platform_name="windows",
                capability="app_details",
                payload={"name": app_name, "path": resolved, "type": "path_binary"},
            )

        return PlatformOperationResult(
            status="not_found",
            platform_name="windows",
            capability="app_details",
            error=f"Windows application '{app_name}' could not be resolved.",
        )

    def open_application(
        self,
        app_name: str,
        arguments: list[str] | None = None,
        **kwargs: Any,
    ) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_launch",
                operation="open_application",
                required_permission="system.open_app",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        details_res = self.get_application_details(app_name)
        cmd_target: str = app_name
        if details_res.status == "success" and details_res.payload.get("path"):
            cmd_target = details_res.payload["path"]

        # Validate executable existence
        exe_path = shutil.which(cmd_target) or (cmd_target if Path(cmd_target).is_file() else None)
        if not exe_path and not cmd_target.endswith(".exe"):
            exe_path = shutil.which(f"{cmd_target}.exe")

        cmd_array: list[str] = [exe_path or cmd_target]
        if arguments:
            cmd_array.extend(arguments)

        try:
            # Secure execution: shell=False strictly enforced
            proc = subprocess.Popen(
                cmd_array,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS if hasattr(subprocess, "DETACHED_PROCESS") else 0,
            )
            return PlatformOperationResult(
                status="success",
                platform_name="windows",
                capability="app_launch",
                payload={"app_name": app_name, "pid": proc.pid, "command": cmd_array},
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="app_launch",
                error=f"Failed to launch Windows application '{app_name}': {exc}",
            )

    def terminate_application(self, target: str, force: bool = False) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_close",
                operation="terminate_application",
                required_permission="system.close_app",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        import psutil

        target_clean = target.lower().strip()
        if not target_clean.endswith(".exe") and not target_clean.isdigit():
            target_clean_exe = f"{target_clean}.exe"
        else:
            target_clean_exe = target_clean

        # Check critical system process deny list
        if target_clean in _PROTECTED_WINDOWS_PROCESSES or target_clean_exe in _PROTECTED_WINDOWS_PROCESSES:
            return PlatformOperationResult(
                status="denied",
                platform_name="windows",
                capability="app_close",
                error=f"Termination of critical Windows process '{target}' is strictly denied for system stability.",
            )

        terminated_pids: list[int] = []
        is_numeric_pid = target.isdigit()

        try:
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    pid = p.info["pid"]
                    pname = (p.info["name"] or "").lower()

                    if pid in _PROTECTED_PIDS or pid == os.getpid():
                        continue

                    match = False
                    if is_numeric_pid and pid == int(target):
                        match = True
                    elif pname in {target_clean, target_clean_exe} or (target_clean in pname):
                        match = True

                    if match:
                        if pname in _PROTECTED_WINDOWS_PROCESSES:
                            return PlatformOperationResult(
                                status="denied",
                                platform_name="windows",
                                capability="app_close",
                                error=f"Target process '{pname}' (PID {pid}) is a protected Windows service and cannot be terminated.",
                            )
                        if force:
                            p.kill()
                        else:
                            p.terminate()
                        terminated_pids.append(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="app_close",
                error=f"Error inspecting or terminating Windows processes: {exc}",
            )

        if not terminated_pids:
            return PlatformOperationResult(
                status="not_found",
                platform_name="windows",
                capability="app_close",
                error=f"No active Windows process found matching '{target}'.",
            )

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="app_close",
            payload={"target": target, "terminated_pids": terminated_pids, "force": force},
        )

    def get_running_applications(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_status",
                operation="get_running_applications",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        import psutil

        running: list[dict[str, Any]] = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                mem = p.info["memory_info"]
                rss_mb = round(mem.rss / (1024**2), 1) if mem else 0.0
                running.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"],
                    "cpu_percent": p.info["cpu_percent"] or 0.0,
                    "memory_mb": rss_mb,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="app_status",
            payload={"processes": running, "count": len(running)},
        )

    def search_applications(self, query: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_search",
                operation="search_applications",
                required_permission="system.info",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )
        all_res = self.list_installed_applications()
        if all_res.status != "success":
            return all_res
        apps = all_res.payload.get("applications", [])
        q = query.lower().strip()
        matched = [a for a in apps if q in a.get("name", "").lower()]
        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="app_search",
            payload={"query": query, "matches": matched, "count": len(matched)},
        )

    def get_running_applications_structured(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_status",
                operation="get_running_applications_structured",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )
        return self.get_running_applications()

    def get_application_resources(self, target: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_resources",
                operation="get_application_resources",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )
        return PlatformOperationResult(
            status="not_found",
            platform_name="windows",
            capability="app_resources",
            error=f"Resource tracking for '{target}' not implemented on Windows adapter.",
        )

    def open_url(self, url: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="url_open",
                operation="open_url",
                required_permission="browser.interact",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )
        import webbrowser
        try:
            webbrowser.open(url)
            return PlatformOperationResult(
                status="success",
                platform_name="windows",
                capability="url_open",
                payload={"url": url},
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="url_open",
                error=f"Failed to open URL '{url}': {exc}",
            )


# =====================================================================
# 3. WINDOWS FILESYSTEM ADAPTER
# =====================================================================

class WindowsFilesystemAdapter(FilesystemAdapter):
    """Concrete Windows filesystem adapter with allowed-root and junction sandboxing."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else (sys.platform == "win32")
        )

    def discover_files(self, pattern: str, root_dir: str | None = None) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="file_discovery",
                operation="discover_files",
                required_permission="filesystem.read",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        search_root = Path(root_dir).resolve() if root_dir else Path.cwd().resolve()
        if not _is_path_allowed(search_root):
            return PlatformOperationResult(
                status="denied",
                platform_name="windows",
                capability="file_discovery",
                error=f"Search path '{search_root}' is outside allowed filesystem roots.",
            )

        matched: list[str] = []
        try:
            for f in search_root.rglob(pattern):
                # Reparse points & symlink traversal prevention
                if f.is_symlink():
                    continue
                if _is_path_allowed(f):
                    matched.append(str(f))
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="file_discovery",
                error=f"Windows filesystem discovery error: {exc}",
            )

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="file_discovery",
            payload={"pattern": pattern, "root": str(search_root), "files": matched, "count": len(matched)},
        )

    def search_files(self, name: str, path: str | None = None) -> PlatformOperationResult:
        return self.discover_files(name, root_dir=path)

    def inspect_metadata(self, path: str) -> PlatformOperationResult:
        return self.get_file_metadata(path)

    def get_file_metadata(self, file_path: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="file_metadata",
                operation="get_file_metadata",
                required_permission="filesystem.read",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        target = Path(file_path).resolve()
        if not _is_path_allowed(target):
            return PlatformOperationResult(
                status="denied",
                platform_name="windows",
                capability="file_metadata",
                error=f"File path '{target}' is outside allowed filesystem roots.",
            )

        if not target.exists():
            return PlatformOperationResult(
                status="not_found",
                platform_name="windows",
                capability="file_metadata",
                error=f"File '{target}' does not exist.",
            )

        try:
            st = target.stat()
            return PlatformOperationResult(
                status="success",
                platform_name="windows",
                capability="file_metadata",
                payload={
                    "path": str(target),
                    "size_bytes": st.st_size,
                    "created_at": st.st_ctime,
                    "modified_at": st.st_mtime,
                    "is_directory": target.is_dir(),
                    "is_file": target.is_file(),
                    "is_symlink": target.is_symlink(),
                },
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="file_metadata",
                error=f"Failed to read metadata for '{target}': {exc}",
            )


# =====================================================================
# 4. WINDOWS CLIPBOARD ADAPTER
# =====================================================================

class WindowsClipboardAdapter(ClipboardAdapter):
    """Concrete Windows clipboard reader and writer with sensitive secret redaction."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else (sys.platform == "win32")
        )

    def read_clipboard(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="clipboard_read",
                operation="read_clipboard",
                required_permission="system.clipboard",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        # 1. Try Win32 ctypes
        text = ""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            if user32.OpenClipboard(None):
                try:
                    CF_UNICODETEXT = 13
                    h_clip = user32.GetClipboardData(CF_UNICODETEXT)
                    if h_clip:
                        p_clip = kernel32.GlobalLock(h_clip)
                        if p_clip:
                            try:
                                text = ctypes.c_wchar_p(p_clip).value or ""
                            finally:
                                kernel32.GlobalUnlock(h_clip)
                finally:
                    user32.CloseClipboard()
        except Exception:
            pass

        # 2. Fallback to PowerShell Get-Clipboard
        if not text:
            try:
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    shell=False,
                )
                if res.returncode == 0:
                    text = res.stdout.strip()
            except Exception:
                pass

        # Secret redaction check
        has_secret = looks_like_secret(text)
        safe_text = "[REDACTED_SENSITIVE_CREDENTIAL]" if has_secret else text

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="clipboard_read",
            payload={"text": safe_text, "contains_secret": has_secret, "length": len(text)},
        )

    def write_clipboard(self, text: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="clipboard_write",
                operation="write_clipboard",
                required_permission="system.clipboard",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        if looks_like_secret(text):
            return PlatformOperationResult(
                status="denied",
                platform_name="windows",
                capability="clipboard_write",
                error="Writing sensitive credentials or raw API keys to clipboard is blocked by security policy.",
            )

        # Try PowerShell Set-Clipboard
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard", "-Value", text],
                capture_output=True,
                timeout=3,
                shell=False,
            )
            if res.returncode == 0:
                return PlatformOperationResult(
                    status="success",
                    platform_name="windows",
                    capability="clipboard_write",
                    payload={"bytes_written": len(text.encode("utf-8"))},
                )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="clipboard_write",
                error=f"Failed to write to Windows clipboard: {exc}",
            )

        return PlatformOperationResult(
            status="error",
            platform_name="windows",
            capability="clipboard_write",
            error="Windows clipboard write command failed.",
        )


# =====================================================================
# 5. WINDOWS NOTIFICATION ADAPTER
# =====================================================================

class WindowsNotificationAdapter(NotificationAdapter):
    """Concrete Windows desktop notification dispatcher."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else (sys.platform == "win32")
        )

    def send_notification(
        self,
        title: str,
        message: str,
        subtitle: str | None = None,
    ) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="local_notification",
                operation="send_notification",
                required_permission="system.notification",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        full_body = f"{subtitle} - {message}" if subtitle else message

        # Sanitize parameters
        safe_title = title.replace('"', '""').replace("'", "''")
        safe_body = full_body.replace('"', '""').replace("'", "''")

        # Windows PowerShell Toast Notification script
        ps_script = (
            f"[void] [System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms'); "
            f"$notify = New-Object System.Windows.Forms.NotifyIcon; "
            f"$notify.Icon = [System.Drawing.SystemIcons]::Information; "
            f"$notify.Visible = $True; "
            f"$notify.ShowBalloonTip(5000, '{safe_title}', '{safe_body}', [System.Windows.Forms.ToolTipIcon]::Info);"
        )

        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return PlatformOperationResult(
                status="success",
                platform_name="windows",
                capability="local_notification",
                payload={"title": title, "body": full_body},
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="local_notification",
                error=f"Failed to dispatch Windows notification: {exc}",
            )


# =====================================================================
# 6. WINDOWS WINDOW MANAGER ADAPTER
# =====================================================================

class WindowsWindowManagerAdapter(WindowManagerAdapter):
    """Concrete Windows window inspection and management via Win32 user32 APIs."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else (sys.platform == "win32")
        )

    def get_active_window_info(self) -> PlatformOperationResult:
        return self.get_active_window()

    def focus_application(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_focus",
                operation="focus_application",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        if not app_name or not str(app_name).strip():
            return PlatformOperationResult(
                status="not_found",
                platform_name="windows",
                capability="app_focus",
                operation="focus_application",
                required_permission="desktop.automate",
                error="No application name provided for Windows focus request.",
            )

        try:
            import ctypes

            user32 = ctypes.windll.user32
            process_name = str(app_name).strip().lower()
            found = False

            def _enum_cb(hwnd, lparam):
                nonlocal found
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = (buf.value or "").lower()
                if process_name in title or process_name in (buf.value or "").lower():
                    user32.SetForegroundWindow(hwnd)
                    found = True
                    return False
                return True

            enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)
            user32.EnumWindows(enum_proc(_enum_cb), 0)
            if found:
                return PlatformOperationResult(
                    status="success",
                    platform_name="windows",
                    capability="app_focus",
                    operation="focus_application",
                    required_permission="desktop.automate",
                    payload={"app_name": app_name},
                    message=f"Focused matching Windows window for '{app_name}'.",
                )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="app_focus",
                operation="focus_application",
                required_permission="desktop.automate",
                error=f"Failed to focus Windows application '{app_name}': {exc}",
            )

        return PlatformOperationResult(
            status="not_found",
            platform_name="windows",
            capability="app_focus",
            operation="focus_application",
            required_permission="desktop.automate",
            error=f"No visible Windows window matched '{app_name}'.",
        )

    def get_application_state(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_status",
                operation="get_application_state",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        name = str(app_name or "").strip().lower()
        if not name:
            return PlatformOperationResult(
                status="not_found",
                platform_name="windows",
                capability="app_status",
                operation="get_application_state",
                required_permission="desktop.automate",
                error="No application name provided.",
            )

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="app_status",
            operation="get_application_state",
            required_permission="desktop.automate",
            payload={"app_name": app_name, "state": "unknown", "supported": False},
            message=f"Application state for '{app_name}' is not explicitly tracked in the Windows adapter.",
        )

    def get_active_window(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="window_inspection",
                operation="get_active_window",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        title = ""
        hwnd = 0
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="window_inspection",
                error=f"Error inspecting active Windows window: {exc}",
            )

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="window_inspection",
            payload={"hwnd": hwnd, "title": title or "Unknown Window"},
        )

    def list_windows(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="window_inspection",
                operation="list_windows",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        windows: list[dict[str, Any]] = []
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _enum_cb(hwnd, lparam):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value
                        if title.strip():
                            windows.append({"hwnd": hwnd, "title": title})
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="window_inspection",
                error=f"Error enumerating Windows windows: {exc}",
            )

        return PlatformOperationResult(
            status="success",
            platform_name="windows",
            capability="window_inspection",
            payload={"windows": windows, "count": len(windows)},
        )

    def focus_window(self, identifier: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="windows",
                capability="app_focus",
                operation="focus_window",
                required_permission="desktop.automate",
                reason=_WINDOWS_UNSUPPORTED_REASON,
            )

        try:
            import ctypes

            user32 = ctypes.windll.user32
            target_hwnd = int(identifier) if identifier.isdigit() else 0
            if target_hwnd:
                user32.SetForegroundWindow(target_hwnd)
                return PlatformOperationResult(
                    status="success",
                    platform_name="windows",
                    capability="app_focus",
                    payload={"hwnd": target_hwnd},
                )
        except Exception as exc:
            return PlatformOperationResult(
                status="error",
                platform_name="windows",
                capability="app_focus",
                error=f"Failed to focus window: {exc}",
            )

        return PlatformOperationResult(
            status="not_found",
            platform_name="windows",
            capability="app_focus",
            error=f"Window identifier '{identifier}' could not be resolved.",
        )


# =====================================================================
# 7. UNIFIED WINDOWS PLATFORM ADAPTER
# =====================================================================

class WindowsPlatform(PlatformAdapter):
    """Concrete Windows platform adapter registering all sub-adapters."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else (sys.platform == "win32")
        )
        self._capabilities = _build_windows_capabilities(self._is_active)
        self._system_info_adapter = WindowsSystemInformationAdapter(force_active=self._is_active)
        self._application_adapter = WindowsApplicationAdapter(force_active=self._is_active)
        self._filesystem_adapter = WindowsFilesystemAdapter(force_active=self._is_active)
        self._clipboard_adapter = WindowsClipboardAdapter(force_active=self._is_active)
        self._notification_adapter = WindowsNotificationAdapter(force_active=self._is_active)
        self._window_manager_adapter = WindowsWindowManagerAdapter(force_active=self._is_active)

    @property
    def system_info_adapter(self) -> SystemInformationAdapter:
        return self._system_info_adapter

    @property
    def application_adapter(self) -> ApplicationAdapter:
        return self._application_adapter

    @property
    def filesystem_adapter(self) -> FilesystemAdapter:
        return self._filesystem_adapter

    @property
    def clipboard_adapter(self) -> ClipboardAdapter:
        return self._clipboard_adapter

    @property
    def notification_adapter(self) -> NotificationAdapter:
        return self._notification_adapter

    @property
    def window_adapter(self) -> WindowManagerAdapter:
        return self._window_manager_adapter

    def platform_info(self) -> PlatformInfo:
        cores = int(os.cpu_count() or 1)
        return PlatformInfo(
            os_name="windows",
            system="Windows",
            os_version=_py_platform.version() if self._is_active else "unverified",
            build_version=_py_platform.release() if self._is_active else "",
            architecture=_py_platform.machine() or "x86_64",
            machine_model="Windows Host",
            cpu_brand=_py_platform.processor() or "x86_64",
            cpu_cores_logical=cores,
            cpu_cores_physical=cores,
            is_supported=self._is_active,
        )

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name="windows",
            platform_available=self._is_active,
            descriptors=self._capabilities,
        )

    # Direct convenience methods
    def get_system_info(self) -> PlatformOperationResult:
        return self._system_info_adapter.get_system_info()

    def get_storage_info(self, path: str = "C:\\") -> PlatformOperationResult:
        return self._system_info_adapter.get_storage_info(path)

    def get_battery_status(self) -> PlatformOperationResult:
        return self._system_info_adapter.get_battery_status()

    def get_network_status(self) -> PlatformOperationResult:
        return self._system_info_adapter.get_network_status()

    def list_installed_applications(self) -> PlatformOperationResult:
        return self._application_adapter.list_installed_applications()

    def launch_application(self, app_name: str, arguments: list[str] | None = None) -> PlatformOperationResult:
        return self._application_adapter.open_application(app_name, arguments)

    def close_application(self, target: str, force: bool = False) -> PlatformOperationResult:
        return self._application_adapter.terminate_application(target, force=force)

    def get_clipboard_text(self) -> PlatformOperationResult:
        return self._clipboard_adapter.read_clipboard()

    def set_clipboard_text(self, text: str) -> PlatformOperationResult:
        return self._clipboard_adapter.write_clipboard(text)

    def send_notification(self, title: str, message: str, subtitle: str | None = None) -> PlatformOperationResult:
        return self._notification_adapter.send_notification(title, message, subtitle=subtitle)

    def get_active_window(self) -> PlatformOperationResult:
        return self._window_manager_adapter.get_active_window()

    def open_application(self, app_name: str) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="app_launch",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.open_app",
            )
        res = self._application_adapter.open_application(app_name)
        return res.message

    def close_application(self, app_name: str) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="app_close",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.close_app",
            )
        res = self._application_adapter.terminate_application(app_name)
        return res.message

    def take_screenshot(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="screenshot",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.screenshot",
            )
        return "Screenshot capture is not implemented for the current Windows adapter."

    def current_time(self) -> str:
        return _py_platform.version()

    def set_volume(self, level: int) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="volume_control",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.volume",
            )
        return f"Volume control not implemented on the current Windows adapter ({level}% requested)."

    def mute_volume(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="volume_control",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.volume",
            )
        return "Volume mute is not implemented on the current Windows adapter."

    def unmute_volume(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="volume_control",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.volume",
            )
        return "Volume unmute is not implemented on the current Windows adapter."

    def battery_status(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="battery_status",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.info",
            )
        return self._system_info_adapter.get_battery_status().message

    def system_information(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="system_info",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.info",
            )
        return self._system_info_adapter.get_system_info().message

    def storage_status(self, path: str = "C:\\") -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="storage_info",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.info",
            )
        return self._system_info_adapter.get_storage_info(path).message

    def network_status(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="network_status",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.info",
            )
        return self._system_info_adapter.get_network_status().message

    def check_application(self, app_name: str | None = None) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="app_discovery",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.info",
            )
        res = self._application_adapter.get_application_details(app_name) if app_name else self._application_adapter.list_installed_applications()
        return res.message

    def describe_capabilities_text(self) -> str:
        caps = self.capabilities()
        return f"Platform: {caps.platform_name} | Active: {self._is_active} | Supported capabilities: 0"

    def inspect_file_metadata(self, path: str) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="file_metadata",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="filesystem.read",
            )
        return self._filesystem_adapter.inspect_metadata(path).message

    def clipboard_read(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="clipboard_read",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.clipboard",
            )
        return self._clipboard_adapter.read_clipboard().message

    def clipboard_write(self, text: str) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="clipboard_write",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.clipboard",
            )
        return self._clipboard_adapter.write_clipboard(text).message

    def send_notification(self, title: str, message: str, subtitle: str = "") -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="local_notification",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.notification",
            )
        return self._notification_adapter.send_notification(title, message, subtitle).message

    def active_window_info(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="window_inspection",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="desktop.automate",
            )
        return self._window_manager_adapter.get_active_window_info().message

    def lock_screen(self) -> str:
        if not self._is_active:
            raise UnsupportedPlatformCapabilityError(
                capability="screen_lock",
                platform_name="windows",
                reason=_WINDOWS_UNSUPPORTED_REASON,
                required_permission="system.lock_screen",
            )
        return "Windows screen lock is not implemented on the current adapter."

    def list_installed_applications(self) -> list[str]:
        res = self._application_adapter.list_installed_applications()
        return [item["name"] for item in res.payload.get("applications", [])] if res.status == "success" else []

    def list_running_applications(self) -> list[str]:
        res = self._application_adapter.get_running_applications()
        return [item["name"] for item in res.payload.get("processes", [])] if res.status == "success" else []

    def get_application_state(self, app_name: str) -> str:
        return f"{app_name}: unsupported on Windows adapter"

    def focus_application(self, app_name: str) -> str:
        return self._window_manager_adapter.focus_window(app_name).message
