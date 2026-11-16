# RIYA v1.0 — Phase 4 Baseline Packaging & Provisioning Audit

**Date:** 2026-11-15  
**Developer:** Tukaram  
**Product:** RIYA — Universal Personal AI Assistant  
**Platform:** macOS (Apple Silicon arm64 first)  
**Document Status:** Complete & Verified  

---

## 1. Executive Summary

Phase 3 successfully delivered the modernized native macOS desktop interface, multi-resolution application iconography (`AppIcon.icns`), hardened runtime entitlements (`entitlements.plist`), initial application bundle generation (`apps/macos/create_app_bundle.py`), and compressed UDZO disk image generation (`apps/macos/create_dmg.py`). The full regression suite achieved **462/462 passing tests** with 100% test integrity.

However, the existing distribution pipeline was designed as an initial launcher bridge rather than a fully autonomous standalone consumer release. Specifically:
1. **Python Environment Binding:** The existing `dist/RIYA.app/Contents/MacOS/RIYA` launcher is a shell script that relies on discovering an external Python interpreter (`/opt/anaconda3/bin/python`, `/opt/homebrew/bin/python3`, or repo `.venv`), which fails on a clean user Mac without developer tools installed.
2. **Repository Root Path Tight-Coupling:** Application assets, models, and databases are addressed relative to `PROJECT_ROOT = Path(__file__).resolve().parents[2]`. When dragged into `/Applications`, writing mutable user data (`data/riya.db`, `data/voice_recordings`, logs) inside the signed read-only app bundle violates macOS security and fails with permission errors.
3. **Incomplete Dependency Manifest:** `pyproject.toml` only specified `numpy` and `sentence-transformers`, omitting critical voice dependencies (`sounddevice`, `faster-whisper`, `onnxruntime`, `soundfile`) and document utilities (`pypdf`, `openpyxl`, `pillow`, `psutil`) from mandatory install specs.
4. **Static Model Expectation:** The AI runtime currently expects `models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf` to be pre-existing on disk. There is no automated model catalog, bandwidth-efficient chunked downloader, SHA-256 integrity check, or unified memory recommendation mechanism for users who download the installer without the 2.5 GB model pre-baked.

Phase 4 transforms RIYA into an enterprise-grade, self-contained desktop application with intelligent, secure model provisioning.

---

## 2. Comprehensive Inventory & Inspection

### 2.1 Configuration & Dependency Manifests
- [pyproject.toml](file:///Users/tukaramgore/Projects/RIYA/pyproject.toml):
  - **Version:** Currently `0.1.0` (needs upgrade to `1.0.0`).
  - **Author:** Contains typo `"Tularam"` (needs correction to `"Tukaram <developer@riya.ai>"`).
  - **Dependencies:** Lacks core voice runtime packages in primary requirements; requires restructuring into unified production dependencies with optional developer extras.

### 2.2 Storage & Path Resolution
- [core/config/settings.py](file:///Users/tukaramgore/Projects/RIYA/core/config/settings.py):
  - Currently defines `PROJECT_ROOT = Path(__file__).resolve().parents[2]`.
  - Allowed roots strictly enumerate `PROJECT_ROOT`, `Desktop`, `Documents`, and `Downloads`.
  - **Production Gap:** Standard macOS user data locations (`~/Library/Application Support/RIYA/`, `~/Library/Caches/RIYA/`, `~/Library/Logs/RIYA/`) are not resolved dynamically.

### 2.3 Native AI & Metal Bridge
- [core/ai/native/llama_runtime.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/native/llama_runtime.py):
  - Bridges directly to `core/ai/native/bridge/libriya_llama.dylib`.
  - Dylib has embedded `LC_RPATH` pointing to `@loader_path/../llama.cpp/build/bin`.
  - Links to Apple Metal framework, `libllama.0.dylib`, `libggml-metal.0.dylib`, and `libggml-base.0.dylib`.
  - Fully offline, thread-safe, and zero-telemetry.

### 2.4 Voice & Audio Engine
- [core/voice/config.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/config.py):
  - Defaults `DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "data" / "voice_settings.json"`.
  - Defaults `DEFAULT_RECORDINGS_DIR = PROJECT_ROOT / "data" / "voice_recordings"`.
  - Must be abstracted to writable macOS Application Support storage.

### 2.5 Packaging & Distribution Scripts
- [apps/macos/create_app_bundle.py](file:///Users/tukaramgore/Projects/RIYA/apps/macos/create_app_bundle.py):
  - Generates `Contents/MacOS/RIYA`, `Contents/Resources/AppIcon.icns`, and `Contents/Info.plist`.
  - Current launcher script hardcodes `REPO_ROOT` and attempts `cd "$REPO_ROOT"`.
- [apps/macos/create_dmg.py](file:///Users/tukaramgore/Projects/RIYA/apps/macos/create_dmg.py):
  - Creates staging directory with `/Applications` symlink and quick-start instructions.
  - Successfully generates read-only UDZO disk image `dist/RIYA-v1.0.0-AppleSilicon.dmg` (CRC32 validated).

---

## 3. Production Gap Analysis

| Component | Current Development State | Target Production State (Phase 4) | Status |
| :--- | :--- | :--- | :--- |
| **App Bundle Portability** | Relies on local Python environment and repo tree | Self-contained launcher supporting bundled/portable runtime & dynamic path fallback | **Blocked (Phase 4.1 & 4.9)** |
| **Data Directory Isolation** | Reads/writes directly inside repository `data/` | Resolves `~/Library/Application Support/RIYA/` for user databases & settings | **Blocked (Phase 4.2)** |
| **Model Provisioning** | Manual file placement in `models/` | Managed `ModelCatalog` with secure streaming downloader & SHA-256 verification | **Blocked (Phase 4.3 & 4.4)** |
| **Hardware Recommender** | Static model selection | Chip generation (M1-M4) and unified memory detection with safe quantization advice | **Blocked (Phase 4.5)** |
| **Desktop UI Integration** | Static model telemetry display | Live model management UI with download progress, ETA, and model switching | **Blocked (Phase 4.6)** |
| **Offline Verification** | Offline inference tested in unit tests | Automated network-cut simulation verifying 100% offline isolation | **Blocked (Phase 4.7)** |

---

## 4. Phase 4 Execution Roadmap

1. **Phase 4.1:** Dependency Management & `pyproject.toml` Hardening.
2. **Phase 4.2:** Standard macOS Path & Storage Abstraction (`core/config/paths.py`).
3. **Phase 4.3 & 4.4:** Model Catalog & Secure Resumable Downloader (`core/ai/provisioning/`).
4. **Phase 4.5:** Hardware-Aware Unified Memory Recommender (`core/ai/provisioning/hardware.py`).
5. **Phase 4.6:** Desktop Onboarding & Settings UI Model Manager (`core/desktop/settings_view.py`).
6. **Phase 4.7 & 4.8:** Offline Operation & Storage Safety Verification.
7. **Phase 4.9 & 4.10:** Self-Contained App Bundle & DMG Packaging.
8. **Phase 4.11 & 4.12:** Automated Test Suite & Final Implementation Report.

