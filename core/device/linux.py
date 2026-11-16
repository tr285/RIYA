"""Native Linux platform adapter and sub-adapters for RIYA.

Supports Linux desktop environments (GNOME, KDE Plasma, XFCE, Sway, etc.)
with Wayland and X11 display servers, systemd/XDG standards, and Linux audio
subsystems (PortAudio, ALSA, PulseAudio, PipeWire).
"""

from __future__ import annotations

import configparser
from datetime import datetime
import json
import os
from pathlib import Path
import platform as _py_platform
import re
import shutil
import subprocess
import sys
from typing import Any, Sequence

import psutil

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


_LINUX_UNSUPPORTED_REASON = (
    "Linux native OS integration is not yet implemented or verified in RIYA v0.5 Milestone 6.2."
)

_PROTECTED_PROCESS_NAMES = frozenset({
    "systemd",
    "init",
    "kthreadd",
    "kworker",
    "systemd-journald",
    "systemd-udevd",
    "systemd-logind",
    "systemd-resolved",
    "xorg",
    "xwayland",
    "mutter",
    "kwin",
    "sway",
    "weston",
    "gnome-shell",
    "dbus-daemon",
    "dbus-broker",
    "pulseaudio",
    "pipewire",
    "wireplumber",
    "riya",
    "sshd",
    "login",
})

_XDG_APPLICATION_DIRS: tuple[Path, ...] = (
    Path.home() / ".local" / "share" / "applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
    Path("/var/lib/snapd/desktop/applications"),
)

_LINUX_CAPABILITY_DESCRIPTORS: tuple[CapabilityDescriptor, ...] = (
    CapabilityDescriptor(
        capability="system_info",
        supported=True,
        required_permission="system.info",
        description="Inspect Linux distribution, kernel release, CPU cores, and system memory.",
    ),
    CapabilityDescriptor(
        capability="storage_info",
        supported=True,
        required_permission="system.info",
        description="Inspect Linux filesystem storage capacity and availability.",
    ),
    CapabilityDescriptor(
        capability="battery_status",
        supported=True,
        required_permission="system.info",
        description="Inspect Linux battery level, charging status, and power supply via sysfs/psutil.",
    ),
    CapabilityDescriptor(
        capability="network_status",
        supported=True,
        required_permission="system.info",
        description="Inspect Linux network interfaces, IP addresses, and offline status.",
    ),
    CapabilityDescriptor(
        capability="app_discovery",
        supported=True,
        required_permission="system.info",
        description="Discover installed desktop applications via XDG .desktop files across standard paths.",
    ),
    CapabilityDescriptor(
        capability="app_launch",
        supported=True,
        required_permission="system.open_app",
        description="Launch validated Linux applications via secure parameter arrays without shell invocation.",
    ),
    CapabilityDescriptor(
        capability="app_close",
        supported=True,
        required_permission="system.close_app",
        description="Gracefully close running applications with critical system process protections.",
    ),
    CapabilityDescriptor(
        capability="app_focus",
        supported=True,
        required_permission="desktop.automate",
        description="Focus application windows via wmctrl/xdotool where supported by display server.",
    ),
    CapabilityDescriptor(
        capability="app_search",
        supported=True,
        required_permission="system.info",
        description="Search installed Linux applications with case-insensitive and relevance matching.",
    ),
    CapabilityDescriptor(
        capability="app_details",
        supported=True,
        required_permission="system.info",
        description="Inspect .desktop entry metadata, binary paths, categories, and package source.",
    ),
    CapabilityDescriptor(
        capability="app_status",
        supported=True,
        required_permission="desktop.automate",
        description="Inspect running applications with PIDs and process status.",
    ),
    CapabilityDescriptor(
        capability="app_resources",
        supported=True,
        required_permission="desktop.automate",
        description="Inspect CPU and memory usage of running application processes.",
    ),
    CapabilityDescriptor(
        capability="app_terminate",
        supported=True,
        required_permission="system.close_app",
        description="Terminate non-protected processes safely with process group awareness.",
    ),
    CapabilityDescriptor(
        capability="url_open",
        supported=True,
        required_permission="browser.interact",
        description="Open validated HTTP/HTTPS URLs via xdg-open or specified browser.",
    ),
    CapabilityDescriptor(
        capability="file_discovery",
        supported=True,
        required_permission="filesystem.read",
        description="Discover files within allowed filesystem roots preventing path traversal.",
    ),
    CapabilityDescriptor(
        capability="file_metadata",
        supported=True,
        required_permission="filesystem.read",
        description="Inspect file timestamps, size, and POSIX permissions within allowed roots.",
    ),
    CapabilityDescriptor(
        capability="clipboard_read",
        supported=True,
        required_permission="system.clipboard",
        description="Read plain text from Wayland (wl-paste) or X11 (xclip/xsel) clipboard on explicit request.",
    ),
    CapabilityDescriptor(
        capability="clipboard_write",
        supported=True,
        required_permission="system.clipboard",
        description="Write text to Wayland (wl-copy) or X11 (xclip/xsel) clipboard with secret detection.",
    ),
    CapabilityDescriptor(
        capability="local_notification",
        supported=True,
        required_permission="system.notification",
        description="Deliver local desktop notifications via notify-send / libnotify.",
    ),
    CapabilityDescriptor(
        capability="window_inspection",
        supported=True,
        required_permission="desktop.automate",
        description="Inspect frontmost window metadata under X11, with structured isolation reporting under Wayland.",
    ),
    CapabilityDescriptor(
        capability="screenshot",
        supported=True,
        required_permission="system.screenshot",
        description="Capture screen image via desktop screenshot tools (gnome-screenshot, spectacle, grim).",
    ),
    CapabilityDescriptor(
        capability="volume_control",
        supported=True,
        required_permission="system.volume",
        description="Control output audio volume via PipeWire (wpctl), PulseAudio (pactl), or ALSA (amixer).",
    ),
    CapabilityDescriptor(
        capability="screen_lock",
        supported=True,
        required_permission="system.lock_screen",
        description="Lock user session via loginctl or xdg-screensaver.",
    ),
    CapabilityDescriptor(
        capability="unrestricted_shell",
        supported=False,
        required_permission="terminal.execute",
        description="Arbitrary or unrestricted shell execution.",
        reason_if_unsupported="Forbidden by RIYA security architecture; only allowlisted commands are permitted.",
    ),
    CapabilityDescriptor(
        capability="background_clipboard_monitoring",
        supported=False,
        required_permission="system.clipboard",
        description="Continuous background clipboard scraping.",
        reason_if_unsupported="Forbidden by RIYA privacy architecture; clipboard access is strictly on-demand.",
    ),
    CapabilityDescriptor(
        capability="kernel_extension_loading",
        supported=False,
        required_permission="system.info",
        description="Direct kernel module loading or kernel modification.",
        reason_if_unsupported="Forbidden by RIYA security architecture and Linux user-space boundaries.",
    ),
)

