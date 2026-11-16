"""Linux first-launch setup and system readiness verification for RIYA.

Provides a truthful, offline-first first-launch flow that:
  - Detects the Linux distribution, desktop environment, and audio configuration.
  - Verifies Python and required library dependencies.
  - Checks writable XDG data directories with proper permissions.
  - Initializes or migrates the user database.
  - Detects existing local AI models without silent downloads.
  - Performs pre-flight disk space validation before any optional download.
  - Presents clear, actionable error and setup messages.

Never downloads or starts multi-GB operations without explicit user consent.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

# Ensure repo root is in path when run standalone
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass
class LinuxRuntimeDependency:
    name: str
    command: str | None          # Binary to locate via shutil.which
    python_module: str | None    # Python module to import-check
    required: bool
    purpose: str
    install_hint: str            # 'apt install <pkg>' style hint
    available: bool = False
    version: str | None = None


@dataclass
class LinuxFirstLaunchReport:
    """Full first-launch diagnostics result."""
    distribution: str
    architecture: str
    desktop_environment: str
    display_server: str
    python_version: str
    python_ok: bool

    data_dir: str
    data_dir_writable: bool
    models_dir: str
    models_dir_writable: bool
    logs_dir: str
    logs_dir_writable: bool
    cache_dir: str

    database_ok: bool
    database_path: str
    database_error: str | None

    model_found: bool
    model_path: str | None
    model_size_gb: float | None
    model_name: str | None

    free_disk_bytes: int
    free_disk_gb: float
    disk_sufficient_for_model: bool

    audio_subsystem: str           # 'pipewire' | 'pulseaudio' | 'alsa' | 'unknown'
    audio_available: bool
    tts_backend: str | None        # 'piper' | 'espeak-ng' | 'spd-say' | None
    tts_available: bool
    stt_model_cached: bool

    runtime_deps: list[LinuxRuntimeDependency]
    missing_required_deps: list[str]
    missing_optional_deps: list[str]

    can_start: bool
    warnings: list[str]
    errors: list[str]
    setup_guidance: list[str]


def detect_linux_distribution() -> dict[str, str]:
    """Parse /etc/os-release for distribution metadata."""
    info: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        info[k.strip()] = v.strip().strip('"')
            if info:
                return info
        except OSError:
            continue
    return {"NAME": "Linux", "ID": "linux", "PRETTY_NAME": platform.system()}


def detect_display_server() -> str:
    """Detect Wayland or X11 display server."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


def detect_desktop_environment() -> str:
    """Detect current Linux desktop environment."""
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if de:
        return de
    if os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return "gnome"
    if os.environ.get("KDE_SESSION_VERSION"):
        return "kde"
    return "unknown"


def detect_audio_subsystem() -> tuple[str, bool]:
    """Detect running audio subsystem and return (name, available)."""
    if shutil.which("pactl"):
        try:
            import subprocess
            result = subprocess.run(
                ["pactl", "info"],
                capture_output=True, timeout=3, text=True, check=False,
            )
            if "PipeWire" in result.stdout:
                return "pipewire", True
            if result.returncode == 0:
                return "pulseaudio", True
        except Exception:
            pass
    if shutil.which("aplay"):
        return "alsa", True
    return "unknown", False


def _check_python_ok() -> bool:
    """Verify Python version meets minimum 3.10."""
    return sys.version_info >= (3, 10)


def _check_stt_model_cached() -> bool:
    """Check whether a faster-whisper STT model is locally cached."""
    hf_hub = Path.home() / ".cache" / "huggingface" / "hub"
    for model_name in ("models--Systran--faster-whisper-base", "models--Systran--faster-whisper-small"):
        if (hf_hub / model_name).is_dir():
            return True
    return False


