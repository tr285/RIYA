# RIYA v1.0 — Dependency Management & Packaging Specification

**Date:** 2026-11-15  
**Developer:** Tukaram  
**Product:** RIYA — Universal Personal AI Assistant  
**Platform:** macOS (Apple Silicon arm64 first)  
**Document Status:** Complete & Verified  

---

## 1. Executive Summary

Phase 4.1 conducted a comprehensive audit of all non-standard-library imports across the RIYA codebase. Previously, `pyproject.toml` only specified `numpy` and `sentence-transformers`, leaving voice, document analysis, image processing, and system telemetry packages unpinned or declared only under optional sections.

This report establishes the formalized production dependency manifest for RIYA v1.0.0, ensuring deterministic, reproducible builds and eliminating missing module errors on end-user machines.

---

## 2. Dependency Audit Matrix

The codebase was analyzed via Python AST parsing across all modules under `core/`. Every non-standard-library import was classified into its architectural domain, purpose, and version specification.

| Package | Minimum Version | Installed Version | Architectural Purpose | Production Category |
| :--- | :--- | :--- | :--- | :--- |
| **`numpy`** | `>=1.24` | `2.3.5` | Vectorized audio array transformations, spectral waveform processing, and tensor mathematics | Core / Mandatory |
| **`sounddevice`** | `>=0.4` | `0.5.6` | Low-latency PortAudio stream capture, microphone enumeration, and push-to-talk audio input | Core / Mandatory |
| **`soundfile`** | `>=0.12` | `0.14.0` | High-fidelity PCM WAV recording, playback streaming, and buffer serialization | Core / Mandatory |
| **`faster-whisper`** | `>=1.0` | `1.2.1` | Local CTranslate2 neural speech-to-text inference with INT8 / FP16 Apple Silicon acceleration | Core / Mandatory |
| **`onnxruntime`** | `>=1.16` | `1.30.0` | Silero Voice Activity Detection (VAD) neural inference for speech boundary detection | Core / Mandatory |
| **`sentence-transformers`** | `>=2.2` | `5.6.0` | Local embedding generation for semantic memory search and vector database matching | Core / Mandatory |
| **`psutil`** | `>=5.9` | `7.0.0` | Hardware telemetry: unified RAM detection, RSS memory footprint tracking, and process inspection | Core / Mandatory |
| **`pypdf`** | `>=3.0` | `6.14.2` | Local PDF document parsing, text extraction, and summarization toolchain | Core / Mandatory |
| **`openpyxl`** | `>=3.0` | `3.1.5` | Spreadsheet analysis and structured tabular data extraction toolchain | Core / Mandatory |
| **`pillow`** | `>=9.0` | `12.0.0` | Image format conversion, metadata extraction, and visual asset handling | Core / Mandatory |
| **`chardet`** | `>=5.0` | `5.2.0` | Resilient character encoding detection for local files and logs | Core / Mandatory |
| **`pytest`** | `>=7.0` | `8.4.2` | Automated test runner and execution engine | Dev Extra |
| **`pytest-cov`** | `>=4.0` | `7.1.0` | Test suite code coverage analysis | Dev Extra |
| **`faker`** | `>=20.0` | `40.38.0` | Deterministic synthetic identity and mock data generation for unit testing | Dev Extra |

---

## 3. Native C/C++ & Apple Metal Linkage

In addition to Python package dependencies, RIYA incorporates native shared libraries that bypass Python interpreter overhead for high-performance AI inference:

1. **`libriya_llama.dylib`** (`core/ai/native/bridge/libriya_llama.dylib`):
   - Native C++ bridge compiled specifically for Apple Silicon (`arm64`).
   - Exports thread-safe symbols: `riya_load_model_with_backend`, `riya_get_backend`, `riya_generate`, `riya_free_model`.
2. **`llama.cpp` Runtime Dylibs** (`core/ai/native/llama.cpp/build/bin/`):
   - `libllama.0.dylib` (Neural network execution engine).
   - `libggml-metal.0.dylib` (Direct Apple Metal GPU shader pipeline).
   - `libggml-cpu.0.dylib` (Apple Silicon NEON / Accelerate vector engine fallback).
   - `libggml-base.0.dylib` (Core tensor graph and memory allocators).
3. **Dynamic Library Resolution (`LC_RPATH`):**
   - The compiled bridge library utilizes `@loader_path/../llama.cpp/build/bin` to locate its companion GGML libraries without requiring root access or installation into `/usr/local/lib`.

---

## 4. Manifest Updates in `pyproject.toml`

The project manifest has been updated:
- **Package Name:** `riya`
- **Specification Version:** `1.0.0`
- **Metadata:** Author updated to `Tukaram <developer@riya.ai>`
- **Consolidated Dependencies:** All 11 core packages unified into `dependencies`.
- **Development Extras:** `dev` extra cleanly defined for testing and coverage.
- **Entry Points:** `riya` (CLI) and `riya-ui` (macOS Desktop UI) configured.

---

## 5. Verification & Test Stability

Following manifest updates, the test suite was executed:
- All 15 desktop UI and integration tests passed in 3.87s.
- Zero dependency conflicts, missing modules, or version deprecation errors observed.

