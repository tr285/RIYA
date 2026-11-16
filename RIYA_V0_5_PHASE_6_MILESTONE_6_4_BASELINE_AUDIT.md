# RIYA v0.5 — Phase 6 — Milestone 6.4 Baseline Audit
## Intelligent Application Control & Desktop Automation — Repository Audit & Architecture Design

**Project:** RIYA — Universal Personal AI Assistant  
**Developer:** Tukaram  
**Milestone:** 6.4 — Intelligent Application Control & Desktop Automation  
**Previous Milestone:** 6.3 — Intelligent File Management & Content Understanding (`330 passed, 0 failed, 0 skipped`)  
**Development Platform:** macOS on Apple Silicon (M1), with Windows and Linux cross-platform contracts  
**Execution Environment:** Local Offline-First AI (LocalLlamaRuntime + Qwen3 GGUF)

---

## 1. Executive Summary & Existing Foundations

Milestone 6.4 expands RIYA from intelligent file understanding into an intelligent desktop automation assistant capable of discovering, inspecting, launching, monitoring, focusing, safely switching, gracefully terminating, and navigating local applications and web URLs under strict user permission and confirmation guardrails.

### 1.1 Existing Reused Capabilities
1. **Application Inspection & Discovery (`core/tools/system/macos.py`, `core/device/macos.py`):**
   - Safe `.app` bundle inspection using `plistlib` reading `Info.plist` (CFBundleName, CFBundleDisplayName, CFBundleIdentifier, CFBundleShortVersionString, CFBundleExecutable).
   - Non-shell, argument-array invocation of `/usr/bin/open` with background (`-g`) or frontmost flags.
   - Post-launch process verification loop using `/usr/bin/pgrep -x -- <name>` across multiple cycles (never assuming launch succeeded merely because `open` returned code 0).
   - Parameterized AppleScript execution via `/usr/bin/osascript -e ... -- argv` to query frontmost application, window count, window title, and application running state (`System Events`).
   - Graceful quit script requesting application quit with a 10-iteration verification loop (`CLOSED`, `NOT_RUNNING`, `STILL_RUNNING`).
2. **Platform Abstraction Architecture (`core/device/platform.py`):**
   - Clean sub-adapter hierarchy: `ApplicationAdapter`, `WindowManagerAdapter`, `SystemInformationAdapter`, `FilesystemAdapter`, `ClipboardAdapter`, `NotificationAdapter`.
   - `PlatformCapabilities` matrix reporting explicit supported/unsupported capabilities and required permissions.
   - `PlatformOperationResult` contract returning structured status (`success`, `failed`, `denied`, `unsupported`, `timed_out`), data payloads, error messages, and `verified` flags.
   - Concrete `MacOSPlatform` (`core/device/macos.py`) implementing native macOS Darwin behavior.
   - Concrete `WindowsPlatform` (`core/device/windows.py`) and `LinuxPlatform` (`core/device/linux.py`) honestly reporting `make_unsupported_result` without faking unverified native platform implementations.
3. **Security, Permissions & Human-in-the-Loop Confirmation (`core/security/`):**
   - Multi-tier permission framework with default `ALLOW`, `CONFIRM` (`ASK`), and `DENY` policies.
   - `SecurityManager` categorizing actions into `SAFE_ACTIONS` (read-only inspection), `CONFIRMATION_ACTIONS` (state mutation), and `HIGH_RISK_ACTIONS` (destructive operations).
   - Single-use, session-bound `ConfirmationGrant` token verification via `ConfirmationManager`.
   - Strict rejection of model prompt-injection commands; executable strings are never passed to shell interpreters (`shell=False` everywhere).

---

## 2. Gap Analysis for Milestone 6.4

Prior to Milestone 6.4, several key application control capabilities were missing or fragmented:

| Capability | Pre-M6.4 Status | Milestone 6.4 Target Implementation |
|---|---|---|
| **Fuzzy Application Search** | Substring filter only on exact names (`desktop_list_apps`) | `search_applications` with weighted relevance scoring (exact match: 100, prefix: 80, substring: 60, word boundary: 40) across display name, bundle name, and bundle ID |
| **Deep Application Metadata** | Basic `name` and `bundle_id` | `get_application_details` inspecting bundle directory structure, version strings, executable name, file count, and bundle size in MB |
| **Process Telemetry & Resources** | Only boolean running check via `pgrep` | `get_running_applications_structured` and `get_application_resources` retrieving PID, CPU %, RSS memory MB, process status, and uptime via `psutil` |
| **Controlled Application Termination** | Raw AppleScript quit without protected-process guards | `terminate_application` with blocklist guarding OS critical processes (`launchd`, `WindowServer`, `Dock`, `Finder`, etc.), RIYA process tree protection, graceful quit, and closure verification |
| **Safe URL Navigation** | Uncontrolled or fragmented | `validate_url` and `open_browser_url` strictly allowing only `http://` and `https://` schemes, validating hostnames, rejecting dangerous shell characters, and supporting optional browser selection |
| **Granular Application Permissions** | Generic `system.open_app`, `system.close_app`, `desktop.automate` | Fine-grained `application.*` permission specifications (`application.discover`, `application.search`, `application.status`, `application.launch`, `application.switch`, `application.terminate`, `application.force_terminate`, `application.window.read`, `application.resource.read`, `application.open_url`) while preserving backward compatibility |
| **Cross-Platform Contracts** | Windows/Linux lacked new M6.4 method stubs | Complete `ApplicationAdapter` methods implemented across `WindowsPlatform` and `LinuxPlatform` returning honest `PlatformOperationResult(status="unsupported")` |

