# RIYA v0.4 — Final System Audit & Real End-to-End Verification Report

**Project:** RIYA v0.4 (Offline-First Personal AI Assistant)  
**Path:** `/Users/tukaramgore/Projects/RIYA`  
**Platform:** macOS (`arm64`, Apple MacBook Air M1, Python 3.13.9)  
**Developer:** Tukaram  
**Audit Status:** **COMPLETE — VERIFIED OFFLINE & END-TO-END** (`146 passed, 0 failed, 0 skipped`; Real M1 `Qwen3-4B` + CoreAudio + Silero VAD + `faster-whisper` E2E `[ALL_E2E_CHECKS_PASSED]` in `74.15s`, exit code `0`)

---

## 1. Executive Summary

An independent full-system audit of RIYA v0.4 (covering Phases 1–4 security hardening and Phase 5 real-time voice engine) was conducted against both automated test suites and **unmocked offline end-to-end execution** using RIYA's actual local LLM (`models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf` via `libriya_llama.dylib` on Apple M1 Metal), `SileroStreamingVAD` (`silero_vad_v6.onnx`), `faster-whisper` (`base`, `int8`), `/usr/bin/say`, and real macOS CoreAudio hardware (`MacBook Air Microphone` and `MacBook Air Speakers`) under `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`.

While Phase 5's implementation and `RIYA_PHASE_5_VOICE_ENGINE_IMPLEMENTATION_REPORT.md` matched the codebase accurately on unit-level behavior, real-model execution, concurrency inspection, and real-hardware end-to-end testing uncovered **7 real integration defects (Defects A–G)** that mocked tests had missed—including spurious LLM `remember()` tool planning on chat questions, a confirmation-staging race on plan interruption, dead `style` wording adaptation in `personality.py`, single-word Whisper `avg_logprob` under-calibration rejecting spoken `"Yes"` confirmations, a cross-thread PortAudio `abort()`/`close()` deadlock/SIGSEGV race in `AudioOutputPlayer.stop()`, an `onnxruntime` C++ telemetry worker teardown `SIGABRT` (`exit 134`) in `SileroStreamingVAD`, and a non-atomic `reset_cancel()` turn-cancellation window in `VoiceOrchestrator`.

All 7 defects were surgically remediated without rewriting Phase 5's architecture or weakening Phases 1–4 security policies. Every fix is backed by a dedicated regression test and verified end-to-end.

---

## 2. Independent Inspection of Phase 5 Report & Architecture Preservation

Inspected Phase 5 source modules (`core/voice/*.py`, `core/main.py`, `core/tools/filesystem/files.py`, `pyproject.toml`), all `7` Phase 5 test suites, and `RIYA_PHASE_5_VOICE_ENGINE_IMPLEMENTATION_REPORT.md`:

- **Accurate Claims Verified:**
  - `VoiceStateMachine` enforces all 8 states (`IDLE`, `LISTENING`, `TRANSCRIBING`, `THINKING`, `SPEAKING`, `INTERRUPTED`, `ERROR`, `STOPPING`).
  - `SileroStreamingVAD` uses bundled `silero_vad_v6.onnx` via `onnxruntime` offline.
  - `LocalSTT` enforces `local_files_only=True` by default and fails closed with `STTModelUnavailableError`.
  - `VoiceSession` isolates temporary WAV storage in `0o700` directories deleted on `stop()`/`close()`, and `core/tools/filesystem/files.py` blocks AI access to `voice_settings.json` (`0o600`) and `voice_recordings`.
  - Voice requests route exclusively through `RIYAAssistant.process()` -> `AgentPlanner` -> `ToolExecutor` -> `SecurityManager` -> `ConfirmationManager`.
  - `LocalTTS.supports_emotional_prosody` is honestly `False` (native macOS `/usr/bin/say` exposes rate and voice, not neural emotion).
- **Phase 1–5 Functionality & Security Preserved:** All `137` prior tests continue to pass after final audit fixes alongside `9` new audit regression tests (`146` total). Targeted no-clobber backups (`*.before_final_audit`) were created for every modified production file (`core/agent/planner.py.before_final_audit`, `core/agent/assistant.py.before_final_audit`, `core/voice/orchestrator.py.before_final_audit`, `core/voice/vad.py.before_final_audit`, `core/voice/stt.py.before_final_audit`, `core/voice/audio_output.py.before_final_audit`).

---

## 3. Confirmed Defects Discovered & Remediated

