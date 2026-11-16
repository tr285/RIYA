"""Real-time microphone audio capture, device enumeration, and resampling."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from core.voice.config import PIPELINE_SAMPLE_RATE, VAD_FRAME_SAMPLES
from core.voice.errors import (
    AudioCaptureError,
    MicrophonePermissionError,
    MicrophoneUnavailableError,
)

logger = logging.getLogger("riya.voice.audio_input")

_PERMISSION_ERROR_TOKENS = (
    "permission",
    "not permitted",
    "access denied",
    "denied",
    "not authorized",
    "operation not permitted",
)


@dataclass(frozen=True)
class AudioDeviceInfo:
    """Normalized metadata for a local audio input or output device."""

    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    is_default: bool = False


def resample_mono(
    audio: np.ndarray,
    src_rate: int,
    dst_rate: int = PIPELINE_SAMPLE_RATE,
) -> np.ndarray:
    """Convert audio samples to mono float32 at ``dst_rate`` Hz."""

    arr = np.asarray(audio, dtype=np.float32)
    if arr.size == 0:
        return np.zeros(0, dtype=np.float32)
    if arr.ndim > 1:
        # Handle either (frames, channels) or (channels, frames)
        if arr.shape[0] <= 8 and arr.shape[1] > arr.shape[0]:
            arr = np.mean(arr, axis=0, dtype=np.float32)
        else:
            arr = np.mean(arr, axis=1, dtype=np.float32)
    else:
        arr = arr.reshape(-1)

    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("Sample rates must be positive integers.")
    if src_rate == dst_rate:
        return arr.astype(np.float32, copy=False)

    out_len = max(1, int(round(arr.size * (dst_rate / float(src_rate)))))
    src_x = np.linspace(0.0, 1.0, num=arr.size, endpoint=False)
    dst_x = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
    return np.interp(dst_x, src_x, arr).astype(np.float32)


def classify_audio_exception(exc: BaseException) -> Exception:
    """Map low-level PortAudio / OS exceptions into explicit VoiceError subclasses."""

    text = str(exc).lower()
    if any(token in text for token in _PERMISSION_ERROR_TOKENS):
        return MicrophonePermissionError(str(exc))
    if any(
        token in text
        for token in (
            "invalid device",
            "no default input",
            "no input device",
            "device unavailable",
            "unavailable",
            "-9996",
            "-9985",
        )
    ):
        return MicrophoneUnavailableError(str(exc))
    return AudioCaptureError(str(exc))


def _query_sounddevice_devices() -> tuple[list[dict[str, Any]], int | None, int | None]:
    import sounddevice as sd

    raw_devices = sd.query_devices()
    devices = [dict(d) for d in raw_devices]
    default_dev = sd.default.device
    default_in = None
    default_out = None
    if isinstance(default_dev, (list, tuple)) and len(default_dev) >= 2:
        default_in = default_dev[0] if isinstance(default_dev[0], int) and default_dev[0] >= 0 else None
        default_out = default_dev[1] if isinstance(default_dev[1], int) and default_dev[1] >= 0 else None
    elif isinstance(default_dev, int) and default_dev >= 0:
        default_in = default_dev
        default_out = default_dev
    return devices, default_in, default_out


def list_input_devices() -> list[AudioDeviceInfo]:
    """List local audio input devices without opening any stream."""

    try:
        devices, default_in, _ = _query_sounddevice_devices()
    except Exception:
        return []
    result: list[AudioDeviceInfo] = []
    for idx, dev in enumerate(devices):
        in_ch = int(dev.get("max_input_channels", 0) or 0)
        if in_ch <= 0:
            continue
        result.append(
            AudioDeviceInfo(
                index=idx,
                name=str(dev.get("name", f"Input {idx}")),
                max_input_channels=in_ch,
                max_output_channels=int(dev.get("max_output_channels", 0) or 0),
                default_samplerate=float(dev.get("default_samplerate", PIPELINE_SAMPLE_RATE) or PIPELINE_SAMPLE_RATE),
                is_default=(idx == default_in),
            )
        )
    return result


def list_output_devices() -> list[AudioDeviceInfo]:
    """List local audio output devices without opening any stream."""

    try:
        devices, _, default_out = _query_sounddevice_devices()
    except Exception:
        return []
    result: list[AudioDeviceInfo] = []
    for idx, dev in enumerate(devices):
        out_ch = int(dev.get("max_output_channels", 0) or 0)
        if out_ch <= 0:
            continue
        result.append(
            AudioDeviceInfo(
                index=idx,
                name=str(dev.get("name", f"Output {idx}")),
                max_input_channels=int(dev.get("max_input_channels", 0) or 0),
                max_output_channels=out_ch,
                default_samplerate=float(dev.get("default_samplerate", 44100.0) or 44100.0),
                is_default=(idx == default_out),
            )
        )
    return result


def resolve_input_device(device: int | str | None) -> tuple[int | None, float]:
    """Validate requested input device and return ``(device_index, native_samplerate)``."""

    try:
        devices, default_in, _ = _query_sounddevice_devices()
    except Exception as exc:
        raise classify_audio_exception(exc) from exc

    if not devices:
        raise MicrophoneUnavailableError("No audio devices were detected on this system.")

    selected_idx: int | None = None
    if device is None:
        if default_in is not None and 0 <= default_in < len(devices):
            if int(devices[default_in].get("max_input_channels", 0) or 0) > 0:
                selected_idx = default_in
        if selected_idx is None:
            for idx, dev in enumerate(devices):
                if int(dev.get("max_input_channels", 0) or 0) > 0:
                    selected_idx = idx
                    break
        if selected_idx is None:
            raise MicrophoneUnavailableError("No microphone input device is available.")
    elif isinstance(device, int) and not isinstance(device, bool):
        if not (0 <= device < len(devices)):
            raise MicrophoneUnavailableError(f"Input device index {device} does not exist.")
        if int(devices[device].get("max_input_channels", 0) or 0) <= 0:
            raise MicrophoneUnavailableError(f"Device index {device} has no input channels.")
        selected_idx = device
    elif isinstance(device, str):
        needle = device.strip().lower()
        for idx, dev in enumerate(devices):
            if int(dev.get("max_input_channels", 0) or 0) <= 0:
                continue
            if str(dev.get("name", "")).lower() == needle:
                selected_idx = idx
                break
        if selected_idx is None:
            for idx, dev in enumerate(devices):
                if int(dev.get("max_input_channels", 0) or 0) <= 0:
                    continue
                if needle in str(dev.get("name", "")).lower():
                    selected_idx = idx
                    break
        if selected_idx is None:
            raise MicrophoneUnavailableError(f"Requested microphone '{device}' was not found.")
    else:
        raise ValueError("device must be an integer index, device name, or None.")

    native_rate = float(devices[selected_idx].get("default_samplerate", PIPELINE_SAMPLE_RATE) or PIPELINE_SAMPLE_RATE)
    return selected_idx, native_rate


class MicrophoneStream:
    """Explicitly activated real-time microphone frame stream (16 kHz mono float32).

    Never captures audio until ``start()`` is called; ``stop()`` closes the
    input stream and clears buffered samples.
    """

    def __init__(
        self,
        *,
        sample_rate: int = PIPELINE_SAMPLE_RATE,
        frame_samples: int = VAD_FRAME_SAMPLES,
        device: int | str | None = None,
        max_queue_frames: int = 500,
        check_permission_silence: bool = False,
        permission_probe_seconds: float = 1.5,
        stream_factory: Callable[..., Any] | None = None,
    ):
        if sample_rate != PIPELINE_SAMPLE_RATE:
            raise ValueError(f"sample_rate must be {PIPELINE_SAMPLE_RATE} Hz.")
        if frame_samples <= 0:
            raise ValueError("frame_samples must be positive.")

        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.device = device
        self.check_permission_silence = check_permission_silence
        self.permission_probe_seconds = float(permission_probe_seconds)
        self._stream_factory = stream_factory

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(10, int(max_queue_frames)))
        self._lock = threading.Lock()
        self._stream: Any = None
        self._active = False
        self._capture_rate = sample_rate
        self._pending = np.zeros(0, dtype=np.float32)
        self._current_level = 0.0
        self._stream_error: Exception | None = None
        self._probe_frames = 0
        self._probe_nonzero_seen = False

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def current_level(self) -> float:
        with self._lock:
            return self._current_level

    def start(self) -> None:
        with self._lock:
            if self._active:
                return
            self._stream_error = None
            self._pending = np.zeros(0, dtype=np.float32)
            self._current_level = 0.0
            self._probe_frames = 0
            self._probe_nonzero_seen = False

        if self._stream_factory is not None:
            capture_rate = self.sample_rate
            device_idx: int | str | None = self.device
            try:
                stream = self._stream_factory(
                    samplerate=capture_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=self.frame_samples,
                    device=device_idx,
                    callback=self._audio_callback,
                )
                if hasattr(stream, "start"):
                    stream.start()
            except Exception as exc:
                raise classify_audio_exception(exc) from exc
            with self._lock:
                self._capture_rate = capture_rate
                self._stream = stream
                self._active = True
            return

        import sounddevice as sd

        device_idx, native_rate = resolve_input_device(self.device)
        # Prefer 16 kHz directly when supported; fall back to device default rate.
        candidates = [float(self.sample_rate)]
        if abs(native_rate - self.sample_rate) >= 1.0:
            candidates.append(native_rate)

        last_exc: Exception | None = None
        for rate in candidates:
            capture_rate = int(round(rate))
            blocksize = max(128, int(round(self.frame_samples * (capture_rate / float(self.sample_rate)))))
            try:
                stream = sd.InputStream(
                    samplerate=capture_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=blocksize,
                    device=device_idx,
                    callback=self._audio_callback,
                )
                stream.start()
                with self._lock:
                    self._capture_rate = capture_rate
                    self._stream = stream
                    self._active = True
                return
            except Exception as exc:
                last_exc = exc
                mapped = classify_audio_exception(exc)
                if isinstance(mapped, MicrophonePermissionError):
                    raise mapped from exc

        assert last_exc is not None
        raise classify_audio_exception(last_exc) from last_exc

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._active = False
            self._pending = np.zeros(0, dtype=np.float32)
            self._current_level = 0.0

        if stream is not None:
            try:
                if hasattr(stream, "stop"):
                    stream.stop()
            except Exception:
                pass
            try:
                if hasattr(stream, "close"):
                    stream.close()
            except Exception:
                pass

        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def push_test_audio(self, samples: np.ndarray, *, sample_rate: int | None = None) -> None:
        """Inject mono audio frames deterministically (for automated tests)."""

        if not self.is_active:
            raise AudioCaptureError("Cannot push audio before MicrophoneStream.start().")
        src_rate = sample_rate or self._capture_rate
        self._ingest_samples(np.asarray(samples, dtype=np.float32), src_rate=src_rate)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        del frames, time_info
        if status is not None and getattr(status, "input_overflow", False):
            logger.debug("audio input overflow reported")
        if status is not None and getattr(status, "input_underflow", False):
            logger.debug("audio input underflow reported")
        try:
            self._ingest_samples(np.asarray(indata, dtype=np.float32), src_rate=self._capture_rate)
        except Exception as exc:
            with self._lock:
                self._stream_error = classify_audio_exception(exc)

    def _ingest_samples(self, arr: np.ndarray, *, src_rate: int) -> None:
        mono = resample_mono(arr, src_rate=src_rate, dst_rate=self.sample_rate)
        if mono.size == 0:
            return

        rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
        norm_level = min(1.0, rms * 10.0)
        is_exact_zero = bool(np.max(np.abs(mono)) == 0.0)

        frames_to_push: list[np.ndarray] = []
        with self._lock:
            if not self._active:
                return
            self._current_level = 0.65 * self._current_level + 0.35 * norm_level
            if not is_exact_zero:
                self._probe_nonzero_seen = True

            combined = mono if self._pending.size == 0 else np.concatenate([self._pending, mono])
            while combined.size >= self.frame_samples:
                frames_to_push.append(combined[: self.frame_samples].copy())
                combined = combined[self.frame_samples :]
                self._probe_frames += 1
            self._pending = combined

            if (
                self.check_permission_silence
                and not self._probe_nonzero_seen
                and self._probe_frames
                >= max(10, int(round(self.permission_probe_seconds * self.sample_rate / self.frame_samples)))
            ):
                self._stream_error = MicrophonePermissionError(
                    "Microphone returned only exact zero samples across permission probe window."
                )

        for frame in frames_to_push:
            try:
                self._queue.put_nowait(frame)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(frame)
                except queue.Full:
                    pass

    def read_frame(self, timeout: float = 0.05) -> np.ndarray | None:
        """Read one 512-sample 16 kHz mono float32 frame, or ``None`` on timeout."""

        with self._lock:
            err = self._stream_error
            active = self._active
        if err is not None:
            raise err
        if not active:
            return None

        try:
            frame = self._queue.get(timeout=max(0.001, float(timeout)))
        except queue.Empty:
            with self._lock:
                err = self._stream_error
            if err is not None:
                raise err
            return None

        with self._lock:
            err = self._stream_error
        if err is not None:
            raise err
        return frame

    def probe_permission_and_read_level(self, duration: float = 0.25) -> float:
        """Briefly verify the microphone opens and returns audio without keeping it open."""

        self.start()
        try:
            deadline = time.monotonic() + max(0.05, min(float(duration), 5.0))
            max_rms = 0.0
            while time.monotonic() < deadline:
                frame = self.read_frame(timeout=0.05)
                if frame is not None:
                    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
                    max_rms = max(max_rms, rms)
            return max_rms
        finally:
            self.stop()
