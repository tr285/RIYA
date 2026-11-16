"""Chunked, interruptible local audio output player."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import sounddevice as sd
import soundfile as sf

from core.voice.config import VoiceConfig
from core.voice.errors import PlaybackError, TTSError
from core.voice.personality import (
    prepare_text_for_speech,
    split_into_speech_chunks,
    style_rate_wpm,
)
from core.voice.tts import LocalTTS

logger = logging.getLogger("riya.voice.audio_output")


def _resolve_output_device(device: int | str | None) -> int | str | None:
    if device is None or isinstance(device, int):
        return device
    needle = device.strip().lower()
    try:
        for idx, dev in enumerate(sd.query_devices()):
            if int(dev.get("max_output_channels", 0) or 0) <= 0:
                continue
            name = str(dev.get("name", "")).lower()
            if name == needle or needle in name:
                return idx
    except Exception:
        pass
    return device


class AudioOutputPlayer:
    """Plays speech chunks cleanly and stops immediately on interruption."""

    def __init__(
        self,
        config: VoiceConfig | None = None,
        *,
        tts: Any | None = None,
        output_stream_factory: Callable[..., Any] | None = None,
    ):
        self.config = config or VoiceConfig()
        self.tts = tts or LocalTTS(
            voice=self.config.voice,
            rate_wpm=style_rate_wpm(self.config.speech_rate_wpm, self.config.response_style),
            volume=self.config.volume,
        )
        self._output_stream_factory = output_stream_factory

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._playing = False
        self._current_level = 0.0
        self._spoken_chunks: list[str] = []
        self._cancelled = False
        self._active_stream: Any = None

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    @property
    def current_level(self) -> float:
        with self._lock:
            return self._current_level

    @property
    def spoken_chunks(self) -> list[str]:
        with self._lock:
            return list(self._spoken_chunks)

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def update_config(self, config: VoiceConfig) -> None:
        self.config = config
        if isinstance(self.tts, LocalTTS):
            self.tts = LocalTTS(
                voice=config.voice,
                rate_wpm=style_rate_wpm(config.speech_rate_wpm, config.response_style),
                volume=config.volume,
                timeout_seconds=self.tts.timeout_seconds,
            )

    def stop(self) -> None:
        """Immediately halt playback and prevent any remaining queued chunks.

        Only signals ``_stop_event``; actual PortAudio stream ``abort``/``stop``/``close``
        runs on the playback thread that owns the stream (every 25ms block) to avoid
        cross-thread PortAudio deadlocks and crashes during ``write()``.
        """

        self._stop_event.set()
        with self._lock:
            self._cancelled = True
            self._playing = False
            self._current_level = 0.0

    def play_text(
        self,
        text: str,
        *,
        style: str | None = None,
        temp_dir: str | Path | None = None,
        turn_check: Callable[[], bool] | None = None,
        on_chunk_start: Callable[[int, str], None] | None = None,
        on_chunk_done: Callable[[int, str], None] | None = None,
    ) -> bool:
        """Prepare text into natural sentence chunks and play them sequentially."""

        effective_style = style or self.config.response_style
        spoken = prepare_text_for_speech(
            text,
            style=effective_style,
            max_chars=self.config.max_spoken_chars,
        )
        chunks = split_into_speech_chunks(spoken, max_chunk_chars=self.config.speech_chunk_chars)
        if not chunks:
            return True
        return self.play_text_chunks(
            chunks,
            style=effective_style,
            temp_dir=temp_dir,
            turn_check=turn_check,
            on_chunk_start=on_chunk_start,
            on_chunk_done=on_chunk_done,
        )

    def play_text_chunks(
        self,
        chunks: Sequence[str],
        *,
        style: str | None = None,
        temp_dir: str | Path | None = None,
        turn_check: Callable[[], bool] | None = None,
        on_chunk_start: Callable[[int, str], None] | None = None,
        on_chunk_done: Callable[[int, str], None] | None = None,
    ) -> bool:
        """Play pre-split text chunks, returning False if interrupted or stale."""

        cleaned_chunks = [str(c).strip() for c in chunks if isinstance(c, str) and c.strip()]
        if not cleaned_chunks:
            return True

        effective_style = style or self.config.response_style
        effective_rate = style_rate_wpm(self.config.speech_rate_wpm, effective_style)

        self._stop_event.clear()
        with self._lock:
            self._playing = True
            self._cancelled = False
            self._spoken_chunks = []
            self._current_level = 0.0

        own_temp_dir: Path | None = None
        if temp_dir is not None:
            work_dir = Path(temp_dir)
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            own_temp_dir = Path(tempfile.mkdtemp(prefix="riya_tts_"))
            try:
                os.chmod(own_temp_dir, 0o700)
            except OSError:
                pass
            work_dir = own_temp_dir

        try:
            for idx, chunk in enumerate(cleaned_chunks):
                if self._should_abort(turn_check):
                    return False

                if on_chunk_start is not None:
                    try:
                        on_chunk_start(idx, chunk)
                    except Exception:
                        pass

                finished = self._play_single_chunk(
                    chunk,
                    chunk_index=idx,
                    work_dir=work_dir,
                    rate_wpm=effective_rate,
                    turn_check=turn_check,
                )
                if not finished:
                    return False

                with self._lock:
                    self._spoken_chunks.append(chunk)

                if on_chunk_done is not None:
                    try:
                        on_chunk_done(idx, chunk)
                    except Exception:
                        pass

            return not self._should_abort(turn_check)
        finally:
            with self._lock:
                self._playing = False
                self._current_level = 0.0
                self._active_stream = None
            if own_temp_dir is not None:
                try:
                    for item in own_temp_dir.iterdir():
                        item.unlink(missing_ok=True)
                    own_temp_dir.rmdir()
                except OSError:
                    pass

    def _should_abort(self, turn_check: Callable[[], bool] | None) -> bool:
        if self._stop_event.is_set():
            return True
        if turn_check is not None:
            try:
                if not turn_check():
                    with self._lock:
                        self._cancelled = True
                    self._stop_event.set()
                    return True
            except Exception:
                return True
        return False

    def _play_single_chunk(
        self,
        chunk: str,
        *,
        chunk_index: int,
        work_dir: Path,
        rate_wpm: int,
        turn_check: Callable[[], bool] | None,
    ) -> bool:
        use_pcm = getattr(self.tts, "supports_wav_synthesis", False) is True
        if not use_pcm:
            try:
                speak_fn = self.tts.speak
                # Pass rate_wpm only if accepted by speak signature.
                code = getattr(getattr(speak_fn, "__code__", None), "co_varnames", ())
                if "rate_wpm" in code:
                    speak_fn(chunk, rate_wpm=rate_wpm)
                else:
                    speak_fn(chunk)
            except TTSError:
                raise
            except Exception as exc:
                raise TTSError(f"Speech synthesis failed: {exc}") from exc
            return not self._should_abort(turn_check)

        wav_path = work_dir / f"tts_chunk_{chunk_index}.wav"
        try:
            synthesized = self.tts.synthesize_to_wav(chunk, wav_path, rate_wpm=rate_wpm)
            if self._should_abort(turn_check):
                return False

            pcm, sample_rate = sf.read(str(synthesized), dtype="float32", always_2d=True)
            if pcm.size == 0:
                return True
            pcm = (pcm * float(self.config.volume)).astype(np.float32, copy=False)
            return self._stream_pcm(
                pcm,
                sample_rate=int(sample_rate),
                channels=int(pcm.shape[1]),
                turn_check=turn_check,
            )
        except (TTSError, PlaybackError):
            raise
        except Exception as exc:
            raise PlaybackError(f"Audio playback failed: {exc}") from exc
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _stream_pcm(
        self,
        pcm: np.ndarray,
        *,
        sample_rate: int,
        channels: int,
        turn_check: Callable[[], bool] | None,
    ) -> bool:
        block_frames = max(256, int(round(sample_rate * 0.025)))
        device_arg = _resolve_output_device(self.config.output_device)

        try:
            if self._output_stream_factory is not None:
                stream = self._output_stream_factory(
                    samplerate=sample_rate,
                    channels=channels,
                    dtype="float32",
                    device=device_arg,
                )
            else:
                stream = sd.OutputStream(
                    samplerate=sample_rate,
                    channels=channels,
                    dtype="float32",
                    device=device_arg,
                )
        except Exception as exc:
            raise PlaybackError(f"Unable to open audio output stream: {exc}") from exc

        with self._lock:
            self._active_stream = stream

        try:
            if hasattr(stream, "start"):
                stream.start()
            total = pcm.shape[0]
            offset = 0
            while offset < total:
                if self._should_abort(turn_check):
                    return False
                block = pcm[offset : offset + block_frames]
                offset += block_frames
                rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
                with self._lock:
                    self._current_level = min(1.0, rms * 8.0)
                if hasattr(stream, "write"):
                    stream.write(block)
            return not self._should_abort(turn_check)
        except Exception as exc:
            if self._stop_event.is_set():
                return False
            raise PlaybackError(f"Audio playback stream error: {exc}") from exc
        finally:
            with self._lock:
                self._current_level = 0.0
                if self._active_stream is stream:
                    self._active_stream = None
            methods = ("abort", "stop", "close") if self._stop_event.is_set() else ("stop", "close")
            for method in methods:
                fn = getattr(stream, method, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