---

## 3. Security, Safety & Risk Controls

### 3.1 Protected System Process Shield
RIYA refuses to terminate critical macOS system processes:
- `launchd`, `kernel_task`, `WindowServer`, `loginwindow`, `SystemUIServer`, `Dock`, `Finder`
- System daemons: `cfprefsd`, `distnoted`, `logd`, `opendirectoryd`, `securityd`, `trustd`, `coreservicesd`, `mds`, `mds_stores`, `fseventsd`, `syslogd`, `diskarbitrationd`, `configd`, `notifyd`, `lsd`, `powerd`, `UserEventAgent`, `coreaudiod`, `airportd`, `mediaremoted`, `remoted`, `bluetoothd`, `hidd`.
- Self-protection: RIYA identifies its own PID and parent PID using `os.getpid()` / `os.getppid()` and refuses termination requests targeting itself.

### 3.2 No Force-Quit Rule
Force termination (`kill -9`, `SIGKILL`, `application.force_terminate`) is strictly `DENY` by default. All termination requests utilize graceful AppleScript `tell application <app> to quit`, allowing applications to save state or report unsaved documents.

### 3.3 Safe URL Validation
`validate_url` enforces:
- Scheme allowlist: `http` and `https` only. Schemes such as `file:`, `javascript:`, `data:`, `applescript:`, `blob:`, `custom:` are denied.
- Length limit: 2048 characters.
- Dangerous character rejection: backticks, quotes, pipes, ampersands, semicolons, dollar signs, shell redirection symbols, and null bytes are rejected before passing to `/usr/bin/open`.
- Target checks: localhost is permitted for local development; broadcast/wildcard addresses like `0.0.0.0` are rejected.

### 3.4 Strict Non-Shell Execution
All external operations invoke allowlisted binaries (`/usr/bin/open`, `/usr/bin/osascript`, `/usr/bin/pgrep`, `/usr/sbin/screencapture`, `/usr/bin/pmset`, `/usr/bin/sw_vers`, `/usr/bin/uname`, `/usr/bin/pbcopy`, `/usr/bin/pbpaste`, `/usr/sbin/sysctl`) with `shell=False` and explicit argument lists.

---

## 4. Phase 6 Milestone 6.4 Architecture & Components

```
                    Natural Language User Request
                                │
                                ▼
                        AgentPlanner & Intent
                 (_OS_EXTENSION_CUE_RE & Tools)
                                │
                                ▼
                       PermissionManager &
                        SecurityManager
         (application.* permissions & confirmation token)
                                │
                                ▼
                           ToolExecutor
        (verifies application running, closed, or focused)
                                │
                                ▼
                       PlatformAdapter
             (MacOSPlatform / Windows / Linux)
                                │
                ┌───────────────┴───────────────┐
                ▼                               ▼
       ApplicationAdapter              WindowManagerAdapter
   ┌──────────────────────────┐    ┌──────────────────────────┐
   │ search_applications      │    │ active_window_info       │
   │ get_application_details  │    │ focus_application        │
   │ get_running_applications │    │ get_application_state    │
   │ terminate_application    │    └──────────────────────────┘
   │ get_application_resources│
   │ open_url                 │
   └──────────────────────────┘
```

---

## 5. Verification Plan

1. **Unit & Integration Tests (`tests/test_phase6_milestone_6_4_application_control.py`):**
   - Application search: exact match, prefix match, substring match, multi-word matching, query validation.
   - Application details: installed app inspection, bundle size calculation, version resolution, missing app handling.
   - Running application inspection: structured process enumeration, PID and memory extraction, psutil integration.
   - Resource monitoring: CPU %, RSS memory in MB, uptime calculation, not-running application status.
   - Application termination: graceful quit verification, non-running app handling, refusal of protected system processes (`launchd`, `WindowServer`, `Finder`, `Dock`), refusal of RIYA's own PID, timeout handling.
   - Safe URL opening: valid http/https URLs, malicious scheme rejection (`javascript:`, `file:`, `data:`), dangerous shell character rejection, length limits, browser selection.
   - Cross-platform parity: Windows and Linux application adapters returning truthful `status="unsupported"`.
   - Security & permissions: `application.*` permissions, confirmation gates on `desktop_terminate_app` and `desktop_open_url`, read-only access for `desktop_search_apps` and `desktop_app_details`.
   - ToolExecutor verification: post-execution verification for termination and URL launch.
2. **Regression Verification:**
   - Full test suite execution ensuring all 330 pre-M6.4 tests remain passing with zero regressions.