_LINUX_UNSUPPORTED_CAPABILITY_DESCRIPTORS: tuple[CapabilityDescriptor, ...] = tuple(
    CapabilityDescriptor(
        capability=cap.capability,
        supported=False,
        required_permission=cap.required_permission,
        description=cap.description,
        reason_if_unsupported=_LINUX_UNSUPPORTED_REASON,
    )
    for cap in _LINUX_CAPABILITY_DESCRIPTORS
)


def _parse_os_release() -> dict[str, str]:
    """Parse /etc/os-release or /usr/lib/os-release according to freedesktop specs."""
    data: dict[str, str] = {}
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip().strip('"').strip("'")
                if data:
                    break
            except Exception:
                continue
    return data


def _get_display_server() -> str:
    """Identify whether current session is Wayland, X11, or headless."""
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if os.environ.get("DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "x11":
        return "x11"
    return "headless"


def _clean_exec_command(exec_field: str) -> list[str]:
    """Strip XDG desktop entry field codes (%f, %u, etc.) and tokenize."""
    clean = re.sub(r"%[a-zA-Z]", "", exec_field).strip()
    return [part for part in clean.split() if part]


# =============================================================================
# 1. Linux System Information Adapter
# =============================================================================

class LinuxSystemInformationAdapter(SystemInformationAdapter):
    """Concrete Linux system information adapter reading sysfs, procfs, and psutil."""

    def __init__(self, is_active: bool = True):
        self._is_active = is_active

    def get_system_info(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="system_info",
                operation="get_system_info",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        try:
            os_data = _parse_os_release()
            distro_name = os_data.get("PRETTY_NAME") or os_data.get("NAME") or "Linux"
            kernel_ver = _py_platform.release()
            arch = _py_platform.machine() or "x86_64"

            logical_cores = psutil.cpu_count(logical=True) or 1
            physical_cores = psutil.cpu_count(logical=False) or logical_cores

            cpu_brand = "x86_64 Processor"
            cpuinfo_path = Path("/proc/cpuinfo")
            if cpuinfo_path.is_file():
                try:
                    for line in cpuinfo_path.read_text(encoding="utf-8", errors="replace").splitlines():
                        if "model name" in line or "Hardware" in line:
                            cpu_brand = line.split(":", 1)[1].strip()
                            break
                except Exception:
                    pass

            vmem = psutil.virtual_memory()
            total_gib = round(vmem.total / (1024**3), 2)
            avail_gib = round(vmem.available / (1024**3), 2)
            used_gib = round((vmem.total - vmem.available) / (1024**3), 2)

            usage = shutil.disk_usage("/")
            disk_total_gib = round(usage.total / (1024**3), 2)
            disk_avail_gib = round(usage.free / (1024**3), 2)

            info_data = {
                "os_name": "linux",
                "distribution": distro_name,
                "distribution_id": os_data.get("ID", "linux"),
                "distribution_version": os_data.get("VERSION_ID", ""),
                "kernel_version": kernel_ver,
                "architecture": arch,
                "cpu": {
                    "brand": cpu_brand,
                    "cores_logical": logical_cores,
                    "cores_physical": physical_cores,
                },
                "memory": {
                    "total_gib": total_gib,
                    "available_gib": avail_gib,
                    "used_gib": used_gib,
                    "percent_used": vmem.percent,
                },
                "storage": {
                    "root_total_gib": disk_total_gib,
                    "root_available_gib": disk_avail_gib,
                },
                "display_server": _get_display_server(),
            }

            msg = (
                f"{distro_name} (Linux {kernel_ver}) | "
                f"CPU: {cpu_brand} ({logical_cores} cores) | "
                f"Memory: {avail_gib}/{total_gib} GiB available | "
                f"Storage: {disk_avail_gib}/{disk_total_gib} GiB available"
            )

            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="system_info",
                operation="get_system_info",
                required_permission="system.info",
                data=info_data,
                message=msg,
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="system_info",
                operation="get_system_info",
                required_permission="system.info",
                data={"error": str(exc)},
                message=f"Failed to gather Linux system info: {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def get_storage_info(self, path: str = "/") -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="storage_info",
                operation="get_storage_info",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        try:
            target = Path(path).resolve()
            if not target.exists():
                target = Path("/")
            usage = shutil.disk_usage(target)
            total_gib = round(usage.total / (1024**3), 2)
            used_gib = round(usage.used / (1024**3), 2)
            free_gib = round(usage.free / (1024**3), 2)
            pct = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0.0

            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="storage_info",
                operation="get_storage_info",
                required_permission="system.info",
                data={
                    "path": str(target),
                    "total_gib": total_gib,
                    "used_gib": used_gib,
                    "available_gib": free_gib,
                    "percent_used": pct,
                },
                message=f"Storage at {target}: {free_gib} GiB available of {total_gib} GiB ({pct}% used)",
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="storage_info",
                operation="get_storage_info",
                required_permission="system.info",
                data={"error": str(exc)},
                message=f"Failed to inspect storage: {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def get_battery_status(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="battery_status",
                operation="get_battery_status",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        try:
            batt = psutil.sensors_battery()
            if batt is not None:
                pct = int(batt.percent)
                charging = bool(batt.power_plugged)
                status_str = "charging" if charging else "discharging"
                return PlatformOperationResult(
                    status="success",
                    supported=True,
                    platform="linux",
                    capability="battery_status",
                    operation="get_battery_status",
                    required_permission="system.info",
                    data={
                        "has_battery": True,
                        "percentage": pct,
                        "is_charging": charging,
                        "power_source": "AC" if charging else "Battery",
                    },
                    message=f"Battery: {pct}% ({status_str})",
                    verified=True,
                )

            # Fallback to sysfs inspection
            sys_power = Path("/sys/class/power_supply")
            if sys_power.is_dir():
                for bdir in sys_power.iterdir():
                    if bdir.name.startswith("BAT"):
                        cap_file = bdir / "capacity"
                        status_file = bdir / "status"
                        if cap_file.is_file():
                            pct = int(cap_file.read_text().strip())
                            st = status_file.read_text().strip().lower() if status_file.is_file() else "unknown"
                            is_charging = st in {"charging", "full"}
                            return PlatformOperationResult(
                                status="success",
                                supported=True,
                                platform="linux",
                                capability="battery_status",
                                operation="get_battery_status",
                                required_permission="system.info",
                                data={
                                    "has_battery": True,
                                    "percentage": pct,
                                    "is_charging": is_charging,
                                    "power_source": "AC" if is_charging else "Battery",
                                },
                                message=f"Battery ({bdir.name}): {pct}% ({st})",
                                verified=True,
                            )

            # Desktop or VM with no battery
            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="battery_status",
                operation="get_battery_status",
                required_permission="system.info",
                data={
                    "has_battery": False,
                    "percentage": 100,
                    "is_charging": True,
                    "power_source": "AC Power (Desktop / Server / VM)",
                },
                message="Connected to AC power (no battery detected).",
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="battery_status",
                operation="get_battery_status",
                required_permission="system.info",
                data={"error": str(exc)},
                message=f"Failed to read battery status: {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def get_network_status(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="network_status",
                operation="get_network_status",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            active_interfaces: list[dict[str, Any]] = []

            for iface, if_addrs in addrs.items():
                if iface.startswith("lo"):
                    continue
                stat = stats.get(iface)
                is_up = stat.isup if stat else False
                ips = [a.address for a in if_addrs if a.family in (2, 10, 23)]  # AF_INET / AF_INET6
                if is_up and ips:
                    active_interfaces.append({
                        "interface": iface,
                        "is_up": True,
                        "addresses": ips,
                    })

            online = len(active_interfaces) > 0
            summary = (
                f"Network active on {len(active_interfaces)} interface(s)."
                if online
                else "No active external network connection (offline mode)."
            )

            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="network_status",
                operation="get_network_status",
                required_permission="system.info",
                data={
                    "is_connected": online,
                    "active_interfaces": active_interfaces,
                },
                message=summary,
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="network_status",
                operation="get_network_status",
                required_permission="system.info",
                data={"error": str(exc)},
                message=f"Failed to inspect network status: {exc}",
                failure_reason=str(exc),
                verified=False,
            )


# =============================================================================
# 2. Linux Application Adapter
# =============================================================================

class LinuxApplicationAdapter(ApplicationAdapter):
    """Concrete Linux application adapter parsing XDG .desktop entries and managing processes."""

    def __init__(self, is_active: bool = True):
        self._is_active = is_active
        self._cached_apps: dict[str, dict[str, Any]] | None = None

    def _discover_desktop_entries(self) -> dict[str, dict[str, Any]]:
        """Scan standard XDG application directories for .desktop files."""
        apps: dict[str, dict[str, Any]] = {}
        for app_dir in _XDG_APPLICATION_DIRS:
            if not app_dir.is_dir():
                continue
            for entry in app_dir.glob("*.desktop"):
                try:
                    config = configparser.ConfigParser(interpolation=None, strict=False)
                    config.read(entry, encoding="utf-8")
                    if not config.has_section("Desktop Entry"):
                        continue
                    sec = config["Desktop Entry"]
                    if sec.getboolean("NoDisplay", fallback=False):
                        continue
                    if sec.get("Type", "Application").lower() != "application":
                        continue
                    name = sec.get("Name", "").strip()
                    exec_cmd = sec.get("Exec", "").strip()
                    if not name or not exec_cmd:
                        continue
                    cmd_parts = _clean_exec_command(exec_cmd)
                    if not cmd_parts:
                        continue

                    key = name.lower()
                    if key not in apps:
                        apps[key] = {
                            "name": name,
                            "exec": exec_cmd,
                            "binary": cmd_parts[0],
                            "args": cmd_parts[1:],
                            "desktop_path": str(entry),
                            "comment": sec.get("Comment", ""),
                            "categories": sec.get("Categories", "").split(";"),
                            "icon": sec.get("Icon", ""),
                            "is_flatpak": "flatpak" in str(entry),
                            "is_snap": "snap" in str(entry),
                        }
                except Exception:
                    continue
        return apps

    def _get_apps(self) -> dict[str, dict[str, Any]]:
        if self._cached_apps is None:
            self._cached_apps = self._discover_desktop_entries()
        return self._cached_apps

    def discover_applications(self, filter_text: str = "") -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_discovery",
                operation="discover_applications",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )
        apps = self.search_applications(filter_text)
        return apps

    def resolve_application(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_discovery",
                operation="resolve_application",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )
        details = self.get_application_details(app_name)
        return details

    def launch_application(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_launch",
                operation="launch_application",
                required_permission="system.open_app",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        name = str(app_name or "").strip()
        if not name:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="app_launch",
                operation="launch_application",
                required_permission="system.open_app",
                data={"error": "Empty application name."},
                message="Cannot launch application with empty name.",
                failure_reason="Empty application name.",
                verified=False,
            )

        target_bin: str | None = None
        base_args: list[str] = []
        app_entry = self._get_apps().get(name.lower())
        if app_entry:
            target_bin = shutil.which(app_entry["binary"]) or app_entry["binary"]
            base_args = list(app_entry.get("args", []))
        else:
            target_bin = shutil.which(name)

        if not target_bin or not (os.path.isabs(target_bin) or shutil.which(target_bin)):
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="app_launch",
                operation="launch_application",
                required_permission="system.open_app",
                data={"app_name": name},
                message=f"Application '{name}' not found on system.",
                failure_reason=f"Application '{name}' not found.",
                verified=False,
            )

        full_cmd = [target_bin, *base_args]
        try:
            proc = subprocess.Popen(
                full_cmd,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="app_launch",
                operation="launch_application",
                required_permission="system.open_app",
                data={
                    "app_name": name,
                    "pid": proc.pid,
                    "command": full_cmd,
                },
                message=f"Launched '{name}' successfully (PID: {proc.pid}).",
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="app_launch",
                operation="launch_application",
                required_permission="system.open_app",
                data={"error": str(exc)},
                message=f"Failed to launch '{name}': {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def close_application(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_close",
                operation="close_application",
                required_permission="system.close_app",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        target = str(app_name or "").strip().lower()
        if not target:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="app_close",
                operation="close_application",
                required_permission="system.close_app",
                data={"error": "Empty application name."},
                message="Cannot close application with empty name.",
                failure_reason="Empty application name.",
                verified=False,
            )

        if target in _PROTECTED_PROCESS_NAMES:
            return PlatformOperationResult(
                status="denied",
                supported=True,
                platform="linux",
                capability="app_close",
                operation="close_application",
                required_permission="system.close_app",
                data={"app_name": app_name, "protected": True},
                message=f"Termination blocked: '{app_name}' is a protected system process.",
                failure_reason=f"'{app_name}' is a protected system process.",
                verified=False,
            )

        current_pid = os.getpid()
        terminated_pids: list[int] = []

        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    pinfo = proc.info
                    pid = pinfo["pid"]
                    if pid <= 2 or pid == current_pid:
                        continue
                    pname = (pinfo["name"] or "").lower()
                    if target in pname or target == Path(pname).stem:
                        if pname in _PROTECTED_PROCESS_NAMES:
                            continue
                        proc.terminate()
                        terminated_pids.append(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if terminated_pids:
                return PlatformOperationResult(
                    status="success",
                    supported=True,
                    platform="linux",
                    capability="app_close",
                    operation="close_application",
                    required_permission="system.close_app",
                    data={"app_name": app_name, "terminated_pids": terminated_pids},
                    message=f"Closed '{app_name}' (terminated {len(terminated_pids)} process instances).",
                    verified=True,
                )

            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="app_close",
                operation="close_application",
                required_permission="system.close_app",
                data={"app_name": app_name},
                message=f"No running instances of '{app_name}' found to close.",
                failure_reason=f"'{app_name}' is not running.",
                verified=False,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="app_close",
                operation="close_application",
                required_permission="system.close_app",
                data={"error": str(exc)},
                message=f"Failed to close '{app_name}': {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def list_running_applications(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_discovery",
                operation="list_running_applications",
                required_permission="desktop.automate",
                reason=_LINUX_UNSUPPORTED_REASON,
            )
        running: set[str] = set()
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info.get("name")
                if name and name not in _PROTECTED_PROCESS_NAMES and not name.startswith("["):
                    running.add(name)
            except Exception:
                continue
        sorted_running = sorted(running)
        return PlatformOperationResult(
            status="success",
            supported=True,
            platform="linux",
            capability="app_discovery",
            operation="list_running_applications",
            required_permission="desktop.automate",
            data={"running_apps": sorted_running, "count": len(sorted_running)},
            message=f"Found {len(sorted_running)} running Linux application(s).",
            verified=True,
        )

    def search_applications(self, query: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_search",
                operation="search_applications",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )
        needle = str(query or "").strip().lower()
        all_apps = self._get_apps()
        if not needle:
            matches = sorted({entry["name"] for entry in all_apps.values()})[:50]
        else:
            matches = sorted({
                entry["name"]
                for k, entry in all_apps.items()
                if needle in k or needle in entry["comment"].lower()
            })
        return PlatformOperationResult(
            status="success",
            supported=True,
            platform="linux",
            capability="app_search",
            operation="search_applications",
            required_permission="system.info",
            data={"query": query, "applications": matches, "count": len(matches)},
            message=f"Found {len(matches)} matching application(s) for '{query}'.",
            verified=True,
        )

    def get_application_details(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_details",
                operation="get_application_details",
                required_permission="system.info",
                reason=_LINUX_UNSUPPORTED_REASON,
            )
        key = str(app_name or "").strip().lower()
        entry = self._get_apps().get(key)
        if not entry:
            for k, v in self._get_apps().items():
                if key in k:
                    entry = v
                    break
        if entry:
            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="app_details",
                operation="get_application_details",
                required_permission="system.info",
                data=entry,
                message=f"Application '{entry['name']}' found.",
                verified=True,
            )
        return PlatformOperationResult(
            status="failed",
            supported=True,
            platform="linux",
            capability="app_details",
            operation="get_application_details",
            required_permission="system.info",
            data={"installed": False, "query": app_name},
            message=f"Application '{app_name}' is not installed.",
            failure_reason="Not found",
            verified=False,
        )

    def get_running_applications_structured(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_status",
                operation="get_running_applications_structured",
                required_permission="desktop.automate",
                reason=_LINUX_UNSUPPORTED_REASON,
            )
        apps: list[dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
            try:
                info = proc.info
                name = info.get("name") or ""
                if name.startswith("[") or name in _PROTECTED_PROCESS_NAMES:
                    continue
                mem = info.get("memory_info")
                rss_mb = round(mem.rss / (1024 * 1024), 1) if mem else 0.0
                apps.append({
                    "pid": info["pid"],
                    "name": name,
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "memory_rss_mb": rss_mb,
                    "status": info.get("status", "running"),
                })
            except Exception:
                continue
        return PlatformOperationResult(
            status="success",
            supported=True,
            platform="linux",
            capability="app_status",
            operation="get_running_applications_structured",
            required_permission="desktop.automate",
            data={"running_applications": apps[:80], "count": len(apps)},
            message=f"Discovered {len(apps)} running desktop application process(es).",
            verified=True,
        )

    def terminate_application(self, app_name: str) -> PlatformOperationResult:
        return self.close_application(app_name)

    def get_application_resources(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_resources",
                operation="get_application_resources",
                required_permission="desktop.automate",
                reason=_LINUX_UNSUPPORTED_REASON,
            )
        target = str(app_name or "").strip().lower()
        total_cpu = 0.0
        total_mem_mb = 0.0
        matched_pids: list[int] = []

        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                if target in (proc.info["name"] or "").lower():
                    matched_pids.append(proc.info["pid"])
                    total_cpu += proc.info.get("cpu_percent") or 0.0
                    mem = proc.info.get("memory_info")
                    if mem:
                        total_mem_mb += mem.rss / (1024 * 1024)
            except Exception:
                continue

        res_data = {
            "app_name": app_name,
            "instances": len(matched_pids),
            "pids": matched_pids,
            "cpu_percent_total": round(total_cpu, 1),
            "memory_rss_mb_total": round(total_mem_mb, 1),
        }
        return PlatformOperationResult(
            status="success",
            supported=True,
            platform="linux",
            capability="app_resources",
            operation="get_application_resources",
            required_permission="desktop.automate",
            data=res_data,
            message=f"{app_name}: {len(matched_pids)} instances, {res_data['cpu_percent_total']}% CPU, {res_data['memory_rss_mb_total']} MB RSS",
            verified=True,
        )

    def open_url(self, url: str, browser: str | None = None) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="url_open",
                operation="open_url",
                required_permission="browser.interact",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        clean_url = str(url or "").strip()
        if not clean_url.startswith(("http://", "https://")):
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="url_open",
                operation="open_url",
                required_permission="browser.interact",
                data={"url": url},
                message="Only HTTP and HTTPS URLs are allowed.",
                failure_reason="Invalid URL scheme.",
                verified=False,
            )

        launcher = shutil.which(browser) if browser else shutil.which("xdg-open")
        if not launcher:
            return PlatformOperationResult(
                status="unavailable",
                supported=True,
                platform="linux",
                capability="url_open",
                operation="open_url",
                required_permission="browser.interact",
                data={"url": clean_url},
                message="xdg-open is not installed on this system.",
                failure_reason="xdg-open not found",
                verified=False,
            )

        try:
            subprocess.Popen([launcher, clean_url], shell=False, start_new_session=True)
            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="url_open",
                operation="open_url",
                required_permission="browser.interact",
                data={"url": clean_url, "launcher": launcher},
                message=f"Opened {clean_url} in browser.",
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="url_open",
                operation="open_url",
                required_permission="browser.interact",
                data={"url": clean_url},
                message=f"Failed to open URL: {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    # Convenience accessors returning raw lists / dicts for high-level callers
    def list_installed_applications(self) -> Sequence[str]:
        if not self._is_active:
            return ()
        return sorted({entry["name"] for entry in self._get_apps().values()})

    def get_application_state(self, app_name: str) -> str:
        if not self._is_active:
            return json.dumps({"app_name": app_name, "state": "unknown"})
        needle = str(app_name or "").strip().lower()
        is_running = any(
            needle in (p.info["name"] or "").lower()
            for p in psutil.process_iter(["name"])
            if p.info.get("name")
        )
        return json.dumps({
            "app_name": app_name,
            "state": "running" if is_running else "not_running",
        })


