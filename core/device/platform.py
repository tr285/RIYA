"""Cross-platform OS abstraction contracts, capability discovery, and adapter registry for RIYA."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import platform as _py_platform
import sys
from typing import Any, Callable


class UnsupportedPlatformCapabilityError(RuntimeError, ValueError):
    """Raised when an OS operation is invoked on a platform or adapter that does not support it."""

    def __init__(
        self,
        capability: str,
        platform_name: str,
        reason: str = "",
        required_permission: str = "",
    ):
        self.capability = str(capability)
        self.platform_name = str(platform_name)
        self.reason = str(
            reason
            or f"Capability '{capability}' is not supported on platform '{platform_name}'."
        )
        self.required_permission = str(required_permission)
        super().__init__(self.reason)


@dataclass(frozen=True)
class PlatformInfo:
    """Structured hardware and operating-system identity for the active host."""

    os_name: str  # "macos" | "windows" | "linux" | "unknown"
    system: str  # e.g. "Darwin", "Windows", "Linux"
    os_version: str
    build_version: str
    architecture: str  # e.g. "arm64", "x86_64"
    machine_model: str = ""
    cpu_brand: str = ""
    cpu_cores_logical: int = 1
    cpu_cores_physical: int = 1
    is_supported: bool = False

    @property
    def platform_id(self) -> str:
        return self.os_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_id": self.os_name,
            "os_name": self.os_name,
            "system": self.system,
            "os_version": self.os_version,
            "build_version": self.build_version,
            "architecture": self.architecture,
            "machine_model": self.machine_model,
            "cpu_brand": self.cpu_brand,
            "cpu_cores_logical": self.cpu_cores_logical,
            "cpu_cores_physical": self.cpu_cores_physical,
            "is_supported": self.is_supported,
        }


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Explicit support and permission metadata for one operating-system capability."""

    capability: str
    supported: bool
    required_permission: str
    description: str = ""
    reason_if_unsupported: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "supported": self.supported,
            "required_permission": self.required_permission,
            "description": self.description,
            "reason_if_unsupported": self.reason_if_unsupported,
        }


@dataclass(frozen=True)
class PlatformCapabilities:
    """Complete capability matrix for a PlatformAdapter."""

    platform_name: str
    platform_available: bool
    descriptors: tuple[CapabilityDescriptor, ...] = field(default_factory=tuple)

    @property
    def supported_capabilities(self) -> tuple[str, ...]:
        return tuple(d.capability for d in self.descriptors if d.supported)

    @property
    def unsupported_capabilities(self) -> tuple[str, ...]:
        return tuple(d.capability for d in self.descriptors if not d.supported)

    @property
    def required_permissions(self) -> dict[str, str]:
        return {d.capability: d.required_permission for d in self.descriptors}

    def supports(self, capability: str) -> bool:
        if not self.platform_available:
            return False
        aliases = {
            "system_information": "system_info",
            "storage_status": "storage_info",
            "application_discovery": "app_discovery",
            "application_launch": "app_launch",
            "application_close": "app_close",
            "application_focus": "app_focus",
            "filesystem_discovery": "file_discovery",
            "filesystem_metadata": "file_metadata",
            "notifications": "local_notification",
            "application_search": "app_search",
            "application_details": "app_details",
            "application_status": "app_status",
            "application_resources": "app_resources",
            "application_terminate": "app_terminate",
            "open_url": "url_open",
        }
        norm = aliases.get(capability, capability)
        for d in self.descriptors:
            if d.capability in {capability, norm}:
                return bool(d.supported)
        return False

    def is_supported(self, capability: str) -> bool:
        return self.supports(capability)

    def supported_keys(self) -> list[str]:
        return list(self.supported_capabilities)

    def unsupported_keys(self) -> list[str]:
        return list(self.unsupported_capabilities)

    def descriptor_for(self, capability: str) -> CapabilityDescriptor | None:
        for d in self.descriptors:
            if d.capability == capability:
                return d
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_name": self.platform_name,
            "platform_available": self.platform_available,
            "supported_capabilities": list(self.supported_capabilities),
            "unsupported_capabilities": list(self.unsupported_capabilities),
            "required_permissions": self.required_permissions,
            "descriptors": [d.to_dict() for d in self.descriptors],
        }


@dataclass(frozen=True)
class PlatformOperationResult:
    """Structured result returned by every platform sub-adapter operation."""

    status: str  # "success" | "failed" | "denied" | "unsupported" | "timed_out"
    capability: str
    platform: str
    operation: str
    supported: bool
    required_permission: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    failure_reason: str = ""
    verified: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    @property
    def unsupported(self) -> bool:
        return self.status == "unsupported" or not self.supported

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "capability": self.capability,
            "platform": self.platform,
            "operation": self.operation,
            "supported": self.supported,
            "required_permission": self.required_permission,
            "data": dict(self.data),
            "message": self.message,
            "failure_reason": self.failure_reason,
            "verified": self.verified,
        }