### Defect A — Real LLM Planner Hallucinated `remember()` on Conversational Questions
- **Discovery:** In unmocked end-to-end testing with `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`, asking `"In one short sentence, who created you and do you work offline?"` caused `AgentPlanner.create_plan()` to generate `remember(category='user_query', key='source_of_creation', value='Tularam')` instead of `{"steps": []}`.
- **Root Cause (`core/agent/planner.py`):** Prompt framing led with `"Convert the user's request into a safe sequence of tool actions"`, burying the `{"steps": []}` chat rule at rule #10, and always exposed `remember`/`forget_memory` in candidate tools even though `MemoryIntentDetector` already handles explicit memory commands cleanly.
- **Fix (`core/agent/planner.py`):**
  1. Promoted conversational `{"steps": []}` to Rule #1 and explicit `remember`/`forget_memory` prohibition to Rule #2.
  2. Added `_request_has_memory_mutation_intent(request)` checking explicit save/forget cues (`remember`, `memorize`, `save`, `store`, `forget`, `erase`, `keep in mind`, `note that`, `record that`, `delete/remove/clear memory`).
  3. Excluded `remember` and `forget_memory` from `_get_tool_descriptions()` and filtered any hallucinated `remember`/`forget_memory` steps post-parse when the request contains no memory-mutation intent.
- **Verification:** Real `Qwen3-4B` returned `pending=None, reply='I was created by Tukaram and work offline.'` (`[CHECK1_CONV]`); unit regression test `test_planner_does_not_hallucinate_remember_on_conversational_question` passed.

### Defect B — Stale Action / Pending-Confirmation Race on Turn Interruption
- **Discovery:** Code audit of `core/agent/assistant.py` and `core/voice/orchestrator.py`.
- **Root Cause:** `RIYAAssistant` had no internal `threading.Event` cancellation flag checked before or inside `_execute_plan_steps()`, nor did `run_with_timeout` in `process()` (chat path) receive one. Additionally, if interruption raced just as `assistant.process()` finished staging `assistant.pending_action`, `VoiceOrchestrator.handle_utterance_audio()` discarded the spoken response without revoking the newly minted confirmation token.
- **Fix (`core/agent/assistant.py`, `core/voice/orchestrator.py`):**
  1. Added `_get_cancel_event()` (lazily creating `self._cancel_event`), set in `cancel()` and cleared in `reset_cancel()`.
  2. Enforced cancellation before chat inference (passing `cancel_event=self._get_cancel_event()` into `run_with_timeout`), before `_execute_plan_steps()`, at the top of each loop iteration in `_execute_plan_steps()`, and before persisting `self.pending_action`/`self.pending_plan` (cancelling the unused token via `self.agent.cancel_confirmation` if cancelled). Re-raised `InferenceCancelledError` out of `_handle_confirmation` instead of swallowing it as a generic error.
  3. Added `_rollback_interrupted_pending_action()` in `VoiceOrchestrator.handle_utterance_audio()` so any confirmation token staged by an interrupted/superseded turn is cancelled via `agent.cancel_confirmation()` and `pending_action`/`pending_plan` are cleared.
- **Verification:** `test_interruption_mid_plan_execution_stops_subsequent_steps_and_clears_stale_pending_action` passed (verifying Step 2 `write_file` never runs and replaying the rolled-back token fails).

### Defect C — Dead `style` Wording Adaptation vs. Honest Non-Emotional TTS Capability
- **Discovery:** Audit of `core/voice/personality.py` line 84 (`del style`).
- **Finding:** `LocalTTS.supports_emotional_prosody = False` honestly reflects that macOS `/usr/bin/say` only adjusts WPM rate (`style_rate_wpm`). However, `prepare_text_for_speech(..., style=...)` completely discarded `style` (`del style`), so personality selection never influenced spoken wording or cadence.
- **Fix (`core/voice/personality.py`):** Added `style_prompt_instruction(style)` and `apply_response_style_wording(text, style=...)` wired into `prepare_text_for_speech()` across `friendly`, `calm`, `excited`, `empathetic`, and `professional` while strictly leaving security confirmation prompts (`"Proceed?"`, `"requires confirmation"`, `"Confirmation Needed"`) and internal error messages verbatim and preserving `LocalTTS.supports_emotional_prosody = False`.
- **Verification:** `test_expressive_response_wording_vs_honest_tts_prosody_capability` passed.