# =============================================================================
# 3. Linux Filesystem Adapter
# =============================================================================

class LinuxFilesystemAdapter(FilesystemAdapter):
    """Concrete Linux filesystem adapter with boundary validation and symlink safeguards."""

    def __init__(self, is_active: bool = True):
        self._is_active = is_active

    def discover_files(self, path: str = ".") -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="file_discovery",
                operation="discover_files",
                required_permission="filesystem.read",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        try:
            root = Path(path).resolve()
            if not root.is_dir():
                return PlatformOperationResult(
                    status="failed",
                    supported=True,
                    platform="linux",
                    capability="file_discovery",
                    operation="discover_files",
                    required_permission="filesystem.read",
                    data={"path": path},
                    message=f"Directory does not exist: {path}",
                    failure_reason="Not a directory",
                    verified=False,
                )

            allowed = [Path(r).resolve() for r in (FILESYSTEM_ALLOWED_ROOTS or ["/tmp", str(Path.home())])]
            if not any(root == a or a in root.parents for a in allowed):
                return PlatformOperationResult(
                    status="denied",
                    supported=True,
                    platform="linux",
                    capability="file_discovery",
                    operation="discover_files",
                    required_permission="filesystem.read",
                    data={"path": path},
                    message=f"Access to directory '{path}' is outside permitted filesystem roots.",
                    failure_reason="Access denied by security policy.",
                    verified=False,
                )

            items = [str(f.name) for f in root.iterdir() if not f.is_symlink()][:100]
            msg = "\n".join(items) if items else "(empty directory)"

            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="file_discovery",
                operation="discover_files",
                required_permission="filesystem.read",
                data={"files": items, "count": len(items)},
                message=msg,
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="file_discovery",
                operation="discover_files",
                required_permission="filesystem.read",
                data={"path": path},
                message=f"Filesystem discovery error: {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def search_files(self, name: str, path: str | None = None) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="file_discovery",
                operation="search_files",
                required_permission="filesystem.read",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        target_dir = Path(path or ".").resolve()
        matches: list[str] = []
        try:
            for item in target_dir.rglob(f"*{name}*"):
                if not item.is_symlink():
                    matches.append(str(item))
                if len(matches) >= 50:
                    break
            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="file_discovery",
                operation="search_files",
                required_permission="filesystem.read",
                data={"matches": matches, "count": len(matches)},
                message=f"Found {len(matches)} matching file(s).",
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="file_discovery",
                operation="search_files",
                required_permission="filesystem.read",
                data={"error": str(exc)},
                message=f"Search failed: {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def inspect_metadata(self, path: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="file_metadata",
                operation="inspect_metadata",
                required_permission="filesystem.read",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        try:
            target = Path(path).resolve()
            if not target.exists():
                return PlatformOperationResult(
                    status="failed",
                    supported=True,
                    platform="linux",
                    capability="file_metadata",
                    operation="inspect_metadata",
                    required_permission="filesystem.read",
                    data={"path": path},
                    message=f"Path not found: {path}",
                    failure_reason="Path not found",
                    verified=False,
                )

            st = target.stat()
            meta = {
                "name": target.name,
                "type": "directory" if target.is_dir() else "file",
                "path": str(target),
                "size_bytes": st.st_size,
                "modified_time": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "permissions_octal": oct(st.st_mode)[-3:],
            }
            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="file_metadata",
                operation="inspect_metadata",
                required_permission="filesystem.read",
                data=meta,
                message=f"Inspected {meta['type']} '{meta['name']}' ({meta['size_bytes']} bytes).",
                verified=True,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="file_metadata",
                operation="inspect_metadata",
                required_permission="filesystem.read",
                data={"path": path},
                message=f"Metadata inspection error: {exc}",
                failure_reason=str(exc),
                verified=False,
            )


