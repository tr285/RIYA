# RIYA v0.5 — Phase 2 Implementation Report
## Permission & Trust Management

**Project:** RIYA — Offline-First Personal AI Assistant  
**Location:** `/Users/tukaramgore/Projects/RIYA`  
**Phase:** v0.5 Phase 2 (Permission & Trust Management)  
**Baseline:** RIYA v0.5 Phase 1 (`161` tests passing)

---

## 1. Executive Summary

Phase 2 implements a user-authoritative, fail-closed, per-user **Permission & Trust Management** layer integrated directly across RIYA's execution pipeline (`Planner -> ToolDecisionEngine -> Assistant -> SecurityManager -> ConfirmationManager -> ToolExecutor -> Memory/Personal Stores -> VoiceOrchestrator/UI`).

Key guarantees enforced in code:

- **User-Only Authority:** Only the authenticated local user (via explicit API calls with trusted actor, interactive CLI `/permissions`, or explicit UI controls) can grant, modify, deny, or revoke permissions. LLM output, planner JSON, tool parameters, and internal tool calls can never self-grant permissions or override `DENY` rules.
- **Strict Decision Precedence:** Active `DENY` rules cannot be overridden by wildcard `ALLOW`, narrower non-`DENY` grants, confirmations, tool parameters, or legacy `UserPermissionStore` allow entries. Higher scope specificity (`path:...` > `dir:...` > `app:...`/`tool:...` > `*`) resolves non-`DENY` rules, with `CONFIRM` preferred over `ALLOW` on ties. Unconfigured capabilities (`NOT_CONFIGURED`) fail closed under `strict_default_deny=True`, and protected host/hardware capabilities (`system.open_app`, `system.close_app`, `system.screenshot`, `system.volume`, `system.lock_screen`, `voice.microphone`, `voice.speaker`, `voice.device`) always fail closed even in compatibility sandbox mode.
- **Explicit Lifetimes & Atomic Consumption:** Supports `one_time`, `session`, `time_limited`, and `persistent` grants. `persistent` grants require `explicit_user_approval=True` and reject untrusted callers. `one_time` and `session` grants never cross user sessions and are never silently converted to `persistent`. `one_time` grants remain unconsumed during confirmation staging and are atomically consumed once under SQLite `BEGIN IMMEDIATE` right before final execution.
- **Confirmation Binding & Instant Revocation:** Pending `ConfirmationManager` tokens and `RIYAAssistant` pending actions are automatically purged when matching permissions are revoked, denied, or reset, and revalidated immediately prior to execution.
- **Append-Only Privacy-Preserving Audit Log:** SQLite `permission_audit_log` records sanitized capability events with triggers blocking `UPDATE` and `DELETE`, strictly scoped per `user_id` on reads and redacting bearer/session tokens and credential strings.

---

## 2. Database Schema Migration (`CURRENT_SCHEMA_VERSION = 3`)

Updated `core/database/migrations.py` with `Migration(3, "0003_permission_trust_and_audit", _up_0003_permission_trust_and_audit, _down_0003_permission_trust_and_audit)`:

- **`permission_grants`**: `grant_id`, `user_id` (`REFERENCES users(user_id) ON DELETE CASCADE`), `permission_key`, `resource_scope`, `decision` (`allow` | `confirm` | `deny`), `lifetime` (`one_time` | `session` | `persistent` | `time_limited`), `session_id`, `expires_at`, `max_uses`, `use_count`, `granted_by`, `reason`, `revoked_at`, `revoked_reason`, `created_at`, `updated_at`.
- **`permission_audit_log`**: Append-only table storing `audit_id`, `user_id`, `session_id`, `permission_category`, `permission_key`, `resource_or_tool`, `decision`, `reason`, `actor`, `created_at`, protected by `trg_permission_audit_no_update` and `trg_permission_audit_no_delete` triggers (`SELECT RAISE(ABORT, ...)`). User deletion cascades `permission_grants` cleanly without failing on audit immutability triggers.
- **Non-Destructive Upgrade Verified:** Existing `/Users/tukaramgore/Projects/RIYA/data/riya.db` upgraded cleanly to schema version `3`, preserving prior Phase 1 tables and adding `permission_grants` and `permission_audit_log`. Rollback tests to version `1` and re-upgrade to `3` continue passing.

---

## 3. Capability Taxonomy Mapped Strictly to Real RIYA Features

