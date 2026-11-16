# RIYA v0.5 — Phase 1 Implementation Report: Multi-User Identity & Data Isolation

**Project:** RIYA — Offline-First Personal AI Assistant  
**Project Path:** `/Users/tukaramgore/Projects/RIYA`  
**Phase:** v0.5 Phase 1 (Multi-User Identity & Data Isolation)  
**Baseline:** RIYA v0.4 (`146` tests passing prior to Phase 1 edits)

---

## 1. Inspected Baseline & Migration Findings

- Verified pre-Phase-1 baseline: `146 passed in 1.47s`, `python -m compileall -q core tests` exit `0`.
- Inspected existing `data/riya.db`: single `memories` table `(id, category, key, value, created_at, updated_at, UNIQUE(category, key))` containing `5` existing rows; `core/database/` existed as an empty directory.
- Backed up every modified module and `data/riya.db` to `*.before_v05_phase1` before making changes (no commits or pushes performed).

---

## 2. Files Created & Modified

### Created
- [core/database/__init__.py](file:///Users/tukaramgore/Projects/RIYA/core/database/__init__.py) — exports migration APIs and `CURRENT_SCHEMA_VERSION = 2`.
- [core/database/migrations.py](file:///Users/tukaramgore/Projects/RIYA/core/database/migrations.py) — atomic transactional migrations (`0001_baseline_memories`, `0002_multi_user_identity_and_isolation`) and `rollback_migration()`.
- [core/identity/__init__.py](file:///Users/tukaramgore/Projects/RIYA/core/identity/__init__.py) — public identity exports.
- [core/identity/errors.py](file:///Users/tukaramgore/Projects/RIYA/core/identity/errors.py) — `IdentityError`, `MissingIdentityError`, `InvalidIdentityError`, `AuthenticationError`, `UserAlreadyExistsError`, `UserNotFoundError`.
- [core/identity/models.py](file:///Users/tukaramgore/Projects/RIYA/core/identity/models.py) — passive `UserProfile` (`is_authenticated == False`) separated from frozen `AuthenticatedIdentity` (with `session_token` redacted in `__repr__`).
- [core/identity/context.py](file:///Users/tukaramgore/Projects/RIYA/core/identity/context.py) — `ExecutionContext`, `ExecutionContextScope`, `get_current_execution_context()` using `contextvars.ContextVar`.
- [core/identity/manager.py](file:///Users/tukaramgore/Projects/RIYA/core/identity/manager.py) — `IdentityManager`, `UserPreferencesService`, `UserPermissionStore`, `ConversationHistoryStore`.
- [tests/test_multiuser_identity_and_isolation.py](file:///Users/tukaramgore/Projects/RIYA/tests/test_multiuser_identity_and_isolation.py) — `15` multi-user security, migration/rollback, concurrency, and E2E isolation tests.

### Modified
- [core/memory/database.py](file:///Users/tukaramgore/Projects/RIYA/core/memory/database.py) — enables `PRAGMA foreign_keys = ON`, restricts DB file mode to `0o600`, executes idempotent schema migrations, exposes `schema_version()`, `rollback_to()`, `has_registered_users()`, `count_legacy_unclaimed_memories()`.
- [core/memory/manager.py](file:///Users/tukaramgore/Projects/RIYA/core/memory/manager.py) — scopes all memory operations (`remember`, `update_memory`, `recall`, `search`, `list_memories`, `forget`, `clear_category`) by validated `user_id`, fails closed when users exist or identity is required without a valid session, adds explicit `claim_legacy_memories()`.
- [core/memory/service.py](file:///Users/tukaramgore/Projects/RIYA/core/memory/service.py) — honors active `ExecutionContextScope.memory_manager`.
- [core/tools/memory.py](file:///Users/tukaramgore/Projects/RIYA/core/tools/memory.py) — routes memory tool calls through active scoped `MemoryManager`.
- [core/tools/filesystem/files.py](file:///Users/tukaramgore/Projects/RIYA/core/tools/filesystem/files.py) — confines `_allowed_roots()` and relative path resolution to `ExecutionContext.allowed_roots` / `workspace_dir` and blocks access to `data/users` across users.
- [core/security/confirmation.py](file:///Users/tukaramgore/Projects/RIYA/core/security/confirmation.py) — binds `ConfirmationGrant` and `_fingerprint()` to `(user_id, session_id, tool_name, parameters)` and adds `cancel_for_session()`.
- [core/security/manager.py](file:///Users/tukaramgore/Projects/RIYA/core/security/manager.py) — adds `check_for_user()` enforcing authenticated identity checks and per-user `UserPermissionStore` policies (`allow` / `confirm` / `deny`).
- [core/agent/tool_registry.py](file:///Users/tukaramgore/Projects/RIYA/core/agent/tool_registry.py) — blocks registration or passing of reserved parameters (`confirmation_token`, `user_id`, `session_id`, `owner_id`, `auth_token`, `identity`).
- [core/agent/tool_executor.py](file:///Users/tukaramgore/Projects/RIYA/core/agent/tool_executor.py) — validates session identity before execution and before confirmation revalidation, enforces per-user policies, binds confirmations to `(user_id, session_id)`, runs tool + post-execution verification inside `ExecutionContextScope`, and cancels prior session confirmations in `set_identity()`.
- [core/agent/agent.py](file:///Users/tukaramgore/Projects/RIYA/core/agent/agent.py) — adds identity options, `set_identity()`, `current_identity`, and `user_context` forwarding.
- [core/agent/assistant.py](file:///Users/tukaramgore/Projects/RIYA/core/agent/assistant.py) — supports `IdentityManager`, `set_identity()`, `switch_user()`, `logout()`, isolated per-user `ConversationContext` map, `ConversationHistoryStore` persistence, cross-user `pending_action` / `pending_plan` isolation, and per-turn `ExecutionContextScope`.
- [core/voice/config.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/config.py) — adds per-user `path_for_user()`, `recordings_dir_for_user()`, `load_for_user()`, `save_for_user()` (`0o700` dirs, `0o600` settings file).
- [core/voice/session.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/session.py) — tracks `user_id` / `auth_session_id` and clears transcript buffer on `bind_user()`.
- [core/voice/orchestrator.py](file:///Users/tukaramgore/Projects/RIYA/core/voice/orchestrator.py) — adds `switch_identity()` cancelling active speech/worker turns and dropping safely to text mode.
- [core/main.py](file:///Users/tukaramgore/Projects/RIYA/core/main.py) — integrates offline CLI login/account creation (`--user`), `/whoami`, `/create-user`, `/switch-user`, `/logout`, and per-user `VoiceConfig`.

---

## 3. Database Schema & Safe Migration Strategy

- `schema_migrations (version, name, applied_at)` tracks applied revisions inside an explicit `BEGIN IMMEDIATE` SQLite transaction.
- `users`: immutable UUID `user_id`, unique normalized `username`, `display_name`, `auth_method`, salted PBKDF2-HMAC-SHA256 (`310,000` iterations) `credential_hash` + `credential_salt`, isolated `workspace_dir`, `is_active`, timestamps.
- `user_sessions`: `session_id`, `user_id` FK (`ON DELETE CASCADE`), `token_hash` (SHA-256 of CSPRNG `secrets.token_urlsafe(32)`), `expires_at`, `revoked_at`, `last_seen_at`.
- `user_preferences`: `(user_id, namespace, pref_key)` JSON store covering `preferences`, `app_settings`, `automation`, and `voice`.
- `user_permissions`: `(user_id, tool_name, policy IN ('allow', 'confirm', 'deny'))` FK `ON DELETE CASCADE`.
- `conversation_history`: `(user_id, session_id, role, content, created_at)` FK `ON DELETE CASCADE`.
- `memories`: migrated non-destructively from v0.4 (`UNIQUE(category, key)`) to `(id, user_id REFERENCES users(user_id) ON DELETE CASCADE, legacy_unclaimed, category, key, value, created_at, updated_at, UNIQUE(user_id, category, key))` plus partial unique index `idx_memories_legacy_unique ON memories(category, key) WHERE user_id IS NULL`.
- Existing `data/riya.db` verification after migration: `schema_migrations = [(1, '0001_baseline_memories'), (2, '0002_multi_user_identity_and_isolation')]`, `memories total = 5`, `legacy unclaimed = 5`. Unclaimed records are never auto-assigned or leaked to any user unless claimed explicitly via `MemoryManager.claim_legacy_memories(identity)`.
- Rollback safety: `db.rollback_to(1)` restores v0.4 `memories` cleanly when no cross-user `(category, key)` collision exists and raises `MigrationError` rather than dropping data when collisions exist.

---

## 4. Security Boundaries Implemented

- **Passive Profile vs. Validated Session:** `UserProfile.is_authenticated` is `False`; `IdentityManager.validate_session()`, `MemoryManager`, `ToolExecutor`, and `RIYAAssistant` reject `UserProfile`, forged `AuthenticatedIdentity`, tampered `user_id`, revoked sessions, and expired sessions.
- **Zero Plaintext Secrets/Tokens Stored or Logged:** Password/PIN credentials use per-user CSPRNG 16-byte salts + PBKDF2-HMAC-SHA256 (`310,000` iterations) and constant-time `hmac.compare_digest`. Session tokens are stored only as SHA-256 digests and masked in `AuthenticatedIdentity.__repr__()`.
- **Execution-Time Context Injection (`user_id` Spoofing Blocked):** `ToolRegistry`, `AgentPlanner`, `ToolDecisionEngine`, `ToolExecutor.execute()`, and `ToolExecutor.execute_plan()` reject `user_id`, `session_id`, `owner_id`, `auth_token`, `identity`, and `confirmation_token`. Active user scope is injected solely via verified `ExecutionContextScope`.
- **Confirmation Token Binding & Switch Cancellation:** Every confirmation grant records and fingerprints `(user_id, session_id, tool_name, parameters)`. Cross-user or cross-session confirmation consumption fails, and `set_identity()` / `logout()` revokes pending confirmations for the outgoing session immediately.
- **Filesystem Sandbox Per User:** Under an active `ExecutionContext`, `_allowed_roots()` restricts reads, writes, directory creation, and post-execution verification strictly to the active user's `workspace` (`0o700`), rejecting `../` escapes and cross-user paths.

---

## 5. Verification Results (Observed After Final Edit)

- **Full Automated Test Suite:** `pytest -q` -> **`161 passed in 4.02s`** (`0` failed, `0` skipped; includes `146` existing v0.4 tests + `15` new multi-user security & isolation tests in `tests/test_multiuser_identity_and_isolation.py`).
- **Bytecode Compilation:** `python -m compileall -q core tests` -> exit code `0`.
- **Real Offline Two-User E2E Run (Unmocked `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` + Temporary SQLite DB):**
  - Authenticated `alice`, prompted `"Remember that my favorite project is Nebula"` -> `"I'll remember 'Nebula' under 'preferences' as 'my_favorite_project'. Proceed?"` -> `"yes"` -> `"Remembered 'my_favorite_project' under 'preferences'."` -> recalled `"Nebula"`.
  - Switched `RIYAAssistant.set_identity(bob)` -> Bob recall returned `"I don't have any memory related to 'favorite_project'."` and independent Qwen3 chat response.
  - Called `assistant.logout()` -> subsequent request failed closed with `"Authentication required: Authenticated user session is required before accessing RIYA."`.

---

## 6. Known Limitations & Recommended Manual Checks

- **Single-Process CLI Auth Model:** `core/main.py` assumes a trusted local interactive terminal for `input()` / `getpass.getpass()`; non-interactive GUI/daemon integrations should supply an external verified session or passphrase callback.
- **Legacy Shared Paths Outside Multi-User Execution:** When `ToolExecutor` / `MemoryManager` is used standalone on a zero-user DB (`0` rows in `users`, no `require_identity` / `identity_manager`), v0.4 single-userless compatibility mode stays active so legacy v0.4 workflows/tests do not break. Once any user is registered, memory access fails closed without an identity.
- **Recommended Manual Checks:**
  1. Launch `python -m core.main` interactively and verify creating two accounts, `/whoami`, `/switch-user`, `/logout`, and claiming legacy `data/riya.db` memories if desired.
  2. Test `python -m core.main --voice --user <username>` and verify per-user `voice_settings.json` creation under `data/users/<user_id>/voice/` with mode `0600`.