### Defect D — Whisper `avg_logprob` Under-Calibration Blocked Spoken Single-Word Confirmations
- **Discovery:** Discovered during real `SileroStreamingVAD` -> `faster-whisper base` E2E Check 3 (`Verify Voice Confirmation Gate`).
- **Root Cause (`core/voice/stt.py`):** When `SileroStreamingVAD` captures a real 1-word spoken confirmation (`"Yes"` or `"No"`) with pre-roll + trailing silence (~1.2s total), `faster-whisper`'s segment `avg_logprob` includes special/EOS tokens (`-0.719` for `"Yes."`, `no_speech_prob=0.10`, word probability `0.737`), producing `exp(avg_logprob) = 0.487 < 0.55` (`min_confirmation_confidence`). Thus `_check_voice_confirmation_gate()` rejected valid spoken `"Yes."` responses (`"I didn't catch that clearly enough to confirm a protected action..."`).
- **Fix (`core/voice/stt.py`):** In `LocalSTT.transcribe_detailed()`, kept `raw_conf = exp(mean_lp)` directly whenever `raw_conf < 0.40` or `mean_nsp > 0.25` (so noisy/low-confidence audio still fails the `0.55` gate), and calibrated only clear low-silence speech (`raw_conf >= 0.40 and mean_nsp <= 0.25`) using `max(raw_conf, 0.65 * raw_conf + 0.35 * (1.0 - mean_nsp))`.
- **Verification:** Real audio check `[CHECK3_CONFIRM]` passed end-to-end (`"Yes"` accepted, `audit_verified.txt` written); `test_short_utterance_stt_confidence_calibration_preserves_security_gate` verified clear `"Yes."` passes (`>= 0.55`) while noisy `"Yes."` (`avg_logprob=-1.2, no_speech_prob=0.45`) still fails (`< 0.55`).

### Defect E — Cross-Thread PortAudio Stream `abort()`/`close()` Deadlock & SIGSEGV (`exit 139`)
- **Discovery:** Discovered during real `MacBook Air Speakers` CoreAudio interruption E2E test (`task-878` exited with `alive=True` after 5.1s join timeout and code `139` segmentation fault at process exit).
- **Root Cause (`core/voice/audio_output.py`):** `AudioOutputPlayer.stop()` called `stream.abort()`, `stream.stop()`, and `stream.close()` directly on the interrupting thread while the playback worker thread was blocked inside PortAudio `Pa_WriteStream` (`stream.write(block)` in 25ms chunks). Concurrent `Pa_AbortStream`/`Pa_CloseStream` from a foreign thread during `Pa_WriteStream` is unsafe in PortAudio on macOS CoreAudio.
- **Fix (`core/voice/audio_output.py`):** Made `stop()` strictly signal `_stop_event` and update state flags (`_cancelled`, `_playing`, `_current_level`) without touching `stream` from the calling thread; `_stream_pcm()` checks `self._stop_event` every 25ms block and executes `("abort", "stop", "close")` cleanly on the stream-owning thread inside its `finally:` block.
- **Verification:** Real hardware interruption `[CHECK4_INTERRUPT]` exited cleanly (`completed=False, error=None, alive=False, cancelled=True, interrupt_ms=0.21, join_ms=126.8, exit code 0`); unit regression `test_audio_output_stop_does_not_close_stream_from_foreign_thread_during_write` passed.

### Defect F — `onnxruntime` Telemetry Background Thread `SIGABRT` Crash (`exit 134`) on Teardown
- **Discovery:** Observed during full real-model E2E execution after Metal/GGUF teardown: `onnxruntime_pybind11_state.so` spawned `Microsoft::Applications::Events::HttpClientManager` (`WorkerThread::threadFunc`) and terminated with `libc++abi: terminating due to uncaught exception of type std::__1::system_error: recursive_mutex lock failed: Invalid argument` (`exit code 134`).
- **Root Cause (`core/voice/vad.py`):** `SileroStreamingVAD.__init__()` initialized `onnxruntime.InferenceSession` without disabling ORT telemetry events, leaving an offline-unwanted HTTP/event worker thread active across process shutdown.
- **Fix (`core/voice/vad.py`):** Called `onnxruntime.disable_telemetry_events()` defensively in `SileroStreamingVAD.__init__()` before creating `ort.InferenceSession`.
- **Verification:** Full unmocked E2E run subsequently exited cleanly with `exit code 0` (`74.15s`, zero crash traces); unit regression `test_silero_vad_disables_onnx_telemetry_offline` passed.

