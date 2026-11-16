# RIYA v0.5 — Phase 3 Implementation Report
## Recover Antigravity Execution, First-Time Setup, Onboarding & User Experience

**Project:** RIYA — Offline-First Personal AI Assistant  
**Location:** `/Users/tukaramgore/Projects/RIYA`  
**Phase:** v0.5 Phase 3 (First-Time Setup, Onboarding, Readiness & Desktop UX)  
**Baseline:** RIYA v0.5 Phase 2 (`180` tests passing)  

---

## 1. Prior Execution Diagnosis & Recovery Actions

- **Diagnosed Cause:** Previous cloud-agent failure (`net/http: TLS handshake timeout` against Google Cloud endpoint `https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse`) was a transient transport/connectivity interruption, not a defect in RIYA, Python, SQLite, or `llama.cpp`.
- **Workspace Recovery Check:** Inspected repository state cleanly (`git status --short`, timestamps, `data/riya.db`, `models/`, `core/`). Confirmed zero partial or broken Phase 3 artifacts existed prior to recovery; the repository stood cleanly at the verified Phase 2 baseline (`180 passed in 23.34s`).
- **Non-Destructive Backups Preserved:** Created `.before_v05_phase3` backups alongside all prior `.before_v05_phase1` / `.before_v05_phase2` backups for:
  - `core/database/migrations.py.before_v05_phase3`
  - `core/agent/assistant.py.before_v05_phase3`
  - `core/voice/ui.py.before_v05_phase3`
  - `core/main.py.before_v05_phase3`
  - `pyproject.toml.before_v05_phase3`
  - `data/riya.db.before_v05_phase3`
- **Live Data Safety:** Preserved `/Users/tukaramgore/Projects/RIYA/data/riya.db` without inserting test accounts; live DB contains `0` registered users and all `5` legacy `v0.4` memories (`legacy_unclaimed = 1`).

---

## 2. Implemented Architecture & Modules

Phase 3 adds a framework-independent onboarding/setup engine under `core/onboarding/`, desktop UI controllers and Tkinter wizards, non-Terminal macOS launchers under `apps/macos/`, degraded-mode resilience in `RIYAAssistant`, and CLI entry points:

- `core/onboarding/state.py` — First-launch state detector (`detect_install_state`, `InstallState`), SQLite-backed `OnboardingStateStore`, and atomic JSON crash-recovery checkpoint (`onboarding_checkpoint.json`).
- `core/onboarding/ai_setup.py` — Native `libriya_llama.dylib` probe, hardware/RAM/disk inspection, GGUF header/size validation (`validate_gguf_file`, `discover_local_models`), and real inference readiness probe (`LocalAISetupService.verify_model_with_inference`).
- `core/onboarding/voice_setup.py` — Offline STT cache inspection (`check_stt_local_availability`), honest language labels (`honest_language_options()`), corrupted `voice_settings.json` safe fallback (`load_user_voice_config_safely`), and `PermissionManager`-gated mic/speaker tests (`VoiceSetupService`).
- `core/onboarding/diagnostics.py` — Mandatory & optional startup/readiness checks (`ReadinessDiagnostics`, `DiagnosticCheck`, `ReadinessReport`, `CheckSeverity`).
- `core/onboarding/manager.py` — End-to-end session & wizard coordinator (`OnboardingCoordinator`) uniting storage, identity, permissions, AI setup, voice setup, diagnostics, and runtime startup.
- `core/onboarding/ui.py` — Headless `OnboardingViewModel` + multi-step Tkinter `_DesktopOnboardingWindow` + `launch_desktop_app()` loop.
- `apps/macos/Launch RIYA.command` & `apps/macos/create_app_bundle.py` — Portable double-clickable macOS Terminal-free launcher & `.app` bundle builder.

---

## 3. First-Launch Detection & Persistent Resumable State

- **Schema Migration `4` (`0004_onboarding_and_setup_state`)** in `core/database/migrations.py` bumps `CURRENT_SCHEMA_VERSION` from `3` to `4` (reversible down-migration preserved, and Migration 2 `_up_0002_multi_user_identity_and_isolation` also hardened when legacy `memories` rows lack `created_at`/`updated_at`).
- Added SQLite tables:
  - `installation_setup_state`: tracks installation `status` (`not_started`, `in_progress`, `completed`, `recovery_required`), `current_step`, `completed_steps_json`, `selected_model_path`, `selected_backend`, `active_user_id`, `last_error`, timestamps.
  - `user_onboarding_state`: tracks per-user setup progress, selected AI model/backend, `voice_configured`, `permissions_reviewed`, `health_checked`.