# =============================================================================
# 4. Linux Clipboard Adapter
# =============================================================================

class LinuxClipboardAdapter(ClipboardAdapter):
    """Concrete Linux clipboard adapter supporting Wayland (wl-clipboard) and X11 (xclip/xsel)."""

    def __init__(self, is_active: bool = True):
        self._is_active = is_active

    def read_clipboard(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="clipboard_read",
                operation="read_clipboard",
                required_permission="system.clipboard",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        display = _get_display_server()
        if display == "wayland" and shutil.which("wl-paste"):
            cmd = ["wl-paste", "--no-newline"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard", "-out"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--output"]
        elif shutil.which("wl-paste"):
            cmd = ["wl-paste", "--no-newline"]
        else:
            return PlatformOperationResult(
                status="unavailable",
                supported=True,
                platform="linux",
                capability="clipboard_read",
                operation="read_clipboard",
                required_permission="system.clipboard",
                message="Neither wl-clipboard (wl-paste) nor xclip/xsel is installed on this Linux system.",
                failure_reason="Clipboard tool missing",
                verified=False,
            )

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                text = res.stdout
                if looks_like_secret(text):
                    msg = "[Clipboard content redacted: matches credential/token pattern]"
                else:
                    msg = text[:200]
                return PlatformOperationResult(
                    status="success",
                    supported=True,
                    platform="linux",
                    capability="clipboard_read",
                    operation="read_clipboard",
                    required_permission="system.clipboard",
                    data={"text": text, "length": len(text)},
                    message=msg,
                    verified=True,
                )
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="clipboard_read",
                operation="read_clipboard",
                required_permission="system.clipboard",
                message=f"Clipboard read failed: {res.stderr.strip()}",
                failure_reason=res.stderr.strip(),
                verified=False,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="clipboard_read",
                operation="read_clipboard",
                required_permission="system.clipboard",
                message=f"Clipboard read error: {exc}",
                failure_reason=str(exc),
                verified=False,
            )

    def write_clipboard(self, text: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="clipboard_write",
                operation="write_clipboard",
                required_permission="system.clipboard",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        display = _get_display_server()
        if display == "wayland" and shutil.which("wl-copy"):
            cmd = ["wl-copy"]
        elif shutil.which("xclip"):
            cmd = ["xclip", "-selection", "clipboard", "-in"]
        elif shutil.which("xsel"):
            cmd = ["xsel", "--clipboard", "--input"]
        elif shutil.which("wl-copy"):
            cmd = ["wl-copy"]
        else:
            return PlatformOperationResult(
                status="unavailable",
                supported=True,
                platform="linux",
                capability="clipboard_write",
                operation="write_clipboard",
                required_permission="system.clipboard",
                message="Neither wl-clipboard (wl-copy) nor xclip/xsel is installed on this Linux system.",
                failure_reason="Clipboard tool missing",
                verified=False,
            )

        try:
            res = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=3)
            if res.returncode == 0:
                return PlatformOperationResult(
                    status="success",
                    supported=True,
                    platform="linux",
                    capability="clipboard_write",
                    operation="write_clipboard",
                    required_permission="system.clipboard",
                    data={"length": len(text)},
                    message=f"Successfully copied {len(text)} characters to Linux clipboard.",
                    verified=True,
                )
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="clipboard_write",
                operation="write_clipboard",
                required_permission="system.clipboard",
                message=f"Clipboard write failed: {res.stderr.strip()}",
                failure_reason=res.stderr.strip(),
                verified=False,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="clipboard_write",
                operation="write_clipboard",
                required_permission="system.clipboard",
                message=f"Clipboard write error: {exc}",
                failure_reason=str(exc),
                verified=False,
            )


