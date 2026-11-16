# RIYA v1.0 — Phase 5 Baseline Cross-Platform Audit
## Architectural Assessment & Cross-Platform Parity Strategy (Linux & Windows)

**Date:** 2026-11-15  
**Developer:** Tukaram  
**Product:** RIYA — Universal Personal AI Assistant  
**Existing Verified Platform:** macOS Apple Silicon (`arm64`)  
**Target Platforms for Parity:** Linux (Ubuntu LTS reference) and Windows (Windows 10/11 x86_64)  
**Document Status:** Complete & Verified (Milestone 5.1 Baseline)  

---

## 1. Executive Summary

Phases 1 through 4 successfully established RIYA v1.0 on macOS Apple Silicon as a verified reference implementation:
- **Phase 1 & 2:** Repository hygiene, 190 redundant backups archived, memory management decoupled with dynamic execution contexts, and zero security regressions.
- **Phase 3:** Modernized desktop UI in [core/desktop/](file:///Users/tukaramgore/Projects/RIYA/core/desktop/) with dark/light theme tokens, markdown rendering, 24-bar audio visualizers, and preferences.
- **Phase 4:** Dependency unification in [pyproject.toml](file:///Users/tukaramgore/Projects/RIYA/pyproject.toml), storage directory separation in [core/config/paths.py](file:///Users/tukaramgore/Projects/RIYA/core/config/paths.py), intelligent model provisioning in [core/ai/provisioning/](file:///Users/tukaramgore/Projects/RIYA/core/ai/provisioning/), 100% offline air-gapped verification, and native packaging (`dist/RIYA.app`, `dist/RIYA-v1.0.0-AppleSilicon.dmg`).
- **Baseline Test Suite:** **476/476 passed** (100% pass rate).

However, RIYA's cross-platform parity currently stands at **40%**. While architectural abstraction interfaces exist in [core/device/platform.py](file:///Users/tukaramgore/Projects/RIYA/core/device/platform.py) and [core/voice/adapters.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/adapters.py), the Linux and Windows implementations are strictly fail-closed stubs returning unsupported errors. Furthermore, چندین critical application components (voice TTS, automation, process verification, AI bridge library loading) contain hardcoded macOS assumptions (`/usr/bin/say`, `libriya_llama.dylib`, `/usr/bin/pgrep`, `MacOSPlatform`).

Phase 5 achieves true cross-platform parity by establishing one shared intelligence core with robust native operating system adapters for Linux and Windows.

---

## 2. Comprehensive Inventory of Current Cross-Platform State

### 2.1 Operating System Device Abstraction (`core/device/`)

| File | Current Status | Lines | Linux Support | Windows Support | macOS Support |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [core/device/platform.py](file:///Users/tukaramgore/Projects/RIYA/core/device/platform.py) | Active Architectural Core | 595 | Full abstraction interfaces | Full abstraction interfaces | Full abstraction interfaces |
| [core/device/macos.py](file:///Users/tukaramgore/Projects/RIYA/core/device/macos.py) | Fully Implemented & Verified | 1,217 | N/A | N/A | 23/23 Capabilities Active |
| [core/device/linux.py](file:///Users/tukaramgore/Projects/RIYA/core/device/linux.py) | **Fail-Closed Stub** | 422 | **0/23 Capabilities** (Returns `_LINUX_UNSUPPORTED_REASON`) | N/A | N/A |
| [core/device/windows.py](file:///Users/tukaramgore/Projects/RIYA/core/device/windows.py) | **Fail-Closed Stub** | 422 | N/A | **0/23 Capabilities** (Returns `_WINDOWS_UNSUPPORTED_REASON`) | N/A |
| [core/device/__init__.py](file:///Users/tukaramgore/Projects/RIYA/core/device/__init__.py) | Registry Layer | 89 | Registered (`LinuxPlatform`) | Registered (`WindowsPlatform`) | Registered (`MacOSPlatform`) |

**Detailed Findings:**
1. [core/device/linux.py](file:///Users/tukaramgore/Projects/RIYA/core/device/linux.py):
   - All 6 sub-adapters (`LinuxSystemInformationAdapter`, `LinuxApplicationAdapter`, `LinuxFilesystemAdapter`, `LinuxClipboardAdapter`, `LinuxNotificationAdapter`, `LinuxWindowManagerAdapter`) return `make_unsupported_result()`.
   - `LinuxPlatform.capabilities()` hardcodes `platform_available=False` and `is_supported=False`.
   - Direct methods (`open_application`, `close_application`, `take_screenshot`, `lock_screen`) immediately raise `UnsupportedPlatformCapabilityError`.
2. [core/device/windows.py](file:///Users/tukaramgore/Projects/RIYA/core/device/windows.py):
   - Identical stub structure: all 6 sub-adapters return `make_unsupported_result()`.
   - `WindowsPlatform.capabilities()` hardcodes `platform_available=False` and `is_supported=False`.
   - Direct methods raise `UnsupportedPlatformCapabilityError`.

---

### 2.2 Voice & Audio Subsystem (`core/voice/`)

| Component | macOS Implementation | Linux Status | Windows Status | Production Gap |
| :--- | :--- | :--- | :--- | :--- |
| **Audio Input Capture** | `CoreAudioAudioInputAdapter` via `sounddevice` | `LinuxAudioInputAdapter` (Stub) | `WindowsAudioInputAdapter` (Stub) | `sounddevice` works cross-platform; needs native device discovery & error mapping |
| **Audio Output Playback** | `CoreAudioAudioOutputAdapter` via `sounddevice` | `LinuxAudioOutputAdapter` (Stub) | `WindowsAudioOutputAdapter` (Stub) | Playback chunk streamer needs cross-platform ALSA/Pulse/PipeWire & WASAPI |
| **Speech-to-Text (STT)** | `LocalSTT` (`faster-whisper` / CTranslate2) | `LinuxSpeechRecognitionAdapter` (Stub) | `WindowsSpeechRecognitionAdapter` (Stub) | CTranslate2 has native wheels for Linux & Windows; needs adapter wiring |
| **Speech Synthesis (TTS)** | [core/voice/tts.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/tts.py) hardcodes `/usr/bin/say` | `LinuxSpeechSynthesisAdapter` (Stub) | `WindowsSpeechSynthesisAdapter` (Stub) | Requires Linux local TTS (`espeak-ng`/`piper-tts`) and Windows SAPI (`System.Speech`) |
| **Voice Activity Detection** | Silero VAD (ONNX Runtime) | Shared, platform-independent | Shared, platform-independent | Supported out-of-the-box via `onnxruntime` |
| **Voice Orchestrator** | [core/voice/orchestrator.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/orchestrator.py) | Shared state machine | Shared state machine | Shared logic; needs concrete platform adapter injection |

**Detailed Findings:**
- [core/voice/adapters.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/adapters.py):
  - Lines 507–586 define `WindowsAudioInputAdapter`, `WindowsAudioOutputAdapter`, `WindowsSpeechRecognitionAdapter`, and `WindowsSpeechSynthesisAdapter`, all inheriting from `_UnsupportedVoiceAdapterBase` and raising `UnsupportedPlatformCapabilityError`.
  - Lines 587–666 define `LinuxAudioInputAdapter`, `LinuxAudioOutputAdapter`, `LinuxSpeechRecognitionAdapter`, and `LinuxSpeechSynthesisAdapter`, also inheriting from `_UnsupportedVoiceAdapterBase` and raising `UnsupportedPlatformCapabilityError`.
- [core/voice/tts.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/tts.py):
  - Line 13 defines `_SAY_BIN = "/usr/bin/say"`. Line 69 builds commands with `[_SAY_BIN, "-v", self.voice]`.
  - On Linux and Windows, `LocalTTS.speak()` crashes with `FileNotFoundError: [Errno 2] No such file or directory: '/usr/bin/say'`.

---

### 2.3 Local Neural AI Engine (`core/ai/`)

| Component | macOS Implementation | Linux Requirement | Windows Requirement | Production Gap |
| :--- | :--- | :--- | :--- | :--- |
| **Bridge Dylib Name** | `libriya_llama.dylib` | `libriya_llama.so` | `riya_llama.dll` | [core/ai/native/llama_runtime.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/native/llama_runtime.py) hardcodes `_BRIDGE_LIB_NAME = "libriya_llama.dylib"` |
| **Backend Enumeration** | `_VALID_BACKENDS = {"auto", "metal", "cpu"}` | `auto`, `cuda`, `vulkan`, `cpu` | `auto`, `cuda`, `vulkan`, `directml`, `cpu` | Must accept CUDA, Vulkan, DirectML based on detected GPU |
| **Bridge Compilation** | Compiled with Apple Clang & Metal | Build script for GCC/Clang + CMake | Build script for MSVC / Clang | Source [riya_llama.cpp](file:///Users/tukaramgore/Projects/RIYA/core/ai/native/bridge/riya_llama.cpp) exists; needs build targets |
| **Hardware Detection** | Queries `sysctl` (`hw.memsize`, `machdep.cpu.brand_string`) | Queries `/proc/meminfo`, `/proc/cpuinfo`, `lspci`, `nvidia-smi` | Queries `psutil`, `wmic`, `GlobalMemoryStatusEx` | [core/ai/provisioning/hardware.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/provisioning/hardware.py) needs Linux/Windows probes |
| **Model Provisioning** | Catalog, downloader, SHA-256 | 100% Shared, platform-independent | 100% Shared, platform-independent | Shared logic in [core/ai/provisioning/](file:///Users/tukaramgore/Projects/RIYA/core/ai/provisioning/) is already cross-platform |

---

### 2.4 Desktop UI & Frontend (`core/desktop/`)

| Component | macOS Implementation | Linux Considerations | Windows Considerations |
| :--- | :--- | :--- | :--- |
| **Window Coordinator** | [core/desktop/app.py](file:///Users/tukaramgore/Projects/RIYA/core/desktop/app.py) | Runs on X11 and Wayland | Runs on Windows Desktop Manager |
| **Keybindings** | Uses `Command` modifier on Darwin | Needs `Control` modifier | Needs `Control` modifier (already handled in `app.py:111`) |
| **Typography** | `-apple-system` font family | Needs `Ubuntu` or `DejaVu Sans` font fallback | Needs `Segoe UI` font fallback |
| **Audio Visualizer** | Tkinter Canvas 24-bar waveform | Supported out-of-the-box | Supported out-of-the-box |
| **Settings & Preferences** | 10-tab Notebook | Supported out-of-the-box | Supported out-of-the-box |

---

### 2.5 Storage, Paths & Filesystem (`core/config/` & `core/tools/filesystem/`)

| Path Function | macOS Path | Linux Path (XDG Base Directory) | Windows Path (Known Folders) |
| :--- | :--- | :--- | :--- |
| `get_app_support_dir()` | `~/Library/Application Support/RIYA` | `$XDG_DATA_HOME/riya` or `~/.local/share/riya` | `%APPDATA%\RIYA` |
| `get_user_data_dir()` | `~/Library/Application Support/RIYA/data` | `~/.local/share/riya/data` | `%APPDATA%\RIYA\data` |
| `get_models_dir()` | `~/Library/Application Support/RIYA/models` | `~/.local/share/riya/models` | `%APPDATA%\RIYA\models` |
| `get_logs_dir()` | `~/Library/Logs/RIYA` | `$XDG_STATE_HOME/riya/logs` or `~/.local/state/riya/logs` | `%LOCALAPPDATA%\RIYA\logs` |
| `get_cache_dir()` | `~/Library/Caches/RIYA` | `$XDG_CACHE_HOME/riya` or `~/.cache/riya` | `%LOCALAPPDATA%\RIYA\cache` |

**Security & Traversal Risks:**
- **Windows Reserved Device Names:** In [core/tools/filesystem/files.py](file:///Users/tukaramgore/Projects/RIYA/core/tools/filesystem/files.py), paths containing `CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9` must be blocked to prevent Windows kernel lockups.
- **Drive Letter & Root Handling:** Paths on Windows start with `C:\`, `D:\`, or UNC `\\server\share`. Allowed root policies must validate Windows root specifiers without allowing drive traversal.
- **`os.O_NOFOLLOW` Availability:** Handled via `getattr(os, "O_NOFOLLOW", 0)` to prevent `AttributeError` on Windows systems where symlink no-follow flags differ.
- **Permissions on Multi-User Linux:** SQLite databases (`data/riya.db`) must enforce file creation mode `0o600` so other local users cannot inspect conversational memories.

---

### 2.6 Automation, System Tools & Agent Execution

1. [core/automation/desktop.py](file:///Users/tukaramgore/Projects/RIYA/core/automation/desktop.py):
   - Line 8 hardcodes `from core.device.macos import MacOSPlatform; _DEFAULT_PLATFORM = MacOSPlatform()`.
   - Must be updated to use `get_platform_adapter()` dynamically.
2. [core/agent/tool_executor.py](file:///Users/tukaramgore/Projects/RIYA/core/agent/tool_executor.py):
   - Lines 1051 & 1132: `from core.tools.system.macos import validate_app_name`.
   - Line 1064: Direct execution of `/usr/bin/pgrep`.
   - Must be refactored to use `psutil` or the active platform adapter's `list_running_applications()`.
3. [core/agent/assistant.py](file:///Users/tukaramgore/Projects/RIYA/core/agent/assistant.py):
   - Lines 638 & 1790: `from core.tools.system.macos import _looks_like_secret`.
   - Must be decoupled into [core/security/manager.py](file:///Users/tukaramgore/Projects/RIYA/core/security/manager.py) or [core/ai/safety.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/safety.py).
4. [core/tools/system/macos.py](file:///Users/tukaramgore/Projects/RIYA/core/tools/system/macos.py):
   - Contains 1,927 lines of macOS-specific automation (`osascript`, `pmset`, `screencapture`, `pbcopy`, `pbpaste`).
   - Requires modular parallel implementations:
     - `core/tools/system/linux.py` (`xdotool`, `wmctrl`, `xclip`/`wl-copy`, `notify-send`, `systemctl`, `loginctl`).
     - `core/tools/system/windows.py` (`powershell`, `tasklist`, `taskkill`, `powershell Set-Clipboard / Get-Clipboard`, `powershell New-BurntToastNotification`).

---

## 3. Platform Gap & Architectural Debt Matrix

| Domain | Current macOS State | Linux Gap | Windows Gap | Priority |
| :--- | :--- | :--- | :--- | :--- |
| **System Info & Hardware** | Full sysctl & battery | Need `/proc` & `lspci` parser | Need `psutil` & `wmic` parser | **P0 (Critical)** |
| **Application Control** | LaunchServices & AppleScript | Need `.desktop` file parser & `gtk-launch` / `subprocess` | Need Start Menu search & `start` / `subprocess` | **P0 (Critical)** |
| **Audio Input (Mic)** | PortAudio / CoreAudio | Need PortAudio / ALSA / Pulse / PipeWire | Need PortAudio / WASAPI | **P0 (Critical)** |
| **Audio Output (Speaker)** | PortAudio / CoreAudio | Need PortAudio / ALSA / Pulse / PipeWire | Need PortAudio / WASAPI | **P0 (Critical)** |
| **Local Speech-to-Text** | `faster-whisper` (CTranslate2) | Connect shared CTranslate2 engine | Connect shared CTranslate2 engine | **P0 (Critical)** |
| **Local Text-to-Speech** | `/usr/bin/say` | Implement `LinuxSpeechSynthesisAdapter` (`espeak-ng` / `piper-tts`) | Implement `WindowsSpeechSynthesisAdapter` (Windows SAPI via PowerShell / COM) | **P0 (Critical)** |
| **AI Bridge Runtime** | `libriya_llama.dylib` | Compile & load `libriya_llama.so` | Compile & load `riya_llama.dll` | **P1 (High)** |
| **Clipboard & Notifications** | `pbcopy`, `pbpaste`, UserNotification | `xclip` / `wl-copy`, `notify-send` | PowerShell clipboard, Windows Toast | **P1 (High)** |
| **Window Management** | Quartz window inspection | `wmctrl`, `xdotool` (X11) / Wayland safe fallbacks | Win32 `GetForegroundWindow`, `SetForegroundWindow` | **P1 (High)** |
| **Application Packaging** | `.app` & `.dmg` | Need AppImage & `.deb` | Need Windows Installer (`.exe`/MSIX) | **P1 (High)** |
| **CI / CD Pipeline** | Local macOS runner | Need Ubuntu GitHub Actions runner | Need Windows Server GitHub Actions runner | **P2 (Medium)** |

---

## 4. Phase 5 Implementation Priorities & Execution Roadmap

To maintain 100% stability, Phase 5 will be executed across the requested milestones:

### Milestone 5.1 — Cross-Platform Architecture Audit and Abstraction (This Milestone)
- Perform read-only audit of all platform dependencies, hardcoded paths, and stubbed contracts.
- Define unified hardware profiling, shared path resolution, and decouple macOS-specific utility imports.
- Deliverable: [RIYA_V1_PHASE_5_BASELINE_AUDIT.md](file:///Users/tukaramgore/Projects/RIYA/RIYA_V1_PHASE_5_BASELINE_AUDIT.md) and [RIYA_V1_CROSS_PLATFORM_ARCHITECTURE.md](file:///Users/tukaramgore/Projects/RIYA/RIYA_V1_CROSS_PLATFORM_ARCHITECTURE.md).

### Milestone 5.2 — Linux OS Integration & Audio
- Implement concrete `LinuxPlatform` sub-adapters:
  - `LinuxSystemInformationAdapter`: Parses `/proc/meminfo`, `/proc/cpuinfo`, `/sys/class/power_supply/`, `ip addr`.
  - `LinuxApplicationAdapter`: Scans `/usr/share/applications` and `~/.local/share/applications` for `.desktop` files.
  - `LinuxClipboardAdapter`: Interfaces with `xclip` / `xsel` (X11) and `wl-copy` / `wl-paste` (Wayland).
  - `LinuxNotificationAdapter`: Sends desktop notifications via `notify-send`.
  - `LinuxWindowManagerAdapter`: Window management via `wmctrl` and `xdotool`.
- Implement concrete Linux voice adapters in `core/voice/adapters.py`:
  - `LinuxAudioInputAdapter`: Hardware stream capture via `sounddevice` (ALSA/PulseAudio/PipeWire).
  - `LinuxAudioOutputAdapter`: Chunked interruptible playback via `sounddevice`.
  - `LinuxSpeechRecognitionAdapter`: Connects shared `faster-whisper`.
  - `LinuxSpeechSynthesisAdapter`: Local neural TTS via `piper-tts` or `espeak-ng`.
- Deliverables: `RIYA_V1_LINUX_IMPLEMENTATION_REPORT.md` and `RIYA_V1_CROSS_PLATFORM_VOICE_REPORT.md`.

### Milestone 5.3 — Linux Application Packaging
- Create `apps/linux/`:
  - Standard desktop entry file: `apps/linux/riya.desktop`.
  - Multi-resolution PNG iconography: 48x48, 64x64, 128x128, 256x256, 512x512.
  - AppImage packaging script (`apps/linux/create_appimage.sh`).
  - Debian package generator (`apps/linux/create_deb.py`).
  - Linux installation and uninstallation guides.

### Milestone 5.4 — Windows OS Integration & Audio
- Implement concrete `WindowsPlatform` sub-adapters:
  - `WindowsSystemInformationAdapter`: Gathers CPU, RAM, battery, and network telemetry via `psutil` and `wmic`.
  - `WindowsApplicationAdapter`: Scans Start Menu folders (`%ProgramData%\Microsoft\Windows\Start Menu\Programs`, `%APPDATA%\Microsoft\Windows\Start Menu\Programs`) and handles `.lnk` resolutions.
  - `WindowsClipboardAdapter`: Uses PowerShell `Get-Clipboard` / `Set-Clipboard` or Win32 clipboard APIs.
  - `WindowsNotificationAdapter`: Sends native Windows Toast notifications via PowerShell.
  - `WindowsWindowManagerAdapter`: Window inspection and focus switching via Win32 API.
- Implement concrete Windows voice adapters in `core/voice/adapters.py`:
  - `WindowsAudioInputAdapter`: Low-latency WASAPI stream capture via `sounddevice`.
  - `WindowsAudioOutputAdapter`: Chunked interruptible playback via `sounddevice`.
  - `WindowsSpeechRecognitionAdapter`: Connects shared `faster-whisper`.
  - `WindowsSpeechSynthesisAdapter`: Native Windows SAPI speech synthesis via `System.Speech.Synthesis`.
- Deliverable: `RIYA_V1_WINDOWS_IMPLEMENTATION_REPORT.md`.

### Milestone 5.5 — Windows Application Packaging
- Create `apps/windows/`:
  - Windows multi-resolution icon: `apps/windows/AppIcon.ico`.
  - Windows installer script: Inno Setup script (`apps/windows/setup.iss`) or Python executable builder (`apps/windows/create_installer.py`).
  - Start Menu shortcuts, desktop shortcut, uninstaller registration.
  - Windows installation and uninstallation guides.

### Milestone 5.6 — Cross-Platform AI Hardware Acceleration
- Refactor [core/ai/native/llama_runtime.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/native/llama_runtime.py) to dynamically load `libriya_llama.so` (Linux), `riya_llama.dll` (Windows), or `libriya_llama.dylib` (macOS).
- Support backend enumeration: `auto`, `metal` (Apple Silicon), `cuda` (NVIDIA Linux/Windows), `vulkan` (cross-platform), `directml` (Windows), `cpu` (universal fallback).
- Hardware detector extensions in [core/ai/provisioning/hardware.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/provisioning/hardware.py) for CUDA and Vulkan discovery.
- Deliverable: `RIYA_V1_HARDWARE_COMPATIBILITY_REPORT.md`.

### Milestone 5.7 — Shared UI, Security & User-Data Verification
- Adapt [core/desktop/theme.py](file:///Users/tukaramgore/Projects/RIYA/core/desktop/theme.py) for cross-platform typography (`Segoe UI` on Windows, `Ubuntu`/`DejaVu Sans` on Linux, `-apple-system` on macOS).
- Enforce Windows-safe path traversal rules in [core/tools/filesystem/files.py](file:///Users/tukaramgore/Projects/RIYA/core/tools/filesystem/files.py).
- Enforce strict `0o600` file permissions on Linux SQLite databases.
- Deliverable: `RIYA_V1_CROSS_PLATFORM_SECURITY_REPORT.md`.

### Milestone 5.8 — Full Cross-Platform Regression & Installation Testing
- Expand unit test suite with mock-based cross-platform test cases in `tests/test_cross_platform_parity.py`.
- Verify 100% test pass rate across all 476 existing tests plus new cross-platform tests.
- Deliverables: `RIYA_V1_PHASE_5_TEST_REPORT.md` and `RIYA_V1_PHASE_5_IMPLEMENTATION_REPORT.md`.

---

## 5. Risk Assessment & Safety Commitments

1. **Safety Rule Compliance:**
   - The verified macOS reference implementation will remain completely functional and untouched in its core execution behavior.
   - Metal GPU acceleration, `libriya_llama.dylib`, local Qwen3 weights, and CoreAudio voice streaming will remain intact.
   - No destructive Git commands will be executed.
2. **Platform Verification Honesty:**
   - Platforms will only report `is_supported=True` when running on that actual operating system with verified native adapters.
   - On macOS, Linux and Windows adapters will be verified via unit tests with comprehensive mocks and platform emulation.
3. **Zero Mandatory Cloud Fallback:**
   - Offline-first execution remains absolute across Linux and Windows.

---

## 6. Conclusion & Recommendation

The baseline audit confirms that RIYA possesses a robust foundation in its shared architecture. The cross-platform gaps are well-defined and can be addressed through platform adapters without duplicating AI, memory, or security business logic.

We are ready to proceed with **Milestone 5.1 Architecture Formalization** (`RIYA_V1_CROSS_PLATFORM_ARCHITECTURE.md`) upon approval.

