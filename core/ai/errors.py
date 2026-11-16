"""Structured error hierarchy for RIYA's local native AI engine."""

from __future__ import annotations

from core.ai.safety import InferenceCancelledError, InferenceTimeoutError


class NativeAIError(RuntimeError):
    """Base error raised by RIYA's local native llama.cpp inference engine."""

    def __init__(self, message: str, *, code: int | None = None, recoverable: bool = True):
        super().__init__(message)
        self.code = code
        self.recoverable = recoverable


class ModelNotFoundError(NativeAIError, FileNotFoundError):
    """Raised when the configured GGUF model file does not exist."""


class BackendUnavailableError(NativeAIError):
    """Raised when the requested native backend (Metal/CPU) or bridge library is unavailable."""


class ModelLoadError(NativeAIError):
    """Raised when a local GGUF model fails to load into llama.cpp."""


class ContextOverflowError(NativeAIError):
    """Raised when the formatted prompt exceeds the configured model context window."""


class InferenceExecutionError(NativeAIError):
    """Raised when native generation fails during tokenization, decoding, or sampling."""


NATIVE_ERROR_DESCRIPTIONS: dict[int, tuple[type[NativeAIError], str]] = {
    -1: (InferenceExecutionError, "Invalid inference arguments, null model handle, or empty prompt."),
    -2: (InferenceExecutionError, "Native llama.cpp context/KV cache state was unavailable."),
    -3: (InferenceExecutionError, "Native tokenization failed for the supplied prompt."),
    -4: (ContextOverflowError, "Prompt exceeded the configured model context window (n_ctx)."),
    -5: (InferenceExecutionError, "Native llama.cpp decode step failed during inference."),
    -6: (InferenceExecutionError, "Native llama.cpp sampler initialization failed."),
}


def map_native_error(code: int, *, context_size: int | None = None) -> NativeAIError:
    """Convert a native C bridge error code into a descriptive typed exception."""
    exc_cls, desc = NATIVE_ERROR_DESCRIPTIONS.get(
        int(code),
        (InferenceExecutionError, f"Unknown native llama.cpp failure (code {code})."),
    )
    suffix = f" [n_ctx={context_size}]" if context_size and code == -4 else ""
    return exc_cls(
        f"RIYA generation failed with code {code}: {desc}{suffix}",
        code=int(code),
        recoverable=(code in {-3, -4, -5}),
    )


__all__ = [
    "BackendUnavailableError",
    "ContextOverflowError",
    "InferenceCancelledError",
    "InferenceExecutionError",
    "InferenceTimeoutError",
    "ModelLoadError",
    "ModelNotFoundError",
    "NATIVE_ERROR_DESCRIPTIONS",
    "NativeAIError",
    "map_native_error",
]