Implemented in `core/security/permissions.py` (`PERMISSION_SPECS`):

| Category | Permission Keys | Mapped Real RIYA Capabilities |
|---|---|---|
| `filesystem` | `filesystem.read`, `filesystem.create`, `filesystem.modify`, `filesystem.directory` | `list_files`, `find_files`, `read_file`, `create_folder`, `write_file` (create vs overwrite resolution + resolved path/directory scopes) |
| `memory` | `memory.read`, `memory.write`, `memory.delete` | `recall_memory`, `list_memories`, `remember`, `forget_memory`, and `MemoryManager` operations |
| `system` | `system.open_app`, `system.close_app`, `system.screenshot`, `system.volume`, `system.lock_screen`, `system.info` | `open_application` (`app:` scope), `close_application` (`app:` scope), `take_screenshot`, `set_volume`/`mute_volume`/`unmute_volume`, `lock_screen`, `current_time`/`battery_status`/`system_information` |
| `voice` | `voice.microphone`, `voice.speaker`, `voice.config`, `voice.device` | `VoiceOrchestrator` mic start/STT transcription, TTS speech output, per-user config edits, and input/output audio device switches |
| `personal` | `personal.preferences`, `personal.history`, `personal.profile` | `UserPreferencesService`, `ConversationHistoryStore`, and user profile stores |

No speculative permissions for unimplemented capabilities (e.g., `delete_file`, email, web browsing, or cron scheduling) were introduced.

---

## 4. Security Architecture & Execution Hardening

- **`core/security/permissions.py`**:
  - `PermissionDecision` (`allow`, `confirm`, `deny`, `not_configured`), `PermissionLifetime` (`one_time`, `session`, `persistent`, `time_limited`).
  - `normalize_resource_scope()` resolves filesystem paths via `Path.expanduser().resolve(strict=False)` to avoid macOS `/tmp -> /private/tmp` and `..` traversal mismatches.
  - `resolve_permissions_for_tool()` dynamically maps `write_file` to `filesystem.create` (new target) vs `filesystem.modify` (existing target) and resolves `open_application`/`close_application` to `app:<name>`.
  - `PermissionManager`: thread-safe evaluation (`evaluate`, `evaluate_tool`) and atomic execution check (`consume_if_authorized`, `consume_tool_if_authorized`), actor validation rejecting untrusted callers (`llm`, `planner`, `model`, `tool`, `assistant_model`, `prompt`, `untrusted`), `PermissionConflictError` blocking grant creation over active `DENY` rules, automatic expiration of TTL / used one-time grants, and revocation listener callbacks.
- **`core/security/audit.py`**:
  - `PermissionAuditLogger` with `sanitize_resource_label()` (collapsing `path:`/`dir:` values to basenames and redacting bearer/session tokens and `password=`/`secret=`/`api_key=` strings) and user-session-authenticated `list_audit_events()`.
- **`core/security/manager.py` & `core/security/confirmation.py`**:
  - `SecurityManager.check_for_user()` integrates `PermissionManager` + legacy `UserPermissionStore` (neither can bypass `DENY`; sensitive `CONFIRMATION_ACTIONS` retain confirmation requirements even under `ALLOW`).
  - `ConfirmationManager.cancel_matching()` selectively cancels pending confirmation tokens by `(user_id, permission_key, tool_names, session_id)` on revocation/deny.
- **Execution & Anti-Injection (`core/agent/`)**:
  - `RESERVED_TOOL_PARAMETERS` in `core/agent/tool_registry.py` and `core/agent/tool_executor.py` expanded to reject `confirmation_token`, `user_id`, `session_id`, `owner_id`, `auth_token`, `identity`, `permission`, `permissions`, `permission_override`, `policy`, `approved`, `grant`, `bypass_confirmation`, and `sudo`.
  - `FORBIDDEN_PERMISSION_ADMIN_TOOLS` prevents registering or executing permission/account-admin tool names (`grant_permission`, `set_permission`, `allow_permission`, `override_permission`, `revoke_permission`, `deny_permission`, `reset_permissions`, `switch_user`, `login_user`).
  - `AgentPlanner` and `ToolDecisionEngine` prompts explicitly instruct models they cannot grant, modify, or override permissions or `DENY` rules.
  - `RIYAAssistant` (`core/agent/assistant.py`), `RiyaAgent` (`core/agent/agent.py`), `MemoryManager` (`core/memory/manager.py`), `UserPreferencesService`, and `ConversationHistoryStore` (`core/identity/manager.py`) integrate `PermissionManager`; `_update_context()` handles denied `personal.history` without breaking turn processing while blocking persistence.
