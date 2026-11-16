"""Low-level ctypes bridge to RIYA's embedded llama.cpp dynamic library."""

from __future__ import annotations

import ctypes
from pathlib import Path
import threading
import time
from typing import Any

from core.ai.errors import (
    BackendUnavailableError,
    InferenceCancelledError,
    InferenceExecutionError,
    InferenceTimeoutError,
    ModelLoadError,
    ModelNotFoundError,
    map_native_error,
)
from core.ai.safety import (
    DEFAULT_INFERENCE_TIMEOUT_SECONDS,
    MAX_CONTEXT_SIZE,
    MAX_GENERATION_TOKENS,
    MAX_MODEL_RESPONSE_CHARS,
    MIN_CONTEXT_SIZE,
)


_BRIDGE_LIB_NAME = "libriya_llama.dylib"
_VALID_BACKENDS = frozenset({"auto", "metal", "cpu"})


class NativeLlamaRuntime:
    """Thread-safe wrapper around libriya_llama.dylib for offline GGUF inference."""

    def __init__(
        self,
        model_path: str,
        context_size: int = 4096,
        backend: str = "auto",
    ):
        if backend not in _VALID_BACKENDS:
            raise ValueError(
                f"Unsupported backend '{backend}'. Choose 'auto', 'metal', or 'cpu'."
            )
        if (
            isinstance(context_size, bool)
            or not isinstance(context_size, int)
            or not (MIN_CONTEXT_SIZE <= context_size <= MAX_CONTEXT_SIZE)
        ):
            raise ValueError(
                f"context_size must be between {MIN_CONTEXT_SIZE} and {MAX_CONTEXT_SIZE}."
            )

        resolved_model = Path(model_path).expanduser().resolve()
        if not resolved_model.is_file():
            raise ModelNotFoundError(f"GGUF model file not found: {resolved_model}")

        lib_path = Path(__file__).resolve().parent / "bridge" / _BRIDGE_LIB_NAME
        if not lib_path.is_file():
            raise BackendUnavailableError(
                f"Native RIYA llama.cpp bridge library not found: {lib_path}"
            )

        try:
            self.lib = ctypes.CDLL(str(lib_path))
        except OSError as exc:
            raise BackendUnavailableError(
                f"Failed to load native llama.cpp bridge '{lib_path}': {exc}"
            ) from exc

        self._configure_ctypes_signatures()

        self.model_path = str(resolved_model)
        self.context_size = int(context_size)
        self.requested_backend = backend
        self.backend = backend
        self._lock = threading.Lock()
        self._in_flight = False
        self._close_requested = False
        self._cancel_event = threading.Event()
        self.handle: Any = None

        path_bytes = self.model_path.encode("utf-8")
        backend_bytes = backend.encode("utf-8")

        if hasattr(self.lib, "riya_load_model_with_backend"):
            handle = self.lib.riya_load_model_with_backend(
                path_bytes,
                ctypes.c_int(self.context_size),
                backend_bytes,
            )
        else:
            handle = self.lib.riya_load_model(
                path_bytes,
                ctypes.c_int(self.context_size),
            )

        if not handle:
            raise ModelLoadError(
                f"Failed to load GGUF model '{resolved_model.name}' using backend '{backend}' "
                f"(context_size={self.context_size})."
            )

        self.handle = ctypes.c_void_p(handle) if not isinstance(handle, ctypes.c_void_p) else handle

        if hasattr(self.lib, "riya_get_backend"):
            try:
                raw_backend = self.lib.riya_get_backend(self.handle)
                if isinstance(raw_backend, bytes):
                    decoded = raw_backend.decode("utf-8", errors="ignore").strip().lower()
                elif isinstance(raw_backend, str):
                    decoded = raw_backend.strip().lower()
                else:
                    decoded = ""
                if decoded in {"metal", "cpu"}:
                    self.backend = decoded
            except Exception:
                pass

    def _configure_ctypes_signatures(self) -> None:
        if hasattr(self.lib, "riya_load_model"):
            self.lib.riya_load_model.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self.lib.riya_load_model.restype = ctypes.c_void_p

        if hasattr(self.lib, "riya_load_model_with_backend"):
            self.lib.riya_load_model_with_backend.argtypes = [
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
            ]
            self.lib.riya_load_model_with_backend.restype = ctypes.c_void_p

        if hasattr(self.lib, "riya_get_backend"):
            self.lib.riya_get_backend.argtypes = [ctypes.c_void_p]
            self.lib.riya_get_backend.restype = ctypes.c_char_p

        if hasattr(self.lib, "riya_free_model"):
            self.lib.riya_free_model.argtypes = [ctypes.c_void_p]
            self.lib.riya_free_model.restype = None

        if hasattr(self.lib, "riya_generate"):
            self.lib.riya_generate.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_int,
            ]
            self.lib.riya_generate.restype = ctypes.c_int

    def _ensure_state_attrs(self) -> None:
        if not hasattr(self, "_lock"):
            self._lock = threading.Lock()
        if not hasattr(self, "_cancel_event"):
            self._cancel_event = threading.Event()
        if not hasattr(self, "_in_flight"):
            self._in_flight = False
        if not hasattr(self, "_close_requested"):
            self._close_requested = False

    @property
    def is_loaded(self) -> bool:
        self._ensure_state_attrs()
        with self._lock:
            return self.handle is not None and not self._close_requested

    def cancel(self) -> None:
        self._ensure_state_attrs()
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        self._ensure_state_attrs()
        self._cancel_event.clear()

    def generate(
        self,
        prompt: str,
        max_tokens: int = 128,
        *,
        timeout_seconds: float | None = DEFAULT_INFERENCE_TIMEOUT_SECONDS,
    ) -> str:
        self._ensure_state_attrs()

        if not isinstance(prompt, str) or not prompt:
            raise ValueError("Prompt must be a non-empty string.")
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not (1 <= max_tokens <= min(MAX_GENERATION_TOKENS, self.context_size))
        ):
            raise ValueError(
                f"max_tokens must be between 1 and {min(MAX_GENERATION_TOKENS, self.context_size)}."
            )
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive when provided.")

        with self._lock:
            if self._cancel_event.is_set():
                raise InferenceCancelledError("Native AI inference was cancelled.")
            if self.handle is None or self._close_requested:
                raise InferenceExecutionError("Native model handle is closed or unloaded.")
            if self._in_flight:
                raise InferenceExecutionError(
                    "Concurrent native generation call rejected while previous C call is still active."
                )
            self._in_flight = True
            active_handle = self.handle

        buf_size = max(16384, min(MAX_MODEL_RESPONSE_CHARS, max_tokens * 64))
        output_buf = ctypes.create_string_buffer(buf_size)
        prompt_bytes = prompt.encode("utf-8", errors="replace")

        outcome: dict[str, Any] = {}
        done = threading.Event()

        def _worker() -> None:
            try:
                rc = self.lib.riya_generate(
                    active_handle,
                    prompt_bytes,
                    output_buf,
                    buf_size,
                    max_tokens,
                )
                outcome["rc"] = int(rc)
            except BaseException as exc:
                outcome["error"] = exc
            finally:
                handle_to_free = None
                with self._lock:
                    self._in_flight = False
                    if self._close_requested and self.handle is not None:
                        handle_to_free = self.handle
                        self.handle = None
                if handle_to_free is not None and hasattr(self.lib, "riya_free_model"):
                    try:
                        self.lib.riya_free_model(handle_to_free)
                    except Exception:
                        pass
                done.set()

        thread = threading.Thread(
            target=_worker,
            name="riya-native-llama",
            daemon=True,
        )
        thread.start()

        deadline = None if timeout_seconds is None else time.monotonic() + float(timeout_seconds)
        while not done.is_set():
            if self._cancel_event.is_set():
                raise InferenceCancelledError("Native AI inference was cancelled.")
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InferenceTimeoutError(
                        f"Native AI inference timed out after {float(timeout_seconds):.2f}s."
                    )
                done.wait(timeout=min(0.02, remaining))
            else:
                done.wait(timeout=0.05)

        if "error" in outcome:
            err = outcome["error"]
            if isinstance(err, RuntimeError):
                raise err
            raise InferenceExecutionError(f"Native generation raised unexpected error: {err}")

        rc = outcome.get("rc", -1)
        if rc < 0:
            raise map_native_error(rc, context_size=self.context_size)
        if rc == 0:
            return ""

        raw_bytes = ctypes.string_at(output_buf, min(rc, buf_size))
        return raw_bytes.decode("utf-8", errors="replace").strip()

    def close(self) -> None:
        self._ensure_state_attrs()
        handle_to_free = None
        with self._lock:
            self._close_requested = True
            self._cancel_event.set()
            if self._in_flight:
                return
            if self.handle is not None:
                handle_to_free = self.handle
                self.handle = None

        if handle_to_free is not None and hasattr(self.lib, "riya_free_model"):
            try:
                self.lib.riya_free_model(handle_to_free)
            except Exception:
                pass

    def __enter__(self) -> "NativeLlamaRuntime":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
