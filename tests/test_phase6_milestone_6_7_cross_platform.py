"""Tests for RIYA Phase 6 Milestone 6.7: Cross-Platform Architecture and Integration.

Covers:
- Standardized platform registry and multi-platform adapter creation.
- Truthful platform detection for macOS, Windows, Linux, Android, iOS, and Web.
- Honest capability reporting without mock execution or false positives.
- Windows and Linux adapters fail closed with explicit unsupported statuses/errors.
- Mobile platform contracts (Android and iOS lifecycle, sandboxing, and capabilities).
- Web client session contracts (transports, origin isolation, and browser sandbox boundaries).
"""

from __future__ import annotations

import pytest

from core.device import (
    AndroidPlatformAdapter,
    DEFAULT_PLATFORM_REGISTRY,
    IOSPlatformAdapter,
    LinuxPlatform,
    MacOSPlatform,
    WebPlatformAdapter,
    WindowsPlatform,
    detect_platform_name,
    get_platform_adapter,
)
from core.device.mobile import MobileLifecycleState
from core.device.platform import (
    CapabilityDescriptor,
    PlatformCapabilities,
    PlatformInfo,
    PlatformOperationResult,
    UnsupportedPlatformCapabilityError,
)
from core.device.web import WebClientSession, WebTransportType


# =============================================================================
# 1. Platform Detection & Registry Tests
# =============================================================================

def test_platform_name_detection_across_all_supported_targets():
    assert detect_platform_name("Darwin") == "macos"
    assert detect_platform_name("macOS") == "macos"
    assert detect_platform_name("Windows") == "windows"
    assert detect_platform_name("win32") == "windows"
    assert detect_platform_name("Linux") == "linux"
    assert detect_platform_name("android") == "android"
    assert detect_platform_name("iOS") == "ios"
    assert detect_platform_name("iPhone") == "ios"
    assert detect_platform_name("web") == "web"
    assert detect_platform_name("browser") == "web"
    assert detect_platform_name("unknown_os") == "unknown"


def test_platform_registry_contains_all_six_platforms():
    registered = DEFAULT_PLATFORM_REGISTRY.list_registered_platforms()
    for expected in ("android", "ios", "linux", "macos", "web", "windows"):
        assert expected in registered

    assert isinstance(DEFAULT_PLATFORM_REGISTRY.create("macos"), MacOSPlatform)
    assert isinstance(DEFAULT_PLATFORM_REGISTRY.create("windows"), WindowsPlatform)
    assert isinstance(DEFAULT_PLATFORM_REGISTRY.create("linux"), LinuxPlatform)
    assert isinstance(DEFAULT_PLATFORM_REGISTRY.create("android"), AndroidPlatformAdapter)
    assert isinstance(DEFAULT_PLATFORM_REGISTRY.create("ios"), IOSPlatformAdapter)
    assert isinstance(DEFAULT_PLATFORM_REGISTRY.create("web"), WebPlatformAdapter)


def test_platform_registry_rejects_unknown_platform():
    with pytest.raises(UnsupportedPlatformCapabilityError) as exc_info:
        DEFAULT_PLATFORM_REGISTRY.create("solaris")
    assert "No platform adapter is registered" in str(exc_info.value)


# =============================================================================
# 2. Host Platform & macOS Truthful Capability Verification
# =============================================================================

def test_host_platform_adapter_resolution():
    host_adapter = get_platform_adapter()
    assert host_adapter is not None
    info = host_adapter.platform_info()
    assert isinstance(info, PlatformInfo)
    assert info.os_name in {"macos", "windows", "linux"}

    caps = host_adapter.capabilities()
    assert isinstance(caps, PlatformCapabilities)
    assert len(caps.descriptors) > 0


def test_macos_platform_capabilities():
    mac = MacOSPlatform()
    info = mac.platform_info()
    assert info.os_name == "macos"
    assert info.is_supported is True
    assert "arm64" in info.architecture or "x86_64" in info.architecture

    caps = mac.capabilities()
    assert caps.platform_available is True
    assert caps.supports("system_info")
    assert caps.supports("file_discovery")
    assert caps.supports("app_launch")
    assert caps.supports("local_notification")
    assert caps.supports("clipboard_read")
    assert caps.supports("clipboard_write")

    summary_text = mac.describe_capabilities_text()
    assert "Platform: Darwin" in summary_text
    assert "Supported: True" in summary_text


# =============================================================================
# 3. Windows & Linux Truthful Fail-Closed Execution
# =============================================================================

def test_windows_platform_truthful_unsupported_reporting():
    win = WindowsPlatform()
    info = win.platform_info()
    assert info.os_name == "windows"
    assert info.is_supported is False

    caps = win.capabilities()
    assert caps.platform_available is False
    assert len(caps.supported_capabilities) == 0
    assert len(caps.unsupported_capabilities) > 0

    # Sub-adapter methods return structured unsupported results with reason
    res_sys = win.system_info_adapter.get_system_info()
    assert isinstance(res_sys, PlatformOperationResult)
    assert res_sys.status == "unsupported"
    assert res_sys.supported is False
    assert "Windows native OS integration is not yet implemented" in res_sys.message

    res_app = win.application_adapter.launch_application("notepad")
    assert res_app.status == "unsupported"
    assert res_app.supported is False

    # Direct methods fail closed with UnsupportedPlatformCapabilityError
    with pytest.raises(UnsupportedPlatformCapabilityError):
        win.open_application("notepad")

    with pytest.raises(UnsupportedPlatformCapabilityError):
        win.take_screenshot()

    with pytest.raises(UnsupportedPlatformCapabilityError):
        win.set_volume(50)


