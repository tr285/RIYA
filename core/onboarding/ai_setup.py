"""Local AI engine discovery, GGUF validation, and real-inference readiness verification."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
from typing import Any, Sequence

from core.ai.local_runtime import LocalLlamaRuntime
from core.config.paths import get_model_search_paths, get_models_dir
from core.config.settings import PROJECT_ROOT


DEFAULT_MODEL_FILENAME = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
_GGUF_MAGIC = b"GGUF"
_MIN_GGUF_BYTES = 64
_DEFAULT_BRIDGE_REL = Path("core") / "ai" / "native" / "bridge" / "libriya_llama.dylib"


@dataclass(frozen=True)
class ModelCandidate:
    name: str
    path: str
    size_bytes: int
    source: str
    valid_gguf: bool
    error: str | None
    recommended_min_ram_gb: float

    @property
    def size_gb(self) -> float:
        return self.size_bytes / float(1024**3)


@dataclass(frozen=True)
class AIEngineStatus:
    native_library_path: str
    native_library_available: bool
    native_library_loadable: bool
    native_library_error: str | None
    architecture: str
    os_info: str
    supported_backends: tuple[str, ...]
    hardware_acceleration: str
    total_ram_bytes: int
    free_disk_bytes: int
    discovered_models: tuple[ModelCandidate, ...]
    selected_model_path: str | None
    model_loaded: bool
    active_backend: str
    inference_verified: bool
    error_message: str | None
    guidance: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelVerificationResult:
    success: bool
    model_path: str | None
    backend_used: str
    output_preview: str
    error_message: str | None


def get_system_ram_bytes() -> int:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return int(pages) * int(page_size)
    except (OSError, ValueError, AttributeError):
        pass
    return 0


def get_free_disk_bytes(target_path: str | Path = PROJECT_ROOT) -> int:
    path = Path(target_path).expanduser().resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return 0


def validate_gguf_file(model_path: str | Path) -> tuple[bool, str | None, int]:
    """Validate that a local path points to an accessible, non-empty GGUF model file."""
    if not isinstance(model_path, (str, Path)) or "\x00" in str(model_path):
        return False, "Model path contains invalid characters.", 0
    try:
        resolved = Path(model_path).expanduser().resolve()
    except OSError as exc:
        return False, f"Cannot resolve model path: {exc}", 0

    if not resolved.exists():
        return False, f"Model file not found: {resolved}", 0
    if not resolved.is_file():
        return False, f"Model path is not a regular file: {resolved}", 0

    try:
        size_bytes = int(resolved.stat().st_size)
    except OSError as exc:
        return False, f"Cannot inspect model file size: {exc}", 0

    if size_bytes < _MIN_GGUF_BYTES:
        return False, "Model file is empty or truncated (< 64 bytes).", size_bytes

    try:
        with resolved.open("rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        return False, f"Model file is not readable: {exc}", size_bytes

    if magic != _GGUF_MAGIC:
        return (
            False,
            f"File is not a valid GGUF model (expected magic header 'GGUF', got {magic!r}).",
            size_bytes,
        )

    return True, None, size_bytes


def inspect_native_library(
    bridge_path: str | Path | None = None,
) -> tuple[str, bool, bool, str | None]:
    """Check whether RIYA's compiled llama.cpp bridge exists and exports required symbols."""
    target = (
        Path(bridge_path).expanduser().resolve()
        if bridge_path is not None
        else (PROJECT_ROOT / _DEFAULT_BRIDGE_REL).resolve()
    )
    if not target.is_file():
        return (
            str(target),
            False,
            False,
            f"Native inference bridge library not found at {target}.",
        )
    try:
        lib = ctypes.CDLL(str(target))
        for sym in (
            "riya_load_model_with_backend",
            "riya_get_backend",
            "riya_generate",
            "riya_free_model",
        ):
            if not hasattr(lib, sym):
                return (
                    str(target),
                    True,
                    False,
                    f"Native bridge is missing expected symbol '{sym}'.",
                )
        return str(target), True, True, None
    except OSError as exc:
        return (
            str(target),
            True,
            False,
            f"Failed to load native inference library: {exc}",
        )


def _scan_dir_for_gguf(root: Path, *, max_depth: int = 4) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found

    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        depth = len(current.parts) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        for fname in sorted(filenames):
            if fname.lower().endswith(".gguf"):
                candidate = current / fname
                try:
                    if candidate.is_file():
                        found.append(candidate.resolve())
                except OSError:
                    continue
    return found


