import os
import subprocess
from datetime import datetime


def open_application(app_name: str) -> str:
    """Open a macOS application."""

    result = subprocess.run(
        ["open", "-a", app_name],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return f"I couldn't open {app_name}."

    return f"Opened {app_name}."


def close_application(app_name: str) -> str:
    """Close a macOS application."""

    result = subprocess.run(
        [
            "osascript",
            "-e",
            f'tell application "{app_name}" to quit',
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return f"I couldn't close {app_name}."

    return f"Closed {app_name}."


def take_screenshot() -> str:
    """Take a screenshot and save it to the Desktop."""

    path = os.path.expanduser(
        "~/Desktop/riya_screenshot.png"
    )

    result = subprocess.run(
        ["screencapture", "-x", path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return "I couldn't take the screenshot."

    return f"Screenshot saved to {path}."


def current_time() -> str:
    """Return the current local time."""

    return datetime.now().strftime(
        "It is %I:%M %p."
    )
def set_volume(level: int) -> str:
    """Set macOS output volume from 0 to 100."""

    level = max(0, min(100, level))

    subprocess.run(
        [
            "osascript",
            "-e",
            f"set volume output volume {level}",
        ],
        check=False,
    )

    return f"Volume set to {level}%."


def mute_volume() -> str:
    """Mute macOS audio."""

    subprocess.run(
        [
            "osascript",
            "-e",
            "set volume output muted true",
        ],
        check=False,
    )

    return "Volume muted."


def unmute_volume() -> str:
    """Unmute macOS audio."""

    subprocess.run(
        [
            "osascript",
            "-e",
            "set volume output muted false",
        ],
        check=False,
    )

    return "Volume unmuted."


def battery_status() -> str:
    """Get the current Mac battery status."""

    result = subprocess.run(
        ["pmset", "-g", "batt"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return "I couldn't read the battery status."

    return result.stdout.strip()


def system_information() -> str:
    """Get basic macOS system information."""

    macos = subprocess.run(
        ["sw_vers"],
        capture_output=True,
        text=True,
    )

    machine = subprocess.run(
        ["uname", "-m"],
        capture_output=True,
        text=True,
    )

    return (
        f"{macos.stdout.strip()}\n"
        f"Architecture: {machine.stdout.strip()}"
    )


def lock_screen() -> str:
    """Lock the Mac screen."""

    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to keystroke "q" using {control down, command down}',
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return "I couldn't lock the screen."

    return "Screen locked."