"""Mobile platform integration contracts for RIYA (Phase 6.7).

Provides formal architecture contracts, lifecycle management, and capability
specifications for Android and iOS mobile companion integrations.
All capabilities report truthful status without mock execution.
"""

from __future__ import annotations

from enum import Enum
import platform as _py_platform
import sys
from typing import Any

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


class MobileLifecycleState(str, Enum):
    """Standard lifecycle states for mobile host execution."""

    FOREGROUND = "foreground"
    BACKGROUND = "background"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


_ANDROID_UNSUPPORTED_HOST_MSG = (
    "Android native integration requires an Android ART / APK host environment. "
    "Current host is not running Android."
)

_IOS_UNSUPPORTED_HOST_MSG = (
    "iOS native integration requires an Apple iOS runtime container (XCTest/UIKit). "
    "Current host is not running iOS."
)


# =============================================================================
# ANDROID PLATFORM CAPABILITIES & ADAPTER
# =============================================================================

_ANDROID_CAPABILITIES: tuple[CapabilityDescriptor, ...] = (
    CapabilityDescriptor(
        capability="system_info",
        supported=False,
        required_permission="system.info",
        description="Android hardware model, Android OS version, and Build ID.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="storage_info",
        supported=False,
        required_permission="system.info",
        description="Internal and external scoped app storage inspection.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="battery_status",
        supported=False,
        required_permission="system.info",
        description="Android BatteryManager level, charging state, and power saver mode.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="network_status",
        supported=False,
        required_permission="system.info",
        description="Android ConnectivityManager Wi-Fi and mobile network telemetry.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="local_notification",
        supported=False,
        required_permission="system.notification",
        description="Android NotificationCompat channel alerts with action buttons.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="clipboard_read",
        supported=False,
        required_permission="system.clipboard",
        description="Android ClipboardManager primary clip inspection.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="clipboard_write",
        supported=False,
        required_permission="system.clipboard",
        description="Android ClipboardManager ClipData placement.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="audio_input",
        supported=False,
        required_permission="voice.microphone",
        description="Android AudioRecord 16kHz PCM stream capture.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="audio_output",
        supported=False,
        required_permission="voice.speaker",
        description="Android AudioTrack / MediaPlayer local playback.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="app_launch",
        supported=False,
        required_permission="system.open_app",
        description="Android Intent-based explicit activity launching.",
        reason_if_unsupported=_ANDROID_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="window_inspection",
        supported=False,
        required_permission="desktop.automate",
        description="Android AccessibilityService view-hierarchy inspection.",
        reason_if_unsupported="Android sandbox restricts window inspection without explicit AccessibilityService grant.",
    ),
    CapabilityDescriptor(
        capability="screen_lock",
        supported=False,
        required_permission="system.lock_screen",
        description="Android lock screen via DevicePolicyManager.",
        reason_if_unsupported="Android lock screen requires enterprise Device Admin elevation.",
    ),
)


class AndroidSystemInfoAdapter(SystemInformationAdapter):
    def get_system_info(self) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="system_info",
            operation="get_system_info",
            required_permission="system.info",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def get_storage_info(self, path: str = "/") -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="storage_info",
            operation="get_storage_info",
            required_permission="system.info",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def get_battery_status(self) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="battery_status",
            operation="get_battery_status",
            required_permission="system.info",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def get_network_status(self) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="network_status",
            operation="get_network_status",
            required_permission="system.info",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )


class AndroidApplicationAdapter(ApplicationAdapter):
    def discover_applications(self, filter_text: str = "") -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="app_discovery",
            operation="discover_applications",
            required_permission="system.info",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def resolve_application(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="app_discovery",
            operation="resolve_application",
            required_permission="system.info",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def launch_application(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="app_launch",
            operation="launch_application",
            required_permission="system.open_app",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def close_application(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="app_close",
            operation="close_application",
            required_permission="system.close_app",
            reason="Android applications cannot be arbitrarily killed without root or system privileges.",
        )

    def list_running_applications(self) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="app_status",
            operation="list_running_applications",
            required_permission="desktop.automate",
            reason="Android ActivityManager restrictions prevent enumerating foreign running tasks.",
        )


class AndroidFilesystemAdapter(FilesystemAdapter):
    def discover_files(self, path: str = ".") -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="file_discovery",
            operation="discover_files",
            required_permission="filesystem.read",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def search_files(self, name: str, path: str | None = None) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="file_discovery",
            operation="search_files",
            required_permission="filesystem.read",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def inspect_metadata(self, path: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="file_metadata",
            operation="inspect_metadata",
            required_permission="filesystem.read",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )


class AndroidClipboardAdapter(ClipboardAdapter):
    def read_clipboard(self) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="clipboard_read",
            operation="read_clipboard",
            required_permission="system.clipboard",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def write_clipboard(self, text: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="clipboard_write",
            operation="write_clipboard",
            required_permission="system.clipboard",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )


class AndroidNotificationAdapter(NotificationAdapter):
    def send_notification(self, title: str, message: str, subtitle: str = "") -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="local_notification",
            operation="send_notification",
            required_permission="system.notification",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )


class AndroidWindowManagerAdapter(WindowManagerAdapter):
    def get_active_window_info(self) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="window_inspection",
            operation="get_active_window_info",
            required_permission="desktop.automate",
            reason="Android window hierarchy inspection is unsupported without AccessibilityService.",
        )

    def focus_application(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="app_focus",
            operation="focus_application",
            required_permission="desktop.automate",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )

    def get_application_state(self, app_name: str) -> PlatformOperationResult:
        return make_unsupported_result(
            platform_name="android",
            capability="app_status",
            operation="get_application_state",
            required_permission="desktop.automate",
            reason=_ANDROID_UNSUPPORTED_HOST_MSG,
        )


class AndroidPlatformAdapter(PlatformAdapter):
    """Truthful Android platform integration adapter."""

    platform_name = "android"

    def __init__(self):
        self._sys = AndroidSystemInfoAdapter()
        self._app = AndroidApplicationAdapter()
        self._fs = AndroidFilesystemAdapter()
        self._clip = AndroidClipboardAdapter()
        self._notif = AndroidNotificationAdapter()
        self._win = AndroidWindowManagerAdapter()

    def platform_info(self) -> PlatformInfo:
        return PlatformInfo(
            os_name="android",
            system="Android",
            os_version="14.0",
            build_version="API_34",
            architecture="aarch64",
            machine_model="Android Device Contract",
            cpu_brand="ARM Cortex",
            cpu_cores_logical=8,
            cpu_cores_physical=8,
            is_supported=False,
        )

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name="android",
            platform_available=False,
            descriptors=_ANDROID_CAPABILITIES,
        )

    @property
    def system_info_adapter(self) -> SystemInformationAdapter:
        return self._sys

    @property
    def application_adapter(self) -> ApplicationAdapter:
        return self._app

    @property
    def filesystem_adapter(self) -> FilesystemAdapter:
        return self._fs

    @property
    def clipboard_adapter(self) -> ClipboardAdapter:
        return self._clip

    @property
    def notification_adapter(self) -> NotificationAdapter:
        return self._notif

    @property
    def window_adapter(self) -> WindowManagerAdapter:
        return self._win

    def open_application(self, app_name: str) -> str:
        raise UnsupportedPlatformCapabilityError("open_application", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def close_application(self, app_name: str) -> str:
        raise UnsupportedPlatformCapabilityError("close_application", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def take_screenshot(self) -> str:
        raise UnsupportedPlatformCapabilityError("take_screenshot", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def current_time(self) -> str:
        raise UnsupportedPlatformCapabilityError("current_time", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def set_volume(self, level: int) -> str:
        raise UnsupportedPlatformCapabilityError("set_volume", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def mute_volume(self) -> str:
        raise UnsupportedPlatformCapabilityError("mute_volume", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def unmute_volume(self) -> str:
        raise UnsupportedPlatformCapabilityError("unmute_volume", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def battery_status(self) -> str:
        raise UnsupportedPlatformCapabilityError("battery_status", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def system_information(self) -> str:
        raise UnsupportedPlatformCapabilityError("system_information", "android", _ANDROID_UNSUPPORTED_HOST_MSG)

    def lock_screen(self) -> str:
        raise UnsupportedPlatformCapabilityError("lock_screen", "android", _ANDROID_UNSUPPORTED_HOST_MSG)


# =============================================================================
# IOS PLATFORM CAPABILITIES & ADAPTER
# =============================================================================

_IOS_CAPABILITIES: tuple[CapabilityDescriptor, ...] = (
    CapabilityDescriptor(
        capability="system_info",
        supported=False,
        required_permission="system.info",
        description="iOS UIDevice model, systemVersion, and localizedModel.",
        reason_if_unsupported=_IOS_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="battery_status",
        supported=False,
        required_permission="system.info",
        description="iOS UIDevice.batteryLevel and batteryState inspection.",
        reason_if_unsupported=_IOS_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="local_notification",
        supported=False,
        required_permission="system.notification",
        description="iOS UNUserNotificationCenter scheduled local alerts.",
        reason_if_unsupported=_IOS_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="clipboard_read",
        supported=False,
        required_permission="system.clipboard",
        description="iOS UIPasteboard string inspection.",
        reason_if_unsupported=_IOS_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="clipboard_write",
        supported=False,
        required_permission="system.clipboard",
        description="iOS UIPasteboard write access.",
        reason_if_unsupported=_IOS_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="audio_input",
        supported=False,
        required_permission="voice.microphone",
        description="iOS AVAudioSession microphone capture.",
        reason_if_unsupported=_IOS_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="audio_output",
        supported=False,
        required_permission="voice.speaker",
        description="iOS AVSpeechSynthesizer local output.",
        reason_if_unsupported=_IOS_UNSUPPORTED_HOST_MSG,
    ),
    CapabilityDescriptor(
        capability="window_inspection",
        supported=False,
        required_permission="desktop.automate",
        description="iOS UIWindow hierarchy inspection.",
        reason_if_unsupported="iOS sandbox strictly forbids foreign window inspection.",
    ),
    CapabilityDescriptor(
        capability="screen_lock",
        supported=False,
        required_permission="system.lock_screen",
        description="iOS lock screen execution.",
        reason_if_unsupported="iOS does not expose programmatic screen locking to sandboxed apps.",
    ),
)


class IOSPlatformAdapter(PlatformAdapter):
    """Truthful iOS platform integration adapter."""

    platform_name = "ios"

    def __init__(self):
        self._sys = AndroidSystemInfoAdapter()
        self._app = AndroidApplicationAdapter()
        self._fs = AndroidFilesystemAdapter()
        self._clip = AndroidClipboardAdapter()
        self._notif = AndroidNotificationAdapter()
        self._win = AndroidWindowManagerAdapter()

    def platform_info(self) -> PlatformInfo:
        return PlatformInfo(
            os_name="ios",
            system="iOS",
            os_version="18.1",
            build_version="22B83",
            architecture="arm64",
            machine_model="Apple iPhone / iPad Contract",
            cpu_brand="Apple Silicon Mobile",
            cpu_cores_logical=6,
            cpu_cores_physical=6,
            is_supported=False,
        )

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name="ios",
            platform_available=False,
            descriptors=_IOS_CAPABILITIES,
        )

    @property
    def system_info_adapter(self) -> SystemInformationAdapter:
        return self._sys

    @property
    def application_adapter(self) -> ApplicationAdapter:
        return self._app

    @property
    def filesystem_adapter(self) -> FilesystemAdapter:
        return self._fs

    @property
    def clipboard_adapter(self) -> ClipboardAdapter:
        return self._clip

    @property
    def notification_adapter(self) -> NotificationAdapter:
        return self._notif

    @property
    def window_adapter(self) -> WindowManagerAdapter:
        return self._win

    def open_application(self, app_name: str) -> str:
        raise UnsupportedPlatformCapabilityError("open_application", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def close_application(self, app_name: str) -> str:
        raise UnsupportedPlatformCapabilityError("close_application", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def take_screenshot(self) -> str:
        raise UnsupportedPlatformCapabilityError("take_screenshot", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def current_time(self) -> str:
        raise UnsupportedPlatformCapabilityError("current_time", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def set_volume(self, level: int) -> str:
        raise UnsupportedPlatformCapabilityError("set_volume", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def mute_volume(self) -> str:
        raise UnsupportedPlatformCapabilityError("mute_volume", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def unmute_volume(self) -> str:
        raise UnsupportedPlatformCapabilityError("unmute_volume", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def battery_status(self) -> str:
        raise UnsupportedPlatformCapabilityError("battery_status", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def system_information(self) -> str:
        raise UnsupportedPlatformCapabilityError("system_information", "ios", _IOS_UNSUPPORTED_HOST_MSG)

    def lock_screen(self) -> str:
        raise UnsupportedPlatformCapabilityError("lock_screen", "ios", _IOS_UNSUPPORTED_HOST_MSG)