def _check_model_provisioned() -> tuple[bool, str | None, float | None, str | None]:
    """Find the first valid local GGUF model across standard XDG search paths."""
    xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    search_paths = [
        Path(os.environ.get("RIYA_MODELS_DIR", "")) if os.environ.get("RIYA_MODELS_DIR") else None,
        Path(xdg_data) / "riya" / "models",
        Path.home() / ".local" / "share" / "riya" / "models",
        _REPO_ROOT / "models",
    ]
    for p in search_paths:
        if p is None or not p.is_dir():
            continue
        for f in sorted(p.glob("*.gguf")):
            if f.stat().st_size > 100_000_000:  # > 100 MB: plausible GGUF model
                size_gb = f.stat().st_size / (1024 ** 3)
                return True, str(f), size_gb, f.name
    return False, None, None, None


def _check_database_ok(data_dir: Path) -> tuple[bool, str, str | None]:
    """Verify or initialize SQLite database."""
    db_path = data_dir / "riya.db"
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("SELECT 1;")
        conn.close()
        return True, str(db_path), None
    except Exception as exc:
        return False, str(db_path), str(exc)


def _get_xdg_dirs() -> tuple[Path, Path, Path, Path]:
    """Return (data_dir, models_dir, logs_dir, cache_dir) under XDG hierarchy."""
    xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    xdg_cache = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))

    # Allow environment overrides (set by installer/AppImage launcher)
    data_dir = Path(os.environ.get("RIYA_DATA_DIR", str(Path(xdg_data) / "riya" / "data")))
    models_dir = Path(os.environ.get("RIYA_MODELS_DIR", str(Path(xdg_data) / "riya" / "models")))
    logs_dir = Path(os.environ.get("RIYA_LOGS_DIR", str(Path(xdg_data) / "riya" / "logs")))
    cache_dir = Path(os.environ.get("RIYA_CACHE_DIR", str(Path(xdg_cache) / "riya")))
    return data_dir, models_dir, logs_dir, cache_dir


RUNTIME_DEPS: list[dict[str, Any]] = [
    # Core requirements
    {"name": "Python 3.10+", "command": "python3", "module": None, "required": True,
     "purpose": "Core language runtime", "hint": "sudo apt install python3"},
    {"name": "python3-tk", "command": None, "module": "tkinter", "required": True,
     "purpose": "Desktop GUI framework", "hint": "sudo apt install python3-tk"},
    {"name": "libportaudio2", "command": None, "module": "sounddevice", "required": True,
     "purpose": "Real-time microphone audio capture", "hint": "sudo apt install libportaudio2"},
    {"name": "libsndfile1", "command": None, "module": "soundfile", "required": True,
     "purpose": "Audio file reading and writing", "hint": "sudo apt install libsndfile1"},
    {"name": "numpy", "command": None, "module": "numpy", "required": True,
     "purpose": "Audio frame processing and ML buffers", "hint": "pip3 install numpy"},
    {"name": "psutil", "command": None, "module": "psutil", "required": True,
     "purpose": "System telemetry and process management", "hint": "pip3 install psutil"},
    {"name": "pillow", "command": None, "module": "PIL", "required": True,
     "purpose": "Image rendering and icon display", "hint": "pip3 install pillow"},
    # Optional voice and AI dependencies
    {"name": "faster-whisper", "command": None, "module": "faster_whisper", "required": False,
     "purpose": "Local offline speech recognition (STT)", "hint": "pip3 install faster-whisper"},
    {"name": "onnxruntime", "command": None, "module": "onnxruntime", "required": False,
     "purpose": "Voice activity detection (Silero VAD)", "hint": "pip3 install onnxruntime"},
    {"name": "sentence-transformers", "command": None, "module": "sentence_transformers", "required": False,
     "purpose": "Semantic memory vector embeddings", "hint": "pip3 install sentence-transformers"},
    # TTS binaries
    {"name": "espeak-ng", "command": "espeak-ng", "module": None, "required": False,
     "purpose": "Offline text-to-speech (TTS) speech synthesis", "hint": "sudo apt install espeak-ng"},
    {"name": "notify-send", "command": "notify-send", "module": None, "required": False,
     "purpose": "Desktop notification delivery", "hint": "sudo apt install libnotify-bin"},
]


