# RIYA v0.5 — Phase 6 Mandatory Repository Baseline Audit

**Project:** RIYA — Universal Personal AI Assistant  
**Developer:** Tukaram  
**Target Phase:** v0.5 Phase 6 (Universal Personal Assistant & Operating-System Integration)  
**Starting Milestone:** 6.1 — Unified Personal Assistant Interface  
**Database Schema Baseline:** v6 (`data/riya.db`, `PRAGMA integrity_check = ok`, 24 tables)  
**Verified Pre-Phase-6 Test Baseline:** `222 passed in 231.59s (0:03:51)`  

---

## 1. Executive Summary & Audit Scope

Before modifying any source code for Phase 6, the complete RIYA repository (`/Users/tukaramgore/Projects/RIYA`), phase implementation reports (Phases 1 through 5), SQLite database state (`data/riya.db`), security model, local GGUF inference engine, voice stack, autonomous agent/task runtime, and existing Tkinter desktop UI (`core/onboarding/ui.py` and `core/voice/ui.py`) were inspected and verified.

Key findings:
- **Phases 1–5 are operational and verified:** Multi-user identity isolation, explicit permission and trust governance, first-time onboarding & readiness diagnostics, native `llama-cpp-python` (`Qwen3-4B-Instruct-2507-Q4_K_M.gguf`) Apple Silicon Metal inference with adaptive memory/learning, and Phase 5 autonomous task execution (`AgentReasoningEngine`, `TaskExecutionEngine`, `SkillManager`, `SafeBrowserController`, `SafeDesktopController`, and voice progress/interruption) all pass 222 automated tests including unmocked real-model E2E suites.
- **No unnecessary UI framework rewrite is needed:** RIYA already has a clean two-layer UI architecture:
  1. Headless, framework-independent controllers (`OnboardingViewModel` in `core/onboarding/ui.py` and `VoiceUIController` / `VoiceVisualSnapshot` in `core/voice/ui.py`).
  2. A native Tkinter desktop interface (`launch_desktop_app()` -> `launch_voice_gui()`).
- **Specific gaps exist in the unified user interface (Milestone 6.1 target):** While the backend supports persisted per-user conversation history, pending confirmation tokens, multi-step autonomous tasks, reusable skills, and user-scoped memory CRUD, several of these capabilities are not yet surfaced as first-class interactive controls in `VoiceUIController` and `launch_voice_gui()`.

---

## 2. Existing Architecture

### 2.1 Core Module Inventory (`core/`, `apps/`, `tests/`)
| Subsystem | Modules | Responsibility |
|---|---|---|
| **Entry Points & Packaging** | `core/main.py`, `apps/macos/Launch RIYA.command`, `core/onboarding/launcher.py` | CLI flags (`--ui`, `--setup`, `--doctor`, `--user`, `--list-users`, `--create-user`, `--voice`), desktop launcher, macOS `.app` bundle builder |
| **Multi-User Identity (Phase 1)** | `core/identity/manager.py`, `core/identity/models.py`, `core/identity/context.py` | Argon2id/scrypt password hashing, authenticated session tokens (`AuthenticatedIdentity`), per-user directories (`data/users/<user_id>/`), `ExecutionContextScope` |
| **Security & Permissions (Phase 2)** | `core/security/manager.py`, `core/security/permissions.py`, `core/security/confirmation.py`, `core/security/audit.py`, `core/security/filesystem.py`, `core/security/InputValidator.py` | 4-tier capability governance (`allow`, `confirm`, `deny`, `protected-default-deny`), `one_time`/`session`/`time_limited`/`persistent` grant lifetimes, user-bound confirmation tokens, revocation listeners, structured audit trail |
| **Onboarding & Diagnostics (Phase 3)** | `core/onboarding/manager.py`, `core/onboarding/state.py`, `core/onboarding/ai_setup.py`, `core/onboarding/voice_setup.py`, `core/onboarding/diagnostics.py`, `core/onboarding/ui.py` | 6-step setup wizard (`welcome`, `account`, `ai_engine`, `voice`, `permissions`, `diagnostics`), incomplete-setup recovery, degraded-mode operation |
| **Native AI & Adaptive Memory (Phase 4)** | `core/ai/engine.py`, `core/ai/llama_engine.py`, `core/ai/prompt_builder.py`, `core/ai/safety.py`, `core/memory/manager.py`, `core/memory/learning.py`, `core/memory/context_ranker.py` | Embedded `llama.cpp` GGUF inference (Metal + CPU fallback), thread-safe lock + cancellation, `inference_metrics`, episodic/semantic/preference memory, `AdaptiveLearningEngine`, `ContextRanker` |
| **Autonomous Agent, Tasks & Skills (Phase 5)** | `core/agent/assistant.py`, `core/agent/planner.py`, `core/agent/reasoning.py`, `core/agent/executor.py`, `core/agent/semantic.py`, `core/tasks/engine.py`, `core/tasks/coordinator.py`, `core/skills/manager.py`, `core/automation/browser_controller.py`, `core/automation/desktop_controller.py` | Goal decomposition, DAG validation, `TaskExecutionEngine` with SQLite checkpoints (`tasks`, `task_steps`, `task_events`), 32 registered tools, 6 built-in skills (`daily_briefing`, `file_organizer`, `research_assistant`, `study_helper`, `code_helper`, `system_health_check`) |
| **Voice & Desktop Interface** | `core/voice/orchestrator.py`, `core/voice/session.py`, `core/voice/stt.py`, `core/voice/tts.py`, `core/voice/vad.py`, `core/voice/ui.py` | Real-time voice capture, Silero/energy VAD, `faster-whisper` offline STT, local TTS with barge-in interruption, `VoiceUIController`, and `launch_voice_gui()` |

