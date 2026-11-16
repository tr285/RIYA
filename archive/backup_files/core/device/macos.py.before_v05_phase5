from core.device.platform import PlatformInterface

from core.tools.system.macos import (
    open_application,
    close_application,
    take_screenshot,
    current_time,
    set_volume,
    mute_volume,
    unmute_volume,
    battery_status,
    system_information,
    lock_screen,
)


class MacOSPlatform(PlatformInterface):
    """macOS implementation of RIYA's platform capabilities."""

    def open_application(self, app_name: str) -> str:
        return open_application(app_name)

    def close_application(self, app_name: str) -> str:
        return close_application(app_name)

    def take_screenshot(self) -> str:
        return take_screenshot()

    def current_time(self) -> str:
        return current_time()

    def set_volume(self, level: int) -> str:
        return set_volume(level)

    def mute_volume(self) -> str:
        return mute_volume()

    def unmute_volume(self) -> str:
        return unmute_volume()

    def battery_status(self) -> str:
        return battery_status()

    def system_information(self) -> str:
        return system_information()

    def lock_screen(self) -> str:
        return lock_screen()