- `detect_install_state()` inspects DB accessibility, schema version, registered users count, `legacy_unclaimed` v0.4 row counts, and `onboarding_checkpoint.json` safely before initialization, classifying into:
  - `FRESH_INSTALL`
  - `SETUP_IN_PROGRESS` (resumes at the interrupted step)
  - `SETUP_COMPLETED`
  - `EXISTING_V04` (preserves v0.4 unclaimed memories cleanly)
  - `EXISTING_V05` (existing Phase 1/2 users route cleanly to Login without redundant onboarding)
  - `NEEDS_RECOVERY` (corrupted checkpoint JSON, unreadable/corrupted SQLite DB, or `completed` status with 0 accounts)

---

## 4. Welcome & Offline-First Privacy Experience

- Exposed `OnboardingWelcomeInfo` & `PRIVACY_NOTICE_SUMMARY` across `OnboardingCoordinator`, CLI `--setup`, and Desktop Welcome UI:
  - Explains offline-first local SQLite DB, per-user profile/workspace/memory isolation, user-authoritative permission confirmations, and embedded `llama.cpp` runtime (no Ollama dependency, no hidden cloud fallbacks).
  - Explicitly notes honest platform/language limits rather than overclaiming guarantees.

---

## 5. Local Account Setup & Authentication Integration

- Integrated cleanly with existing Phase 1 `IdentityManager`:
  - Supports creating local accounts (`local_password`, `local_pin`, `local_unlock`) with confirmation checks, or signing in / switching between existing profiles.
  - Password/PIN credentials remain exclusively PBKDF2-HMAC-SHA256 hashed in SQLite (`users`); plaintext secrets are never persisted in `onboarding_checkpoint.json` or SQLite setup tables.
  - When `legacy_memories_count > 0` (`EXISTING_V04`), UI/CLI surfaces an **explicit opt-in** option (unchecked by default) calling `MemoryManager.claim_legacy_memories()` into the authenticated user's isolated scope.
  - Added full logout cycle returning desktop UI cleanly back to the Sign-In / Create Profile screen without leaking cross-user transcripts (`VoiceSession.clear_transcript()`).

---

## 6. Local AI Engine Discovery, Verification & Degraded Mode

- **Embedded Native `llama.cpp` Verification:** `LocalAISetupService` inspects `core/ai/native/bridge/libriya_llama.dylib` for required exported symbols (`riya_load_model_with_backend`, `riya_get_backend`, `riya_generate`, `riya_free_model`), hardware architecture (`arm64` / Metal + CPU vs CPU), physical RAM, and free disk space.
- **Strict Readiness Before Claiming Ready:** Local GGUF candidates in `models/`, Hugging Face cache blobs, or user-selected paths undergo file/size/`GGUF` magic validation AND an actual local inference probe (`verify_model_with_inference()`). Empty output, truncated files, invalid magic bytes, or missing/unloadable bridge libraries fail verification honestly.
- **Explicit Limited Mode (`allow_degraded_ai=True`):** Enhanced `RIYAAssistant` (`core/agent/assistant.py`) with `ai_status()`, `reload_ai_model()`, `PROJECT_ROOT`-relative `_find_model()`, and safe unloaded runtime behavior. When the user skips AI setup (`selected_backend="unloaded"`), `RIYAAssistant` honors Limited Mode without silently auto-loading `models/*.gguf`, keeping deterministic commands, personal memory, and permission/tool guardrails working while returning honest Limited Mode explanations on open-ended LLM prompts.

---

## 7. Permission-Gated Voice Setup & Per-User Audio Settings

- **Zero Auto-Grants & Hardware Gating:** `VoiceSetupService.run_microphone_test()` and `run_speaker_test()` enforce `PermissionManager.consume_if_authorized(..., "voice.microphone")` and `"voice.speaker"` before opening any hardware device or playing audio.
- **Config/Device Permissions:** `VoiceSetupService.save_voice_preferences()` validates `VoiceConfig`, authenticates user session, and enforces `"voice.config"` and `"voice.device"` before writing per-user `voice_settings.json`.
- **Honest Multilingual Support:** `honest_language_options()` marks English (`en`) as verified offline and marks Hindi (`hi`), Marathi (`mr`), and Auto-detect (`auto`) explicitly as `(accuracy untested)`.
- **Safe Defaults & Error Handling:** Microphone stays OFF by default in Text mode; OS microphone privacy denials return actionable macOS System Settings guidance (`os_permission_denied`) cleanly.

---

## 8. Permission & Privacy Onboarding