### 2.2 Database Schema State (Schema v6)
- **Live Database:** `/Users/tukaramgore/Projects/RIYA/data/riya.db` (backed up to `data/riya.db.before_v05_phase6`).
- **Integrity Check:** `PRAGMA integrity_check = ok`.
- **Applied Migrations:** `v1` through `v6` (`schema_migrations`).
- **Tables (24):** `users`, `user_sessions`, `user_permissions`, `permission_audit_log`, `security_audit_log`, `onboarding_state`, `memories`, `conversation_history`, `conversations`, `messages`, `tasks`, `task_steps`, `task_events`, `skills`, `skill_executions`, `learned_patterns`, `user_feedback`, `inference_metrics`, `scheduled_jobs`, `reminders`, `notes`, `documents`, `document_chunks`, `knowledge_embeddings`.
- **Preserved Baseline Data:** `users = 0`, `memories = 5` (`legacy_unclaimed = 5`), `tasks = 0`.

---

## 3. Verified Features (Pre-Phase-6 Baseline)

1. **Pre-Phase-6 Full Regression Suite:**
   - Command: `bash -n "apps/macos/Launch RIYA.command" && python3 -m compileall -q core tests apps && pytest -q`
   - Result: **`222 passed in 231.59s (0:03:51)`**, `0 failed`, `0 skipped`.
2. **Real Offline Model & Hardware Verification:**
   - Local `models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf` loads on Apple Silicon Metal (`metal`) and passes both Phase 4 (`tests/test_phase4_real_offline_e2e.py`) and Phase 5 (`tests/test_phase5_real_offline_e2e.py`) unmocked E2E suites.
3. **Security & Isolation:**
   - Cross-user memory, file, permission, session, task, and skill isolation verified across multi-user tests (`tests/test_multiuser_identity_and_isolation.py`, `tests/test_permission_and_trust.py`, `tests/test_phase5_tasks_skills_voice_recovery.py`).

---

## 4. Missing Functionality Across Phase 6 Milestones

| Milestone | Current Status | Remaining Work Needed |
|---|---|---|
| **6.1 — Unified Personal Assistant Interface** | Partial (`core/voice/ui.py` has chat, voice toggle, permissions, settings, read-only memories) | Hydrate persisted per-user conversation history into UI session; add progressive/streaming response updates; add interactive **Approval / Confirmation** banner with Approve & Reject buttons; add **Task Submission & Live Task Execution Progress** bar + **Tasks & Skills** manager dialog (run/resume/cancel tasks, inspect tool & skill execution results); add interactive **Memory Access Controls** (search, remember/add, forget/delete, clear history); surface explicit **Offline/Online Connectivity Status** and detailed **Local Model Status**; surface actionable **Error & Recovery** banner |
| **6.2 — Cross-Platform OS Abstraction Layer** | Partial (`core/automation/system_control.py` + `desktop_controller.py` are primarily macOS-oriented) | Dedicated `core/platform/` abstraction (`PlatformAdapter`, `MacOSPlatform`, `WindowsPlatform`, `LinuxPlatform`) with structured capability inspection and unsupported-feature reporting |
| **6.3 — File, Workspace & Personal Document Intelligence** | Partial (sandbox file read/write/move/copy/delete/list/search exist) | Local document indexing & semantic search over `.txt`, `.md`, `.pdf`, `.docx`, `.csv`, `.json`, source files; duplicate detection; reversible workspace organization plans |
| **6.4 — Safe Application & Workflow Automation** | Partial (`open_application`, `close_application`, `focus_window`, `take_screenshot`, `clipboard_*`, `run_approved_command`) | Structured workflow routines (`morning_workspace`, `coding_session`, `study_session`, `research_mode`, `focus_mode`) with step-by-step confirmation and verification |
| **6.5 — Local Knowledge Base & Personal Productivity** | Partial (SQLite tables `notes`, `reminders`, `scheduled_jobs` exist; `ReminderService` exists) | Full local productivity service for notes, to-do lists, reminders, daily agenda, project/goal tracking, and habit/study routines wired to assistant tools & UI |
| **6.6 — Voice Assistant Upgrade** | Operational baseline (`core/voice/`) | Enhance multi-step voice task status readouts, spoken confirmation prompts, and low-latency voice turn feedback |
| **6.7 — Plugin & Skill SDK** | Partial (`core/skills/` has 6 built-in skills and `skills` DB table) | Formal local Skill manifest validation, declared permission enforcement, sandboxed skill execution, enable/disable toggle, and user skill registration |

