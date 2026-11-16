# RIYA v0.5 — Phase 6 — Milestone 6.4 Implementation Report
## Intelligent Application Control & Desktop Automation

**Project:** RIYA — Offline-First Universal Personal AI Assistant  
**Developer:** Tukaram  
**Phase:** 6 — Cross-Platform Intelligence & Native Device Integration  
**Milestone:** 6.4 — Intelligent Application Control & Desktop Automation  
**Previous Baseline:** Milestone 6.3 (`330 passed, 0 failed, 0 skipped`)  
**New Test Baseline:** Milestone 6.4 (`369 passed, 0 failed, 0 skipped`)  
**Development Platform:** macOS on Apple Silicon (M1), with Windows and Linux cross-platform contracts  
**Execution Environment:** Local Offline-First AI (LocalLlamaRuntime + Qwen3 GGUF)

---

## 1. Executive Summary & Objectives

Milestone 6.4 transitions RIYA from basic application launching to an **intelligent desktop automation assistant** capable of discovering, inspecting, searching, launching, monitoring, focusing, safely switching, gracefully terminating, and navigating local applications and web URLs.

All interactions enforce RIYA's core tenets:
- **Offline-First & Local-Only:** Native macOS Darwin APIs, `plistlib`, and `psutil` operate completely offline.
- **Safety First & Non-Destructive:** Force-kills (`kill -9`, `SIGKILL`) are strictly forbidden and default-denied; applications are terminated only via graceful AppleScript signals allowing users to save work.
- **System Shields:** 28+ critical system processes (`launchd`, `kernel_task`, `WindowServer`, `loginwindow`, `Dock`, `Finder`, system daemons) and RIYA's own process tree are permanently protected from termination.
- **Strict URL Scheme Allowlisting:** Only `http://` and `https://` URLs are permitted; malicious schemes (`javascript:`, `file:`, `data:`, `applescript:`, `blob:`) and shell metacharacters are rejected.
- **Truthful Cross-Platform Reporting:** `WindowsPlatform` and `LinuxPlatform` report structured `PlatformOperationResult(status="unsupported")` rather than faking execution or crashing.
- **Zero Regression:** All 330 pre-existing regression tests remain fully passing, with 39 new comprehensive tests bringing the total to **369 passed**.

---

## 2. Pre-Implementation Audit & Gap Analysis

The Milestone 6.4 baseline audit (`RIYA_V0_5_PHASE_6_MILESTONE_6_4_BASELINE_AUDIT.md`) identified that while Milestone 6.2 provided the foundational `PlatformAdapter` structure and `open_application` / `close_application` tools:
1. Application search was strictly exact-match or simple substring, unable to rank relevance across display names, bundle identifiers, and executables.
2. Application details lacked deep bundle inspection, version extraction, and disk footprint analysis.
3. Process inspection was limited to boolean `pgrep` checks without PID, CPU %, RSS memory, or process status telemetry.
4. Process termination lacked guards preventing accidental termination of critical macOS daemons or the assistant itself.
5. Browser URL opening was fragmented, lacking URL validation, scheme restriction, and shell character filtering.
6. The permission model lacked granular `application.*` permission keys.

---

## 3. Architecture & Key Additions

### 3.1 macOS Native Automation (`core/tools/system/macos.py`)
- **`search_applications(query, search_dirs, max_results)`**:
  - Multi-tier weighted relevance scoring:
    - Exact match on display name or bundle name: Score 100
    - Prefix match on display name or bundle name: Score 80
    - Substring match on name, bundle name, or bundle ID: Score 60
    - Word-boundary token matching: Score 40
  - Sorted by score descending, then alphabetically.
- **`get_application_details(app_name, search_dirs)`**:
  - Resolves application bundle safely without following symlinks.
  - Extracts `CFBundleDisplayName`, `CFBundleName`, `CFBundleIdentifier`, `CFBundleShortVersionString`, `CFBundleVersion`, `CFBundleExecutable`.
  - Walks the `.app` bundle non-recursively across symlinks to calculate exact bundle size in bytes, megabytes, and file count.
- **`get_running_applications_structured()`**:
  - Combines AppleScript non-background running application listing with `psutil` process iteration.
  - Extracts PID, RSS memory in MB, CPU %, process status, and creation time.
- **`get_application_resources(app_name)`**:
  - Target-specific resource inspection.
  - Returns process count, individual process PIDs, total RSS MB, CPU %, and uptime in seconds.
  - Returns `status="not_running"` gracefully when the application is not active.
