# RIYA v0.5 — Phase 6 — Milestone 6.2 Implementation Report
## Cross-Platform OS Abstraction Layer & Native macOS Integration

**Project:** RIYA — Offline-First Universal Personal AI Assistant  
**Developer:** Tukaram  
**Phase:** 6 — Universal Personal Assistant & Operating-System Integration  
**Milestone:** 6.2 — Cross-Platform OS Abstraction Layer & Native macOS Integration  
**Platform:** macOS (Apple Silicon `arm64`, primary supported host) + Architecture-Level Contracts for Windows & Linux  
**Database Schema:** v5 (preserved; zero destructive migrations; live `data/riya.db` SHA-256 unchanged)

---

## 1. Architecture Summary

Milestone 6.2 introduces a clean, capability-driven, permission-aware operating-system integration layer in `core/device/` and connects it end-to-end through RIYA's security boundary (`PermissionManager`, `SecurityManager`, `ConfirmationManager`), `ToolRegistry`, `ToolExecutor`, deterministic `IntentDetector`/`CommandExecutor`, `RIYAAssistant`, and the Milestone 6.1 unified `VoiceUIController`/Tkinter workspace.

### Core Architectural Principles Enforced
1. **Capability-Based Platform Abstraction (`core/device/`):**
   - Every operating-system adapter implements `PlatformInterface` (`PlatformAdapter`) and exposes six domain sub-adapters:
     - `SystemInformationAdapter` (`system_info`)
     - `ApplicationAdapter` (`applications`)
     - `FilesystemAdapter` (`filesystem`)
     - `ClipboardAdapter` (`clipboard`)
     - `NotificationAdapter` (`notifications`)
     - `WindowManagerAdapter` (`window_manager`)
   - Every capability is described by a structured `CapabilityDescriptor` (`capability_id`, `category`, `supported`, `permission_key`, `requires_confirmation`, `description`, `reason_if_unsupported`) aggregated inside `PlatformCapabilities`.
   - Every adapter operation returns a structured `PlatformOperationResult` (`status`, `supported`, `capability_id`, `platform_name`, `data`, `message`, `reason`, `outcome_category`, `verified`, `permission_key`) so callers and the Milestone 6.1 UI can distinguish `successful`, `denied`, `unsupported`, and `failed` outcomes without ambiguity.
2. **Real Native macOS Integration (`core/device/macos.py` & `core/tools/system/macos.py`):**
   - Full Apple Silicon (`arm64`) and Intel (`x86_64`) macOS support using allowlisted absolute system binaries (`/usr/bin/sw_vers`, `/usr/bin/uname`, `/usr/bin/pmset`, `/usr/bin/open`, `/usr/bin/osascript`, `/usr/bin/pbcopy`, `/usr/bin/pbpaste`, `/usr/bin/pgrep`, `/usr/sbin/system_profiler`, `/usr/bin/df`, `/sbin/ifconfig`), `plistlib` `.app` bundle inspection, `psutil`, and descriptor-safe (`O_NOFOLLOW`) filesystem inspection.
   - Zero `shell=True` calls, zero arbitrary shell commands, and zero dynamic AppleScript code interpolation (all dynamic arguments are passed via `--` `argv`).
3. **Honest Non-macOS Contracts (`core/device/windows.py` & `core/device/linux.py`):**
   - `WindowsPlatform` and `LinuxPlatform` implement the full `PlatformAdapter` contract and return structured `PlatformOperationResult(status="unsupported", supported=False, outcome_category="unsupported")` (or raise `UnsupportedPlatformCapabilityError` when `raise_on_unsupported=True`) with clear platform-specific explanations instead of fake integrations or silent no-ops.

---

## 2. Platform Adapter Hierarchy