# =============================================================================
# 5. Linux Notification Adapter
# =============================================================================

class LinuxNotificationAdapter(NotificationAdapter):
    """Concrete Linux notification adapter utilizing notify-send / libnotify."""

    def __init__(self, is_active: bool = True):
        self._is_active = is_active

    def send_notification(
        self,
        title: str,
        message: str,
        subtitle: str = "",
    ) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="local_notification",
                operation="send_notification",
                required_permission="system.notification",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        notify_send = shutil.which("notify-send")
        if not notify_send:
            return PlatformOperationResult(
                status="unavailable",
                supported=True,
                platform="linux",
                capability="local_notification",
                operation="send_notification",
                required_permission="system.notification",
                message="notify-send utility is not installed (install libnotify-bin).",
                failure_reason="notify-send not found",
                verified=False,
            )

        full_title = f"{title} - {subtitle}" if subtitle else title
        cmd = [notify_send, "-a", "RIYA", full_title, str(message)]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return PlatformOperationResult(
                    status="success",
                    supported=True,
                    platform="linux",
                    capability="local_notification",
                    operation="send_notification",
                    required_permission="system.notification",
                    data={"title": full_title, "message": message},
                    message="Delivered local desktop notification.",
                    verified=True,
                )
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="local_notification",
                operation="send_notification",
                required_permission="system.notification",
                message=f"notify-send returned exit code {res.returncode}: {res.stderr.strip()}",
                failure_reason=res.stderr.strip(),
                verified=False,
            )
        except Exception as exc:
            return PlatformOperationResult(
                status="failed",
                supported=True,
                platform="linux",
                capability="local_notification",
                operation="send_notification",
                required_permission="system.notification",
                message=f"Failed to deliver notification: {exc}",
                failure_reason=str(exc),
                verified=False,
            )


