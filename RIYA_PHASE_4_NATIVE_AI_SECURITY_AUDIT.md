# RIYA v0.4 — Phase 4: Native AI Engine & LLM Tool-Calling Security Audit

**Project:** RIYA v0.4 (Offline-First Personal AI Assistant)  
**Location:** `/Users/tukaramgore/Projects/RIYA`  
**Developer:** Tukaram  
**Audit Date:** 2026-11-14  

---

## 1. Executive Summary

Phase 4 audited and hardened the complete trust boundary between RIYA's offline local LLM runtime and its host-system execution layers (`filesystem`, `macos` automation, and SQLite `memory`).

Before Phase 4, malformed model outputs containing escaped quotes or quoted braces (`}`), duplicate JSON objects or keys, missing or mistyped tool parameters, prompt-control tokens (`<|im_start|>`, `<|im_end|>`), unbounded context/output payloads, and missing inference timeouts could crash parsers, bypass intent validation, fall through to unintended chat inference on planner failure, risk use-after-free in native memory management when closing after a hung C call, or leak private project files inside `FILESYSTEM_ALLOWED_ROOTS` (such as `.env` or `data/riya.db`).

All identified Python-layer issues were remediated without adding cloud dependencies, external frameworks, or third-party packages, and without weakening Phase 1–3 confirmation, filesystem, or macOS security controls. After final edits, all **112 automated tests pass** (`92` existing baseline tests + `20` new Phase 4 regression tests, `0` failed, `0` skipped) and `python -m compileall -q core tests` exited with code `0`. Real GGUF C++ inference against a live weights file was **not** executed during verification; native runtime wrapper hardening was tested via mocked native/Python hooks (`SimpleNamespace`, `Mock`, stubbed `ctypes` callbacks), and remaining native C++ limitations in `core/ai/native/bridge/riya_llama.cpp` are explicitly documented in Sections 12–14.

---

## 2. Actual AI Engine Architecture

RIYA's native offline inference stack is composed of pure Python orchestration over a thin C++/FFI wrapper around `llama.cpp`:

