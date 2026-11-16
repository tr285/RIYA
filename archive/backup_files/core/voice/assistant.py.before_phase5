from core.voice.microphone import Microphone
from core.voice.stt import LocalSTT
from core.voice.tts import LocalTTS
from core.ai.local_runtime import LocalLlamaRuntime


class VoiceAssistant:
    """Complete local voice pipeline for RIYA."""

    def __init__(
        self,
        audio_path: str = "data/riya_voice.wav",
    ):
        self.audio_path = audio_path

        self.microphone = Microphone()
        self.stt = LocalSTT()
        self.tts = LocalTTS()
        self.runtime = LocalLlamaRuntime()

    def listen_and_respond(self, duration: float = 5) -> str:
        """Listen, understand, generate, and speak a response."""

        # 1. Record voice
        self.microphone.record(
            duration=duration,
            output_path=self.audio_path,
        )

        # 2. Speech → text
        text = self.stt.transcribe(self.audio_path)

        if not text:
            response = "I couldn't hear anything clearly."
            self.tts.speak(response)
            return response

        print(f"You: {text}")

        # 3. Text → RIYA
        self.runtime.load_model()

        try:
            response = self.runtime.generate(text)
        finally:
            self.runtime.unload_model()

        print(f"RIYA: {response}")

        # 4. RIYA → speech
        self.tts.speak(response)

        return response