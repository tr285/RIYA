"""Cross-platform operating-system abstraction and adapter registry for RIYA."""

from __future__ import annotations

from core.device.linux import (
    LinuxApplicationAdapter,
    LinuxClipboardAdapter,
    LinuxFilesystemAdapter,
    LinuxNotificationAdapter,
    LinuxPlatform,
    LinuxSystemInformationAdapter,
    LinuxWindowManagerAdapter,
)
from core.device.mobile import AndroidPlatformAdapter, IOSPlatformAdapter
from core.device.web import WebPlatformAdapter
from core.device.macos import (
    MacOSApplicationAdapter,
    MacOSClipboardAdapter,
    MacOSFilesystemAdapter,
    MacOSNotificationAdapter,
    MacOSPlatform,
    MacOSSystemInformationAdapter,
    MacOSWindowManagerAdapter,
)
from core.device.platform import (
    DEFAULT_PLATFORM_REGISTRY,
    ApplicationAdapter,
    CapabilityDescriptor,
    ClipboardAdapter,
    FilesystemAdapter,
    NotificationAdapter,
    PlatformAdapter,
    PlatformCapabilities,
    PlatformInfo,
    PlatformInterface,
    PlatformOperationResult,
    PlatformRegistry,
    SystemInformationAdapter,
    UnsupportedPlatformCapabilityError,
    WindowManagerAdapter,
    detect_platform_name,
    make_unsupported_result,
)
from core.device.windows import (
    WindowsApplicationAdapter,
    WindowsClipboardAdapter,
    WindowsFilesystemAdapter,
    WindowsNotificationAdapter,
    WindowsPlatform,
    WindowsSystemInformationAdapter,
    WindowsWindowManagerAdapter,
)


DEFAULT_PLATFORM_REGISTRY.register("macos", MacOSPlatform)
DEFAULT_PLATFORM_REGISTRY.register("windows", WindowsPlatform)
DEFAULT_PLATFORM_REGISTRY.register("linux", LinuxPlatform)
DEFAULT_PLATFORM_REGISTRY.register("android", AndroidPlatformAdapter)
DEFAULT_PLATFORM_REGISTRY.register("ios", IOSPlatformAdapter)
DEFAULT_PLATFORM_REGISTRY.register("web", WebPlatformAdapter)


def get_platform_adapter(
    platform_name: str | None = None,
    *,
    fresh: bool = False,
) -> PlatformAdapter:
    """Resolve the active host platform adapter (or a requested platform adapter) without silent fallback."""
    if platform_name is not None:
        return DEFAULT_PLATFORM_REGISTRY.get_adapter(platform_name, fresh=fresh)
    return DEFAULT_PLATFORM_REGISTRY.resolve_host_adapter(fresh=fresh)


__all__ = [
    "AndroidPlatformAdapter",
    "ApplicationAdapter",
    "CapabilityDescriptor",
    "ClipboardAdapter",
    "DEFAULT_PLATFORM_REGISTRY",
    "FilesystemAdapter",
    "IOSPlatformAdapter",
    "LinuxApplicationAdapter",
    "LinuxClipboardAdapter",
    "LinuxFilesystemAdapter",
    "LinuxNotificationAdapter",
    "LinuxPlatform",
    "LinuxSystemInformationAdapter",
    "LinuxWindowManagerAdapter",
    "MacOSApplicationAdapter",
    "MacOSClipboardAdapter",
    "MacOSFilesystemAdapter",
    "MacOSNotificationAdapter",
    "MacOSPlatform",
    "MacOSSystemInformationAdapter",
    "MacOSWindowManagerAdapter",
    "NotificationAdapter",
    "PlatformAdapter",
    "PlatformCapabilities",
    "PlatformInfo",
    "PlatformInterface",
    "PlatformOperationResult",
    "PlatformRegistry",
    "SystemInformationAdapter",
    "UnsupportedPlatformCapabilityError",
    "WebPlatformAdapter",
    "WindowManagerAdapter",
    "WindowsApplicationAdapter",
    "WindowsClipboardAdapter",
    "WindowsFilesystemAdapter",
    "WindowsNotificationAdapter",
    "WindowsPlatform",
    "WindowsSystemInformationAdapter",
    "WindowsWindowManagerAdapter",
    "detect_platform_name",
    "get_platform_adapter",
    "make_unsupported_result",
]