def make_unsupported_result(
    *,
    platform_name: str,
    capability: str,
    operation: str,
    required_permission: str,
    reason: str = "",
) -> PlatformOperationResult:
    msg = (
        reason
        or f"Unsupported capability: '{capability}' ({operation}) is not implemented or supported on '{platform_name}'."
    )
    return PlatformOperationResult(
        status="unsupported",
        capability=capability,
        platform=platform_name,
        operation=operation,
        supported=False,
        required_permission=required_permission,
        data={},
        message=msg,
        failure_reason=msg,
        verified=False,
    )


# =====================================================================
# SUB-ADAPTER INTERFACES
# =====================================================================


class SystemInformationAdapter(ABC):
    """Adapter contract for OS version, CPU, memory, storage, battery, and network inspection."""

    @abstractmethod
    def get_system_info(self) -> PlatformOperationResult:
        pass

    @abstractmethod
    def get_storage_info(self, path: str = "/") -> PlatformOperationResult:
        pass

    @abstractmethod
    def get_battery_status(self) -> PlatformOperationResult:
        pass

    @abstractmethod
    def get_network_status(self) -> PlatformOperationResult:
        pass


class ApplicationAdapter(ABC):
    """Adapter contract for discovering, resolving, launching, closing, and inspecting local applications."""

    @abstractmethod
    def discover_applications(self, filter_text: str = "") -> PlatformOperationResult:
        pass

    @abstractmethod
    def resolve_application(self, app_name: str) -> PlatformOperationResult:
        pass

    @abstractmethod
    def launch_application(self, app_name: str) -> PlatformOperationResult:
        pass

    @abstractmethod
    def close_application(self, app_name: str) -> PlatformOperationResult:
        pass

    @abstractmethod
    def list_running_applications(self) -> PlatformOperationResult:
        pass

    # Milestone 6.4 extensions — default implementations return unsupported
    def search_applications(self, query: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="unknown",
            capability="app_search",
            operation="search_applications",
            required_permission="system.info",
        )

    def get_application_details(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="unknown",
            capability="app_details",
            operation="get_application_details",
            required_permission="system.info",
        )

    def get_running_applications_structured(self) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="unknown",
            capability="app_status",
            operation="get_running_applications_structured",
            required_permission="desktop.automate",
        )

    def terminate_application(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="unknown",
            capability="app_terminate",
            operation="terminate_application",
            required_permission="system.close_app",
        )

    def get_application_resources(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="unknown",
            capability="app_resources",
            operation="get_application_resources",
            required_permission="desktop.automate",
        )

    def open_url(self, url: str, browser: str | None = None) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="unknown",
            capability="url_open",
            operation="open_url",
            required_permission="browser.interact",
        )


class FilesystemAdapter(ABC):
    """Adapter contract for sandboxed, symlink-safe file discovery, search, and metadata inspection."""

    @abstractmethod
    def discover_files(self, path: str = ".") -> PlatformOperationResult:
        pass

    @abstractmethod
    def search_files(self, name: str, path: str | None = None) -> PlatformOperationResult:
        pass

    @abstractmethod
    def inspect_metadata(self, path: str) -> PlatformOperationResult:
        pass


class ClipboardAdapter(ABC):
    """Adapter contract for explicit permission-gated clipboard read and write operations."""

    @abstractmethod
    def read_clipboard(self) -> PlatformOperationResult:
        pass

    @abstractmethod
    def write_clipboard(self, text: str) -> PlatformOperationResult:
        pass


class NotificationAdapter(ABC):
    """Adapter contract for delivering local user notifications."""

    @abstractmethod
    def send_notification(
        self,
        title: str,
        message: str,
        subtitle: str = "",
    ) -> PlatformOperationResult:
        pass


class WindowManagerAdapter(ABC):
    """Adapter contract for active application and window metadata discovery and focus."""

    @abstractmethod
    def get_active_window_info(self) -> PlatformOperationResult:
        pass

    @abstractmethod
    def focus_application(self, app_name: str) -> PlatformOperationResult:
        pass

    @abstractmethod
    def get_application_state(self, app_name: str) -> PlatformOperationResult:
        pass


# =====================================================================
# UNIFIED PLATFORM INTERFACE & ADAPTER
# =====================================================================


class PlatformInterface(ABC):
    """
    Common interface for RIYA device capabilities.

    RIYA Core uses this interface instead of directly
    depending on macOS, Windows, Linux, Android, or iOS.
    """

    @abstractmethod
    def open_application(self, app_name: str) -> str:
        pass

    @abstractmethod
    def close_application(self, app_name: str) -> str:
        pass

    @abstractmethod
    def take_screenshot(self) -> str:
        pass

    @abstractmethod
    def current_time(self) -> str:
        pass

    @abstractmethod
    def set_volume(self, level: int) -> str:
        pass

    @abstractmethod
    def mute_volume(self) -> str:
        pass

    @abstractmethod
    def unmute_volume(self) -> str:
        pass

    @abstractmethod
    def battery_status(self) -> str:
        pass

    @abstractmethod
    def system_information(self) -> str:
        pass

    @abstractmethod
    def lock_screen(self) -> str:
        pass

    def list_installed_applications(self) -> list[str]:
        return []

    def list_running_applications(self) -> list[str]:
        return []

    def get_application_state(self, app_name: str) -> str:
        return f"{app_name}: unsupported"

    def focus_application(self, app_name: str) -> str:
        return "Focusing applications is unsupported on this platform."