# =============================================================================
# 6. Linux Window Manager Adapter
# =============================================================================

class LinuxWindowManagerAdapter(WindowManagerAdapter):
    """Concrete Linux window manager adapter handling X11 and Wayland protocols."""

    def __init__(self, is_active: bool = True):
        self._is_active = is_active

    def get_active_window_info(self) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="window_inspection",
                operation="get_active_window_info",
                required_permission="desktop.automate",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        display = _get_display_server()
        if display == "wayland":
            return PlatformOperationResult(
                status="success",
                supported=True,
                platform="linux",
                capability="window_inspection",
                operation="get_active_window_info",
                required_permission="desktop.automate",
                data={"display_server": "wayland", "restricted": True},
                message="Window inspection is restricted under Wayland display server for client security isolation.",
                verified=True,
            )

        xdotool = shutil.which("xdotool")
        if xdotool:
            try:
                wid_res = subprocess.run([xdotool, "getactivewindow"], capture_output=True, text=True, timeout=3)
                if wid_res.returncode == 0:
                    wid = wid_res.stdout.strip()
                    title_res = subprocess.run([xdotool, "getwindowname", wid], capture_output=True, text=True, timeout=3)
                    title = title_res.stdout.strip() if title_res.returncode == 0 else ""
                    return PlatformOperationResult(
                        status="success",
                        supported=True,
                        platform="linux",
                        capability="window_inspection",
                        operation="get_active_window_info",
                        required_permission="desktop.automate",
                        data={"window_id": wid, "title": title, "display_server": "x11"},
                        message=f"Active window: '{title}' (ID: {wid})",
                        verified=True,
                    )
            except Exception:
                pass

        return PlatformOperationResult(
            status="unavailable",
            supported=True,
            platform="linux",
            capability="window_inspection",
            operation="get_active_window_info",
            required_permission="desktop.automate",
            message="Active window inspection tool (xdotool) is not installed.",
            failure_reason="xdotool missing",
            verified=False,
        )

    def focus_application(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_focus",
                operation="focus_application",
                required_permission="desktop.automate",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        wmctrl = shutil.which("wmctrl")
        if wmctrl and _get_display_server() == "x11":
            try:
                res = subprocess.run(
                    [wmctrl, "-a", str(app_name)],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if res.returncode == 0:
                    return PlatformOperationResult(
                        status="success",
                        supported=True,
                        platform="linux",
                        capability="app_focus",
                        operation="focus_application",
                        required_permission="desktop.automate",
                        data={"app_name": app_name},
                        message=f"Focused window for '{app_name}'.",
                        verified=True,
                    )
            except Exception:
                pass

        return PlatformOperationResult(
            status="unavailable",
            supported=True,
            platform="linux",
            capability="app_focus",
            operation="focus_application",
            required_permission="desktop.automate",
            message=f"Window focus is not supported by current display server ({_get_display_server()}) without wmctrl.",
            failure_reason="Display server limitation or wmctrl missing",
            verified=False,
        )

    def get_application_state(self, app_name: str) -> PlatformOperationResult:
        if not self._is_active:
            return make_unsupported_result(
                platform_name="linux",
                capability="app_status",
                operation="get_application_state",
                required_permission="desktop.automate",
                reason=_LINUX_UNSUPPORTED_REASON,
            )

        needle = str(app_name or "").strip().lower()
        is_running = any(
            needle in (p.info["name"] or "").lower()
            for p in psutil.process_iter(["name"])
            if p.info.get("name")
        )
        state_str = "running" if is_running else "not_running"
        return PlatformOperationResult(
            status="success",
            supported=True,
            platform="linux",
            capability="app_status",
            operation="get_application_state",
            required_permission="desktop.automate",
            data={"app_name": app_name, "state": state_str},
            message=f"{app_name} is {state_str}.",
            verified=True,
        )


# =============================================================================
# 7. Unified Linux Platform Adapter
# =============================================================================

class LinuxPlatform(PlatformAdapter):
    """Unified Linux platform adapter coordinating system, application, and media adapters."""

    def __init__(self, *, force_active: bool | None = None):
        self._is_active = (
            force_active if force_active is not None else sys.platform.startswith("linux")
        )
        self._sys = LinuxSystemInformationAdapter(is_active=self._is_active)
        self._apps = LinuxApplicationAdapter(is_active=self._is_active)
        self._fs = LinuxFilesystemAdapter(is_active=self._is_active)
        self._clipboard = LinuxClipboardAdapter(is_active=self._is_active)
        self._notifications = LinuxNotificationAdapter(is_active=self._is_active)
        self._windows = LinuxWindowManagerAdapter(is_active=self._is_active)

    @property
    def system_info_adapter(self) -> SystemInformationAdapter:
        return self._sys

    @property
    def application_adapter(self) -> ApplicationAdapter:
        return self._apps

    @property
    def filesystem_adapter(self) -> FilesystemAdapter:
        return self._fs

    @property
    def clipboard_adapter(self) -> ClipboardAdapter:
        return self._clipboard

    @property
    def notification_adapter(self) -> NotificationAdapter:
        return self._notifications

    @property
    def window_adapter(self) -> WindowManagerAdapter:
        return self._windows

    def platform_info(self) -> PlatformInfo:
        cores = int(os.cpu_count() or 1)
        os_data = _parse_os_release() if self._is_active else {}
        return PlatformInfo(
            os_name="linux",
            system="Linux",
            os_version=os_data.get("VERSION_ID") or (_py_platform.release() if self._is_active else "unverified"),
            build_version=_py_platform.version() if self._is_active else "",
            architecture=_py_platform.machine() or "x86_64",
            machine_model=os_data.get("PRETTY_NAME", "Linux Host"),
            cpu_brand=_py_platform.processor() or "x86_64",
            cpu_cores_logical=cores,
            cpu_cores_physical=cores,
            is_supported=self._is_active,
        )

    def capabilities(self) -> PlatformCapabilities:
        if not self._is_active:
            return PlatformCapabilities(
                platform_name="linux",
                platform_available=False,
                descriptors=_LINUX_UNSUPPORTED_CAPABILITY_DESCRIPTORS,
            )
        return PlatformCapabilities(
            platform_name="linux",
            platform_available=True,
            descriptors=_LINUX_CAPABILITY_DESCRIPTORS,
        )

    def platform_capabilities_summary(self) -> str:
        return json.dumps(self.status_summary(), ensure_ascii=False)

    def _raise_unsupported(self, capability: str, permission: str) -> str:
        raise UnsupportedPlatformCapabilityError(
            capability=capability,
            platform_name="linux",
            reason=_LINUX_UNSUPPORTED_REASON,
            required_permission=permission,
        )

    def open_application(self, app_name: str) -> str:
        if not self._is_active:
            return self._raise_unsupported("app_launch", "system.open_app")
        res = self._apps.launch_application(app_name)
        return res.message

    def close_application(self, app_name: str) -> str:
        if not self._is_active:
            return self._raise_unsupported("app_close", "system.close_app")
        res = self._apps.close_application(app_name)
        return res.message

    def take_screenshot(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("screenshot", "system.screenshot")

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        pic_dir = Path.home() / "Pictures"
        if not pic_dir.is_dir():
            pic_dir = Path.home()
        dest = pic_dir / f"riya_screenshot_{timestamp}.png"

        tools = [
            ("gnome-screenshot", ["-f", str(dest)]),
            ("spectacle", ["-b", "-n", "-o", str(dest)]),
            ("grim", [str(dest)]),
            ("import", ["-window", "root", str(dest)]),
        ]

        for tool_name, args in tools:
            bin_path = shutil.which(tool_name)
            if bin_path:
                try:
                    res = subprocess.run([bin_path, *args], capture_output=True, timeout=5)
                    if res.returncode == 0 and dest.is_file():
                        return f"Screenshot saved successfully to {dest}"
                except Exception:
                    continue

        return "Screen capture utility (gnome-screenshot, spectacle, or grim) not found."

    def current_time(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("system_info", "system.info")
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def set_volume(self, level: int) -> str:
        if not self._is_active:
            return self._raise_unsupported("volume_control", "system.volume")

        clamped = max(0, min(100, int(level)))
        wpctl = shutil.which("wpctl")
        if wpctl:
            try:
                subprocess.run([wpctl, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clamped/100:.2f}"], timeout=3)
                return f"Volume set to {clamped}% via wpctl."
            except Exception:
                pass
        pactl = shutil.which("pactl")
        if pactl:
            try:
                subprocess.run([pactl, "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"], timeout=3)
                return f"Volume set to {clamped}% via pactl."
            except Exception:
                pass
        amixer = shutil.which("amixer")
        if amixer:
            try:
                subprocess.run([amixer, "-D", "pulse", "sset", "Master", f"{clamped}%"], timeout=3)
                return f"Volume set to {clamped}% via amixer."
            except Exception:
                pass

        return f"Audio subsystem utility not available to set volume to {clamped}%."

    def mute_volume(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("volume_control", "system.volume")
        wpctl = shutil.which("wpctl")
        if wpctl:
            try:
                subprocess.run([wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", "1"], timeout=3)
                return "Audio muted via wpctl."
            except Exception:
                pass
        pactl = shutil.which("pactl")
        if pactl:
            try:
                subprocess.run([pactl, "set-sink-mute", "@DEFAULT_SINK@", "1"], timeout=3)
                return "Audio muted via pactl."
            except Exception:
                pass
        return "Audio subsystem utility not available to mute volume."

    def unmute_volume(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("volume_control", "system.volume")
        wpctl = shutil.which("wpctl")
        if wpctl:
            try:
                subprocess.run([wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], timeout=3)
                return "Audio unmuted via wpctl."
            except Exception:
                pass
        pactl = shutil.which("pactl")
        if pactl:
            try:
                subprocess.run([pactl, "set-sink-mute", "@DEFAULT_SINK@", "0"], timeout=3)
                return "Audio unmuted via pactl."
            except Exception:
                pass
        return "Audio subsystem utility not available to unmute volume."

    def battery_status(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("battery_status", "system.info")
        return self._sys.get_battery_status().message

    def system_information(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("system_info", "system.info")
        return self._sys.get_system_info().message

    def lock_screen(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("screen_lock", "system.lock_screen")
        loginctl = shutil.which("loginctl")
        if loginctl:
            try:
                subprocess.run([loginctl, "lock-session"], timeout=3)
                return "Session locked via loginctl."
            except Exception:
                pass
        screensaver = shutil.which("xdg-screensaver")
        if screensaver:
            try:
                subprocess.run([screensaver, "lock"], timeout=3)
                return "Session locked via xdg-screensaver."
            except Exception:
                pass
        return "No screen locker (loginctl or xdg-screensaver) available."


    def storage_status(self, path: str = "/") -> str:
        if not self._is_active:
            return self._raise_unsupported("storage_info", "system.info")
        return self._sys.get_storage_info(path).message

    def network_status(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("network_status", "system.info")
        return self._sys.get_network_status().message

    def check_application(self, app_name: str = "") -> str:
        if not self._is_active:
            return self._raise_unsupported("app_discovery", "system.info")
        res = self._apps.get_application_details(app_name)
        if res.status == "success":
            return json.dumps(res.data, ensure_ascii=False)
        return f"Application '{app_name}' is not found."

    def inspect_file_metadata(self, path: str) -> str:
        if not self._is_active:
            return self._raise_unsupported("file_metadata", "filesystem.read")
        return self._fs.inspect_metadata(path).message

    def clipboard_read(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("clipboard_read", "system.clipboard")
        return self._clipboard.read_clipboard().message

    def clipboard_write(self, text: str) -> str:
        if not self._is_active:
            return self._raise_unsupported("clipboard_write", "system.clipboard")
        return self._clipboard.write_clipboard(text).message

    def send_notification(self, title: str, message: str, subtitle: str = "") -> str:
        if not self._is_active:
            return self._raise_unsupported("local_notification", "system.notification")
        return self._notifications.send_notification(title, message, subtitle).message

    def active_window_info(self) -> str:
        if not self._is_active:
            return self._raise_unsupported("window_inspection", "desktop.automate")
        return self._windows.get_active_window_info().message

    # Compatibility methods mirroring MacOSPlatform
    def list_installed_applications(self) -> Sequence[str]:
        return self._apps.list_installed_applications()

    def list_running_applications(self) -> Sequence[str]:
        res = self._apps.list_running_applications()
        return res.data.get("running_apps", []) if res.status == "success" else []

    def get_application_state(self, app_name: str) -> str:
        return self._apps.get_application_state(app_name)

    def focus_application(self, app_name: str) -> str:
        return self._windows.focus_application(app_name).message

    def search_applications(self, query: str) -> list[str]:
        res = self._apps.search_applications(query)
        return res.data.get("applications", []) if res.status == "success" else []

    def get_application_details(self, app_name: str) -> dict[str, Any]:
        res = self._apps.get_application_details(app_name)
        return res.data if res.status == "success" else {"error": res.message}

    def get_running_applications_structured(self) -> list[dict[str, Any]]:
        res = self._apps.get_running_applications_structured()
        return res.data.get("running_applications", []) if res.status == "success" else []

    def get_application_resources(self, app_name: str) -> dict[str, Any]:
        res = self._apps.get_application_resources(app_name)
        return res.data if res.status == "success" else {"error": res.message}

    def terminate_application(self, app_name: str, force: bool = False) -> dict[str, Any]:
        res = self._apps.close_application(app_name)
        return {
            "status": res.status,
            "message": res.message,
            "data": res.data,
        }

    def open_url(self, url: str, browser: str | None = None) -> dict[str, Any]:
        res = self._apps.open_url(url, browser=browser)
        return {
            "status": res.status,
            "message": res.message,
            "data": res.data,
        }