---

## 5. Known Defects & Interface Gaps in Milestone 6.1 (Target of Current Implementation)

1. **Persisted User Conversation History Not Hydrated into UI Session:**
   - `RIYAAssistant` loads persisted conversation history per user from `ConversationHistoryStore` (`conversation_history` table), but `VoiceSession._transcript` starts empty on launch or identity switch. Users reopening RIYA do not see their prior conversation turns in the unified transcript view, nor can they clear persisted conversation history from the UI.
2. **No Explicit Interactive Approval / Confirmation Controls in UI:**
   - When `RIYAAssistant` stages a `pending_action` (e.g., `file_delete`, `memory_write`, `system_command`) or pauses an autonomous task in `TaskStatus.AWAITING_CONFIRMATION`, the UI does not expose a structured `pending_confirmation` state or dedicated **Approve** / **Reject** buttons.
3. **Autonomous Task Submission, Progress & Control Missing from Desktop Window:**
   - `VoiceUIController` has `run_task()`, `list_tasks()`, and `list_skills()`, and `VoiceVisualSnapshot` has `active_task_progress`, but `launch_voice_gui()` never renders task progress, cannot submit a task directly, cannot resume an `awaiting_confirmation`/`paused` task after approval, cannot cancel an active task, and does not display structured tool/skill execution results.
4. **Memory Dialog is Read-Only:**
   - `_open_memories()` in `core/voice/ui.py` only lists memories (`list_memories()`). Users cannot search memories, add a memory, or delete/forget a memory from the Memory panel.
5. **Connectivity & Model Status Indicators Need Unification:**
   - The UI header displays `AI: Ready (metal)` or `AI: Limited Mode`, but does not display an explicit **Offline / Online Connectivity** indicator (`Offline (Local Only)` vs allowlisted browser online mode) or model name/telemetry summary in the snapshot.
6. **Progressive / Streaming Response Updates:**
   - `VoiceOrchestrator.send_text()` appends the assistant transcript entry only after generation finishes; adding progressive chunk/preview updates (`streaming_response` / chunk callback) improves responsiveness.

---

## 6. Security Risks & Guardrails Verified

- **LLM Is Never an Authority:** All tool executions pass through `SecurityManager.check_for_user()`, `PermissionManager`, and `ConfirmationManager`. UI actions in Milestone 6.1 (`approve_pending_action`, `reject_pending_action`, `remember_fact`, `forget_fact`, `submit_task`, `run_skill`) must route through the exact same permission and confirmation pipeline.
- **Confirmation Token Binding:** Confirmation tokens in `ConfirmationManager` are bound to `(user_id, session_id, tool_name, target)` and expire after TTL. Approving from the UI must verify and consume the active user's pending confirmation rather than bypassing `ConfirmationManager`.
- **Cross-User Transcript & History Isolation:** Hydrating or clearing conversation history in `VoiceUIController` must strictly scope by the active `AuthenticatedIdentity.user_id` and clear in-memory buffers on `switch_identity()` / logout.
- **No Uncontrolled Shell or Network Access:** Offline-first mode remains default (`SafeBrowserController.online_enabled == False`); microphone and speaker remain OFF by default until explicitly enabled by the user with `voice.microphone` / `voice.speaker` permission.

---

## 7. Existing Platform Support

- **Primary Verified Platform:** macOS on Apple Silicon M1 (`arm64`, Metal GPU offload for `llama.cpp`, `say`/`afplay` local TTS, CoreAudio via `sounddevice`, `osascript`/`open` desktop integration, `Launch RIYA.command` + `RIYA.app` bundle).
- **Fallback / Cross-Platform Foundations:** Pure-Python + SQLite + `psutil` + `pyttsx3` fallback paths exist for CPU inference and basic system metrics; full cross-platform OS adapters (`macOS`, `Windows`, `Linux`) are scheduled for Milestone 6.2.
