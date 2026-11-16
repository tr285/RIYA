"""Voice subsystem exceptions with safe, user-facing messages.

Every exception carries a ``user_message`` that is safe to show in the UI or
speak aloud. Internal details (device names, paths, stack traces, transcript
content) are never placed in ``user_message``.
"""

from __future__ import annotations


class VoiceError(Exception):
    """Base class for all RIYA voice errors."""

    default_user_message = "Something went wrong with voice mode."

    def __init__(self, message: str | None = None, *, user_message: str | None = None):
        super().__init__(message or self.default_user_message)
        self.user_message = user_message or self.default_user_message


class MicrophoneUnavailableError(VoiceError):
    default_user_message = (
        "I couldn't find a working microphone. Check that one is connected "
        "and selected in Voice Settings."
    )


class MicrophonePermissionError(VoiceError):
    default_user_message = (
        "Microphone access appears to be blocked. Allow microphone access for "
        "this app in System Settings > Privacy & Security > Microphone, then "
        "start voice mode again."
    )


class AudioCaptureError(VoiceError):
    default_user_message = "The microphone stopped unexpectedly. Voice mode was stopped safely."


class STTModelUnavailableError(VoiceError):
    default_user_message = (
        "The local speech-recognition model is not installed. RIYA will not "
        "download models automatically; see the voice setup guide."
    )


class TranscriptionError(VoiceError):
    default_user_message = "I couldn't understand that audio. Please try again."


class TTSError(VoiceError):
    default_user_message = "I couldn't generate speech for that response. The text is shown in the transcript."


class PlaybackError(VoiceError):
    default_user_message = "I couldn't play audio on the selected output device."


class VoiceSessionBusyError(VoiceError):
    default_user_message = "A voice session is already active."


class InvalidStateTransition(VoiceError):
    default_user_message = "Voice mode reached an unexpected state and was reset."


class VoicePermissionError(VoiceError, PermissionError):
    default_user_message = (
        "This voice or audio action is blocked by the user's RIYA permission policy."
    )
