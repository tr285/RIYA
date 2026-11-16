# RIYA v1.0 — MILESTONE 5.3 BASELINE AUDIT
## Linux Packaging, Distribution & Installation Pre-Implementation Assessment

**Developer:** Tukaram  
**Project:** RIYA — Universal Offline-First Personal AI Assistant  
**Target Platform:** Linux (Ubuntu 24.04 LTS reference target, Debian/Ubuntu ecosystem, portable AppImage)  
**Architecture:** Shared Core + Native Platform Adapters  
**Date:** November 15, 2026  
**Status:** AUDIT COMPLETED — BASELINE VERIFIED  

---

## 1. Executive Summary

Milestone 5.3 addresses the packaging, distribution, and installation architecture for RIYA on Linux. Following the successful implementation and verification of Linux OS adapters and audio pipelines in Milestone 5.2, RIYA must now be converted into an installable, standalone Linux desktop application across two standard distribution formats:
1. **DEB package (`.deb`):** Native Debian/Ubuntu package for system-wide installation via `apt` / `dpkg`.
2. **AppImage (`.AppImage`):** Portable, self-contained single-file Linux application executable without root privileges.

Both distribution targets must provide desktop launcher integration, hicolor application icons, permission enforcement, local offline AI and voice execution, and clean first-launch model provisioning. Crucially, the multi-gigabyte GGUF neural model must **NOT** be embedded in the installer payloads; model provisioning remains an explicit, user-consented, checksum-verified, and resumable operation stored in user data.

---

## 2. Repository & Branch State

### 2.1 Git Status & Branch Identification
- **Active Branch:** `main` (tracked against `origin/main`).
- **Working Tree State:** Working directory contains uncommitted modifications from prior verified milestones (Milestones 5.1 & 5.2) and previous phases.
- **Preservation Policy:** In strict adherence to global safety rules, no destructive operations (`git reset`, `git clean`, `git checkout .`) will be executed. All uncommitted user work is preserved intact.

### 2.2 Repository Directory Inventory
- `core/`: Application business logic, agent orchestration, security manager, memory services, platform adapters, voice pipelines, desktop UI.
- `apps/macos/`: Reference macOS packaging scripts (`create_app_bundle.py`, `create_dmg.py`, `generate_icon.py`, `entitlements.plist`).
- `apps/linux/`: Target directory for Linux desktop application assets and packaging specifications.
- `models/`: Local storage for neural weights (currently houses `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` ~2.5 GB).
- `data/`: Local SQLite user database (`data/riya.db`) and historical database backups.
- `dist/`: Target directory for generated distributables (`RIYA.app`, `RIYA-v1.0.0-AppleSilicon.dmg`).
- `tests/`: Automated test suite covering platform adapters, voice, security, agent, memory, and UI.

---

## 3. Architecture & Dependency Audit

### 3.1 Project Definition & Version Authority (`pyproject.toml`)
- **Package Name:** `riya`
- **Current Version:** `1.0.0`
- **Author:** Tukaram (`developer@riya.ai`)
- **Python Compatibility:** `>=3.10` (Ubuntu 24.04 LTS defaults to Python 3.12).
- **Declared Entry Points:**
  - `riya = "core.main:main"` (Interactive CLI, setup wizard, diagnostics, and voice mode)
  - `riya-ui = "core.main:main_ui"` (Desktop conversation GUI)

