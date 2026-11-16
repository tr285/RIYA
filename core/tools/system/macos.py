from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform as _py_platform
import plistlib
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Sequence


_OPEN_BIN = "/usr/bin/open"
_OSASCRIPT_BIN = "/usr/bin/osascript"
_SCREENCAPTURE_BIN = "/usr/sbin/screencapture"
_PMSET_BIN = "/usr/bin/pmset"
_SW_VERS_BIN = "/usr/bin/sw_vers"
_UNAME_BIN = "/usr/bin/uname"
_PBCOPY_BIN = "/usr/bin/pbcopy"
_PBPASTE_BIN = "/usr/bin/pbpaste"
_PGREP_BIN = "/usr/bin/pgrep"
_SYSCTL_BIN = "/usr/sbin/sysctl"

_ALLOWED_EXECUTABLES = frozenset({
    _OPEN_BIN,
    _OSASCRIPT_BIN,
    _SCREENCAPTURE_BIN,
    _PMSET_BIN,
    _SW_VERS_BIN,
    _UNAME_BIN,
    _PBCOPY_BIN,
    _PBPASTE_BIN,
    _PGREP_BIN,
    _SYSCTL_BIN,
})

from core.security.manager import looks_like_secret

_DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_CLIPBOARD_CHARS = 32_768
_APP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,127}$")
_SECRET_CLIPBOARD_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9]{16,}|bearer\s+[a-z0-9._\-]{12,}|api[_-]?key\s*[:=]\s*\S+|password\s*[:=]\s*\S+)"
)

_looks_like_secret = looks_like_secret

_DEFAULT_APP_DIRS: tuple[Path, ...] = (
    Path("/Applications"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
)

_CLOSE_APPLICATION_SCRIPT = [
    "on run argv",
    "set targetApp to item 1 of argv",
    'tell application "System Events"',
    "if not (exists process targetApp) then",
    'return "NOT_RUNNING"',
    "end if",
    "end tell",
    "tell application targetApp to quit",
    "repeat 10 times",
    'tell application "System Events"',
    "if not (exists process targetApp) then",
    'return "CLOSED"',
    "end if",
    "end tell",
    "delay 0.1",
    "end repeat",
    'return "STILL_RUNNING"',
    "end run",
]

_SET_VOLUME_SCRIPT = [
    "on run argv",
    "set targetLevel to (item 1 of argv) as integer",
    "set volume output volume targetLevel",
    "end run",
]

_NOTIFICATION_SCRIPT = [
    "on run argv",
    "set notifTitle to item 1 of argv",
    "set notifBody to item 2 of argv",
    "set notifSub to item 3 of argv",
    'if notifSub is not "" then',
    "display notification notifBody with title notifTitle subtitle notifSub",
    "else",
    "display notification notifBody with title notifTitle",
    "end if",
    'return "DELIVERED"',
    "end run",
]

_ACTIVE_WINDOW_SCRIPT = [
    'tell application "System Events"',
    "set frontProc to first application process whose frontmost is true",
    "set appName to name of frontProc",
    "set winTitle to \"\"",
    "set winCount to 0",
    "try",
    "set winCount to count of windows of frontProc",
    "if winCount > 0 then",
    "set winTitle to name of front window of frontProc",
    "end if",
    "end try",
    "return appName & linefeed & (winCount as text) & linefeed & winTitle",
    "end tell",
]


def validate_app_name(app_name: str) -> str:
    """Validate and normalize a macOS application name."""

    if not isinstance(app_name, str):
        raise ValueError("Application name must be a string.")

    cleaned = app_name.strip()

    if not cleaned or len(cleaned) > 128 or ".." in cleaned:
        raise ValueError("Invalid application name.")

    if not _APP_NAME_PATTERN.fullmatch(cleaned):
        raise ValueError("Application name contains unsupported characters.")

    return cleaned


def _validate_single_line_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    cleaned = value.strip()
    if not allow_empty and not cleaned:
        raise ValueError(f"{field_name} cannot be empty.")
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length ({max_length}).")
    for ch in cleaned:
        code = ord(ch)
        if code < 32 or code == 127:
            raise ValueError(f"{field_name} contains disallowed control characters.")
    return cleaned


def _run_executable(
    executable: str,
    args: Sequence[str] = (),
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted system executable safely without a shell."""

    if (
        not isinstance(executable, str)
        or not os.path.isabs(executable)
        or executable not in _ALLOWED_EXECUTABLES
    ):
        raise ValueError(f"Executable is not permitted: {executable!r}")

    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "shell": False,
        "check": False,
        "timeout": timeout,
    }
    if input_text is not None:
        kwargs["input"] = input_text

    return subprocess.run(
        [executable, *args],
        **kwargs,
    )


def _run_osascript(
    script_lines: Sequence[str],
    script_args: Sequence[str] = (),
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Execute static AppleScript lines, passing dynamic values via argv."""

    cmd_args: list[str] = []
    for line in script_lines:
        cmd_args.extend(["-e", line])

    if script_args:
        cmd_args.append("--")
        cmd_args.extend(script_args)

    return _run_executable(_OSASCRIPT_BIN, cmd_args, timeout=timeout)


def _is_permission_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "-1743" in lowered
        or "-25211" in lowered
        or "-1719" in lowered
        or "not authorized" in lowered
        or "not permitted" in lowered
        or "permission denied" in lowered
        or "assistive access" in lowered
    )


def _is_missing_app_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "-600" in lowered
        or "-1728" in lowered
        or "isn't running" in lowered
        or "not running" in lowered
        or "can't get application" in lowered
        or "unable to find application" in lowered
    )