def run_first_launch_check(*, verbose: bool = False) -> LinuxFirstLaunchReport:
    """Execute the complete Linux first-launch diagnostic sweep."""

    dist_info = detect_linux_distribution()
    distribution = dist_info.get("PRETTY_NAME", "Linux")
    architecture = platform.machine()
    de = detect_desktop_environment()
    display = detect_display_server()
    python_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_ok = _check_python_ok()

    # 1. Establish XDG directories
    data_dir, models_dir, logs_dir, cache_dir = _get_xdg_dirs()
    data_dir_writable = False
    models_dir_writable = False
    logs_dir_writable = False

    for d in (data_dir, models_dir, logs_dir, cache_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    data_dir_writable = os.access(data_dir, os.W_OK)
    models_dir_writable = os.access(models_dir, os.W_OK)
    logs_dir_writable = os.access(logs_dir, os.W_OK)

    # 2. Database check
    db_ok, db_path, db_error = _check_database_ok(data_dir)

    # 3. Model detection
    model_found, model_path, model_size_gb, model_name = _check_model_provisioned()

    # 4. Disk space
    try:
        usage = shutil.disk_usage(str(data_dir))
        free_disk_bytes = usage.free
    except OSError:
        free_disk_bytes = 0
    free_disk_gb = free_disk_bytes / (1024 ** 3)
    # 2.5 GB model + 1 GB headroom = 3.5 GB minimum
    disk_sufficient = free_disk_bytes > 3_758_096_384

    # 5. Audio subsystem
    audio_subsystem, audio_available = detect_audio_subsystem()

    # 6. TTS availability
    tts_backend = None
    for b in ("piper", "espeak-ng", "espeak", "spd-say"):
        if shutil.which(b):
            tts_backend = b
            break
    tts_available = tts_backend is not None

    # 7. STT model
    stt_cached = _check_stt_model_cached()

    # 8. Runtime dependencies
    checked_deps: list[LinuxRuntimeDependency] = []
    for dep in RUNTIME_DEPS:
        available = False
        version = None
        if dep["command"]:
            if shutil.which(dep["command"]):
                available = True
        if dep["module"] and not available:
            try:
                mod = __import__(dep["module"])
                available = True
                version = getattr(mod, "__version__", None)
            except ImportError:
                pass
        elif dep["module"] and available:
            try:
                mod = __import__(dep["module"])
                version = getattr(mod, "__version__", None)
            except ImportError:
                pass
        checked_deps.append(LinuxRuntimeDependency(
            name=dep["name"],
            command=dep["command"],
            python_module=dep["module"],
            required=dep["required"],
            purpose=dep["purpose"],
            install_hint=dep["hint"],
            available=available,
            version=version,
        ))

    missing_required = [d.name for d in checked_deps if d.required and not d.available]
    missing_optional = [d.name for d in checked_deps if not d.required and not d.available]

    # 9. Determine overall startup readiness
    errors: list[str] = []
    warnings: list[str] = []
    guidance: list[str] = []

    if not python_ok:
        errors.append(f"Python 3.10+ required; running {python_ver}. Install via: sudo apt install python3")
    if not data_dir_writable:
        errors.append(f"Data directory not writable: {data_dir}")
    if not db_ok and db_error:
        errors.append(f"Database initialization failed: {db_error}")
    if missing_required:
        for dep_name in missing_required:
            dep_obj = next((d for d in checked_deps if d.name == dep_name), None)
            if dep_obj:
                errors.append(f"Missing required dependency '{dep_obj.name}': {dep_obj.install_hint}")

    if not model_found:
        warnings.append("No local AI model found. RIYA can start but AI inference will be unavailable.")
        if disk_sufficient:
            guidance.append(
                "To enable AI inference, run: riya --setup\n"
                "RIYA will offer to download the Qwen3 4B model (~2.5 GB, SHA-256 verified)."
            )
        else:
            warnings.append(
                f"Insufficient disk space ({free_disk_gb:.1f} GB free). "
                "At least 3.5 GB free required to download the Qwen3 model."
            )

    if not audio_available:
        warnings.append("No audio subsystem detected. Voice features will be unavailable.")
    if not tts_available:
        warnings.append("No TTS synthesizer found. Install espeak-ng: sudo apt install espeak-ng")
    if not stt_cached:
        warnings.append("STT model not cached. Run: pip3 install faster-whisper (then first use will auto-download)")
    if missing_optional:
        guidance.append(
            "Optional features with missing dependencies:\n" +
            "\n".join(f"  • {dep_name}: {next((x.install_hint for x in checked_deps if x.name == dep_name), '')}"
                      for dep_name in missing_optional)
        )

    can_start = python_ok and data_dir_writable and not bool(missing_required)

    return LinuxFirstLaunchReport(
        distribution=distribution,
        architecture=architecture,
        desktop_environment=de,
        display_server=display,
        python_version=python_ver,
        python_ok=python_ok,
        data_dir=str(data_dir),
        data_dir_writable=data_dir_writable,
        models_dir=str(models_dir),
        models_dir_writable=models_dir_writable,
        logs_dir=str(logs_dir),
        logs_dir_writable=logs_dir_writable,
        cache_dir=str(cache_dir),
        database_ok=db_ok,
        database_path=db_path,
        database_error=db_error,
        model_found=model_found,
        model_path=model_path,
        model_size_gb=model_size_gb,
        model_name=model_name,
        free_disk_bytes=free_disk_bytes,
        free_disk_gb=free_disk_gb,
        disk_sufficient_for_model=disk_sufficient,
        audio_subsystem=audio_subsystem,
        audio_available=audio_available,
        tts_backend=tts_backend,
        tts_available=tts_available,
        stt_model_cached=stt_cached,
        runtime_deps=checked_deps,
        missing_required_deps=missing_required,
        missing_optional_deps=missing_optional,
        can_start=can_start,
        warnings=warnings,
        errors=errors,
        setup_guidance=guidance,
    )


def print_first_launch_report(report: LinuxFirstLaunchReport) -> None:
    """Display formatted first-launch report on the terminal."""
    sep = "=" * 68
    print(sep)
    print("  RIYA — Linux First-Launch System Check")
    print(sep)
    print(f"  Distribution  : {report.distribution}")
    print(f"  Architecture  : {report.architecture}")
    print(f"  Desktop       : {report.desktop_environment} / {report.display_server}")
    print(f"  Python        : {report.python_version} {'✓' if report.python_ok else '✗ UPGRADE REQUIRED'}")
    print(sep)
    print(f"  Data directory     : {report.data_dir} {'✓' if report.data_dir_writable else '✗ NOT WRITABLE'}")
    print(f"  Models directory   : {report.models_dir} {'✓' if report.models_dir_writable else '✗'}")
    print(f"  Database           : {report.database_path} {'✓' if report.database_ok else '✗'}")
    print(f"  Disk free          : {report.free_disk_gb:.1f} GB {'✓' if report.disk_sufficient_for_model else '⚠ low'}")
    print(sep)
    if report.model_found:
        print(f"  AI Model           : ✓ {report.model_name} ({report.model_size_gb:.1f} GB)")
    else:
        print("  AI Model           : ✗ Not provisioned — run: riya --setup")
    print(f"  Audio subsystem    : {report.audio_subsystem} {'✓' if report.audio_available else '✗'}")
    print(f"  TTS backend        : {report.tts_backend or 'none'} {'✓' if report.tts_available else '✗'}")
    print(f"  STT model cached   : {'✓' if report.stt_model_cached else '✗ run: pip3 install faster-whisper'}")
    print(sep)
    if report.errors:
        print("  ERRORS (must fix before RIYA can start):")
        for e in report.errors:
            print(f"    ✗ {e}")
    if report.warnings:
        print("  WARNINGS (optional / degraded functionality):")
        for w in report.warnings:
            print(f"    ⚠ {w}")
    if report.setup_guidance:
        print("  SETUP GUIDANCE:")
        for g in report.setup_guidance:
            print(f"    → {g}")
    status = "READY" if report.can_start else "NOT READY — fix errors above"
    print(sep)
    print(f"  Overall status     : {status}")
    print(sep)


if __name__ == "__main__":
    report = run_first_launch_check(verbose=True)
    print_first_launch_report(report)
    sys.exit(0 if report.can_start else 1)