- **Low-level Native Wrapper (`core/ai/native/llama_runtime.py`, `NativeLlamaRuntime`):** Loads `libriya_llama.dylib` via `ctypes`, validates model paths, initializes a native handle, serializes generation behind a `threading.Lock`, enforces bounded `context_size` (`128..32768`) and `max_tokens` (`1..min(2048, context_size)`), caps prompt UTF-8 byte length (`min(context_size * 8, 65536)`), sanitizes ChatML/control characters, enforces Python wall-clock timeouts via `run_with_timeout`, supports cooperative `cancel()`/`reset_cancel()`, and defers `riya_free_model` if `close()` is called while a C inference worker is still in flight.
- **C++ Native Bridge (`core/ai/native/bridge/riya_llama.cpp`, unchanged/not recompiled in Phase 4):** Implements `build_chat_prompt` (which hardcodes a system message naming `"created by Tularam"` and places the caller's prompt inside `messages[1]` with role `"user"` via `llama_chat_apply_template`) and `riya_generate` (which writes up to `output_size` bytes—`16384` from Python—into the caller buffer).
- **High-level Runtime Adapter (`core/ai/local_runtime.py`, `LocalLlamaRuntime`):** Selects the native library backend and Qwen3 GGUF model path (`is_file()` verified), isolates `<conversation_history>` and `<user_input>` inside tagged untrusted blocks with instruction-hierarchy guidance, validates constructor limits (`max_prompt_chars`, `max_output_chars`, `timeout_seconds`), propagates cancellation/timeouts, and bounds returned text (`bound_model_output`).
- **Conversation Memory Window (`core/agent/context.py`, `ConversationContext`):** Stores rolling turns strictly with roles `{"user", "assistant"}`, strips unsafe control chars, neutralizes ChatML tokens and spoofed role-line prefixes (`User:`, `RIYA:`, `System:`, `Assistant:` -> `[quoted-*]:`) via `neutralize_roles=True`, and trims to `max_messages`, `max_message_chars=1000`, and `max_total_chars=4000` (FIFO).
- **Safety Primitives (`core/ai/safety.py`):** Centralizes bounds (`MAX_USER_INPUT_CHARS=4000`, `MAX_PROMPT_CHARS=12000`, `MAX_CONTEXT_CHARS=4000`, `MAX_MODEL_RESPONSE_CHARS=32768`, `DEFAULT_INFERENCE_TIMEOUT_SECONDS=30.0`, `MIN_CONTEXT_SIZE=128`, `MAX_CONTEXT_SIZE=32768`, `MAX_GENERATION_TOKENS=2048`), control/delimiter sanitization, timeout execution (`run_with_timeout`), `InferenceTimeoutError`, and `InferenceCancelledError`.
- **Strict JSON Output Parser (`core/agent/structured_output.py`):** Provides `parse_strict_json_object` enforcing single-object output, size limits (`<=32768`), max JSON nesting depth (`<=6`), duplicate key rejection via `object_pairs_hook`, and rejection of non-standard `NaN`/`Infinity` constants via `parse_constant`.
- **Execution & Authorization Layers:**
  - `ToolRegistry` (`core/agent/tool_registry.py`): Defines `ParameterSchema` and `BUILTIN_TOOL_SCHEMAS` for all 19 tools (`validate_parameters`).
  - `SecurityManager` & `ConfirmationManager` (`core/security/manager.py`, `core/security/confirmation.py`): Fail-closed tool classification (`READ_ONLY`, `CONFIRMATION_REQUIRED`) and cryptographic single-use parameter-bound confirmation tokens.
  - `AgentPlanner` (`core/agent/planner.py`) & `ToolDecisionEngine` (`core/agent/tool_decision.py`): Untrusted proposal engines returning validated `Plan`/`PlanStep` or `ToolDecision` objects.
  - `ToolExecutor` (`core/agent/tool_executor.py`), `RiyaAgent` (`core/agent/agent.py`), `CommandExecutor` (`core/agent/executor.py`), `MemoryManager` (`core/memory/manager.py`), `MemoryService` (`core/memory/service.py`), `files.py` (`core/tools/filesystem/files.py`), and `RIYAAssistant` (`core/agent/assistant.py`).
- **Stub / Unused Modules Verified:** `core/automation/__init__.py` and `core/browser/__init__.py` are empty, `core/database/` and `apps/*` contain no runnable code, and no streaming generation is implemented anywhere in `core/`.

---

## 3. Model-to-Tool Execution Flow

```text
User Input
  │
  ▼
RIYAAssistant.process() [core/agent/assistant.py]
  ├─ validate_user_input(max_chars=4000) + control char cleanup
  ├─ Pending confirmation active? -> _handle_confirmation() [strict "yes"/"no" handling]
  ├─ MemoryIntent detected?
  │     ├─ RECALL / LIST -> MemoryService.execute_safe() [read-only; REMEMBER/FORGET blocked]
  │     └─ REMEMBER / FORGET -> ToolExecutor (requires confirmation before DB mutation)
  ├─ Deterministic Intent != CHAT? -> CommandExecutor.execute() -> ToolExecutor
  └─ Else -> AgentPlanner.create_plan() [core/agent/planner.py]
        │
        ▼  (model output is UNTRUSTED data)
     LocalLlamaRuntime.generate() -> NativeLlamaRuntime.generate() [lock + timeout + cancel]
        │
        ▼
     parse_strict_json_object() [single object, depth <= 6, no duplicate keys, <= 32768 chars]
        │
        ▼
     Plan schema checks [steps <= 8, no duplicate steps, ToolRegistry.validate_parameters()]
        │
        ▼
     _execute_plan_steps() -> ToolExecutor.execute()
        ├─ Rejects injected confirmation_token / invalid params
        ├─ Revalidates tool + parameters + SecurityManager + ConfirmationManager immediately before execution
        ├─ If confirmation_required -> pauses plan, locks pending_action=(step_action, deepcopy(step_params)),
        │                              renders explicit preview via _format_action_preview(), asks "Proceed?"
        └─ On failure/exception/verification failure -> clears pending_plan & pending_action, halts plan immediately
```

Alternate direct entrypoints (`ToolDecisionEngine.decide()`, `RiyaAgent.decide_and_execute()`, `RiyaAgent.execute()`, `CommandExecutor.execute()`, and `ToolExecutor.execute_plan()`) all route through `ToolRegistry.validate_parameters()` and `ToolExecutor` + `SecurityManager` + `ConfirmationManager`, preventing bypass of confirmation or schema constraints. No `eval()` or `exec()` exists anywhere in model/tool pipelines.

---

## 4. Vulnerabilities Discovered & 5. Severity Classifications

| ID | Component | Vulnerability Description | Severity | Status |
|---|---|---|---|---|
| **V1** | `core/agent/tool_decision.py` | Naive `{`/`}` brace counting broke on `}` inside string literals, ignored trailing JSON objects (duplicate calls), allowed missing `"tool"` (`{}`), and permitted unexpected keys (e.g. `confirmation_token`, `approved`). | **High** | **Fixed & Verified** |
| **V2** | `core/agent/planner.py` | `raw_decode` ignored trailing JSON objects, did not require `"steps"`, allowed duplicate JSON keys, allowed identical duplicate plan steps, lacked nesting/size bounds, and embedded raw user input without untrusted-data isolation tags. | **High** | **Fixed & Verified** |
| **V3** | `core/agent/tool_registry.py` | `validate_parameters` only checked for unknown parameter keys; missing required args, wrong types (`bool` accepted as `int`), `None` values, nested `dict`/`list` payloads, oversized strings, and control bytes (`\x00`, `\n`) could reach tools directly. | **High** | **Fixed & Verified** |
| **V4** | `core/ai/local_runtime.py`, `core/ai/native/llama_runtime.py` | Missing ChatML/control-token sanitization (`<|im_start|>`, `<|im_end|>`, `\x00`), no inference timeouts or cancellation, no `threading.Lock` on native C++ handle, and use-after-free risk if `close()` freed `handle` while a timed-out worker thread remained inside `riya_generate`. | **High** | **Fixed & Verified (mocked native/runtime tests)** |
| **V5** | `core/agent/assistant.py`, `core/agent/context.py` | Bare `except Exception` on planner errors fell through to a second `self.ai.generate(user_input)` chat call (planner-breakout DoS & masking errors); unsanitized user/memory/tool text entered `ConversationContext` unbounded; confirmation prompts omitted target tool/path visibility (or truncated paths from the right, hiding filenames); `pending_action` was not deep-copied and locked against step mutation or rogue response dicts. | **High** | **Fixed & Verified** |
| **V6** | `core/agent/tool_executor.py`, `core/agent/agent.py`, `core/agent/executor.py` | Lacked pre-invocation parameter deep-copy & revalidation inside `ToolExecutor.execute`; `execute_plan` had an undocumented `"understand"` step bypass and no step cap; `(PermissionError, ValueError)` was not normalized as halting plan failures. | **Medium** | **Fixed & Verified** |
| **V7** | `core/tools/filesystem/files.py`, `core/memory/manager.py`, `core/memory/service.py` | Sensitive private files within `PROJECT_ROOT` (`.env*`, `riya.db*`, `.git`, `.ssh`, `*.pem`, `*.key`) were readable/discoverable by filesystem tools; `read_file`/`write_file`/`list_files` lacked size/entry caps; `MemoryManager` lacked bounds on `category`/`key`/`value`/query limits and silently converted `\n` in identifiers via `strip_unsafe_control_chars` instead of rejecting control chars. | **Medium** | **Fixed & Verified** |
| **V8** | `core/ai/native/bridge/riya_llama.cpp` | Compiled C++ bridge hardcodes `"created by Tularam"` in `build_chat_prompt`, wraps incoming prompt under `"user"` role, and `riya_generate` has no native mid-generation abort callback on Python timeout. | **Medium / Low** | **Mitigated at Python boundary; Native bridge deferred to Phase 5** |

---

## 6. Files Modified and Created

### Newly Created Files
1. `core/ai/safety.py` — Safety constants, control-token/role sanitization, input/output bounding, `InferenceTimeoutError`, `InferenceCancelledError`, and `run_with_timeout`.
2. `core/agent/structured_output.py` — Strict JSON object parser (`parse_strict_json_object`).
3. `tests/test_native_ai_security.py` — 20 Phase 4 security regression tests.
4. `RIYA_PHASE_4_NATIVE_AI_SECURITY_AUDIT.md` — This audit report.

### Modified Production Files & Non-Overwriting Backups (`*.before_phase4`)
All 13 modified production files were backed up cleanly before editing:
1. `core/agent/agent.py` (`core/agent/agent.py.before_phase4`)
2. `core/agent/assistant.py` (`core/agent/assistant.py.before_phase4`)
3. `core/agent/context.py` (`core/agent/context.py.before_phase4`)
4. `core/agent/executor.py` (`core/agent/executor.py.before_phase4`)
5. `core/agent/planner.py` (`core/agent/planner.py.before_phase4`)
6. `core/agent/tool_decision.py` (`core/agent/tool_decision.py.before_phase4`)
7. `core/agent/tool_executor.py` (`core/agent/tool_executor.py.before_phase4`)
8. `core/agent/tool_registry.py` (`core/agent/tool_registry.py.before_phase4`)
9. `core/ai/local_runtime.py` (`core/ai/local_runtime.py.before_phase4`)
10. `core/ai/native/llama_runtime.py` (`core/ai/native/llama_runtime.py.before_phase4`)
11. `core/memory/manager.py` (`core/memory/manager.py.before_phase4`)
12. `core/memory/service.py` (`core/memory/service.py.before_phase4`)
13. `core/tools/filesystem/files.py` (`core/tools/filesystem/files.py.before_phase4`)

---

## 7. Fixes Implemented

1. **Sanitization, Hierarchy Isolation, and Timeouts (`core/ai/safety.py`, `core/ai/local_runtime.py`, `core/ai/native/llama_runtime.py`):**
   - Neutralizes `<|`, `|>`, `<s>`, `[INST]`, `<<SYS>>`, XML prompt tags, and control bytes (`\x00`..`\x1f`, `\x7f`); isolates history in `<conversation_history>` and user requests in `<user_input>` / `<user_request>`.
   - Adds `threading.Lock`, `_in_flight` tracking, `run_with_timeout`, `cancel()`, `reset_cancel()`, context/token bounds, and deferred native handle teardown in `NativeLlamaRuntime.close()`.
2. **Strict Structured Output Parsing (`core/agent/structured_output.py`, `core/agent/planner.py`, `core/agent/tool_decision.py`):**
   - Rejects malformed JSON, non-string/empty/oversized (`>32768`) payloads, trailing prose or second JSON objects, nesting depth `>6`, duplicate JSON keys, and `NaN`/`Infinity`.
   - Enforces required keys (`"steps"` for planner with exact top-level key set `{"steps"}` and step keys `<= {"action", "parameters", "description"}`; `"tool"` for decision engine with top-level keys `<= {"tool", "parameters", "reason"}`), rejects duplicate steps in a single plan, caps `max_steps=8`, and rejects leading/trailing whitespace in tool names.
3. **Comprehensive Tool Parameter Schema Validation (`core/agent/tool_registry.py`):**
   - Added `ParameterSchema` and `BUILTIN_TOOL_SCHEMAS` covering all 19 built-in tools, enforcing `^[a-zA-Z_][a-zA-Z0-9_]{0,63}$` identifier format, required parameters, exact primitive types (`bool` rejected for `int`), nullability, integer bounds (`set_volume` `0..100`), max string length (`MAX_PARAM_STRING_LENGTH=200_000`), control-character rejection, nested collection rejection (`dict`/`list`/`set`/`tuple`), and blocking reserved `"confirmation_token"`.
4. **Execution & Confirmation Hardening (`core/agent/tool_executor.py`, `core/agent/agent.py`, `core/agent/executor.py`, `core/agent/assistant.py`):**
   - `ToolExecutor.execute` deep-copies parameters and revalidates tool existence, schemas, `SecurityManager`, and `ConfirmationManager` immediately before calling `tool.function`. Removed `"understand"` bypass in `execute_plan`; capped plans at `max_plan_steps=8`; normalized `(PermissionError, ValueError)` failures to stop execution immediately.
   - `RiyaAgent.decide_and_execute` validates decisions and string keys prior to `execute`; `CommandExecutor.execute` normalizes `CREATE_FOLDER` `path`/`folder_name`.
   - `RIYAAssistant` bounds user input (`MAX_USER_INPUT_CHARS=4000`), isolates planner error handling without falling back to a second `ai.generate` call, locks `pending_action` to `(step_action, copy.deepcopy(step_params))`, and formats confirmation previews with `_format_action_preview` preserving both root and basename for long `path` values.
5. **Memory & Private Filesystem Boundaries (`core/memory/manager.py`, `core/memory/service.py`, `core/tools/filesystem/files.py`, `core/agent/context.py`):**
   - `MemoryManager._validate_identifier` rejects empty, oversized (`category<=64`, `key<=128`), and control-character identifiers; caps `value<=2000`, `search<=50`, `list_memories<=100`.
   - `MemoryService.execute_safe` enforces `MemoryIntent` typing and blocks `REMEMBER`/`FORGET`.
   - `files.py` blocks and excludes `.env*`, `.git`, `.ssh`, `.aws`, `.gnupg`, `.kube`, `.docker`, `.netrc`, `.npmrc`, `.pypirc`, `.git-credentials`, history files, SSH keys, credential JSONs, `riya.db*`, and key/DB extensions (`.pem`, `.key`, `.p12`, `.pfx`, `.kdbx`, `.db`, `.sqlite`, `.sqlite3`), and bounds `MAX_READ_FILE_BYTES=262_144`, `MAX_WRITE_CHARS=200_000`, and `MAX_LIST_ENTRIES=500`.
   - `ConversationContext` bounds roles, message length, total size, and neutralizes ChatML/role-spoofing lines.

---

## 8. Tests Added

Created `tests/test_native_ai_security.py` containing **20 regression tests** covering all requested Phase 7 scenarios without network calls, model downloads, or destructive OS commands:

1. `test_malformed_model_output_is_rejected_while_valid_unicode_succeeds`
2. `test_unknown_and_whitespace_tools_are_rejected`
3. `test_invalid_parameters_fail_strict_schema_validation`
4. `test_unexpected_fields_missing_required_keys_and_deep_nesting_rejected`
5. `test_confirmation_token_injection_blocked_everywhere`
6. `test_confirmation_replay_fails`
7. `test_parameter_modification_after_approval_fails`
8. `test_unauthorized_tool_and_private_files_are_denied`
9. `test_planner_manipulation_limits_and_transparent_confirmation`
10. `test_plan_stops_after_execution_or_verification_failure`
11. `test_prompt_injection_in_user_input_is_sanitized_and_isolated`
12. `test_prompt_injection_through_retrieved_memory_is_neutralized_and_unprivileged`
13. `test_memory_writes_require_confirmation_and_service_blocks_mutations`
14. `test_excessive_model_output_is_rejected_safely`
15. `test_oversized_user_input_and_bounded_conversation_context`
16. `test_inference_timeout_cancellation_and_native_uaf_safety`
17. `test_invalid_structured_output_controls_recovery`
18. `test_duplicate_tool_calls_rejected`
19. `test_alternate_execution_paths_enforce_confirmation_and_schema`
20. `test_safe_recovery_after_model_errors_in_chat_and_planner`

---

## 9. Commands Executed

All commands below were executed after the final production and test edits (except the initial baseline run before edits):

- Baseline pre-edit:
  - `pytest -q`
- Focused Phase 4 and policy suites:
  - `pytest -v tests/test_native_ai_security.py`
  - `pytest -v tests/test_native_ai_security.py tests/test_security_policy.py`
- Confirmation & memory suites:
  - `pytest -v tests/test_confirmation_manager.py tests/test_confirmation_flow.py tests/test_memory_authorization.py`
- Filesystem & macOS security suites:
  - `pytest -v tests/test_filesystem_security.py tests/test_tool_executor_filesystem.py tests/test_macos_security.py`
- Full suite and Python bytecode compilation:
  - `pytest -q && python -m compileall -q core tests`
- Backup verification:
  - `find core tests -name "*.before_phase4" | sort && python -m compileall -q core tests`

---

## 10. Actual Test Results

- **Pre-edit Baseline:** `92 passed` in `0.15s` (exit code `0`).
- **`pytest -v tests/test_native_ai_security.py`:** `20 passed` in `0.24s` (exit code `0`).
- **`pytest -v tests/test_native_ai_security.py tests/test_security_policy.py`:** `26 passed` in `0.22s` (exit code `0`).
- **`pytest -v tests/test_confirmation_manager.py tests/test_confirmation_flow.py tests/test_memory_authorization.py`:** `28 passed` in `0.07s` (exit code `0`).
- **`pytest -v tests/test_filesystem_security.py tests/test_tool_executor_filesystem.py tests/test_macos_security.py`:** `39 passed` in `0.09s` (exit code `0`).
- **Full Post-Edit Suite (`pytest -q`):** **`112 passed` in `0.29s`** (`0` failed, `0` skipped, exit code `0`) — `92` baseline tests + `20` new tests, with zero skipped or weakened assertions.

---

## 11. Compilation Results

- `python -m compileall -q core tests` completed with exit code `0` and zero syntax or bytecode errors after the final edit.

---

## 12. Remaining Risks

1. **Untested Live C++ Model Binary Execution:** `NativeLlamaRuntime` and `LocalLlamaRuntime` security controls were verified using mocked runtime objects and stubbed `ctypes` callbacks; `libriya_llama.dylib` was not rebuilt or executed against an actual multi-GB `.gguf` weights file in this audit.
2. **In-Flight Native Thread Continuation on Python Timeout:** While `run_with_timeout` raises `InferenceTimeoutError` promptly and `NativeLlamaRuntime` prevents concurrent calls (`_in_flight`) and defers `riya_free_model` on `close()`, the underlying C++ `llama_decode` loop in `riya_llama.cpp` cannot be aborted mid-token until generation completes or hits `max_tokens`.
3. **Double User-Role Prompt Wrapping in `riya_llama.cpp`:** `riya_llama.cpp::build_chat_prompt` inserts a hardcoded system prompt and wraps all Python-supplied prompt text into `messages[1].role="user"`, meaning Python-side `planner`/`tool_decision` system instructions remain inside the user turn at the C++ chat-template boundary.
4. **Broad Default `PROJECT_ROOT` Membership in `FILESYSTEM_ALLOWED_ROOTS`:** Although private filenames/patterns (`.env*`, `riya.db*`, `.git`, `.ssh`, `*.pem`, `*.key`) are now denied and filtered, ordinary non-secret source files inside `PROJECT_ROOT` remain readable via `read_file`/`list_files` if `FILESYSTEM_ALLOWED_ROOTS` includes the repository root.

---

## 13. Known Limitations

- Single-user local security model (no multi-user authentication or per-user OS ACL isolation).
- Prompt injection defense relies on syntactic neutralization plus strict structural validation and host confirmation rather than ML-based prompt-injection classification.
- Deterministic intent matching (`IntentDetector`) runs ahead of the AI planner for explicit command phrases (such as `"tell me the time"`), which reduces unnecessary model calls but means ambiguous phrases could still route to deterministic commands before reaching the LLM.
- No streaming token output or `core/automation` / `core/browser` runtime exists in RIYA v0.4; those directories are placeholders.
- C++ native library (`libriya_llama.dylib`) compilation and GGUF runtime inference behavior remain unverified by live weights execution in this suite.

---

## 14. Recommended Phase 5

1. **Native C++ Bridge Hardening (`core/ai/native/bridge/riya_llama.cpp`):** Add an atomic abort flag checked inside the C++ decode loop (exposed via FFI to `NativeLlamaRuntime.cancel()`), support explicit separate `system_prompt` and `user_prompt` C API arguments instead of embedding Python system instructions inside `messages[1].role="user"`, and add C++ buffer bounds tests.
2. **Dedicated Workspace Sandbox Root:** Restrict `FILESYSTEM_ALLOWED_ROOTS` away from the full `PROJECT_ROOT` codebase to a dedicated user workspace/documents directory (or explicit read-only vs read-write root separation).
3. **Optional Offline Live-Model Smoke Suite:** Add a gated integration marker (e.g., `@pytest.mark.native_model`, skipped by default unless `RIYA_LOCAL_MODEL` exists) to exercise `libriya_llama.dylib` lifecycle, memory limits, and JSON adherence against the actual local Qwen3 GGUF model.
4. **Voice & Upcoming Automation/Browser Modules:** Apply the same `ToolRegistry` + `SecurityManager` + `ConfirmationManager` + `safety.py` sanitization boundary before enabling `core/voice/`, `core/automation/`, or `core/browser/` capabilities.