def open_application(app_name: str) -> str:
    """Open a macOS application safely."""

    cleaned = validate_app_name(app_name)

    try:
        result = _run_executable(
            _OPEN_BIN,
            ["-a", cleaned],
            timeout=10.0,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out trying to open {cleaned}."
    except OSError:
        return f"I couldn't open {cleaned}."

    if result.returncode != 0:
        stderr = result.stderr or ""
        if _is_permission_error(stderr):
            return f"Permission denied: RIYA is not authorized to open {cleaned}."
        if _is_missing_app_error(stderr):
            return f"I couldn't find the application '{cleaned}'."
        return f"I couldn't open {cleaned}."

    return f"Opened {cleaned}."


def close_application(app_name: str) -> str:
    """Close a macOS application safely via parameterized AppleScript argv."""

    cleaned = validate_app_name(app_name)

    try:
        result = _run_osascript(
            _CLOSE_APPLICATION_SCRIPT,
            [cleaned],
            timeout=10.0,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out waiting for {cleaned} to close."
    except OSError:
        return f"I couldn't close {cleaned}."

    if result.returncode != 0:
        stderr = result.stderr or ""
        if _is_permission_error(stderr):
            return f"Permission denied: RIYA is not authorized to close {cleaned}."
        if _is_missing_app_error(stderr):
            return f"{cleaned} is not running or could not be found."
        return f"I couldn't close {cleaned}."

    status = (result.stdout or "").strip()

    if status == "CLOSED":
        return f"Closed {cleaned}."

    if status == "NOT_RUNNING":
        return f"{cleaned} is not running or could not be found."

    if status == "STILL_RUNNING":
        return f"I couldn't close {cleaned}; it is still running."

    return f"I couldn't confirm that {cleaned} closed."


def take_screenshot() -> str:
    """Take a screenshot and save it to the Desktop."""

    target = Path.home() / "Desktop" / "riya_screenshot.png"

    if target.is_symlink():
        return "I couldn't take the screenshot safely."

    try:
        result = _run_executable(
            _SCREENCAPTURE_BIN,
            ["-x", str(target)],
            timeout=10.0,
        )
    except subprocess.TimeoutExpired:
        return "Timed out taking the screenshot."
    except OSError:
        return "I couldn't take the screenshot."

    if result.returncode != 0:
        stderr = result.stderr or ""
        if _is_permission_error(stderr):
            return "Permission denied: RIYA is not authorized to capture the screen."
        return "I couldn't take the screenshot."

    return f"Screenshot saved to {target}."


def current_time() -> str:
    """Return the current local time."""

    return datetime.now().strftime(
        "It is %I:%M %p."
    )


def set_volume(level: int) -> str:
    """Set macOS output volume from 0 to 100."""

    if isinstance(level, bool) or not isinstance(level, int):
        raise ValueError("Volume level must be an integer.")

    clamped = max(0, min(100, level))

    try:
        result = _run_osascript(
            _SET_VOLUME_SCRIPT,
            [str(clamped)],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return "Timed out setting the volume."
    except OSError:
        return "I couldn't set the volume."

    if result.returncode != 0:
        if _is_permission_error(result.stderr or ""):
            return "Permission denied: RIYA is not authorized to change system volume."
        return "I couldn't set the volume."

    return f"Volume set to {clamped}%."


def mute_volume() -> str:
    """Mute macOS audio."""

    try:
        result = _run_osascript(
            ["set volume output muted true"],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return "Timed out muting the volume."
    except OSError:
        return "I couldn't mute the volume."

    if result.returncode != 0:
        if _is_permission_error(result.stderr or ""):
            return "Permission denied: RIYA is not authorized to mute system volume."
        return "I couldn't mute the volume."

    return "Volume muted."


def unmute_volume() -> str:
    """Unmute macOS audio."""

    try:
        result = _run_osascript(
            ["set volume output muted false"],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return "Timed out unmuting the volume."
    except OSError:
        return "I couldn't unmute the volume."

    if result.returncode != 0:
        if _is_permission_error(result.stderr or ""):
            return "Permission denied: RIYA is not authorized to unmute system volume."
        return "I couldn't unmute the volume."

    return "Volume unmuted."


def battery_status() -> str:
    """Get the current Mac battery status."""

    try:
        result = _run_executable(
            _PMSET_BIN,
            ["-g", "batt"],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return "Timed out reading battery status."
    except OSError:
        return "I couldn't read the battery status."

    if result.returncode != 0:
        return "I couldn't read the battery status."

    return result.stdout.strip()


def get_battery_status_structured() -> dict[str, Any]:
    """Return structured battery telemetry on macOS."""
    raw = battery_status()
    if raw.startswith("Timed out"):
        return {
            "status": "timed_out",
            "has_battery": False,
            "percent": None,
            "power_source": "unknown",
            "state": "timed_out",
            "raw": raw,
        }
    if raw.startswith("I couldn't"):
        return {
            "status": "failed",
            "has_battery": False,
            "percent": None,
            "power_source": "unknown",
            "state": "unavailable",
            "raw": raw,
        }
    pct_match = re.search(r"(\d{1,3})%", raw)
    percent = int(pct_match.group(1)) if pct_match else None
    source_match = re.search(r"Now drawing from '([^']+)'", raw)
    power_source = source_match.group(1) if source_match else "unknown"
    state = "unknown"
    for candidate in ("discharging", "charging", "charged", "finishing charge", "AC attached"):
        if candidate.lower() in raw.lower():
            state = candidate.lower()
            break
    return {
        "status": "success",
        "has_battery": bool("InternalBattery" in raw or percent is not None),
        "percent": percent,
        "power_source": power_source,
        "state": state,
        "raw": raw,
    }


def _collect_memory_metrics() -> dict[str, Any]:
    try:
        import psutil

        vm = psutil.virtual_memory()
        total = int(vm.total)
        avail = int(vm.available)
        used = int(vm.used)
        pct = float(vm.percent)
    except Exception:
        total = 0
        avail = 0
        used = 0
        pct = 0.0
    return {
        "total_bytes": total,
        "available_bytes": avail,
        "used_bytes": used,
        "total_gib": round(total / (1024 ** 3), 2) if total else 0.0,
        "available_gib": round(avail / (1024 ** 3), 2) if avail else 0.0,
        "percent_used": round(pct, 1),
    }


def get_storage_info_structured(path: str = "/") -> dict[str, Any]:
    """Return structured disk capacity and available storage for a validated path."""
    if not isinstance(path, str) or "\x00" in path or not path.strip():
        raise ValueError("Storage path must be a non-empty string.")
    raw = path.strip()
    if raw in {"/", ".", "~"}:
        target = Path(raw).expanduser()
    else:
        from core.tools.filesystem.files import _resolve_path

        target = _resolve_path(raw)
    if target.is_symlink():
        raise PermissionError(f"Access denied: symlink storage path is not permitted: {target}")
    usage = shutil.disk_usage(target)
    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    pct = round((used / total) * 100.0, 1) if total > 0 else 0.0
    return {
        "path": str(target),
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": free,
        "total_gib": round(total / (1024 ** 3), 2),
        "used_gib": round(used / (1024 ** 3), 2),
        "available_gib": round(free / (1024 ** 3), 2),
        "percent_used": pct,
    }


def storage_status(path: str = "/") -> str:
    """Return human-readable and JSON disk capacity and available storage information."""
    info = get_storage_info_structured(path)
    return (
        f"Disk storage ({info['path']}): "
        f"{info['available_gib']} GiB available of {info['total_gib']} GiB total "
        f"({info['used_gib']} GiB used, {info['percent_used']}% full)."
    )


def get_network_status_structured() -> dict[str, Any]:
    """Inspect local network interfaces and RIYA's offline-first status without external traffic."""
    hostname = "localhost"
    try:
        hostname = socket.gethostname()
    except Exception:
        pass

    active_interfaces: list[str] = []
    ipv4_addrs: list[str] = []
    try:
        import psutil

        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        for iface, st in stats.items():
            if not getattr(st, "isup", False):
                continue
            active_interfaces.append(iface)
            for addr in addrs.get(iface, ()):
                if getattr(addr, "family", None) == socket.AF_INET:
                    ip = str(getattr(addr, "address", ""))
                    if ip and not ip.startswith("127."):
                        ipv4_addrs.append(ip)
    except Exception:
        pass

    allow_online = False
    try:
        from core.browser.adapter import _DEFAULT_BROWSER

        allow_online = bool(getattr(_DEFAULT_BROWSER, "allow_online", False))
    except Exception:
        pass

    has_non_loopback = bool(ipv4_addrs or [i for i in active_interfaces if i != "lo0"])
    return {
        "hostname": hostname,
        "active_interfaces": sorted(active_interfaces),
        "ipv4_addresses": sorted(set(ipv4_addrs)),
        "local_network_up": has_non_loopback,
        "riya_offline_first": not allow_online,
        "online_browsing_enabled": allow_online,
    }


def network_status() -> str:
    """Return a concise summary of local network interface state and RIYA's offline-first mode."""
    net = get_network_status_structured()
    link_state = "Connected (local interface up)" if net["local_network_up"] else "Disconnected (loopback only)"
    mode_state = "Offline-First (Local Only)" if net["riya_offline_first"] else "Online (Allowlisted Web)"
    ifaces = ", ".join(net["active_interfaces"]) or "none"
    return (
        f"Network: {link_state} | Active interfaces: {ifaces} | "
        f"Host: {net['hostname']} | RIYA mode: {mode_state}"
    )


def get_detailed_system_info() -> dict[str, Any]:
    """Collect structured macOS version, architecture, CPU, memory, disk, battery, and network info."""
    macos_res = _run_executable(_SW_VERS_BIN, timeout=5.0)
    machine_res = _run_executable(_UNAME_BIN, ["-m"], timeout=5.0)
    if macos_res.returncode != 0 or machine_res.returncode != 0:
        raise RuntimeError("I couldn't read system information.")

    sw_raw = (macos_res.stdout or "").strip()
    kv: dict[str, str] = {}
    for line in sw_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip()] = v.strip()

    arch = (machine_res.stdout or "").strip() or _py_platform.machine() or "arm64"
    cpu_cores_logical = int(os.cpu_count() or 1)
    cpu_cores_physical = cpu_cores_logical
    try:
        import psutil

        phys = psutil.cpu_count(logical=False)
        if phys:
            cpu_cores_physical = int(phys)
    except Exception:
        pass

    cpu_brand = _py_platform.processor() or ("Apple Silicon" if arch == "arm64" else arch)
    mem = _collect_memory_metrics()
    storage = get_storage_info_structured("/")
    storage.setdefault("free_bytes", storage.get("available_bytes", 0))
    net = get_network_status_structured()
    return {
        "platform": kv.get("ProductName", "macOS"),
        "os_name": "macos",
        "product_name": kv.get("ProductName", "macOS"),
        "os_version": kv.get("ProductVersion", _py_platform.mac_ver()[0] or "unknown"),
        "build_version": kv.get("BuildVersion", ""),
        "architecture": arch,
        "cpu_brand": cpu_brand,
        "cpu_logical_cores": cpu_cores_logical,
        "cpu_cores_logical": cpu_cores_logical,
        "cpu_cores_physical": cpu_cores_physical,
        "memory": mem,
        "storage": storage,
        "network": net,
        "raw_sw_vers": sw_raw,
    }


def system_information() -> str:
    """Get comprehensive macOS system information (OS, architecture, CPU, memory, disk, network)."""

    try:
        macos = _run_executable(
            _SW_VERS_BIN,
            timeout=5.0,
        )
        machine = _run_executable(
            _UNAME_BIN,
            ["-m"],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return "Timed out reading system information."
    except OSError:
        return "I couldn't read system information."

    if macos.returncode != 0 or machine.returncode != 0:
        return "I couldn't read system information."

    arch = machine.stdout.strip()
    lines = [
        macos.stdout.strip(),
        f"Architecture: {arch}",
    ]
    try:
        cores = os.cpu_count() or 1
        mem = _collect_memory_metrics()
        storage = get_storage_info_structured("/")
        net = get_network_status_structured()
        lines.append(f"CPU Cores: {cores} ({'Apple Silicon' if arch == 'arm64' else arch})")
        if mem["total_bytes"] > 0:
            lines.append(
                f"Memory: {mem['available_gib']} GiB available / {mem['total_gib']} GiB total"
            )
        lines.append(
            f"Storage (/): {storage['available_gib']} GiB available / {storage['total_gib']} GiB total"
        )
        lines.append(
            f"Network: {'Local interface up' if net['local_network_up'] else 'Offline'} "
            f"(RIYA Offline-First: {'Yes' if net['riya_offline_first'] else 'No'})"
        )
    except Exception:
        pass

    return "\n".join(lines)


def lock_screen() -> str:
    """Lock the Mac screen."""

    try:
        result = _run_osascript(
            [
                'tell application "System Events" to keystroke "q" '
                "using {control down, command down}"
            ],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return "Timed out locking the screen."
    except OSError:
        return "I couldn't lock the screen."

    if result.returncode != 0:
        if _is_permission_error(result.stderr or ""):
            return "Permission denied: RIYA is not authorized to lock the screen."
        return "I couldn't lock the screen."

    return "Screen locked."


_RUNNING_APPS_SCRIPT = [
    'tell application "System Events"',
    "set appList to name of every application process whose background only is false",
    "set AppleScript's text item delimiters to linefeed",
    "return appList as text",
    "end tell",
]

_APP_STATE_SCRIPT = [
    "on run argv",
    "set targetApp to item 1 of argv",
    'tell application "System Events"',
    "if not (exists process targetApp) then",
    'return "not_running"',
    "end if",
    "set isFront to frontmost of process targetApp",
    "if isFront then",
    'return "frontmost"',
    "end if",
    'return "running"',
    "end tell",
    "end run",
]

_FOCUS_APP_SCRIPT = [
    "on run argv",
    "set targetApp to item 1 of argv",
    'tell application "System Events"',
    "if not (exists process targetApp) then",
    'return "NOT_RUNNING"',
    "end if",
    "end tell",
    "tell application targetApp to activate",
    'return "FOCUSED"',
    "end run",
]


def _candidate_app_dirs(search_dirs: Sequence[Path] | None = None) -> tuple[Path, ...]:
    if search_dirs is not None:
        return tuple(Path(d) for d in search_dirs)
    user_apps = Path.home() / "Applications"
    if user_apps.is_dir() and not user_apps.is_symlink():
        return (*_DEFAULT_APP_DIRS, user_apps)
    return _DEFAULT_APP_DIRS


def _inspect_app_bundle(bundle_path: Path) -> dict[str, Any] | None:
    """Safely inspect a macOS .app bundle directory and its Info.plist without following symlinks."""
    try:
        if bundle_path.is_symlink() or not bundle_path.is_dir():
            return None
        if bundle_path.suffix.lower() != ".app":
            return None
        contents_dir = bundle_path / "Contents"
        if contents_dir.is_symlink():
            return None
        info_plist = contents_dir / "Info.plist"
        plist_data: dict[str, Any] = {}
        if info_plist.exists():
            if info_plist.is_symlink() or not info_plist.is_file():
                return None
            with info_plist.open("rb") as fp:
                loaded = plistlib.load(fp)
                if isinstance(loaded, dict):
                    plist_data = loaded
    except Exception:
        return None

    stem = bundle_path.stem
    display_name = str(
        plist_data.get("CFBundleDisplayName")
        or plist_data.get("CFBundleName")
        or stem
    ).strip() or stem
    bundle_id = str(plist_data.get("CFBundleIdentifier") or "").strip()
    version = str(
        plist_data.get("CFBundleShortVersionString")
        or plist_data.get("CFBundleVersion")
        or ""
    ).strip()
    executable = str(plist_data.get("CFBundleExecutable") or stem).strip() or stem
    return {
        "name": display_name,
        "bundle_name": stem,
        "bundle_id": bundle_id,
        "version": version,
        "executable": executable,
        "path": str(bundle_path.resolve()),
    }


def discover_installed_applications(
    filter_text: str = "",
    *,
    search_dirs: Sequence[Path] | None = None,
) -> list[dict[str, Any]]:
    """Discover installed macOS .app bundles with bundle identifiers and paths safely."""
    needle = str(filter_text or "").strip().lower()
    by_key: dict[str, dict[str, Any]] = {}
    for base_dir in _candidate_app_dirs(search_dirs):
        try:
            if base_dir.is_symlink() or not base_dir.is_dir():
                continue
            for entry in sorted(base_dir.iterdir(), key=lambda p: p.name.lower()):
                if entry.suffix.lower() != ".app":
                    continue
                meta = _inspect_app_bundle(entry)
                if meta is None:
                    continue
                if needle:
                    haystack = f"{meta['name']} {meta['bundle_name']} {meta['bundle_id']}".lower()
                    if needle not in haystack:
                        continue
                dedup_key = meta["bundle_id"].lower() or meta["bundle_name"].lower()
                if dedup_key not in by_key:
                    by_key[dedup_key] = meta
        except OSError:
            continue
    return sorted(by_key.values(), key=lambda item: item["name"].lower())


def list_installed_applications() -> list[str]:
    """List installed macOS .app bundles cleanly without shell execution."""
    names: set[str] = set()
    for base_dir in (
        Path("/Applications"),
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
    ):
        try:
            if not base_dir.is_dir():
                continue
            for entry in base_dir.iterdir():
                if entry.suffix.lower() == ".app" and not entry.is_symlink():
                    names.add(entry.stem)
        except OSError:
            continue
    return sorted(names)


def is_application_running(app_name: str, executable_name: str = "") -> bool:
    """Check whether a validated application process is currently running on macOS."""
    candidates: list[str] = []
    for raw in (app_name, executable_name):
        if isinstance(raw, str) and raw.strip():
            try:
                candidates.append(validate_app_name(raw))
            except ValueError:
                continue
    for candidate in dict.fromkeys(candidates):
        try:
            proc = _run_executable(_PGREP_BIN, ["-x", "--", candidate], timeout=3.0)
            if proc.returncode == 0:
                return True
        except Exception:
            continue
    return False


def resolve_application_bundle(
    app_name: str,
    *,
    search_dirs: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Resolve an application by display name, bundle name, or bundle identifier safely."""
    cleaned = validate_app_name(app_name)
    lowered = cleaned.lower()
    if lowered.endswith(".app"):
        lowered = lowered[:-4].strip()

    apps = discover_installed_applications(search_dirs=search_dirs)
    matched: dict[str, Any] | None = None
    for item in apps:
        if lowered in {
            item["name"].lower(),
            item["bundle_name"].lower(),
            item["bundle_id"].lower(),
        }:
            matched = item
            break

    if matched is None:
        return {
            "installed": False,
            "query": cleaned,
            "name": cleaned,
            "bundle_name": "",
            "bundle_id": "",
            "version": "",
            "executable": "",
            "path": "",
            "running": False,
            "reason": f"Application '{cleaned}' was not found in permitted macOS application directories.",
        }

    running = is_application_running(matched["bundle_name"], matched["executable"])
    return {
        "installed": True,
        "query": cleaned,
        "name": matched["name"],
        "bundle_name": matched["bundle_name"],
        "bundle_id": matched["bundle_id"],
        "version": matched["version"],
        "executable": matched["executable"],
        "path": matched["path"],
        "running": running,
        "reason": f"Resolved '{matched['name']}' ({matched['bundle_id'] or 'no bundle id'}) at {matched['path']}.",
    }


def check_application(
    app_name: str = "",
    *,
    search_dirs: Sequence[Path] | None = None,
) -> str:
    """Check whether a specific application is installed, or list installed applications with bundle IDs."""
    query = str(app_name or "").strip()
    if not query or query in {"*", "all"}:
        apps = discover_installed_applications(search_dirs=search_dirs)
        preview = [
            f"{a['name']} ({a['bundle_id'] or 'app'})"
            for a in apps[:40]
        ]
        return (
            f"Discovered {len(apps)} installed macOS application(s): "
            + (", ".join(preview) if preview else "none")
        )

    info = resolve_application_bundle(query, search_dirs=search_dirs)
    if not info["installed"]:
        return f"Application '{info['query']}' is NOT installed on this Mac."
    state_label = "running" if info["running"] else "not running"
    bundle_label = info["bundle_id"] or "unknown-bundle-id"
    ver_label = f" v{info['version']}" if info["version"] else ""
    return (
        f"Application '{info['name']}'{ver_label} is installed "
        f"(bundle_id: {bundle_label}, path: {info['path']}, status: {state_label})."
    )


def launch_and_verify_application(
    app_name: str,
    *,
    background: bool = True,
    search_dirs: Sequence[Path] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Validate, resolve, launch, and verify a macOS application bundle."""
    cleaned = validate_app_name(app_name)
    resolved = resolve_application_bundle(cleaned, search_dirs=search_dirs)
    if not resolved["installed"]:
        return {
            "status": "failed",
            "app_name": cleaned,
            "installed": False,
            "verified": False,
            "path": "",
            "bundle_id": "",
            "error": resolved["reason"],
        }

    bundle_path = Path(resolved["path"])
    if bundle_path.is_symlink() or not bundle_path.is_dir():
        return {
            "status": "failed",
            "app_name": cleaned,
            "installed": False,
            "verified": False,
            "path": str(bundle_path),
            "bundle_id": resolved["bundle_id"],
            "error": f"Refused to launch unsafe or symlinked application bundle: {bundle_path}",
        }

    open_args = ["-g", "-a", str(bundle_path)] if background else ["-a", str(bundle_path)]
    try:
        proc = _run_executable(_OPEN_BIN, open_args, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "status": "timed_out",
            "app_name": resolved["name"],
            "installed": True,
            "verified": False,
            "path": str(bundle_path),
            "bundle_id": resolved["bundle_id"],
            "error": f"Timed out trying to open {resolved['name']}.",
        }
    except OSError as exc:
        return {
            "status": "failed",
            "app_name": resolved["name"],
            "installed": True,
            "verified": False,
            "path": str(bundle_path),
            "bundle_id": resolved["bundle_id"],
            "error": f"OS error launching {resolved['name']}: {exc}",
        }

    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if _is_permission_error(stderr):
            return {
                "status": "denied",
                "app_name": resolved["name"],
                "installed": True,
                "verified": False,
                "path": str(bundle_path),
                "bundle_id": resolved["bundle_id"],
                "error": f"Permission denied: RIYA is not authorized to open {resolved['name']}.",
            }
        return {
            "status": "failed",
            "app_name": resolved["name"],
            "installed": True,
            "verified": False,
            "path": str(bundle_path),
            "bundle_id": resolved["bundle_id"],
            "error": f"Launch command failed for {resolved['name']}.",
        }

    # Verify the process is actually running — never claim success merely because open returned 0.
    verified = False
    for _ in range(15):
        if is_application_running(resolved["bundle_name"], resolved["executable"]):
            verified = True
            break
        time.sleep(0.12)

    if not verified:
        return {
            "status": "failed",
            "app_name": resolved["name"],
            "installed": True,
            "verified": False,
            "path": str(bundle_path),
            "bundle_id": resolved["bundle_id"],
            "error": f"Launch command submitted for {resolved['name']}, but running process could not be verified.",
        }

    return {
        "status": "success",
        "app_name": resolved["name"],
        "installed": True,
        "running": True,
        "verified": True,
        "path": str(bundle_path),
        "bundle_id": resolved["bundle_id"],
        "version": resolved["version"],
        "message": f"Opened and verified {resolved['name']} ({resolved['bundle_id'] or bundle_path.name}).",
    }


def list_running_applications() -> list[str]:
    """List currently running non-background macOS applications."""
    try:
        result = _run_osascript(_RUNNING_APPS_SCRIPT, timeout=5.0)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def get_application_state(app_name: str) -> str:
    """Return whether a validated macOS application is not_running, running, or frontmost."""
    cleaned = validate_app_name(app_name)
    try:
        result = _run_osascript(_APP_STATE_SCRIPT, [cleaned], timeout=5.0)
    except subprocess.TimeoutExpired:
        return f"Timed out checking state for {cleaned}."
    except OSError:
        return f"I couldn't inspect state for {cleaned}."
    if result.returncode != 0:
        if _is_permission_error(result.stderr or ""):
            return f"Permission denied checking state for {cleaned}."
        return f"I couldn't inspect state for {cleaned}."
    state = (result.stdout or "").strip() or "unknown"
    return f"{cleaned}: {state}"


def focus_application(app_name: str) -> str:
    """Bring a running validated macOS application to the foreground."""
    cleaned = validate_app_name(app_name)
    try:
        result = _run_osascript(_FOCUS_APP_SCRIPT, [cleaned], timeout=5.0)
    except subprocess.TimeoutExpired:
        return f"Timed out focusing {cleaned}."
    except OSError:
        return f"I couldn't focus {cleaned}."
    if result.returncode != 0:
        if _is_permission_error(result.stderr or ""):
            return f"Permission denied focusing {cleaned}."
        if _is_missing_app_error(result.stderr or ""):
            return f"{cleaned} is not installed."
        return f"I couldn't focus {cleaned}."
    status = (result.stdout or "").strip()
    if status == "NOT_RUNNING":
        return f"{cleaned} is not running."
    return f"Focused {cleaned}."


def clipboard_read_structured() -> dict[str, Any]:
    """Read plain text from the macOS clipboard explicitly without logging sensitive data."""
    try:
        proc = _run_executable(_PBPASTE_BIN, timeout=5.0)
    except subprocess.TimeoutExpired:
        return {
            "status": "timed_out",
            "length": 0,
            "truncated": False,
            "contains_sensitive_pattern": False,
            "content": "",
            "error": "Timed out reading the macOS clipboard.",
        }
    except OSError as exc:
        return {
            "status": "failed",
            "length": 0,
            "truncated": False,
            "contains_sensitive_pattern": False,
            "content": "",
            "error": f"Could not access macOS clipboard: {exc}",
        }

    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if _is_permission_error(stderr):
            return {
                "status": "denied",
                "length": 0,
                "truncated": False,
                "contains_sensitive_pattern": False,
                "content": "",
                "error": "Permission denied reading the macOS clipboard.",
            }
        return {
            "status": "failed",
            "length": 0,
            "truncated": False,
            "contains_sensitive_pattern": False,
            "content": "",
            "error": "Failed to read the macOS clipboard.",
        }

    raw = proc.stdout or ""
    truncated = len(raw) > MAX_CLIPBOARD_CHARS
    bounded = raw[:MAX_CLIPBOARD_CHARS]
    has_secret = bool(_SECRET_CLIPBOARD_RE.search(bounded))
    return {
        "status": "success",
        "length": len(bounded),
        "truncated": truncated,
        "contains_sensitive_pattern": has_secret,
        "content": bounded,
        "sha256_prefix": hashlib.sha256(bounded.encode("utf-8", errors="ignore")).hexdigest()[:12],
    }


def clipboard_read() -> str:
    """Read text from the macOS clipboard (requires explicit permission & confirmation)."""
    res = clipboard_read_structured()
    if res["status"] != "success":
        raise RuntimeError(str(res.get("error") or "Could not read clipboard."))
    if not res["content"]:
        return "Clipboard is currently empty."
    if res["contains_sensitive_pattern"]:
        return "[REDACTED_CLIPBOARD_SECRET: clipboard contains credential-like data and is not echoed to logs]"
    return str(res["content"])


def clipboard_write_structured(text: str) -> dict[str, Any]:
    """Write validated text to the macOS clipboard without logging its contents."""
    if not isinstance(text, str):
        raise ValueError("Clipboard text must be a string.")
    if "\x00" in text:
        raise ValueError("Clipboard text cannot contain null bytes.")
    if len(text) > MAX_CLIPBOARD_CHARS:
        raise ValueError(f"Clipboard text exceeds maximum length ({MAX_CLIPBOARD_CHARS} chars).")

    try:
        proc = _run_executable(_PBCOPY_BIN, timeout=5.0, input_text=text)
    except subprocess.TimeoutExpired:
        return {
            "status": "timed_out",
            "length": 0,
            "verified": False,
            "error": "Timed out writing to the macOS clipboard.",
        }
    except OSError as exc:
        return {
            "status": "failed",
            "length": 0,
            "verified": False,
            "error": f"Could not write to the macOS clipboard: {exc}",
        }

    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if _is_permission_error(stderr):
            return {
                "status": "denied",
                "length": 0,
                "verified": False,
                "error": "Permission denied writing to the macOS clipboard.",
            }
        return {
            "status": "failed",
            "length": 0,
            "verified": False,
            "error": "Failed to write to the macOS clipboard.",
        }

    return {
        "status": "success",
        "length": len(text),
        "verified": True,
        "sha256_prefix": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:12],
        "message": f"Copied {len(text)} character(s) to the macOS clipboard.",
    }


def clipboard_write(text: str) -> str:
    """Write validated text to the macOS clipboard and return a non-sensitive confirmation."""
    res = clipboard_write_structured(text)
    if res["status"] != "success":
        raise RuntimeError(str(res.get("error") or "Could not write to clipboard."))
    return str(res["message"])


def send_notification_structured(
    title: str,
    message: str,
    subtitle: str = "",
) -> dict[str, Any]:
    """Deliver a local macOS user notification via parameterized AppleScript argv."""
    clean_title = _validate_single_line_text(title, field_name="Notification title", max_length=120)
    clean_msg = _validate_single_line_text(message, field_name="Notification message", max_length=500)
    clean_sub = _validate_single_line_text(
        subtitle or "",
        field_name="Notification subtitle",
        max_length=120,
        allow_empty=True,
    )

    try:
        proc = _run_osascript(
            _NOTIFICATION_SCRIPT,
            [clean_title, clean_msg, clean_sub],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timed_out",
            "delivered": False,
            "title": clean_title,
            "subtitle": clean_sub,
            "error": "Timed out sending macOS notification.",
        }
    except OSError as exc:
        return {
            "status": "failed",
            "delivered": False,
            "title": clean_title,
            "subtitle": clean_sub,
            "error": f"OS error sending notification: {exc}",
        }

    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if _is_permission_error(stderr):
            return {
                "status": "denied",
                "delivered": False,
                "title": clean_title,
                "subtitle": clean_sub,
                "error": "Permission denied delivering macOS notification.",
            }
        return {
            "status": "failed",
            "delivered": False,
            "title": clean_title,
            "subtitle": clean_sub,
            "error": "macOS notification could not be delivered.",
        }

    delivered = (proc.stdout or "").strip() == "DELIVERED" or proc.returncode == 0
    return {
        "status": "success" if delivered else "failed",
        "delivered": delivered,
        "title": clean_title,
        "subtitle": clean_sub,
        "message_length": len(clean_msg),
        "message": f"Notification '{clean_title}' delivered via macOS Notification Center.",
    }


def send_notification(title: str, message: str, subtitle: str = "") -> str:
    """Deliver a local macOS notification and return a structured status string."""
    res = send_notification_structured(title=title, message=message, subtitle=subtitle)
    if res["status"] != "success":
        raise RuntimeError(str(res.get("error") or "Notification delivery failed."))
    return str(res["message"])


def get_active_window_structured() -> dict[str, Any]:
    """Inspect frontmost macOS application and window metadata where Accessibility permissions allow."""
    try:
        proc = _run_osascript(_ACTIVE_WINDOW_SCRIPT, timeout=5.0)
    except subprocess.TimeoutExpired:
        return {
            "status": "timed_out",
            "accessibility_permission_required": False,
            "frontmost_app": "",
            "window_count": 0,
            "window_title": "",
            "error": "Timed out inspecting active window metadata.",
        }
    except OSError as exc:
        return {
            "status": "failed",
            "accessibility_permission_required": False,
            "frontmost_app": "",
            "window_count": 0,
            "window_title": "",
            "error": f"Could not inspect active window metadata: {exc}",
        }

    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if _is_permission_error(stderr):
            return {
                "status": "denied",
                "accessibility_permission_required": True,
                "frontmost_app": "",
                "window_count": 0,
                "window_title": "",
                "error": (
                    "macOS Accessibility / Automation permission is not granted for System Events window inspection."
                ),
            }
        return {
            "status": "failed",
            "accessibility_permission_required": False,
            "frontmost_app": "",
            "window_count": 0,
            "window_title": "",
            "error": "Could not inspect active window metadata.",
        }

    lines = (proc.stdout or "").splitlines()
    front_app = lines[0].strip() if len(lines) >= 1 else ""
    try:
        win_count = int(lines[1].strip()) if len(lines) >= 2 else 0
    except ValueError:
        win_count = 0
    win_title = lines[2].strip() if len(lines) >= 3 else ""
    return {
        "status": "success",
        "accessibility_permission_required": False,
        "frontmost_app": front_app,
        "window_count": win_count,
        "window_title": win_title,
        "message": (
            f"Frontmost application: {front_app or 'unknown'} | "
            f"Open windows: {win_count}"
            + (f" | Front window: {win_title}" if win_title else "")
        ),
    }


def active_window_info() -> str:
    """Return active macOS application and window metadata or a clear Accessibility permission notice."""
    info = get_active_window_structured()
    if info.get("accessibility_permission_required"):
        return str(info["error"])
    if info["status"] != "success":
        return str(info.get("error") or "I couldn't inspect the active window.")
    return json.dumps(
        {
            "frontmost_app": info["frontmost_app"],
            "window_count": info["window_count"],
            "window_title": info["window_title"],
            "accessibility_permission_required": False,
        },
        ensure_ascii=False,
    )



# =====================================================================
# MILESTONE 6.4 — INTELLIGENT APPLICATION CONTROL & DESKTOP AUTOMATION
# =====================================================================

# Protected system processes that must NEVER be terminated by RIYA.
_PROTECTED_PROCESSES: frozenset[str] = frozenset({
    "launchd",
    "kernel_task",
    "WindowServer",
    "loginwindow",
    "SystemUIServer",
    "Dock",
    "Finder",          # Finder restarts itself; terminating it is disruptive
    "cfprefsd",
    "distnoted",
    "logd",
    "opendirectoryd",
    "securityd",
    "trustd",
    "coreservicesd",
    "mds",
    "mds_stores",
    "fseventsd",
    "syslogd",
    "diskarbitrationd",
    "configd",
    "notifyd",
    "lsd",
    "powerd",
    "UserEventAgent",
    "coreaudiod",
    "airportd",
    "mediaremoted",
    "remoted",
    "bluetoothd",
    "hidd",
})

# Maximum URL length to prevent abuse
_MAX_URL_LENGTH = 2048
# Allowed URL schemes for open_browser_url
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
# Dangerous characters in URLs
_URL_DANGEROUS_CHARS = frozenset({"`", "'", '"', ";", "|", "&", "$", "(", ")", "{", "}", "<", ">", "\\", "\x00"})


def _is_protected_process(name: str) -> bool:
    """Check whether a process name is in the protected system process list."""
    return str(name).strip() in _PROTECTED_PROCESSES


def _is_riya_own_process(pid: int) -> bool:
    """Check whether a PID belongs to the currently running RIYA process tree."""
    try:
        own_pid = os.getpid()
        if pid == own_pid:
            return True
        # Also check parent PID in case RIYA is running as a child
        own_ppid = os.getppid()
        if pid == own_ppid:
            return True
    except Exception:
        pass
    return False


def search_applications(
    query: str,
    *,
    search_dirs: Sequence[Path] | None = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Search installed macOS applications by fuzzy/substring matching on name, bundle name, and bundle ID.

    Returns a list of matching application metadata dictionaries sorted by relevance.
    """
    raw_query = str(query or "").strip().lower()
    if not raw_query:
        return []
    if len(raw_query) > 128:
        raise ValueError("Search query exceeds maximum length (128 characters).")

    all_apps = discover_installed_applications(search_dirs=search_dirs)
    scored: list[tuple[int, dict[str, Any]]] = []

    for app in all_apps:
        name_lower = app["name"].lower()
        bundle_lower = app["bundle_name"].lower()
        bid_lower = app["bundle_id"].lower()

        # Exact match on name or bundle name scores highest
        if raw_query == name_lower or raw_query == bundle_lower:
            scored.append((100, app))
        # Starts-with scores high
        elif name_lower.startswith(raw_query) or bundle_lower.startswith(raw_query):
            scored.append((80, app))
        # Substring match on any field
        elif raw_query in name_lower or raw_query in bundle_lower or raw_query in bid_lower:
            scored.append((60, app))
        # Word-boundary matching for multi-word queries
        elif any(word in name_lower for word in raw_query.split() if len(word) > 1):
            scored.append((40, app))

    # Sort by relevance score descending, then alphabetically
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"].lower()))
    return [item for _, item in scored[:max(1, min(max_results, 200))]]


def get_application_details(
    app_name: str,
    *,
    search_dirs: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Return detailed application metadata including installation info, version, and running state."""
    cleaned = validate_app_name(app_name)
    resolved = resolve_application_bundle(cleaned, search_dirs=search_dirs)
    if not resolved["installed"]:
        return {
            "found": False,
            "query": cleaned,
            "error": resolved["reason"],
        }

    # Enrich with additional details
    details = {
        "found": True,
        "query": cleaned,
        "name": resolved["name"],
        "bundle_name": resolved["bundle_name"],
        "bundle_id": resolved["bundle_id"],
        "version": resolved["version"],
        "executable": resolved["executable"],
        "path": resolved["path"],
        "running": resolved["running"],
    }

    # Get file size of the .app bundle
    try:
        bundle_path = Path(resolved["path"])
        if bundle_path.is_dir() and not bundle_path.is_symlink():
            total_size = 0
            file_count = 0
            for root, dirs, files in os.walk(str(bundle_path)):
                # Safety: don't follow symlinks inside the bundle
                dirs[:] = [d for d in dirs if not Path(root, d).is_symlink()]
                for f in files:
                    fp = Path(root, f)
                    if not fp.is_symlink():
                        try:
                            total_size += fp.stat().st_size
                            file_count += 1
                        except OSError:
                            pass
            details["bundle_size_bytes"] = total_size
            details["bundle_size_mb"] = round(total_size / (1024 * 1024), 1)
            details["bundle_file_count"] = file_count
    except OSError:
        pass

    return details


def get_running_applications_structured() -> list[dict[str, Any]]:
    """Return structured information about running non-background macOS applications.

    Each entry includes: name, pid (if available via psutil), memory usage, and frontmost state.
    """
    running_names = list_running_applications()
    if not running_names:
        return []

    result: list[dict[str, Any]] = []

    # Try to enrich with psutil data
    proc_map: dict[str, dict[str, Any]] = {}
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "status", "create_time"]):
            try:
                pinfo = proc.info
                pname = str(pinfo.get("name") or "")
                if pname:
                    # Store first occurrence (main process)
                    if pname not in proc_map:
                        mem = pinfo.get("memory_info")
                        rss_bytes = int(getattr(mem, "rss", 0)) if mem else 0
                        proc_map[pname] = {
                            "pid": int(pinfo.get("pid") or 0),
                            "rss_bytes": rss_bytes,
                            "rss_mb": round(rss_bytes / (1024 * 1024), 1) if rss_bytes else 0.0,
                            "cpu_percent": round(float(pinfo.get("cpu_percent") or 0.0), 1),
                            "status": str(pinfo.get("status") or "unknown"),
                            "create_time": float(pinfo.get("create_time") or 0.0),
                        }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except ImportError:
        pass

    for app_name in running_names:
        entry: dict[str, Any] = {"name": app_name}
        pdata = proc_map.get(app_name, {})
        if pdata:
            entry.update(pdata)
        result.append(entry)

    return result


def get_application_resources(
    app_name: str,
) -> dict[str, Any]:
    """Return resource usage (PID, CPU%, memory, status) for a running application using psutil.

    Does not terminate or modify the application. Read-only inspection.
    """
    cleaned = validate_app_name(app_name)

    try:
        import psutil
    except ImportError:
        return {
            "status": "unavailable",
            "app_name": cleaned,
            "error": "psutil is not installed; resource inspection is unavailable.",
        }

    # Find matching processes
    matches: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "status", "create_time"]):
        try:
            pinfo = proc.info
            pname = str(pinfo.get("name") or "")
            if pname.lower() == cleaned.lower():
                mem = pinfo.get("memory_info")
                rss = int(getattr(mem, "rss", 0)) if mem else 0
                create_ts = float(pinfo.get("create_time") or 0.0)
                uptime_sec = round(time.time() - create_ts, 1) if create_ts > 0 else 0.0
                matches.append({
                    "pid": int(pinfo.get("pid") or 0),
                    "name": pname,
                    "rss_bytes": rss,
                    "rss_mb": round(rss / (1024 * 1024), 1) if rss else 0.0,
                    "cpu_percent": round(float(pinfo.get("cpu_percent") or 0.0), 1),
                    "status": str(pinfo.get("status") or "unknown"),
                    "uptime_seconds": uptime_sec,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if not matches:
        return {
            "status": "not_running",
            "app_name": cleaned,
            "processes": [],
            "message": f"No running process found for '{cleaned}'.",
        }

    return {
        "status": "success",
        "app_name": cleaned,
        "process_count": len(matches),
        "processes": matches,
        "total_rss_mb": round(sum(m["rss_mb"] for m in matches), 1),
        "message": f"Found {len(matches)} process(es) for '{cleaned}'.",
    }


def terminate_application(
    app_name: str,
    *,
    search_dirs: Sequence[Path] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Gracefully terminate a running macOS application after validation.

    Safety constraints:
    - Never terminates protected system processes.
    - Never terminates RIYA's own process.
    - Never force-quits; uses graceful AppleScript quit only.
    - Verifies termination after sending quit signal.
    """
    cleaned = validate_app_name(app_name)

    # Check protected processes
    if _is_protected_process(cleaned):
        return {
            "status": "denied",
            "app_name": cleaned,
            "terminated": False,
            "error": f"'{cleaned}' is a protected system process and cannot be terminated by RIYA.",
            "protected": True,
        }

    # Check if it's RIYA itself
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pinfo = proc.info
                if str(pinfo.get("name") or "").lower() == cleaned.lower():
                    if _is_riya_own_process(int(pinfo.get("pid") or 0)):
                        return {
                            "status": "denied",
                            "app_name": cleaned,
                            "terminated": False,
                            "error": "RIYA cannot terminate its own process.",
                            "protected": True,
                        }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass

    # Verify the application exists and is running
    running = list_running_applications()
    # Case-insensitive check
    running_lower = {r.lower(): r for r in running}
    if cleaned.lower() not in running_lower:
        return {
            "status": "not_running",
            "app_name": cleaned,
            "terminated": False,
            "error": f"'{cleaned}' is not currently running.",
        }

    # Use the actual process name from running apps for AppleScript
    actual_name = running_lower[cleaned.lower()]

    # Graceful quit via AppleScript (reuse existing close_application)
    try:
        result = _run_osascript(
            _CLOSE_APPLICATION_SCRIPT,
            [actual_name],
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timed_out",
            "app_name": actual_name,
            "terminated": False,
            "error": f"Timed out waiting for '{actual_name}' to quit gracefully.",
        }
    except OSError as exc:
        return {
            "status": "failed",
            "app_name": actual_name,
            "terminated": False,
            "error": f"OS error terminating '{actual_name}': {exc}",
        }

    if result.returncode != 0:
        stderr = result.stderr or ""
        if _is_permission_error(stderr):
            return {
                "status": "denied",
                "app_name": actual_name,
                "terminated": False,
                "error": f"Permission denied: RIYA is not authorized to quit '{actual_name}'.",
            }
        return {
            "status": "failed",
            "app_name": actual_name,
            "terminated": False,
            "error": f"Failed to quit '{actual_name}'.",
        }

    status_text = (result.stdout or "").strip()

    if status_text == "CLOSED":
        return {
            "status": "success",
            "app_name": actual_name,
            "terminated": True,
            "verified": True,
            "message": f"'{actual_name}' has been gracefully terminated.",
        }
    elif status_text == "NOT_RUNNING":
        return {
            "status": "not_running",
            "app_name": actual_name,
            "terminated": False,
            "error": f"'{actual_name}' was not running when quit was attempted.",
        }
    elif status_text == "STILL_RUNNING":
        return {
            "status": "failed",
            "app_name": actual_name,
            "terminated": False,
            "error": f"'{actual_name}' did not respond to the graceful quit request. It may have unsaved data.",
        }
    else:
        return {
            "status": "failed",
            "app_name": actual_name,
            "terminated": False,
            "error": f"Could not confirm whether '{actual_name}' terminated.",
        }


def validate_url(url: str) -> dict[str, Any]:
    """Validate a URL for safe opening in a browser.

    Returns a dict with 'valid', 'url', and optionally 'error'.
    Only http:// and https:// are permitted.
    """
    if not isinstance(url, str):
        return {"valid": False, "url": "", "error": "URL must be a string."}

    cleaned = url.strip()
    if not cleaned:
        return {"valid": False, "url": "", "error": "URL cannot be empty."}

    if len(cleaned) > _MAX_URL_LENGTH:
        return {"valid": False, "url": cleaned[:100], "error": f"URL exceeds maximum length ({_MAX_URL_LENGTH})."}

    # Check for null bytes
    if "\x00" in cleaned:
        return {"valid": False, "url": "", "error": "URL contains null bytes."}

    # Parse the URL and check scheme first
    from urllib.parse import urlparse
    try:
        parsed = urlparse(cleaned)
    except Exception:
        return {"valid": False, "url": cleaned[:100], "error": "URL could not be parsed."}

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        return {
            "valid": False,
            "url": cleaned[:100],
            "error": f"URL scheme '{scheme}' is not permitted. Only http:// and https:// are allowed.",
        }

    # Check for dangerous shell characters
    for ch in _URL_DANGEROUS_CHARS:
        if ch in cleaned:
            return {"valid": False, "url": "", "error": f"URL contains forbidden character: {ch!r}"}

    host = (parsed.hostname or "").strip()
    if not host:
        return {"valid": False, "url": cleaned[:100], "error": "URL has no hostname."}

    # Block IP addresses that look like local/internal networks (optional safeguard)
    # But allow localhost for development
    if host in {"0.0.0.0", "[::]"}:
        return {"valid": False, "url": cleaned[:100], "error": "URL targets a blocked address."}

    return {"valid": True, "url": cleaned, "scheme": scheme, "host": host}


def open_browser_url(
    url: str,
    browser: str | None = None,
) -> dict[str, Any]:
    """Open a validated URL in the default or specified browser on macOS.

    Security:
    - Only http:// and https:// schemes are permitted.
    - javascript:, file:, data: schemes are rejected.
    - Shell characters are rejected.
    - Uses /usr/bin/open with argument arrays (no shell=True).
    """
    validation = validate_url(url)
    if not validation["valid"]:
        return {
            "status": "failed",
            "url": validation.get("url", ""),
            "error": validation["error"],
            "opened": False,
        }

    clean_url = validation["url"]

    # Build the open command
    open_args: list[str] = [clean_url]

    # If a specific browser is requested, validate and add -a flag
    if browser is not None:
        browser_str = str(browser).strip()
        if browser_str:
            try:
                validated_browser = validate_app_name(browser_str)
            except ValueError as exc:
                return {
                    "status": "failed",
                    "url": clean_url,
                    "browser": browser_str,
                    "error": f"Invalid browser name: {exc}",
                    "opened": False,
                }
            open_args = ["-a", validated_browser, clean_url]

    try:
        proc = _run_executable(_OPEN_BIN, open_args, timeout=10.0)
    except subprocess.TimeoutExpired:
        return {
            "status": "timed_out",
            "url": clean_url,
            "error": "Timed out opening URL.",
            "opened": False,
        }
    except OSError as exc:
        return {
            "status": "failed",
            "url": clean_url,
            "error": f"OS error opening URL: {exc}",
            "opened": False,
        }

    if proc.returncode != 0:
        stderr = proc.stderr or ""
        if _is_permission_error(stderr):
            return {
                "status": "denied",
                "url": clean_url,
                "error": "Permission denied opening URL.",
                "opened": False,
            }
        return {
            "status": "failed",
            "url": clean_url,
            "error": "Failed to open URL in browser.",
            "opened": False,
        }

    return {
        "status": "success",
        "url": clean_url,
        "host": validation.get("host", ""),
        "browser": browser or "default",
        "opened": True,
        "message": f"Opened {clean_url} in {browser or 'default browser'}.",
    }