### Defect G — Non-Atomic `reset_cancel()` Window Around Voice Confirmation & Text Turns
- **Discovery:** Concurrency audit of `VoiceOrchestrator.handle_utterance_audio()` and `VoiceOrchestrator.send_text()`.
- **Root Cause (`core/voice/orchestrator.py`):** Between `stt.transcribe_detailed()` / `send_text()` state setup and `self.assistant.process()`, `self.assistant.reset_cancel()` ran outside `self._lock` without re-verifying turn validity (`_is_turn_active(turn_id)` / `_turn_cancel_event`). An overlapping `interrupt()` arriving in that window could have its assistant cancellation cleared and allow `assistant.process()` (including a spoken confirmation `"Yes"`) to run on a cancelled turn; `send_text()` also lacked `InferenceCancelledError` handling and stale pending-action rollback.
- **Fix (`core/voice/orchestrator.py`):** Guarded `_is_turn_active(turn_id)` / `_turn_cancel_event` and `self.assistant.reset_cancel()` atomically under `self._lock` in both `handle_utterance_audio()` and `send_text()`, added `InferenceCancelledError` + stale check rollback in `send_text()`, and added `_rollback_interrupted_pending_action()` on unexpected `handle_utterance_audio()` exceptions.
- **Verification:** `test_interrupted_turn_id_never_resets_cancel_or_confirms_pending_action` verified an interrupted `"Yes"` turn never reaches execution and `write_file` never creates the target file.

---

## 4. Test Inventory: Mocked vs. Unmocked Real Coverage (`146` Tests Across `21` Files)

Running `pytest -q && python -m compileall -q core tests` after all final fixes passed **`146 passed, 0 failed, 0 skipped`**.

| Category | Test Files Included | Test Count | What Is Real vs. Mocked |
|---|---|---|---|
| **Zero-Mock Unit & Real-Asset Suites** | `test_confirmation_manager.py` (`15`), `test_memory_database.py` (`4`), `test_memory_intent.py` (`1`), `test_native_backend_modes.py` (`6`), `test_voice_state_machine.py` (`5`) | **`31`** | Pure real code: cryptographic HMAC/tokens, real SQLite DB, native backend option validation, 8-state FSM transitions (no `Mock`/`patch`). |
| **Security & Tool Suites w/ Controlled Mocks** | `test_confirmation_flow.py` (`11`), `test_filesystem_security.py` (`8`), `test_macos_security.py` (`16`), `test_memory_authorization.py` (`2`), `test_memory_service.py` (`3`), `test_native_ai_security.py` (`20`), `test_security_policy.py` (`6`), `test_startup_resilience.py` (`5`), `test_tool_executor_filesystem.py` (`15`), `test_final_system_audit.py` (`9`) | **`95`** | Real `RiyaAgent`, `ToolExecutor`, `SecurityManager`, `ConfirmationManager`, `AgentPlanner`, `MemoryManager`, `files.py`, `macos.py` policies; mocks `ai.generate`, `subprocess.run` (for `osascript`/macOS tools), `LocalSTT`/`WhisperModel`, `onnxruntime.InferenceSession` (telemetry test), or fake audio streams for deterministic CI speed. |
| **Voice Pipeline Suites w/ Mocked LLM/HW** | `test_voice_conversation_integration.py` (`2`), `test_voice_interruption.py` (`2`), `test_voice_security.py` (`3`), `test_voice_session.py` (`7`), `test_voice_stt.py` (`3`), `test_voice_tts.py` (`3`) | **`20`** | Real `VoiceOrchestrator`, `UtteranceSegmenter`, `VoiceSession`, `VoiceConfig`, `AudioOutputPlayer`, and real `silero_vad_v6.onnx` (`test_real_bundled_silero_onnx_vad_offline`); mocks `WhisperModel`, `MicrophoneStream` device streams, `/usr/bin/say` subprocess in unit tests, and `assistant.ai` in `test_voice_*` suites. |
| **Separate Unmocked Real-Model & Hardware E2E Suite** | Executed offline verification harness (`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 caffeinate -i`) | **`5` E2E Scenarios** | **Zero mocked AI/VAD/STT/TTS/Hardware:** Real `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` on Apple M1 Metal (`37/37` GPU layers), real `SemanticToolEngine` (`all-MiniLM-L6-v2`), real `MacBook Air Microphone` open stream, real `SileroStreamingVAD`, real `faster-whisper` (`base`), real `/usr/bin/say`, real `MacBook Air Speakers` CoreAudio PCM playback & interruption, real `SQLite` DB, real disk file creation. |

