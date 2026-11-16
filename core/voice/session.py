"""Voice session lifecycle, single-session guard, transcript store, and temp audio cleanup."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from core.voice.audio_input import MicrophoneStream
from core.voice.config import VoiceConfig
from core.voice.errors import VoiceSessionBusyError
from core.voice.state import ACTIVE_STATES, VoiceState, VoiceStateMachine


@dataclass
class TranscriptEntry:
    """Single message entry displayed across text and voice modes."""

    entry_id: str
    role: str
    text: str
    modality: str
    timestamp: float = field(default_factory=time.time)
    status: str = "final"
    language: str = "en"
    confidence: float | None = None
    spoken_text: str = ""


class VoiceSession:
    """Manages one exclusive active voice session and its private resources."""

    _registry_lock = threading.Lock()
    _active_session_id: str | None = None

    def __init__(
        self,
        config: VoiceConfig | None = None,
        *,
        state_machine: VoiceStateMachine | None = None,
        mic_factory: Callable[[VoiceConfig], MicrophoneStream] | None = None,
        user_id: str | None = None,
        auth_session_id: str | None = None,
    ):
        self.session_id = uuid.uuid4().hex
        self.user_id = user_id
        self.auth_session_id = auth_session_id
        self.config = config or VoiceConfig()
        self.state_machine = state_machine or VoiceStateMachine()
        self._mic_factory = mic_factory or (
            lambda cfg: MicrophoneStream(
                sample_rate=cfg.sample_rate,
                device=cfg.input_device,
                permission_probe_seconds=cfg.permission_probe_seconds,
            )
        )

        self._lock = threading.RLock()
        self._mic: MicrophoneStream | None = None
        self._temp_dir: Path | None = None
        self._retained_files: list[Path] = []
        self._transcript: list[TranscriptEntry] = []
        self._is_recording: bool = False
        self._recorded_frames: list[np.ndarray] = []

    @property
    def state(self) -> VoiceState:
        return self.state_machine.state

    @property
    def is_active(self) -> bool:
        return self.state_machine.state in ACTIVE_STATES

    @property
    def microphone(self) -> MicrophoneStream | None:
        with self._lock:
            return self._mic

    @property
    def microphone_active(self) -> bool:
        with self._lock:
            return bool(self._mic is not None and self._mic.is_active)

    @property
    def microphone_level(self) -> float:
        with self._lock:
            if self._mic is None or not self._mic.is_active:
                return 0.0
            return self._mic.current_level

    @property
    def temp_dir(self) -> Path | None:
        with self._lock:
            return self._temp_dir

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._is_recording

    def start_recording(self) -> None:
        """Begin accumulating frames in memory for push-to-talk mode."""
        with self._lock:
            self._recorded_frames.clear()
            self._is_recording = True

    def append_recording_frame(self, frame: np.ndarray) -> None:
        """Append an audio frame while push-to-talk recording is active."""
        with self._lock:
            if self._is_recording:
                self._recorded_frames.append(np.asarray(frame, dtype=np.float32).reshape(-1))

    def stop_recording(self) -> np.ndarray:
        """Stop accumulating frames and return concatenated 1D float32 audio."""
        with self._lock:
            self._is_recording = False
            if not self._recorded_frames:
                return np.zeros(0, dtype=np.float32)
            data = np.concatenate(self._recorded_frames)
            self._recorded_frames.clear()
            return data

    # ------------------------------------------------------------------
    def _claim_global_slot(self) -> None:
        with self._registry_lock:
            if (
                VoiceSession._active_session_id is not None
                and VoiceSession._active_session_id != self.session_id
            ):
                raise VoiceSessionBusyError("Another voice session is already active.")
            VoiceSession._active_session_id = self.session_id

    def _release_global_slot(self) -> None:
        with self._registry_lock:
            if VoiceSession._active_session_id == self.session_id:
                VoiceSession._active_session_id = None

    # ------------------------------------------------------------------
    def start(self) -> MicrophoneStream:
        """Activate microphone and allocate private temporary storage."""

        self._claim_global_slot()
        mic: MicrophoneStream | None = None
        temp_path: Path | None = None
        try:
            with self._lock:
                if self.state_machine.state in (VoiceState.ERROR, VoiceState.STOPPING):
                    self.state_machine.try_transition(VoiceState.IDLE, "reset_before_start")
                if self.state_machine.state != VoiceState.IDLE:
                    raise VoiceSessionBusyError("This voice session is already running.")

                temp_path = Path(tempfile.mkdtemp(prefix="riya_voice_"))
                try:
                    os.chmod(temp_path, 0o700)
                except OSError:
                    pass

                mic = self._mic_factory(self.config)
                mic.start()

                self._temp_dir = temp_path
                self._mic = mic
                self.state_machine.transition(VoiceState.LISTENING, "voice_session_started")
                return mic
        except Exception:
            if mic is not None:
                try:
                    mic.stop()
                except Exception:
                    pass
            if temp_path is not None and temp_path.exists():
                shutil.rmtree(temp_path, ignore_errors=True)
            with self._lock:
                self._mic = None
                self._temp_dir = None
            if self.state_machine.state != VoiceState.IDLE:
                self.state_machine.try_transition(VoiceState.ERROR, "start_failed")
                self.state_machine.try_transition(VoiceState.IDLE, "cleanup_after_start_failure")
            self._release_global_slot()
            raise

    def stop(self, *, to_error: bool = False) -> None:
        """Stop microphone stream cleanly and wipe temporary audio files."""

        with self._lock:
            mic = self._mic
            self._mic = None
            temp_path = self._temp_dir
            self._temp_dir = None
            self._is_recording = False
            self._recorded_frames.clear()

        if mic is not None:
            try:
                mic.stop()
            except Exception:
                pass

        if temp_path is not None and temp_path.exists():
            shutil.rmtree(temp_path, ignore_errors=True)

        if to_error:
            if self.state_machine.state != VoiceState.IDLE:
                self.state_machine.try_transition(VoiceState.ERROR, "session_error")
        else:
            if self.state_machine.state != VoiceState.IDLE:
                if self.state_machine.state == VoiceState.ERROR:
                    self.state_machine.try_transition(VoiceState.IDLE, "session_stopped")
                else:
                    self.state_machine.try_transition(VoiceState.STOPPING, "session_stopping")
                    self.state_machine.try_transition(VoiceState.IDLE, "session_stopped")

        if not to_error or self.state_machine.state == VoiceState.IDLE:
            self._release_global_slot()

    def recover_from_error(self) -> None:
        """Reset ERROR -> IDLE cleanly and release exclusive slot."""

        self.stop(to_error=False)
        self._release_global_slot()

    def close(self) -> None:
        self.stop(to_error=False)

    # ------------------------------------------------------------------
    def create_temp_wav_path(self, prefix: str = "utterance") -> Path:
        """Allocate a secure temporary WAV path cleaned up on stop()."""

        with self._lock:
            base = self._temp_dir
            if base is None:
                base = Path(tempfile.mkdtemp(prefix="riya_voice_"))
                try:
                    os.chmod(base, 0o700)
                except OSError:
                    pass
                self._temp_dir = base
        return base / f"{prefix}_{uuid.uuid4().hex}.wav"

    def maybe_save_recording(self, audio: np.ndarray, *, prefix: str = "turn") -> Path | None:
        """Persist raw user audio ONLY when ``config.retain_recordings`` is enabled."""

        if not self.config.retain_recordings:
            return None
        target_dir = Path(self.config.recordings_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target_dir, 0o700)
        except OSError:
            pass
        file_path = target_dir / f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}.wav"
        sf.write(str(file_path), np.asarray(audio, dtype=np.float32), self.config.sample_rate)
        try:
            os.chmod(file_path, 0o600)
        except OSError:
            pass
        with self._lock:
            self._retained_files.append(file_path)
        return file_path

    # ------------------------------------------------------------------
    def append_transcript(
        self,
        *,
        role: str,
        text: str,
        modality: str,
        status: str = "final",
        language: str = "en",
        confidence: float | None = None,
    ) -> TranscriptEntry:
        entry = TranscriptEntry(
            entry_id=uuid.uuid4().hex[:12],
            role=role,
            text=text,
            modality=modality,
            status=status,
            language=language,
            confidence=confidence,
        )
        with self._lock:
            self._transcript.append(entry)
        return entry

    def update_transcript(
        self,
        entry_id: str,
        *,
        text: str | None = None,
        status: str | None = None,
        spoken_text: str | None = None,
    ) -> TranscriptEntry | None:
        with self._lock:
            for item in self._transcript:
                if item.entry_id == entry_id:
                    if text is not None:
                        item.text = text
                    if status is not None:
                        item.status = status
                    if spoken_text is not None:
                        item.spoken_text = spoken_text
                    return item
        return None

    def transcript_entries(self) -> list[TranscriptEntry]:
        with self._lock:
            return list(self._transcript)

    def clear_transcript(self) -> None:
        """Clear buffered in-memory transcript entries for the current session."""
        with self._lock:
            self._transcript.clear()

    def bind_user(
        self,
        user_id: str | None,
        auth_session_id: str | None = None,
        *,
        config: VoiceConfig | None = None,
    ) -> None:
        """Bind session metadata to a new user and clear cross-user transcript buffer."""
        with self._lock:
            if user_id != self.user_id or auth_session_id != self.auth_session_id:
                self._transcript.clear()
            self.user_id = user_id
            self.auth_session_id = auth_session_id
            if config is not None:
                self.config = config