- **Voice & User Interfaces (`core/voice/` & `core/main.py`)**:
  - `VoicePermissionError(VoiceError, PermissionError)` added in `core/voice/errors.py`.
  - `VoiceOrchestrator` enforces `voice.microphone`, `voice.speaker`, `voice.config`, and `voice.device`, drops out of voice mode if `voice.microphone` is revoked while active, and safeguards `MagicMock` test doubles via `isinstance` checks.
  - `VoiceUIController` saves per-user voice configs via `VoiceConfig.save_for_user()` when authenticated and exposes `list_permissions`, `grant_permission`, `deny_permission`, `revoke_permission`, `reset_permissions`, `audit_history`.
  - `launch_voice_gui` adds a Tkinter **Permissions & Trust** modal with unchecked-by-default persistent approval confirmation, and `core/main.py` provides `/permissions [list|grant|deny|revoke|reset|audit]`.

---

## 5. Files Created & Modified

**Created:**
- `core/security/audit.py`
- `core/security/permissions.py`
- `tests/test_permission_and_trust.py`
- `RIYA_V0_5_PHASE_2_IMPLEMENTATION_REPORT.md`

**Modified:**
- `core/database/migrations.py`
- `core/security/__init__.py`
- `core/security/confirmation.py`
- `core/security/manager.py`
- `core/agent/tool_registry.py`
- `core/agent/tool_executor.py`
- `core/agent/planner.py`
- `core/agent/tool_decision.py`
- `core/agent/agent.py`
- `core/agent/assistant.py`
- `core/memory/manager.py`
- `core/identity/manager.py`
- `core/voice/errors.py`
- `core/voice/orchestrator.py`
- `core/voice/ui.py`
- `core/main.py`
- `tests/test_multiuser_identity_and_isolation.py` (schema version checks updated to `CURRENT_SCHEMA_VERSION`)

Backups with suffix `.before_v05_phase2` were preserved alongside existing `.before_v05_phase1` backups.

---

## 6. Verification Evidence

1. **Bytecode Compilation:** `python -m compileall -q core tests` — passed (exit code `0`).
2. **Automated Test Suite (`pytest -q`):** `180 passed, 0 failed, 0 skipped` (`161` baseline v0.4 + Phase 1 tests preserved, plus `19` new Phase 2 permission, security, concurrency, anti-injection, voice/UI, CLI, and two-user E2E tests in `tests/test_permission_and_trust.py`).
   - *Mocking note:* In `tests/test_permission_and_trust.py`, GUI macOS app launch (`MacOSPlatform.open_application`), STT/TTS/microphone hardware streams, CLI `input()`, and the fast CI assistant workflow stub (`_StubAI` / `_InjectedAI`) are mocked to stay deterministic and headless; SQLite storage, migrations, triggers, `IdentityManager`, `PermissionManager`, `SecurityManager`, `ConfirmationManager`, `ToolRegistry`, `ToolExecutor`, `MemoryManager`, `UserPreferencesService`, `ConversationHistoryStore`, and `PermissionAuditLogger` execute unmocked against real temp SQLite databases.
3. **Unmocked Real-Model Offline Two-User E2E Verification:** Executed standalone script with real `models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (`LocalLlamaRuntime`), verifying default-deny block before grant, Alice granting `filesystem.create`/`filesystem.read` and confirming file creation, switching to Bob (zero permissions inherited, unable to read Alice's workspace), switching back to Alice, staging `write_file` overwrite confirmation (`filesystem.modify`), revoking `filesystem.modify`, confirming stale pending action is invalidated/blocked, and original file content stays intact (`REAL_MODEL_OFFLINE_E2E: PASS`).
4. **Live DB Migration Check:** Verified `/Users/tukaramgore/Projects/RIYA/data/riya.db` reports `LIVE_DB_VERSION: 3` with `permission_grants`, `permission_audit_log`, `trg_permission_audit_no_update`, and `trg_permission_audit_no_delete` present.
5. **Known Scope Boundary:** Tkinter desktop widgets in `launch_voice_gui()` were verified through Python compilation and `VoiceUIController` method/state tests rather than launching an interactive Cocoa/Tk window event loop in CI.
