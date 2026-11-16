from abc import ABC, abstractmethod


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