- **`terminate_application(app_name, search_dirs, timeout)`**:
  - **Protected Process Guard:** Refuses requests targeting any process in `_PROTECTED_PROCESSES` (`launchd`, `kernel_task`, `WindowServer`, `loginwindow`, `Dock`, `Finder`, `cfprefsd`, `distnoted`, `logd`, `securityd`, `trustd`, `coreservicesd`, `mds`, `fseventsd`, `syslogd`, `diskarbitrationd`, `configd`, `notifyd`, `lsd`, `powerd`, `coreaudiod`, `airportd`, `mediaremoted`, `bluetoothd`, `hidd`).
  - **Self-Termination Guard:** Checks PID and parent PID against `os.getpid()` / `os.getppid()` to prevent terminating RIYA.
  - **Graceful Quit:** Sends AppleScript `tell application <app> to quit` with a 10-cycle verification check.
  - Returns structured outcome: `success`, `not_running`, `denied`, `still_running`, or `failed`.
- **`validate_url(url)` & `open_browser_url(url, browser)`**:
  - Parses scheme and enforces allowlist: `http` and `https` only.
  - Rejects malicious schemes: `file:`, `javascript:`, `data:`, `applescript:`, `blob:`, etc.
  - Rejects dangerous shell metacharacters: `` ` ``, `'`, `"`, `;`, `|`, `&`, `$`, `(`, `)`, `{`, `}`, `<`, `>`, `\`, `\x00`.
  - Enforces length ceiling of 2048 characters.
  - Rejects wildcard targets (`0.0.0.0`, `[::]`).
  - Safe execution via `/usr/bin/open` with argument arrays (`-a <browser>` if specified, `shell=False`).

### 3.2 Platform Abstraction & Cross-Platform Parity
- **`ApplicationAdapter` (`core/device/platform.py`)**:
  - Extended with `search_applications`, `get_application_details`, `get_running_applications_structured`, `terminate_application`, `get_application_resources`, and `open_url`.
- **`MacOSApplicationAdapter` & `MacOSPlatform` (`core/device/macos.py`)**:
  - Full native implementations wrapping the macOS system tools.
  - Capabilities matrix updated with: `app_search`, `app_details`, `app_status`, `app_resources`, `app_terminate`, `url_open`.
- **`WindowsPlatform` (`core/device/windows.py`) & `LinuxPlatform` (`core/device/linux.py`)**:
  - Implement all new `ApplicationAdapter` methods.
  - Explicitly return `PlatformOperationResult(status="unsupported")` with detailed explanation without throwing unexpected exceptions.

### 3.3 Desktop Automation Layer (`core/automation/desktop.py`)
- Added tool wrappers:
  - `desktop_search_apps(query)`
  - `desktop_app_details(app_name)`
  - `desktop_running_apps_detailed()`
  - `desktop_app_resources(app_name)`
  - `desktop_terminate_app(app_name)`
  - `desktop_open_url(url, browser=None)`
- Updated `DesktopController` to delegate dynamically to the active platform adapter.

### 3.4 Security & Permissions (`core/security/`)
- Added fine-grained permission specs in `core/security/permissions.py`:
  - `application.discover` (Default: ALLOW)
  - `application.search` (Default: ALLOW)
  - `application.status` (Default: ALLOW)
  - `application.launch` (Default: ASK / confirmation required)
  - `application.switch` (Default: ASK / confirmation required)
  - `application.terminate` (Default: ASK / confirmation required)
  - `application.force_terminate` (Default: DENY)
  - `application.window.read` (Default: ASK / confirmation required)
  - `application.resource.read` (Default: ASK / confirmation required)
  - `application.open_url` (Default: ASK / confirmation required)
- `SecurityManager` (`core/security/manager.py`):
  - Read-only tools added to `SAFE_ACTIONS` (RiskLevel.LOW): `desktop_search_apps`, `desktop_app_details`, `desktop_running_apps_detailed`, `desktop_app_resources`.
  - Mutating tools added to `CONFIRMATION_ACTIONS` (RiskLevel.MEDIUM): `desktop_terminate_app`, `desktop_open_url`.
- `ToolExecutor` (`core/agent/tool_executor.py`):
  - Verification hooks added for `desktop_terminate_app` (verifying process closure via pgrep) and `desktop_open_url`.

### 3.5 Agent & Planner Integration (`core/agent/`)
- Registered new tools in `RiyaAgent._register_browser_and_desktop_tools` (`core/agent/agent.py`).
- Added parameter schemas in `BUILTIN_TOOL_SCHEMAS` (`core/agent/tool_registry.py`).
- Added application control tools to `_OS_EXTENSION_TOOLS` and cues to `_OS_EXTENSION_CUE_RE` in `AgentPlanner` (`core/agent/planner.py`).

---

## 4. Test Verification & Results

