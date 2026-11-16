# RIYA v1.0 — MILESTONE 5.4 BASELINE AUDIT
## Windows OS Integration & Audio Implementation Assessment

**Developer:** Tukaram  
**Project:** RIYA — Universal Offline-First Personal AI Assistant  
**Target Platform:** Windows 10 (22H2) and Windows 11  
**Architecture:** Shared Single Core + Native Windows Adapters  
**Date:** November 15, 2026  
**Status:** BASELINE AUDIT COMPLETED — IMPLEMENTATION AUTHORIZED  

---

## 1. Executive Summary

Milestone 5.4 implements native Windows operating-system integration and an offline conversational voice pipeline for RIYA v1.0. Following the Single Core Native Adapters architecture established in Milestone 5.1 and proven across macOS (reference host) and Linux (Milestone 5.2 & 5.3), the existing fail-closed Windows platform and voice stubs must be replaced with concrete, production-grade Windows implementations.

The Windows implementation must adhere to the same non-negotiable principles:
- **Zero Duplication:** Shared AI engine, memory manager, identity/auth, and security pipeline remain unified.
- **Fail-Closed by Default on Non-Windows Hosts:** To guarantee test isolation on macOS/Linux hosts without breaking existing cross-platform regression assertions, Windows adapters will activate dynamically when `sys.platform == "win32"` or when explicitly tested with `force_active=True`.
- **Truthful Capabilities:** Operations requiring Windows hardware (such as direct WASAPI loopback, Win32 window handles, or Windows Registry access) will be executed via native APIs and standard fallbacks (PowerShell / `ctypes.windll` / `psutil`), reporting execution status honestly.
- **Zero Cloud Leakage:** All reasoning, transcription, and speech synthesis operate completely offline.

---

## 2. Repository & Branch State

### 2.1 Git Status & Branch Identification
- **Active Branch:** `main` (tracking `origin/main`).
- **Working Tree:** All uncommitted modifications from Milestones 5.1, 5.2, and 5.3 are preserved intact.
- **Safety Policy:** No destructive git commands (`git reset`, `git clean`, `git checkout .`) and no commits or pushes will be executed.

### 2.2 Test Baseline
- **Verified Pre-Milestone Test Count:** **605 tests collected** across the repository (508 baseline tests + 97 Linux packaging tests).
- **Regression Quality Gate:** All existing 605 tests must continue to pass with 100% fidelity.

---

## 3. Subsystem Audit & Windows Integration Plan

### 3.1 Device & OS Integration (`core/device/windows.py`)
- **Current State:** 422 lines of fail-closed stubs returning `make_unsupported_result(...)` for all 23 platform capabilities.
- **Target Implementation:**
  1. `WindowsSystemInformationAdapter`:
     - OS version & edition via `platform.win32_ver()` / `sys.getwindowsversion()` / `wmic` / registry / `psutil`.
     - Hardware resources via `psutil.virtual_memory()`, `psutil.cpu_count()`, `shutil.disk_usage()`.
     - Battery telemetry via `psutil.sensors_battery()` with desktop AC fallback.
     - Active network interfaces via `psutil.net_if_addrs()` and `psutil.net_if_stats()`.
  2. `WindowsApplicationAdapter`:
     - Application discovery across `%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LOCALAPPDATA%`, and Start Menu shortcuts (`%ProgramData%\Microsoft\Windows\Start Menu\Programs`, `%APPDATA%\Microsoft\Windows\Start Menu\Programs`).
     - Safe `.lnk` resolution via PowerShell COM `WScript.Shell` or native path extraction without arbitrary script evaluation.
     - Secure launching via `subprocess.Popen(array, shell=False)`.
     - Process inspection & safeguards: Protects system critical processes (`smss.exe`, `csrss.exe`, `wininit.exe`, `services.exe`, `lsass.exe`, `svchost.exe`, `explorer.exe`, `riya.exe`, PID 0, PID 4, and current process).
  3. `WindowsFilesystemAdapter`:
     - Enforces `FILESYSTEM_ALLOWED_ROOTS` (project root, user data directory, Desktop, Documents, Downloads, `%TEMP%`).
     - Canonical path resolution via `Path.resolve()`.
     - Reparse-point / junction / symlink escape protection.
  4. `WindowsClipboardAdapter`:
     - Text read/write via PowerShell `Get-Clipboard`/`Set-Clipboard` or `ctypes.windll.user32`.
     - Sensitive credential scrubbing via `looks_like_secret()`.
  5. `WindowsNotificationAdapter`:
     - Native Windows notifications via PowerShell BurntToast / WinRT or standard script notifications with title and body.
  6. `WindowsWindowManagerAdapter`:
     - Foreground window title and handle retrieval via `ctypes.windll.user32.GetForegroundWindow` and `GetWindowTextW`.
     - Window enumeration via `EnumWindows`.