def discover_local_models(
    *,
    extra_dirs: Sequence[str | Path] = (),
    selected_path: str | Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> list[ModelCandidate]:
    """Discover local GGUF model files without downloading or calling cloud APIs."""
    roots: list[tuple[str, Path]] = [
        ("project_models", project_root / "models"),
        ("application_models", get_models_dir()),
        (
            "huggingface_cache",
            Path.home() / ".cache" / "huggingface" / "hub",
        ),
    ]
    for p in get_model_search_paths():
        if p != (project_root / "models") and p != get_models_dir():
            roots.append(("search_path", p))
    for extra in extra_dirs:
        roots.append(("custom", Path(extra).expanduser()))

    seen: set[str] = set()
    candidates: list[ModelCandidate] = []

    def _add(path: Path, source: str) -> None:
        try:
            key = str(path.expanduser().resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        seen.add(key)
        valid, err, size_bytes = validate_gguf_file(path)
        est_ram_gb = round(max(1.0, (size_bytes * 1.35) / float(1024**3)), 1)
        candidates.append(
            ModelCandidate(
                name=Path(key).name,
                path=key,
                size_bytes=size_bytes,
                source=source,
                valid_gguf=valid,
                error=err,
                recommended_min_ram_gb=est_ram_gb,
            )
        )

    if selected_path is not None:
        sel = Path(selected_path).expanduser()
        if sel.exists():
            _add(sel, "user_selected")

    for source_label, root in roots:
        for model_file in _scan_dir_for_gguf(root):
            _add(model_file, source_label)

    return candidates


class LocalAISetupService:
    """Service coordinating offline AI backend inspection, model selection, and verification."""

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        bridge_path: str | Path | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.bridge_path = bridge_path

    def inspect_environment(
        self,
        *,
        selected_model_path: str | Path | None = None,
        assistant: Any | None = None,
        inference_verified: bool = False,
        extra_model_dirs: Sequence[str | Path] = (),
    ) -> AIEngineStatus:
        lib_path, lib_exists, lib_loadable, lib_err = inspect_native_library(
            self.bridge_path
        )
        arch = platform.machine() or "unknown"
        os_info = f"{platform.system()} {platform.release()}"
        is_apple_metal = platform.system() == "Darwin" and arch.lower() == "arm64"
        supported_backends: tuple[str, ...] = (
            ("auto", "metal", "cpu") if is_apple_metal else ("auto", "cpu")
        )
        hw_accel = (
            "Apple Silicon Metal GPU + CPU (llama.cpp)"
            if is_apple_metal
            else "CPU (llama.cpp)"
        )

        total_ram = get_system_ram_bytes()
        free_disk = get_free_disk_bytes(self.project_root / "models")
        models = tuple(
            discover_local_models(
                extra_dirs=extra_model_dirs,
                selected_path=selected_model_path,
                project_root=self.project_root,
            )
        )

        resolved_selected: str | None = None
        if selected_model_path is not None:
            resolved_selected = str(Path(selected_model_path).expanduser().resolve())
        elif assistant is not None and getattr(assistant, "model_path", None):
            resolved_selected = str(getattr(assistant, "model_path"))
        elif models:
            valid_first = next((m for m in models if m.valid_gguf), models[0])
            resolved_selected = valid_first.path

        model_loaded = False
        active_backend = "unloaded"
        ai_error = lib_err
        verified_flag = bool(inference_verified)

        if assistant is not None:
            ai_runtime = getattr(assistant, "ai", None)
            if ai_runtime is not None and getattr(ai_runtime, "is_loaded", False) is True:
                model_loaded = True
                active_backend = str(getattr(ai_runtime, "selected_backend", "unknown"))
            elif getattr(assistant, "ai_unavailable_reason", None):
                ai_error = ai_error or str(assistant.ai_unavailable_reason)
                verified_flag = False
            else:
                verified_flag = False

        guidance: list[str] = [
            "RIYA uses its embedded native llama.cpp backend directly — Ollama and internet access are not required.",
            "Local quantized 4B GGUF models typically require ~2.5-3.5 GB disk space and ~4-6 GB available RAM.",
        ]
        if not lib_loadable:
            verified_flag = False
            guidance.append(
                "Repair or rebuild core/ai/native/bridge/libriya_llama.dylib before loading a local model."
            )
        if not models:
            verified_flag = False
            guidance.append(
                f"No compatible .gguf model was found. Place a compatible GGUF file in {self.project_root / 'models'} or browse for an existing .gguf file."
            )
            if not ai_error:
                ai_error = "No compatible local GGUF model installed."
        elif resolved_selected:
            valid, val_err, size_bytes = validate_gguf_file(resolved_selected)
            if not valid:
                ai_error = val_err
                verified_flag = False
                guidance.append(
                    "Select a valid .gguf file or continue in Limited Mode (deterministic tools & memory still work without LLM chat)."
                )
            elif total_ram and size_bytes * 1.25 > total_ram:
                guidance.append(
                    "Warning: selected model is large relative to system memory and may fail to load."
                )
        else:
            verified_flag = False

        return AIEngineStatus(
            native_library_path=lib_path,
            native_library_available=lib_exists,
            native_library_loadable=lib_loadable,
            native_library_error=lib_err,
            architecture=arch,
            os_info=os_info,
            supported_backends=supported_backends,
            hardware_acceleration=hw_accel,
            total_ram_bytes=total_ram,
            free_disk_bytes=free_disk,
            discovered_models=models,
            selected_model_path=resolved_selected,
            model_loaded=model_loaded,
            active_backend=active_backend,
            inference_verified=verified_flag and (model_loaded or assistant is None),
            error_message=ai_error,
            guidance=tuple(guidance),
        )

    def verify_model_with_inference(
        self,
        model_path: str | Path,
        *,
        backend: str = "auto",
        assistant: Any | None = None,
        timeout_seconds: float = 45.0,
    ) -> ModelVerificationResult:
        """Load the chosen GGUF model and run a minimal real inference probe."""
        valid, err, _size = validate_gguf_file(model_path)
        if not valid:
            return ModelVerificationResult(
                success=False,
                model_path=str(model_path),
                backend_used="unloaded",
                output_preview="",
                error_message=err or "Invalid model file.",
            )

        _, _, lib_loadable, lib_err = inspect_native_library(self.bridge_path)
        if not lib_loadable:
            return ModelVerificationResult(
                success=False,
                model_path=str(Path(model_path).expanduser().resolve()),
                backend_used="unloaded",
                output_preview="",
                error_message=lib_err or "Native llama.cpp library cannot be loaded.",
            )

        resolved = str(Path(model_path).expanduser().resolve())
        temp_runtime: LocalLlamaRuntime | None = None

        try:
            if assistant is not None and hasattr(assistant, "reload_ai_model"):
                ok, msg = assistant.reload_ai_model(resolved, backend=backend)
                if not ok:
                    return ModelVerificationResult(
                        success=False,
                        model_path=resolved,
                        backend_used="unloaded",
                        output_preview="",
                        error_message=msg,
                    )
                runtime = assistant.ai
                output = runtime.generate(
                    "Reply with the single word READY.",
                    timeout_seconds=timeout_seconds,
                )
                if not isinstance(output, str) or not output.strip():
                    return ModelVerificationResult(
                        success=False,
                        model_path=resolved,
                        backend_used=str(getattr(runtime, "selected_backend", "unknown")),
                        output_preview="",
                        error_message="Model loaded but produced an empty inference output.",
                    )
                return ModelVerificationResult(
                    success=True,
                    model_path=resolved,
                    backend_used=str(getattr(runtime, "selected_backend", backend)),
                    output_preview=output.strip()[:120],
                    error_message=None,
                )

            temp_runtime = LocalLlamaRuntime(
                context_size=512,
                max_tokens=16,
                backend=backend,
                timeout_seconds=timeout_seconds,
            )
            temp_runtime.load_model(resolved)
            output = temp_runtime.generate(
                "Reply with the single word READY.",
                timeout_seconds=timeout_seconds,
            )
            if not isinstance(output, str) or not output.strip():
                return ModelVerificationResult(
                    success=False,
                    model_path=resolved,
                    backend_used=temp_runtime.selected_backend,
                    output_preview="",
                    error_message="Model loaded but produced an empty inference output.",
                )
            return ModelVerificationResult(
                success=True,
                model_path=resolved,
                backend_used=temp_runtime.selected_backend,
                output_preview=output.strip()[:120],
                error_message=None,
            )
        except Exception as exc:
            return ModelVerificationResult(
                success=False,
                model_path=resolved,
                backend_used="unloaded",
                output_preview="",
                error_message=f"Model verification failed: {exc}",
            )
        finally:
            if temp_runtime is not None:
                try:
                    temp_runtime.unload_model()
                except Exception:
                    pass
