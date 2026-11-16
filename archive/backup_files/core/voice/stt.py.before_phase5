from faster_whisper import WhisperModel


class LocalSTT:
    """Local multilingual Speech-to-Text engine for RIYA."""

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> str:
        """Convert local audio into text."""

        segments, info = self.model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            vad_filter=True,
        )

        text = " ".join(
            segment.text.strip()
            for segment in segments
        )

        print(f"Detected language: {info.language}")

        return text.strip()