```
PlatformInterface (ABC — core/device/platform.py)
 └── PlatformAdapter (Capability-enforcing base adapter — core/device/platform.py)
      ├── MacOSPlatform / MacOSDevice (core/device/macos.py)
      │    ├── MacOSSystemInformationAdapter
      │    ├── MacOSApplicationAdapter
      │    ├── MacOSFilesystemAdapter
      │    ├── MacOSClipboardAdapter
      │    ├── MacOSNotificationAdapter
      │    └── MacOSWindowManagerAdapter
      ├── WindowsPlatform (core/device/windows.py)
      │    ├── _UnsupportedSystemInformationAdapter("windows")
      │    ├── _UnsupportedApplicationAdapter("windows")
      │    ├── _UnsupportedFilesystemAdapter("windows")
      │    ├── _UnsupportedClipboardAdapter("windows")
      │    ├── _UnsupportedNotificationAdapter("windows")
      │    └── _UnsupportedWindowManagerAdapter("windows")
      └── LinuxPlatform (core/device/linux.py)
           ├── _UnsupportedSystemInformationAdapter("linux")
           ├── _UnsupportedApplicationAdapter("linux")
           ├── _UnsupportedFilesystemAdapter("linux")
           ├── _UnsupportedClipboardAdapter("linux")
           ├── _UnsupportedNotificationAdapter("linux")
           └── _UnsupportedWindowManagerAdapter("linux")
```

- **Runtime Registry (`PlatformRegistry` & `DEFAULT_PLATFORM_REGISTRY`):**
  - Detects host OS (`darwin` -> `macos`, `win32`/`cygwin` -> `windows`, `linux` -> `linux`) via `detect_platform_name()`.
  - Supports dependency injection and deterministic testing of `macos`, `windows`, and `linux` adapters on any host via `get_platform_adapter("windows")` / `get_platform_adapter("linux")` or `Agent(platform=WindowsPlatform())`.

---

## 3. Capability Matrix (`macOS`, `Windows`, `Linux`)