def test_linux_platform_truthful_unsupported_reporting():
    linux = LinuxPlatform()
    info = linux.platform_info()
    assert info.os_name == "linux"
    assert info.is_supported is False

    caps = linux.capabilities()
    assert caps.platform_available is False
    assert len(caps.supported_capabilities) == 0

    res_clip = linux.clipboard_adapter.read_clipboard()
    assert res_clip.status == "unsupported"
    assert res_clip.supported is False
    assert "Linux native OS integration is not yet implemented" in res_clip.message

    with pytest.raises(UnsupportedPlatformCapabilityError):
        linux.open_application("firefox")

    with pytest.raises(UnsupportedPlatformCapabilityError):
        linux.lock_screen()


# =============================================================================
# 4. Mobile Integration Contracts (Android & iOS)
# =============================================================================

def test_mobile_lifecycle_states():
    assert MobileLifecycleState.FOREGROUND == "foreground"
    assert MobileLifecycleState.BACKGROUND == "background"
    assert MobileLifecycleState.SUSPENDED == "suspended"
    assert MobileLifecycleState.TERMINATED == "terminated"


def test_android_platform_adapter_contract():
    android = AndroidPlatformAdapter()
    info = android.platform_info()
    assert info.os_name == "android"
    assert info.system == "Android"
    assert info.is_supported is False  # not on Android device

    caps = android.capabilities()
    assert caps.platform_name == "android"
    assert caps.platform_available is False

    res_sys = android.system_info_adapter.get_system_info()
    assert res_sys.status == "unsupported"
    assert "Android native integration requires an Android ART" in res_sys.message

    res_close = android.application_adapter.close_application("com.example.app")
    assert res_close.status == "unsupported"
    assert "cannot be arbitrarily killed" in res_close.message

    with pytest.raises(UnsupportedPlatformCapabilityError):
        android.open_application("Chrome")

    with pytest.raises(UnsupportedPlatformCapabilityError):
        android.take_screenshot()


def test_ios_platform_adapter_contract():
    ios = IOSPlatformAdapter()
    info = ios.platform_info()
    assert info.os_name == "ios"
    assert info.system == "iOS"
    assert info.is_supported is False  # not on iOS device

    caps = ios.capabilities()
    assert caps.platform_name == "ios"
    assert caps.platform_available is False

    desc_win = caps.descriptor_for("window_inspection")
    assert desc_win is not None
    assert desc_win.supported is False
    assert "sandbox strictly forbids" in desc_win.reason_if_unsupported

    with pytest.raises(UnsupportedPlatformCapabilityError):
        ios.open_application("Safari")

    with pytest.raises(UnsupportedPlatformCapabilityError):
        ios.lock_screen()


# =============================================================================
# 5. Web Client Session & Platform Contracts
# =============================================================================

def test_web_client_session_model():
    sess = WebClientSession(
        session_id="ws_sess_001",
        user_id="alice",
        client_ip="127.0.0.1",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        transport=WebTransportType.WEBSOCKET,
        authenticated=True,
    )
    d = sess.to_dict()
    assert d["session_id"] == "ws_sess_001"
    assert d["user_id"] == "alice"
    assert d["transport"] == "websocket"
    assert d["authenticated"] is True
    assert "created_at" in d


def test_web_platform_adapter_capabilities_and_boundaries():
    web = WebPlatformAdapter()
    info = web.platform_info()
    assert info.os_name == "web"
    assert info.is_supported is True

    caps = web.capabilities()
    assert caps.platform_available is True

    # Web-supported capabilities
    assert caps.supports("system_info")
    assert caps.supports("storage_info")
    assert caps.supports("battery_status")
    assert caps.supports("network_status")
    assert caps.supports("local_notification")
    assert caps.supports("clipboard_read")
    assert caps.supports("clipboard_write")
    assert caps.supports("audio_input")
    assert caps.supports("audio_output")

    # Host operations restricted by browser sandbox
    assert not caps.supports("app_launch")
    assert not caps.supports("window_inspection")
    assert not caps.supports("screen_lock")

    res_sys = web.system_info_adapter.get_system_info()
    assert res_sys.status == "success"
    assert "WebRTC" in res_sys.data["standards"]

    res_notif = web.notification_adapter.send_notification("Alert", "Task completed")
    assert res_notif.status == "success"

    # Direct process spawning / desktop control raises UnsupportedPlatformCapabilityError
    with pytest.raises(UnsupportedPlatformCapabilityError) as exc_app:
        web.open_application("Terminal")
    assert "Web platform integration requires" in str(exc_app.value)

    with pytest.raises(UnsupportedPlatformCapabilityError):
        web.set_volume(80)

    with pytest.raises(UnsupportedPlatformCapabilityError):
        web.lock_screen()