---

## 5. Real End-to-End Offline Verification Results (No Mocked `assistant.ai`)

Command run offline (`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 caffeinate -i`) with isolated temporary `FILESYSTEM_ALLOWED_ROOTS`, `voice_settings.json`, and SQLite `MemoryDatabase`:

1. **Initialization & Live Hardware Opening:**
   - `RIYAAssistant()` loaded `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (`MTL0 (Apple M1)`, `37/37` layers offloaded) + cached `SemanticToolEngine` in **`11.55s`**.
   - `VoiceOrchestrator.start_voice_mode(background_workers=False)` opened `MacBook Air Microphone` (`state=listening, mode=voice, mic_active=True`).
2. **`[CHECK1_CONV]` Real LLM Conversational Turn (Voice -> Silero VAD -> `faster-whisper` -> `Qwen3-4B` -> Response):**
   - Synthesized spoken prompt `"In one short sentence, who created you and do you work offline?"` -> `vad_max=0.997` -> `dt=10.30s` -> `pending=None`, reply: `'I was created by Tukaram and work offline.'`
   - Confirmed Defect A fix under real Qwen3 inference (`{"steps": []}`, no hallucinated tool call).
3. **`[CHECK2_READ_TOOL]` Voice -> Agent -> Read-Only Tool Execution (`SecurityManager` + `ToolExecutor`):**
   - Spoken `"What time is it right now?"` -> `vad_max=0.996` -> `dt=9.72s` -> routed to `current_time` -> `'It is 01:06 PM.'`
4. **`[CHECK3_STAGE]` + `[CHECK3_CONFIRM]` Protected Tool Confirmation Gate (`write_file`):**
   - Request `"Write the text 'RIYA_E2E_VERIFIED' to .../audit_verified.txt"` planned `write_file`, triggered `SecurityManager` `CONFIRMATION_REQUIRED`, issued cryptographic token, and asked `"Proceed?"`.
   - Synthesized spoken `"Yes"` (`vad_max=0.970`) -> `faster-whisper` -> Defect D calibrated confidence passed `min_confirmation_confidence=0.55` -> `ConfirmationManager` verified & consumed token -> `ToolExecutor` wrote and verified `audit_verified.txt` (`file_content='RIYA_E2E_VERIFIED'`) in **`0.80s`**.
5. **`[CHECK4_INTERRUPT]` Real Hardware CoreAudio Playback Interruption:**
   - Played multi-sentence response via `AudioOutputPlayer` on `MacBook Air Speakers`; after active PCM streaming began (`current_level > 0`), invoked `orchestrator.interrupt("audit_barge_in")`.
   - `interrupt()` returned in **`0.21 ms`**, playback thread joined cleanly in **`126.8 ms`** (`alive=False, completed=False, cancelled=True, error=None, state=listening`), zero crashes (`exit code 0`).
6. **`[CHECK5_SAVE_MEM]` -> `[CHECK5_RESTART]` Offline Persistence & Cold Restart Recovery:**
   - Spoken `"Remember that my codename is Project Atlas"` triggered `MemoryIntentType.REMEMBER` confirmation (`"I'll remember 'Project Atlas.' under 'personal' as 'my_code_name'. Proceed?"`); spoken `"Yes"` confirmed and saved to SQLite DB.
   - Saved `VoiceConfig(speech_rate_wpm=192, response_style="calm")`, shut down `orchestrator.close()` and `assistant.close()` (unloading Metal context cleanly), cold-restarted brand-new `RIYAAssistant` + `VoiceOrchestrator` offline from disk (`VoiceConfig.load`, `MemoryDatabase`), and recalled via spoken `"What do you remember about me?"` -> `'personal: my_code_name = Project Atlas.'`.
   - Total E2E script elapsed time: **`74.15s`**, exit code **`0`** (confirming Defect F telemetry teardown fix).

---

## 6. Feature Verification & Honest Readiness Table

| Feature / Boundary | Status | Evidence & Notes |
|---|---|---|
| Confirmation Security & Anti-Bypass (`ConfirmationManager`, `SecurityManager`, `ToolExecutor`) | **PASS** | Unpredictable 32-byte tokens, HMAC/exact param binding, single-use, TTL expiry, unknown tool fail-closed; `test_confirmation_manager.py` (15), `test_confirmation_flow.py` (11), `test_tool_executor_filesystem.py` (15), `test_voice_security.py` (3), `[CHECK3]`. |
| macOS & Filesystem Security | **PASS** | Argv-only `osascript` `on run argv`, `shell=False`, path canonicalization, sensitive paths (`voice_settings.json`, `voice_recordings`, `.db`, `.env`) blocked; `test_macos_security.py` (16), `test_filesystem_security.py` (8). |
| Native AI Security & Structured Planner Guard | **PASS** | Strict JSON parsing, untrusted `<user_request>` isolation, no token injection, timeout & cancellation enforcement, unrequested memory-tool suppression (Defect A fix); `test_native_ai_security.py` (20), `[CHECK1]`. |
| Voice-to-Agent-to-Tool Real AI Pipeline | **PASS** | Verified end-to-end with real `Qwen3-4B` (Metal), `SileroStreamingVAD`, `faster-whisper`, `SecurityManager`, `ConfirmationManager`, and `ToolExecutor` (`[CHECK1]`–`[CHECK3]`, `test_final_system_audit.py`). |
| Real-Time Interruption & Stale-Action Prevention | **PASS** | Defect B (`assistant.py` + `orchestrator.py`) + Defect E (`audio_output.py` thread-safe stream close) + Defect G (`orchestrator.py` atomic `reset_cancel()` guard) verified via `[CHECK4]` (`0.21 ms` signal / `126.8 ms` join) and `test_final_system_audit.py`. |
| Offline Cold Restart & Recovery | **PASS** | `VoiceConfig` (`0o600`), SQLite `MemoryDatabase`, `VoiceSession`, and offline `RIYAAssistant` reload verified in `[CHECK5]` and `test_offline_restart_and_recovery_preserves_voice_config_and_memory`. |
| Expressive Wording vs. Honest TTS Prosody | **PASS** | `prepare_text_for_speech` adapts style wording/cadence + `style_rate_wpm` WPM while `LocalTTS.supports_emotional_prosody = False` and `"Proceed?"` prompts remain unaltered (`test_expressive_response_wording_vs_honest_tts_prosody_capability`). |
| Live Multi-Turn Human Mic Conversation & GUI Click Walkthrough | **LIMITATION (Manual Interactive)** | Mic stream hardware permission (`MacBook Air Microphone`), Silero VAD, and speaker playback were verified programmatically using `/usr/bin/say`-synthesized WAV utterances + live mic stream open/close; full live human spoken dialogue with acoustic echo feedback in a room and visual Tkinter mouse-click walkthrough remain interactive manual steps. |
| Hindi (`hi`) & Marathi (`mr`) Voice Accuracy | **LIMITATION (Untested E2E)** | Correctly labeled `(untested)` in UI (`VERIFIED_LANGUAGES = {"en"}`); real non-English speech recognition and macOS voice pronunciation were not tested end-to-end. |

---

## 7. Files Added or Modified During Final Audit

- **Production Files Hardened (with `*.before_final_audit` backups):**
  1. `core/agent/planner.py` — Strengthened conversational empty-plan rule and added `_request_has_memory_mutation_intent()` guard.
  2. `core/agent/assistant.py` — Added internal `_cancel_event`, pre/mid-loop plan cancellation checks, and confirmation staging cancellation rollback.
  3. `core/voice/orchestrator.py` — Added `_rollback_interrupted_pending_action()` on interrupted/cancelled turns and atomic `_is_turn_active()`/`reset_cancel()` locking in `handle_utterance_audio()` and `send_text()`.
  4. `core/voice/vad.py` — Disabled `onnxruntime` telemetry events (`ort.disable_telemetry_events()`) in `SileroStreamingVAD.__init__()` to eliminate offline telemetry threads and process-exit `SIGABRT` crashes.
  5. `core/voice/personality.py` — Added `style_prompt_instruction()` and `apply_response_style_wording()` in `prepare_text_for_speech()`.
  6. `core/voice/stt.py` — Added bounded short-utterance speech-presence confidence calibration (`raw_conf >= 0.40 and mean_nsp <= 0.25`).
  7. `core/voice/audio_output.py` — Moved PortAudio stream `abort()`/`stop()`/`close()` exclusively to the owning playback thread.
- **Tests & Documentation Created:**
  1. `tests/test_final_system_audit.py` — `9` new regression tests covering Defects A–G, full voice-to-tool confirmation pipeline, and cold offline restart.
  2. `RIYA_FINAL_SYSTEM_AUDIT_REPORT.md` — This final audit report.