### 3.2 Voice & Audio Subsystem (`core/voice/adapters.py` & `core/voice/tts.py`)
- **Current State:** Windows voice classes inherit from `_UnsupportedVoiceAdapterBase` and raise `UnsupportedPlatformCapabilityError`.
- **Target Implementation:**
  1. `WindowsAudioInputAdapter`:
     - Real-time 16 kHz mono float32 microphone stream capture via PortAudio/`sounddevice` (WASAPI, DirectSound, or MME).
     - Clean worker thread pump and hardware release.
  2. `WindowsAudioOutputAdapter`:
     - Chunked playback via `AudioOutputPlayer` with instant barge-in stop support.
  3. `WindowsSpeechRecognitionAdapter`:
     - Offline local transcription via `LocalSTT` (`faster-whisper` CTranslate2) supporting CPU or CUDA where available.
  4. `WindowsSpeechSynthesisAdapter` & `WindowsTTS`:
     - SAPI 5 speech synthesis via PowerShell `System.Speech.Synthesis.SpeechSynthesizer` or `win32com.client`.
     - Direct WAV export for seamless chunked audio streaming.
     - Secondary fallback to `piper` or `espeak-ng`.
  5. `TTSFactory`:
     - Extended to instantiate `WindowsTTS` when `target == "windows"`.

### 3.3 AI Runtime & Native Bridge (`core/ai/native/llama_runtime.py`)
- **Current State:** Dynamic bridge library hardcoded to `libriya_llama.dylib` on macOS.
- **Target Implementation:**
  - Dynamic bridge library name resolution:
    - macOS: `libriya_llama.dylib`
    - Linux: `libriya_llama.so`
    - Windows: `riya_llama.dll` or `libriya_llama.dll`
  - Safe DLL loading using `ctypes.WinDLL` / `ctypes.CDLL` restricted strictly to the local `bridge` directory to prevent DLL search-order hijacking.
  - Backends: `auto`, `cpu`, `metal` (macOS), `cuda` (Windows/Linux where built).

### 3.4 Security & Permissions Alignment
- Permission checks for all Windows operations (`system.info`, `system.open_app`, `system.close_app`, `system.clipboard`, `system.notification`, `voice.microphone`, `filesystem.read`, `filesystem.write`).
- All subprocess invocations enforce argument arrays and `shell=False`.
- Secret credential redaction enforced across clipboard and logs.

---

## 4. Test Strategy & Acceptance Targets

We will author 6 comprehensive test suites for Windows integration:
1. `tests/test_windows_platform.py`: Telemetry, application discovery, execution safety, process safeguards, window management.
2. `tests/test_windows_voice_adapters.py`: Audio input/output, VAD, STT, SAPI TTS, and `TTSFactory`.
3. `tests/test_windows_security.py`: Protected process denial, junction/symlink escape blocking, secret scrubbing.
4. `tests/test_windows_clipboard.py`: Windows clipboard reading, writing, and token redaction.
5. `tests/test_windows_notifications.py`: Windows notification dispatch and parameter formatting.
6. `tests/test_windows_integration.py`: End-to-end integration with `RIYAAssistant`, `get_platform_adapter()`, and CLI dispatcher.

---

## 5. Preservation Guarantee

- Zero modifications to Qwen3 neural model weights.
- Zero modifications to native `libriya_llama.dylib` or Apple Metal acceleration.
- Zero modifications to user database `data/riya.db`.
- macOS and Linux reference implementations remain 100% operational.