- Added `PERMISSION_ONBOARDING_GUIDE` and a read-only interactive Permissions Intro step in both Tkinter and CLI `--setup`.
- Explicitly preserves Phase 2 security invariants:
  - Onboarding grants **zero** permissions automatically (`user_permissions` remains empty after completing setup).
  - Persistent approval checkboxes always initialize unchecked (`False`) and require explicit user opt-in confirmation.
  - LLM output and internal planner prompts remain non-authoritative.

---

## 9. System Readiness & Health Check Diagnostics

`ReadinessDiagnostics` runs real verification checks categorized into `mandatory` vs `optional` with `CheckSeverity` (`passed`, `warning`, `failed`, `not_configured`):

- **Mandatory:** Python & SQLite runtime, writable directories & disk space (>= 50 MB / warning < 500 MB), SQLite integrity (`PRAGMA quick_check`) & schema migration version (`4`), authenticated user session & workspace folder, Permission & Audit Engine, per-user configuration persistence.
- **Optional (graceful degradation allowed):** Local AI inference engine (`libriya_llama.dylib` + GGUF model inference verification), Voice Input (mic hardware + Silero VAD + offline `faster-whisper` cache + `voice.microphone` policy), Voice Output (speakers + `/usr/bin/say` voices + `voice.speaker` policy).
- `OnboardingCoordinator.complete_onboarding()` refuses completion if `mandatory_passed` is `False` or no user is signed in, while allowing entry in Limited Mode when optional capabilities are absent or unconfigured (honestly listing `degraded_capabilities`).

---

## 10. Desktop UI, Non-Terminal macOS Launchers & CLI

- **Desktop UI (`core/onboarding/ui.py` & `core/voice/ui.py`):**
  - `OnboardingViewModel` + `_DesktopOnboardingWindow` render Recovery, Welcome, Local Account/Login, AI Engine Setup, Permission-Gated Voice Setup, Permissions Guide, and System Diagnostics steps before launching `launch_voice_gui()`.
  - Updated `launch_voice_gui()` in `core/voice/ui.py` with active user header badge, AI Ready/Limited badge, Mic state badge, non-blocking message dispatch, **Memories** viewer (`list_memories()`), **System Status / Diagnostics** dialog, cleanly cancelled `_tick` timers on window close, and **Logout** flow returning seamlessly to Login.
- **macOS Launchers (`apps/macos/` & `pyproject.toml`):**
  - `apps/macos/Launch RIYA.command` (`+x`): portable repository-relative Finder launcher locating Python 3 + Tkinter without hardcoded user home paths and displaying native macOS `osascript` alert dialogs if GUI dependencies are missing.
  - `apps/macos/create_app_bundle.py`: CLI tool (`build_app_bundle`) generating a double-clickable `RIYA.app` bundle (`Info.plist` + `NSMicrophoneUsageDescription` + executable wrapper invoking `python3 -m core.main --ui`).
  - `pyproject.toml` `[project.scripts]`: added `riya = "core.main:main"` and `riya-ui = "core.main:main_ui"`.
- **CLI (`core/main.py`):**
  - Preserved all interactive Phase 1/2/5 commands (`/voice`, `/text`, `/interrupt`, `/status`, `/settings`, `/permissions`, `/whoami`, `/create-user`, `/switch-user`, `/logout`, `exit`).
  - Added `--ui`, `--setup`, `--diagnostics`, `--database`, and REPL commands `/diagnostics`, `/onboarding`, `/memories`.

---

## 11. Crash, Migration & Error Recovery Behavior

- **Corrupted `onboarding_checkpoint.json`:** Classified as `NEEDS_RECOVERY`; `recover_corrupted_checkpoint()` moves the corrupt file to `onboarding_checkpoint.json.corrupt.<timestamp>` cleanly before continuing onboarding.
- **Corrupted `voice_settings.json`:** `load_user_voice_config_safely()` surfaces clear corruption error text cleanly without crashing; `recover_corrupted_voice_config()` backs up the corrupt JSON before restoring safe defaults.
- **Corrupted SQLite Database:** `detect_install_state()` fails safe with `InstallState.NEEDS_RECOVERY` and refuses destructive deletes.
- **v0.4 Database Preservation Verified:** Tested upgrade of a copy of live `data/riya.db` plus synthetic v0.4 schemas cleanly through Migration 4 with zero loss of `legacy_unclaimed` rows.

---

## 12. Security & Multi-User Isolation Verification

- Verified cross-user isolation for newly created accounts (`tukaram` / `bob` / `alice`), isolated SQLite memory rows, isolated per-user `voice_settings.json`, session-bound confirmation grants, atomic `one_time` permission consumption, zero automatic grants during setup, and zero plaintext passwords/PINs in SQLite or JSON checkpoints.

