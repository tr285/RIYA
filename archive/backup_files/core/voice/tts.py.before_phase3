import subprocess


class LocalTTS:
    """Local text-to-speech engine for RIYA."""

    def __init__(self, voice: str = "Samantha"):
        self.voice = voice

    def speak(self, text: str) -> None:
        """Speak text using the local operating-system voice."""

        if not text.strip():
            return

        subprocess.run(
            [
                "say",
                "-v",
                self.voice,
                text,
            ],
            check=True,
        )