### 3.2 Runtime & System Dependencies
| Dependency | Declared Specification | Linux System Equivalent / Package | Purpose in RIYA |
| :--- | :--- | :--- | :--- |
| **Python** | `>=3.10` | `python3` (>=3.10), `python3-venv` | Core language runtime |
| **Tkinter** | Standard Library | `python3-tk` | Modernized CustomTkinter/Tkinter desktop GUI |
| **NumPy** | `>=1.24` | `python3-numpy` / pip wheel | Numerical audio frame buffering, RMS, tensor math |
| **Sentence-Transformers**| `>=2.2` | pip wheel | Semantic embedding search and memory vectorization |
| **SoundDevice** | `>=0.4` | `libportaudio2` | Real-time audio stream capture and playback |
| **SoundFile** | `>=0.12` | `libsndfile1` | Audio reading and writing (WAV generation) |
| **Faster-Whisper** | `>=1.0` | `libgomp1` | CTranslate2-based local offline speech recognition |
| **ONNX Runtime** | `>=1.16` | `libgomp1` | Streaming Silero VAD voice activity detection |
| **Psutil** | `>=5.9` | `python3-psutil` | System telemetry, CPU/RAM stats, process management |
| **Pillow** | `>=9.0` | `python3-pil` | Image rendering, desktop icons, multimodal inputs |
| **Chardet** | `>=5.0` | pip wheel | Character encoding detection for file ingestion |
| **Audio Sound Server** | System Service | `pipewire` / `pulseaudio` / `libasound2` | Desktop audio routing and hardware arbitration |
| **Speech Synthesizer** | System Binary | `espeak-ng` / `piper` / `speech-dispatcher` | Offline text-to-speech voice generation |
| **Clipboard Support** | System Binary | `wl-clipboard` (Wayland) / `xclip` (X11) | Desktop clipboard synchronization and scrubbing |
| **Notifications** | System Binary | `libnotify-bin` (`notify-send`) | Freedesktop desktop notifications |

---

## 4. Inspection of Existing Packaging Logic & Assets

