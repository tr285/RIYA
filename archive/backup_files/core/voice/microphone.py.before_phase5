import sounddevice as sd
import soundfile as sf


class Microphone:
    """Local microphone recorder for RIYA."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
    ):
        self.sample_rate = sample_rate
        self.channels = channels

    def record(self, duration: float, output_path: str) -> str:
        """Record microphone audio and save it as a WAV file."""

        frames = int(duration * self.sample_rate)

        print("🎤 RIYA is listening...")

        audio = sd.rec(
            frames,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
        )

        sd.wait()

        sf.write(
            output_path,
            audio,
            self.sample_rate,
        )

        print(f"Audio saved: {output_path}")

        return output_path