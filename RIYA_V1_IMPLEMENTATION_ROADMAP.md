# RIYA v1.0 — Production Implementation Roadmap

**Date:** November 15, 2026  
**Author / Lead:** Tukaram  
**Project:** RIYA (Responsible Intelligent Yielding Assistant)  
**Target Milestone:** RIYA v1.0 Production Release  
**Status:** **PLANNING COMPLETE — AWAITING USER APPROVAL**

---

## 1. Vision & Strategy

RIYA v1.0 will be the premier **offline-first, privacy-respecting, personal AI assistant** for macOS (with subsequent Linux and Windows expansion). It pairs local Apple Silicon Metal-accelerated inference (Qwen3-4B-Instruct) with an offline voice engine, cryptographic confirmation safeguards, and comprehensive desktop automation.

To transition from the current v0.5 / Milestone 6.9 baseline to a rock-solid, production-grade v1.0, work is organized into **six prioritized phases**.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        RIYA v1.0 RELEASE ROADMAP                       │
├────────────────────┬────────────────────┬──────────────────────────────┤
│ Phase 1            │ Phase 2            │ Phase 3                      │
│ Repository Hygiene │ Architectural      │ Desktop UI &                 │
│ & Consolidation    │ Decoupling         │ Native Packaging             │
│ (201 Backups, Voice)│ (God Object, Test) │ (Tkinter polish, Notarize)   │
├────────────────────┼────────────────────┼──────────────────────────────┤
│ Phase 4            │ Phase 5            │ Phase 6                      │
│ Packaging &        │ Cross-Platform     │ Hardening, Docs              │
│ Dependencies       │ Parity             │ & Launch                     │
│ (pyproject, GGUF)  │ (Linux / Windows)  │ (v1.0 Release Candidate)     │
└────────────────────┴────────────────────┴──────────────────────────────┘
```

---

## 2. Phase 1: Repository Hygiene & Codebase Consolidation

**Goal:** Clean up accumulated technical debt, eliminate duplicate implementations, and archive intermediate milestone files without breaking git history.

### Tasks:
1. **Archive 201 Backup Files:**
   - Safe removal of `*.before*` and `*.bak` files across `core/`, `tests/`, and `apps/`.
   - Ensure all changes reflected in the current `.py` files match or exceed backup versions.
2. **Consolidate Voice Implementations:**
   - Deprecate Phase 5 single-turn wrappers (`core/voice/microphone.py`, `core/voice/tts.py`, `core/voice/stt.py`, `core/voice/assistant.py`).
   - Standardize all internal imports on Phase 6.5 streaming components:
     - `MicrophoneStream` from [core/voice/audio_input.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/audio_input.py)
     - `AudioOutputPlayer` from [core/voice/audio_output.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/audio_output.py)
     - `FasterWhisperSTTAdapter` & `MacOSSayTTSAdapter` from [core/voice/adapters.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/adapters.py)
     - `VoiceOrchestrator` from [core/voice/orchestrator.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/orchestrator.py)
3. **Archive Root Markdown Reports:**
   - Move past phase reports (`RIYA_V0_5_PHASE_*_REPORT.md`, `RIYA_FINAL_SYSTEM_AUDIT_REPORT.md`) into `docs/reports/archive/`, keeping only current v1.0 documentation in root.

---

## 3. Phase 2: Architectural Decoupling & Test Isolation Fixes

**Goal:** Refactor the monolithic `RIYAAssistant`, eliminate global singletons, and achieve 100% test suite pass rate.

### Tasks:
1. **Fix Memory Test Isolation Defect:**
   - In `core/tools/memory.py`, ensure `_active_memory_manager()` never falls back to an unmanaged singleton if an isolated database is specified in test fixtures.
   - Update `_make_isolated_assistant` in `tests/test_native_ai_security.py` to establish an explicit `ExecutionContextScope` with the isolated memory manager.
   - Verify `tests/test_native_ai_security.py` passes 20/20 (bringing test suite to 439/439 passed).
2. **Decouple `RIYAAssistant`:**
   - Break down `core/agent/assistant.py` (2,454 lines) into focused domain delegates:
     - `AssistantTurnManager`: Handles turn tokenization, input validation, and execution context.
     - `AssistantMemoryDelegate`: Manages memory recall, intent detection, and context injection.
     - `AssistantPlanDelegate`: Coordinates multi-step plans, confirmation pauses, and task checkpointing.
     - `AssistantIdentityDelegate`: Manages active user authentication and profile switching.
   - Retain `RIYAAssistant` as a clean facade coordinating these delegates.
3. **Eliminate Resource Warnings:**
   - Add explicit context managers (`with sqlite3.connect(...) as conn:`) across memory database tests to eliminate all `ResourceWarning: unclosed database` messages.

---

## 4. Phase 3: Desktop UI Modernization & Native macOS Packaging

**Goal:** Elevate RIYA's desktop presence into a polished, secure, and native macOS experience.

### Tasks:
1. **Desktop UI Polish (Tkinter / Native Layer):**
   - Enhance [core/voice/ui.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/ui.py) and [core/onboarding/ui.py](file:///Users/tukaramgore/Projects/RIYA/core/onboarding/ui.py):
     - Native macOS dark mode and light mode theming.
     - Smooth real-time microphone VU level indicator with anti-aliasing.
     - Thread-safe UI updates marshaled via `root.after()` to prevent window freezing during speech synthesis or AI inference.
     - Accessibility labels for VoiceOver compatibility.
2. **Apple Developer Code Signing & Hardened Runtime:**
   - Configure [apps/macos/create_app_bundle.py](file:///Users/tukaramgore/Projects/RIYA/apps/macos/create_app_bundle.py) to support `codesign` with Hardened Runtime (`--options runtime`).
   - Create `entitlements.plist` requesting required macOS capabilities:
     - `com.apple.security.device.audio-input` (Microphone access)
     - `com.apple.security.automation.apple-events` (Application control)
   - Add notarization workflow (`xcrun notarytool submit`).
3. **DMG Distribution Package:**
   - Script DMG disk image creation with standard Drag-to-Applications layout and custom RIYA icon.

---

## 5. Phase 4: Production Packaging & Dependency Unification

**Goal:** Create a reproducible, single-command installation experience for end users and developers.

### Tasks:
1. **Unify `pyproject.toml`:**
   - Update metadata:
     - Version: `1.0.0`
     - Author: `Tukaram <developer@riya.ai>`
   - Move required core dependencies into `dependencies`:
     ```toml
     dependencies = [
         "numpy>=1.24",
         "sounddevice>=0.4",
         "soundfile>=0.12",
         "faster-whisper>=1.0",
         "onnxruntime>=1.16",
         "sentence-transformers>=2.2",
         "faker>=20.0",
     ]
     ```
   - Define clear optional extras for development and building (`[project.optional-dependencies] dev = ["pytest", "pytest-cov", "black"]`).
2. **Automated GGUF Model Provisioning:**
   - Implement `core/ai/downloader.py`:
     - Checks if `models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf` exists.
     - If missing during interactive startup, prompts user with a progress-bar download from the official HuggingFace repository with SHA-256 integrity verification.
     - Supports chunked resuming on network interruption.

---

## 6. Phase 5: Cross-Platform Parity (Linux & Windows)

**Goal:** Expand RIYA beyond macOS to full Linux and Windows desktop support.

### Tasks:
1. **Linux Audio & Voice Support:**
   - Implement `LinuxPiperTTSAdapter` using local Piper neural TTS (`piper-tts`).
   - Add ALSA / PulseAudio device handling via `sounddevice`.
   - Implement Linux desktop automation in `core/device/linux.py` using `xdotool` and `wmctrl` (X11) / `ydotool` (Wayland).
2. **Windows Audio & Voice Support:**
   - Implement `WindowsSapiTTSAdapter` using native Windows SAPI.SpVoice / Windows.Media.SpeechSynthesis.
   - Add WASAPI / DirectSound audio capture.
   - Implement Windows desktop automation in `core/device/windows.py` via PowerShell / Win32 API.
3. **Cross-Platform CI Pipeline:**
   - Establish GitHub Actions workflows testing Ubuntu, Windows Server, and macOS runners on each PR.

---

## 7. Phase 6: Documentation, Hardening & v1.0 Launch

**Goal:** Complete security auditing, write user-facing documentation, and execute the official v1.0 release.

### Tasks:
1. **User Documentation:**
   - Write comprehensive `README.md` with visual feature tour, architecture diagram, and quickstart commands.
   - Author `docs/USER_GUIDE.md`:
     - CLI commands reference table (`/voice`, `/pause`, `/resume`, `/interrupt`, `/text`, `/status`, `/tasks`, `/permissions`).
     - Voice conversation best practices (PTT vs continuous mode).
     - Privacy & security policy (100% offline guarantee).
2. **Final Red-Team Security Audit:**
   - Verify prompt injection fences, confirmation token burn on tamper, path sandboxing, and multi-user isolation under simulated malicious workloads.
3. **Tag & Release:**
   - Tag `v1.0.0` in Git.
   - Publish wheel to internal/public index.
   - Publish macOS DMG release artifact.

---

## 8. Prioritized Execution Matrix

| Phase | Milestone | Priority | Estimated Effort | Prerequisite |
|---|---|---|---|---|
| **Phase 1** | Hygiene & Consolidation | **P0 (Immediate)** | 1-2 days | User Approval |
| **Phase 2** | Architecture & Test Fixes | **P0 (Immediate)** | 2-3 days | Phase 1 |
| **Phase 4** | Packaging & Model Downloader | **P1 (High)** | 1-2 days | Phase 2 |
| **Phase 3** | Desktop UI & macOS DMG | **P1 (High)** | 3-4 days | Phase 2 |
| **Phase 6** | Docs, Hardening & Launch | **P1 (High)** | 2 days | Phases 3, 4 |
| **Phase 5** | Linux & Windows Parity | **P2 (Post-v1.0)** | 5-7 days | Phase 6 |

---

## 9. Next Steps

Execution will begin immediately upon receipt of user approval:
1. **Awaiting User Review:** Review audit findings and roadmap.
2. **Step 1 Action upon approval:** Execute **Phase 1 (Repository Hygiene & Consolidation)** and **Phase 2 (Architectural Decoupling & Test Fixes)**.