class PlatformAdapter(PlatformInterface):
    """
    Structured OS integration adapter combining sub-adapters and capability introspection.

    Subclasses must explicitly report supported and unsupported capabilities and
    must never silently fall back to another operating system's implementation.
    """

    platform_name: str = "unknown"

    @abstractmethod
    def platform_info(self) -> PlatformInfo:
        """Return structured OS and hardware identity for this adapter."""

    @abstractmethod
    def capabilities(self) -> PlatformCapabilities:
        """Return explicit supported/unsupported capability descriptors and required permissions."""

    @property
    @abstractmethod
    def system_info_adapter(self) -> SystemInformationAdapter:
        pass

    @property
    @abstractmethod
    def application_adapter(self) -> ApplicationAdapter:
        pass

    @property
    @abstractmethod
    def filesystem_adapter(self) -> FilesystemAdapter:
        pass

    @property
    @abstractmethod
    def clipboard_adapter(self) -> ClipboardAdapter:
        pass

    @property
    @abstractmethod
    def notification_adapter(self) -> NotificationAdapter:
        pass

    @property
    @abstractmethod
    def window_adapter(self) -> WindowManagerAdapter:
        pass

    def status_summary(self) -> dict[str, Any]:
        info = self.platform_info()
        caps = self.capabilities()
        return {
            "platform": info.to_dict(),
            "capabilities": caps.to_dict(),
        }

    def describe_capabilities_text(self) -> str:
        info = self.platform_info()
        caps = self.capabilities()
        supported = ", ".join(caps.supported_capabilities) or "none"
        unsupported = ", ".join(caps.unsupported_capabilities) or "none"
        return (
            f"Platform: {info.system} ({info.os_name}) {info.os_version} [{info.architecture}] | "
            f"Supported: {info.is_supported} | "
            f"Supported capabilities: {supported} | "
            f"Unsupported capabilities: {unsupported}"
        )


# =====================================================================
# PLATFORM DETECTION & ADAPTER REGISTRY
# =====================================================================


def detect_platform_name(system_name: str | None = None) -> str:
    """Normalize OS detection into 'macos', 'windows', 'linux', 'android', 'ios', 'web', or 'unknown'."""
    raw = str(system_name if system_name is not None else (_py_platform.system() or sys.platform)).strip().lower()
    if raw in {"darwin", "macos", "mac"}:
        return "macos"
    if raw.startswith("win") or raw == "windows":
        return "windows"
    if "android" in raw:
        return "android"
    if raw in {"ios", "iphone", "ipad"}:
        return "ios"
    if raw in {"web", "browser", "wasm"}:
        return "web"
    if raw.startswith("linux"):
        return "linux"
    return "unknown"


class PlatformRegistry:
    """Registry of platform adapters with strict no-silent-fallback resolution."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], PlatformAdapter]] = {}
        self._instances: dict[str, PlatformAdapter] = {}

    def register(
        self,
        platform_name: str,
        factory: Callable[[], PlatformAdapter],
        *,
        overwrite: bool = True,
    ) -> None:
        key = str(platform_name or "").strip().lower()
        if not key:
            raise ValueError("platform_name must be a non-empty string.")
        if not callable(factory):
            raise ValueError(f"Adapter factory for '{key}' must be callable.")
        if key in self._factories and not overwrite:
            raise ValueError(f"Platform adapter already registered for '{key}'.")
        self._factories[key] = factory
        self._instances.pop(key, None)

    def list_registered_platforms(self) -> list[str]:
        return sorted(self._factories.keys())

    def list_platforms(self) -> list[str]:
        return self.list_registered_platforms()

    def create(self, platform_name: str) -> PlatformAdapter:
        return self.get_adapter(platform_name, fresh=True)

    def has(self, platform_name: str) -> bool:
        return str(platform_name or "").strip().lower() in self._factories

    def get_adapter(self, platform_name: str, *, fresh: bool = False) -> PlatformAdapter:
        key = str(platform_name or "").strip().lower()
        factory = self._factories.get(key)
        if factory is None:
            raise UnsupportedPlatformCapabilityError(
                capability="platform_adapter",
                platform_name=key or "unknown",
                reason=f"No platform adapter is registered for '{key or 'unknown'}'.",
            )
        if fresh or key not in self._instances:
            self._instances[key] = factory()
        return self._instances[key]

    def resolve_host_adapter(
        self,
        *,
        system_name: str | None = None,
        fresh: bool = False,
    ) -> PlatformAdapter:
        detected = detect_platform_name(system_name)
        return self.get_adapter(detected, fresh=fresh)


DEFAULT_PLATFORM_REGISTRY = PlatformRegistry()