---

## 13. Modified & Added Files Inventory

- **Added:**
  - `core/onboarding/__init__.py`
  - `core/onboarding/state.py`
  - `core/onboarding/ai_setup.py`
  - `core/onboarding/voice_setup.py`
  - `core/onboarding/diagnostics.py`
  - `core/onboarding/manager.py`
  - `core/onboarding/ui.py`
  - `apps/macos/Launch RIYA.command`
  - `apps/macos/create_app_bundle.py`
  - `tests/test_onboarding_and_setup.py`
  - `RIYA_V0_5_PHASE_3_IMPLEMENTATION_REPORT.md`
- **Modified:**
  - `core/database/migrations.py`
  - `core/agent/assistant.py`
  - `core/voice/session.py`
  - `core/voice/ui.py`
  - `core/main.py`
  - `pyproject.toml`
  - `tests/test_permission_and_trust.py` (updated strict schema version assertion to `CURRENT_SCHEMA_VERSION >= 3`)

---

## 14. Test & Verification Results

All checks below ran directly in `/Users/tukaramgore/Projects/RIYA` after the final code edit:

- **Shell Launcher Syntax Check:** `bash -n "apps/macos/Launch RIYA.command"` -> exit code `0`.
- **Bytecode Compilation:** `python3 -m compileall -q core tests apps` -> exit code `0`.
- **Full Automated Test Suite:** `pytest -q` -> **`194 passed in 30.86s`** (`0` failed, `0` skipped), covering prior v0.4, Phase 1, Phase 2, and all `14` new Phase 3 onboarding/recovery/AI/voice/permission/UI/CLI/launcher tests in `tests/test_onboarding_and_setup.py`.
- **Live Non-Destructive Database & Migration Verification:**
  - Live `data/riya.db`: schema v`4`, `0` registered users, `5` preserved `legacy_unclaimed = 1` v0.4 memories intact; all `.before_v05_phase1`/`.before_v05_phase2`/`.before_v05_phase3` backups preserved.
- **Unmocked Offline End-to-End Hardware & Model Verification (isolated temp DB):**
  - **Local Qwen3 Inference Probe (`LocalAISetupService` + Apple M1 Metal + `libriya_llama.dylib` + `models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`):** `ok=True`, `backend=metal`, `preview='READY'`.
  - **Real E2E Session Output:** Deterministic time response `'It is 02:54 PM.'`; real Metal Qwen3 response `'RIYA is online.'`.
  - **Real Audio Hardware & Voice Inspection:** Detected `1` input (`MacBook Air Microphone`), `1` output (`MacBook Air Speakers`), `silero` VAD, cached `faster-whisper` `base`, `/usr/bin/say` with `74` local voices. Mic/speaker tests were **blocked before permission** (`not_configured`); after explicit `one_time` grant, live MacBook Air Microphone probe passed (`RMS=0.01527`) and live macOS `say` speaker playback completed cleanly (`passed=True`), leaving `0` persistent/active permission grants afterwards.
- **Live Desktop Tkinter UI Smoke Verification:** Instantiated real `tk.Tk()` and rendered all `8` `_DesktopOnboardingWindow` routes/steps (`recovery`, `welcome`, `account`, `ai_engine`, `voice`, `permissions`, `diagnostics`, `login`) with `update_idletasks()` and `0` errors.
- **Live CLI Diagnostics Check:** Executed `python3 -m core.main --diagnostics` directly against live repository environment -> exit code `0`.

---

## 15. Remaining Limitations & Honest Notes

- **Platform Scope:** Desktop GUI (`Tkinter`), audio TTS (`/usr/bin/say`), and macOS Finder/bundle launchers target macOS first (`macOS + Apple Silicon / Python 3.13.9` tested); non-macOS systems are not targets for `Launch RIYA.command` or `macos_say`.
- **Voice Language Accuracy:** Offline STT/TTS end-to-end accuracy has been exercised on English (`en`); Hindi (`hi`), Marathi (`mr`), and Auto-detect (`auto`) are honestly labeled `(accuracy untested)` in configuration and onboarding UIs.
- **Desktop Distribution Packaging:** `apps/macos/create_app_bundle.py` builds an unsigned local `RIYA.app` pointing to the local Python environment rather than a notarized/codesigned standalone DMG installer.
- **Human Window Interaction vs Widget Render:** Live `tk.Tk()` smoke verification directly rendered all 8 wizard/login views without error; extended manual Finder double-click mouse walkthroughs were not synthesized via OS accessibility automation.
- **Git Discipline:** Zero Git commits or pushes performed (`data/riya.db` and backups preserved locally).