| Capability ID | Category | macOS (`darwin` / Apple Silicon) | Windows (`win32`) | Linux (`linux`) | Permission Key | Confirmation Required |
|---|---|---|---|---|---|---|
| `system.info` | `system` | **Supported** (`sw_vers`, `uname -m`, `psutil`, CPU/RAM/storage/network) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.info` | No (`ALLOW` default) |
| `system.storage` | `system` | **Supported** (`shutil.disk_usage` + allowed-root validation) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.info` | No (`ALLOW` default) |
| `system.battery` | `system` | **Supported** (`/usr/bin/pmset -g batt` structured parser) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.info` | No (`ALLOW` default) |
| `system.network` | `system` | **Supported** (local `psutil` interfaces + offline-first state) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.info` | No (`ALLOW` default) |
| `system.hardware` | `system` | **Supported** (`/usr/sbin/system_profiler` + `psutil`) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.hardware` | No (`ALLOW` default) |
| `system.media_volume` | `system` | **Supported** (`/usr/bin/osascript` bounded `0..100`) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.media` | No (`ALLOW` default) |
| `system.brightness` | `system` | **Supported** (`/usr/bin/osascript` Display Services) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.brightness` | No (`ALLOW` default) |
| `system.lock_screen` | `system` | **Supported** (`/usr/bin/osascript` lock keystroke) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.lock` | **Yes** (`ASK` default) |
| `applications.discover` | `applications` | **Supported** (`Info.plist` bundle scan across permitted `.app` dirs) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.info` | No (`ALLOW` default) |
| `applications.running` | `applications` | **Supported** (`/usr/bin/pgrep -x` + `System Events` foreground list) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.info` | No (`ALLOW` default) |
| `applications.launch` | `applications` | **Supported** (`/usr/bin/open -a` + post-launch verification) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.open_app` | **Yes** (`DENY` default + confirmation) |
| `applications.focus` | `applications` | **Supported** (`System Events` frontmost activation + verification) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `desktop.automate` | **Yes** (`DENY` default + confirmation) |
| `filesystem.read` | `filesystem` | **Supported** (`O_NOFOLLOW` descriptor-bound read in allowed roots) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `filesystem.read` | No (`ALLOW` default) |
| `filesystem.write` | `filesystem` | **Supported** (`O_NOFOLLOW` descriptor-bound write in allowed roots) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `filesystem.write` | **Yes** (`ASK` default + confirmation) |
| `filesystem.directory` | `filesystem` | **Supported** (`list_files`, `find_files`, `create_folder`) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `filesystem.directory` | `create_folder` requires confirmation |
| `filesystem.metadata` | `filesystem` | **Supported** (`inspect_file_metadata` via `O_NOFOLLOW` + `lstat`) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `filesystem.read` | No (`ALLOW` default) |
| `clipboard.read` | `clipboard` | **Supported** (`/usr/bin/pbpaste` + automatic secret redaction) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.clipboard` | No (`DENY` default; explicit grant required) |
| `clipboard.write` | `clipboard` | **Supported** (`/usr/bin/pbcopy` + length cap + secret redaction) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.clipboard` | **Yes** (`DENY` default + confirmation) |
| `notifications.send` | `notifications` | **Supported** (`display notification` via parameterized `argv`) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `system.notification` | No (`ASK` default; explicit grant required) |
| `window_manager.active_window` | `window_manager` | **Supported** (`System Events` frontmost app + window title) | `Unsupported` (explicit structured report) | `Unsupported` (explicit structured report) | `desktop.automate` | No (`DENY` default; explicit grant required) |

---

## 4. New and Modified Files

### New Files Created
- `core/device/platform.py` — Cross-platform adapter interfaces (`PlatformInterface`, `PlatformAdapter`, `SystemInformationAdapter`, `ApplicationAdapter`, `FilesystemAdapter`, `ClipboardAdapter`, `NotificationAdapter`, `WindowManagerAdapter`), capability descriptors (`CapabilityDescriptor`, `PlatformCapabilities`, `PlatformInfo`, `PlatformOperationResult`), `UnsupportedPlatformCapabilityError`, and `PlatformRegistry`.
- `core/device/windows.py` — Honest architecture-level `WindowsPlatform` adapter reporting structured unsupported capability results without fake commands.
- `core/device/linux.py` — Honest architecture-level `LinuxPlatform` adapter reporting structured unsupported capability results without fake commands.
- `tests/test_phase6_milestone_6_2_os_integration.py` — Dedicated Milestone 6.2 verification suite covering all 17 requirement areas.
- `RIYA_V0_5_PHASE_6_MILESTONE_6_2_REPORT.md` — This implementation report.

### Existing Files Updated (with `.before_v05_phase6_m62` backups preserved)
- `core/device/macos.py` — Upgraded `MacOSDevice` into `MacOSPlatform(PlatformAdapter)` with full sub-adapters (`system_info`, `applications`, `filesystem`, `clipboard`, `notifications`, `window_manager`), capability descriptors, and backward-compatible `execute(action, **kwargs)` routing.
- `core/device/__init__.py` — Exported the new platform abstraction classes, `MacOSPlatform`, `WindowsPlatform`, `LinuxPlatform`, and `get_platform_adapter`.
- `core/tools/system/macos.py` — Added structured and human-readable macOS integrations (`get_detailed_system_info`, `get_storage_info_structured`, `storage_status`, `get_battery_status_structured`, `get_network_status_structured`, `network_status`, `discover_installed_applications`, `is_application_running`, `resolve_application_bundle`, `check_application`, `launch_and_verify_application`, `clipboard_read_structured`, `clipboard_read`, `clipboard_write_structured`, `clipboard_write`, `send_notification_structured`, `send_notification`, `get_active_window_structured`, `active_window_info`, `_looks_like_secret`).
- `core/tools/filesystem/files.py` — Added `inspect_file_metadata_structured` and `inspect_file_metadata` with unfollowed symlink blocking (`is_symlink()` + `O_NOFOLLOW` + `follow_symlinks=False`) and explicit macOS TCC privacy error reporting (`EACCES`/`EPERM`).
- `core/security/permissions.py` — Added `system.clipboard` (`DENY` default, `sensitive=True`) and `system.notification` (`ASK` default) permission specs; mapped new Milestone 6.2 tools (`storage_status`, `network_status`, `check_application`, `inspect_file_metadata`, `clipboard_read`, `clipboard_write`, `send_notification`, `active_window_info`, `platform_capabilities`) into `PERMISSION_SPECS`, `_TOOL_TO_PRIMARY_KEY`, and `_SENSITIVE_TOOLS`.
- `core/security/manager.py` — Added read-only tools (`storage_status`, `network_status`, `check_application`, `inspect_file_metadata`, `clipboard_read`, `send_notification`, `active_window_info`, `platform_capabilities`) to `SAFE_ACTIONS` and `clipboard_write` to `CONFIRMATION_ACTIONS`.
- `core/agent/tool_registry.py` — Registered Milestone 6.2 tools with strict parameter schemas, timeout budgets, and post-execution verification hooks (`open_application`, `clipboard_write`).
- `core/agent/tool_executor.py` — Added structured handling of `UnsupportedPlatformCapabilityError` (`status="unsupported"`, `unsupported=True`, `outcome_category="unsupported"`).
- `core/agent/agent.py` — Wired `self.platform = platform or get_platform_adapter()` onto `Agent` with `platform_info()` and `platform_capabilities()` helpers.
- `core/agent/intent.py` & `core/agent/executor.py` — Added deterministic offline routing for macOS version, storage space, network status, installed/running application checks, file metadata inspection, clipboard read/write, desktop notifications, active window inspection, and platform capability queries.
- `core/agent/planner.py` — Added `_OS_EXTENSION_TOOLS` and `_OS_EXTENSION_CUE_RE` intent gating so new zero-argument OS inspection tools only enter semantic top-$k$ planning when the user's request mentions OS/device/clipboard/notification/window/platform cues, preserving 100% of Phase 1–5 conversational Qwen3 plans.
- `core/agent/assistant.py` — Added `unsupported` outcome categorization, secret redaction in `_format_action_preview` and `_record_tool_outcome` for `clipboard_write`, and identity-aware `recent_tool_results` / `last_error_info`.
- `core/voice/orchestrator.py` & `core/voice/ui.py` — Synchronized assistant identity on `switch_identity`, exposed `platform_info()` and `platform_capabilities()` on `VoiceUIController`, and surfaced platform capability badges and `"Unsupported"` outcome styling in the Tkinter UI.

---

## 5. Permission Mappings & Security Controls

| Tool Name | Primary Permission Key | Default Decision | Sensitive (`explicit_user_approval` for persistent grant) | Confirmation Token Required (`ConfirmationManager`) |
|---|---|---|---|---|
| `system_information` | `system.info` | `ALLOW` | No | No |
| `storage_status` | `system.info` | `ALLOW` | No | No |
| `battery_status` | `system.info` | `ALLOW` | No | No |
| `network_status` | `system.info` | `ALLOW` | No | No |
| `check_application` | `system.info` | `ALLOW` | No | No |
| `platform_capabilities` | `system.info` | `ALLOW` | No | No |
| `inspect_file_metadata` | `filesystem.read` | `ALLOW` (within user's allowed roots) | No | No |
| `open_application` | `system.open_app` | `DENY` | **Yes** | **Yes** |
| `clipboard_read` | `system.clipboard` | `DENY` | **Yes** | No (gated by `system.clipboard` grant) |
| `clipboard_write` | `system.clipboard` | `DENY` | **Yes** | **Yes** |
| `send_notification` | `system.notification` | `ASK` | No | No (gated by `system.notification` grant) |
| `active_window_info` | `desktop.automate` | `DENY` | **Yes** | No (gated by `desktop.automate` grant) |
| `focus_application` | `desktop.automate` | `DENY` | **Yes** | **Yes** |

### Defense-in-Depth Security Controls
1. **No Arbitrary Shell Execution:** All macOS interactions execute via `_run_executable` with `shell=False` against `_ALLOWED_EXECUTABLES` (`frozenset` of absolute paths in `/usr/bin`, `/usr/sbin`, `/sbin`) and bounded timeouts (`3.0s–5.0s`).
2. **Parameterized AppleScript Only:** `send_notification`, `focus_application`, `active_window_info`, and `running_applications` pass user strings exclusively via `osascript ... -- <argv>` and never interpolate untrusted text into AppleScript source lines.
3. **Clipboard Secret Protection:** `_looks_like_secret` detects API keys (`sk-...`, `ghp_...`, `AIza...`, `AKIA...`), JWTs, PEM private keys, Bearer tokens, and `password`/`secret`/`token`/`api_key` assignments. Secret-like values are redacted in `clipboard_read` output (`[REDACTED: clipboard contains sensitive/secret-like content ...]`), in confirmation previews (`_format_action_preview`), and in `_record_tool_outcome` audit trails.
4. **Symlink & TCC Protection:** `inspect_file_metadata_structured` rejects symlinks both before path resolution (covering intra-workspace symlinks) and at the file-descriptor level (`O_NOFOLLOW` + `os.stat(..., follow_symlinks=False)`), and translates `EACCES`/`EPERM` into clear macOS TCC privacy guidance.

---

## 6. Real macOS Integrations Verified on Apple Silicon (`arm64`)

1. **System Telemetry (`get_detailed_system_info`, `storage_status`, `battery_status`, `network_status`):**
   - Verified real macOS version (`sw_vers`), Apple Silicon architecture (`arm64`), logical/physical CPU core counts, RAM availability, root filesystem capacity (`/`), battery percentage/power source (`pmset -g batt`), and local network interface state (`lo0`/`en0`) + RIYA offline-first status.
2. **Application Discovery, Running Check & Launch Verification (`discover_installed_applications`, `check_application`, `launch_and_verify_application`):**
   - Verified real `.app` bundle discovery in `/System/Applications` (`Calculator.app` -> `com.apple.calculator`), `Info.plist` parsing without following symlinks, running-state check via `pgrep -x`, and verified application launch + post-launch verification hook.
3. **Safe Filesystem Metadata (`inspect_file_metadata_structured`, `inspect_file_metadata`):**
   - Verified real file/directory metadata (`size_bytes`, `mode_octal`, `modified_at`, `created_at`, `readable`, `writable`, `allowed_root`), intra-workspace and out-of-workspace symlink blocking, cross-user path denial, and macOS TCC `EPERM` handling.
4. **Clipboard, Notifications & Active Window Metadata (`clipboard_read`, `clipboard_write`, `send_notification`, `active_window_info`):**
   - Verified default-deny enforcement on `system.clipboard` and `desktop.automate`, ask-by-default on `system.notification`, secret redaction on `clipboard_read` and `clipboard_write`, length cap (`MAX_CLIPBOARD_WRITE_CHARS = 10_000`), confirmation token requirement on `clipboard_write`, parameterized notification delivery, and graceful Accessibility TCC permission denial handling on `active_window_info`.

---

## 7. Unsupported Platform Behavior (`WindowsPlatform` & `LinuxPlatform`)

- `WindowsPlatform` (`core/device/windows.py`) and `LinuxPlatform` (`core/device/linux.py`) implement every method of `PlatformInterface` and all six domain sub-adapters.
- Every capability descriptor in `WindowsPlatform.capabilities()` and `LinuxPlatform.capabilities()` reports `supported=False` with a concrete `reason_if_unsupported`.
- Calling any operation on `WindowsPlatform` or `LinuxPlatform` returns `PlatformOperationResult(status="unsupported", supported=False, outcome_category="unsupported")` (or raises `UnsupportedPlatformCapabilityError` when invoked through `execute(...)`, which `ToolExecutor` converts into `status="unsupported"`, `unsupported=True` and `RIYAAssistant` categorizes as `outcome_category="unsupported"`).

---

## 8. Test & Regression Results

- **Shell Launcher Syntax Check:** `bash -n "apps/macos/Launch RIYA.command"` — **PASSED** (`exit code 0`).
- **Python Bytecode Compilation:** `python3 -m compileall -q core tests apps` — **PASSED** (`exit code 0`).
- **Milestone 6.2 Dedicated Verification Suite:** `pytest -q tests/test_phase6_milestone_6_2_os_integration.py` — **7 passed in 14.69s** (`0 failed, 0 skipped`).
- **Full Regression Suite (Phases 1–5 + Milestone 6.1 + Milestone 6.2):** **234 passed in 250.86s, 0 failed, 0 skipped** (`227` baseline tests + `7` Milestone 6.2 tests).
- **Live Database Integrity (`data/riya.db`):** SHA-256 checksum matches `data/riya.db.before_v05_phase6_m62` (`11a364d9ea4114d51b43ead1aa59a0bbe17832c8387851c402148a411dd90813`).

---

## 9. Known Limitations & Readiness for Milestone 6.3

### Known Limitations
- Full OS adapter execution is currently implemented only for **macOS** (`darwin`); **Windows** and **Linux** adapters intentionally return structured `unsupported` outcomes until native platform implementations are scheduled in a future milestone.
- Active window title inspection (`active_window_info`) and application focus (`focus_application`) depend on macOS Accessibility (`System Events`) TCC approval granted to the host terminal/app; when TCC is not granted, RIYA reports a clear, non-crashing permission message.

### Readiness for Milestone 6.3
- All Milestone 6.2 architecture, security, macOS integration, UI inspection, and regression requirements are complete and verified.
- The codebase is clean, backed up, and ready to proceed to **Milestone 6.3** upon user instruction.