### 4.1 Lessons from macOS Packaging (`apps/macos/`)
The macOS packaging implementation established three critical principles that directly apply to Linux packaging:
1. **Clean Code Isolation:** The application binaries and Python packages reside in an immutable directory (`/opt/riya/` on Linux, `RIYA.app/Contents/Resources/app` on macOS).
2. **Strict User State Separation:** All mutable data (databases, memory vectors, logs, downloaded GGUF models) are routed through standardized path resolvers ([core/config/paths.py](file:///Users/tukaramgore/Projects/RIYA/core/config/paths.py)) to XDG directories:
   - Data & Databases: `$XDG_DATA_HOME/riya` (defaults to `~/.local/share/riya`)
   - Downloaded Models: `$XDG_DATA_HOME/riya/models` or `$XDG_CACHE_HOME/riya/models`
   - Logs: `$XDG_DATA_HOME/riya/logs` (defaults to `~/.local/share/riya/logs`)
   - Temporary Audio: `$XDG_CACHE_HOME/riya` (defaults to `~/.cache/riya`)
3. **Executable Wrapper Script:** A dedicated launcher resolves the bundled environment, sets environment flags (`PYTHONUNBUFFERED=1`, `PYTHONPATH`), verifies hardware/library prerequisites, and starts the GUI cleanly.

### 4.2 Application Icon Generation
- macOS uses `apps/macos/generate_icon.py` (Pillow-based rendering of the deep slate navy squircle with cyan/blue orbital rings).
- For Linux, Freedesktop icon theme standards mandate PNG icons across the standard hicolor icon directory hierarchy:
  - 16x16, 32x32, 48x48, 64x64, 128x128, 256x256, 512x512
  - Installed to `/usr/share/icons/hicolor/<size>x<size>/apps/riya.png`
- A dedicated Linux icon generation utility will be authored to render and export all standard dimensions automatically.

### 4.3 Model Provisioning Architecture
- As verified in [core/ai/provisioning/catalog.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/provisioning/catalog.py) and [core/ai/provisioning/downloader.py](file:///Users/tukaramgore/Projects/RIYA/core/ai/provisioning/downloader.py):
  - Model: `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2.49 GB, SHA-256 `8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc`).
  - Pre-flight checks verify available disk space before starting.
  - Downloads use HTTP Range resumption to prevent starting over on connection dropouts.
  - SHA-256 integrity verification is strictly enforced before moving `.part` files into the active models directory.
  - Model files are never bundled into `.deb` or `.AppImage` packages.

---

## 5. Linux Packaging Architecture Plan

### 5.1 Directory Layout
```text
packaging/
└── linux/
    ├── debian/
    │   ├── control               # Package metadata, dependencies, architecture, maintainer
    │   ├── rules                 # Build rules
    │   ├── changelog             # Debian package changelog
    │   ├── copyright             # Licensing information
    │   ├── postinst              # Post-installation script (icon cache update, symlinks)
    │   ├── prerm                 # Pre-removal script
    │   └── postrm                # Post-removal script (preserves user data!)
    ├── appimage/
    │   ├── AppDir/               # AppImage staging directory
    │   ├── AppRun                # Primary executable entry point
    │   └── riya.desktop          # Freedesktop desktop launcher configuration
    ├── icons/                    # Multi-resolution PNG icon generator and cache
    ├── build_deb.py              # Pure-Python / system tool DEB package builder
    ├── build_appimage.py         # AppImage bundler and AppDir assembler
    └── validate_package.py       # Package integrity, structure, and permission validator
```

### 5.2 Filesystem Installation Targets (DEB Package)
| Target Linux Path | Contents | Permissions |
| :--- | :--- | :--- |
| `/opt/riya/` | Application codebase, bundled runtime modules, resources | `0755` (dirs), `0644` (files) |
| `/opt/riya/bin/riya` | Executable launcher wrapper | `0755` |
| `/usr/bin/riya` | Symlink or wrapper pointing to `/opt/riya/bin/riya` | `0755` |
| `/usr/share/applications/riya.desktop` | Freedesktop desktop launcher entry | `0644` |
| `/usr/share/icons/hicolor/*/apps/riya.png` | Application icons at 16, 32, 48, 64, 128, 256, 512 | `0644` |

### 5.3 AppImage Targets & Layout
- Portable `dist/RIYA-1.0.0-x86_64.AppImage` (and `dist/RIYA-1.0.0-aarch64.AppImage` where applicable).
- Self-contained `AppDir/` containing `AppRun`, `riya.desktop`, `riya.png`, and Python application code.
- Uses standard `$XDG_DATA_HOME/riya` for all user state, ensuring zero state pollution inside the mounted read-only squashfs image.

---

## 6. Safety, Data Preservation & Security Policies

1. **Uninstallation Safety:**
   - The Debian `postrm` script must **NEVER** run `rm -rf` on `$HOME/.local/share/riya`, `$HOME/.cache/riya`, or `$HOME/.config/riya`.
   - Purging the package (`apt purge riya`) should only remove system files in `/opt/riya/` and `/usr/share/`. User models, memories, and databases remain untouched.
2. **Root Privilege Isolation:**
   - RIYA must refuse to run as `root` for normal desktop operations to prevent privilege escalation or accidental corruption of system directories.
3. **Command Execution Safeguards:**
   - All wrapper scripts and sub-process calls enforce argument arrays (`shell=False`).
4. **Offline AI Guarantee:**
   - The packaged application contains no cloud AI telemetry, analytics hooks, or automatic remote connection logic.

---

## 7. Test Baseline & Verification Targets

- **Pre-Milestone 5.3 Baseline:** 508 passing tests.
- **Milestone 5.3 Target Test Suites:**
  - `tests/test_linux_packaging.py`: Package metadata, DEB control file syntax, structure, launcher paths.
  - `tests/test_linux_installation.py`: Installation simulations, directory placement, icon verification.
  - `tests/test_linux_appimage.py`: AppDir layout, AppRun script generation, environment variable exports.
  - `tests/test_linux_data_preservation.py`: Verifies upgrade and uninstall procedures preserve user database, models, and settings.
- **Regression Quality Gate:** All 508 baseline tests + all new packaging tests must pass with 100% pass rate.