### 4.1 Focused Milestone 6.4 Test Suite
`tests/test_phase6_milestone_6_4_application_control.py` contains 39 tests covering:
1. Application search: empty query, length limit, exact/prefix/substring/multi-word scoring.
2. Application details: found bundle metadata, file count, bundle size, missing app handling.
3. Running applications telemetry: structured process enumeration, psutil metrics.
4. Application resources: running CPU/memory/uptime metrics, not-running state.
5. Application termination safety: rejection of 5+ protected system processes (`launchd`, `WindowServer`, `Finder`, `Dock`, `kernel_task`), rejection of RIYA's own PID, non-running handling, graceful quit verification, unsaved data failure reporting.
6. Safe URL validation & opening: valid HTTP/HTTPS URLs, rejection of 7 malicious schemes (`file:`, `javascript:`, `data:`, `applescript:`, `blob:`, `ftp:`, `ssh:`), rejection of 8 shell injection patterns, URL length limits, browser validation.
7. Cross-platform parity: macOS capability descriptors, Windows and Linux honest unsupported results.
8. Security & permissions: `application.*` permissions, `SecurityManager` risk levels, `ToolExecutor` verification hooks.
9. `DesktopController` delegation.

**Result:** `39 passed in 0.48s`.

### 4.2 Full System Regression Run
The complete test suite was executed:
- Baseline before Milestone 6.4: 330 passed.
- Milestone 6.4 addition: 39 passed.
- **Total: 369 passed, 0 failed, 0 skipped.**

---

## 5. Artifacts and File Manifest

### Backups Created
- `data/riya.db.before_v05_phase6_m64`
- `core/device/platform.py.before_v05_phase6_m64`
- `core/device/macos.py.before_v05_phase6_m64`
- `core/device/windows.py.before_v05_phase6_m64`
- `core/device/linux.py.before_v05_phase6_m64`
- `core/tools/system/macos.py.before_v05_phase6_m64`
- `core/automation/desktop.py.before_v05_phase6_m64`
- `core/security/permissions.py.before_v05_phase6_m64`
- `core/security/manager.py.before_v05_phase6_m64`
- `core/agent/tool_registry.py.before_v05_phase6_m64`
- `core/agent/tool_executor.py.before_v05_phase6_m64`
- `core/agent/agent.py.before_v05_phase6_m64`
- `core/agent/planner.py.before_v05_phase6_m64`
- `core/agent/assistant.py.before_v05_phase6_m64`

### New & Modified Production Files
- `core/tools/system/macos.py`: Added `search_applications`, `get_application_details`, `get_running_applications_structured`, `get_application_resources`, `terminate_application`, `validate_url`, `open_browser_url`, protected processes blocklist, self-process detection.
- `core/device/platform.py`: Added new abstract/default methods on `ApplicationAdapter` and capability aliases in `PlatformCapabilities`.
- `core/device/macos.py`: Implemented new `ApplicationAdapter` methods on `MacOSApplicationAdapter` and `MacOSPlatform`, added M6.4 capability descriptors.
- `core/device/windows.py`: Implemented new `ApplicationAdapter` methods with structured `unsupported` results and capability descriptors.
- `core/device/linux.py`: Implemented new `ApplicationAdapter` methods with structured `unsupported` results and capability descriptors.
- `core/automation/desktop.py`: Added tool wrappers and updated `DesktopController` to delegate to the platform adapter.
- `core/automation/__init__.py`: Exported new desktop automation tools.
- `core/security/permissions.py`: Added 10 `application.*` permission specifications, updated tool-to-key mappings, sensitive tools, and `resolve_permissions_for_tool`.
- `core/security/manager.py`: Added new read-only tools to `SAFE_ACTIONS` and mutating tools to `CONFIRMATION_ACTIONS`.
- `core/agent/tool_registry.py`: Added schemas for 6 new tools in `BUILTIN_TOOL_SCHEMAS`.
- `core/agent/agent.py`: Registered 6 new desktop tools in `RiyaAgent._register_browser_and_desktop_tools`.
- `core/agent/planner.py`: Added tools to `_OS_EXTENSION_TOOLS` and updated `_OS_EXTENSION_CUE_RE`.
- `core/agent/tool_executor.py`: Imported `json` and added verification logic for `desktop_terminate_app` and `desktop_open_url`.
- `tests/test_phase6_milestone_6_4_application_control.py`: 39 comprehensive unit, integration, and security tests.
- `RIYA_V0_5_PHASE_6_MILESTONE_6_4_BASELINE_AUDIT.md`: Pre-implementation audit artifact.
- `RIYA_V0_5_PHASE_6_MILESTONE_6_4_REPORT.md`: This comprehensive milestone implementation report.

---

## 6. Milestone 6.5 Recommendations & Next Steps

1. **System Preferences & Display Control:** Integrate safe brightness, dark mode toggle, and multi-monitor resolution inspection via native Darwin CoreGraphics bindings.
2. **Calendar & Reminders Integration:** Extend cross-platform personal productivity adapters to query EventKit via localized, permission-gated AppleScript / PyObjC wrappers.
3. **Menu Bar & System Status Item:** Provide a native macOS menu bar status icon for RIYA with instant voice/text trigger, offline status indicator, and quick permission toggles.
