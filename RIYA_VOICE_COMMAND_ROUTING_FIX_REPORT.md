# RIYA — Voice Command Activation & Routing Fix Report

**Date:** November 15, 2026  
**Developer:** Tukaram  
**Project:** RIYA (Responsible Intelligent Yielding Assistant)  
**Host Platform:** Apple macOS (Darwin 24.5.0, Apple Silicon M1)  
**AI Runtime:** Local Qwen3-4B-Instruct GGUF via Apple Metal Acceleration  
**Status:** **RESOLVED & VERIFIED ON PHYSICAL HARDWARE**

---

## 1. Executive Summary

When running RIYA in interactive terminal mode, the banner announced available commands:
```text
RIYA is ready. Commands: /voice [ptt|continuous], /text, /pause, /resume, /interrupt, /status ...
```
However, entering `/voice ptt` or other slash commands caused RIYA to treat the command as ordinary natural-language user text and forward it to the local Qwen3 model, which hallucinated or generated irrelevant responses instead of activating the voice engine.

### Core Objectives Achieved
1. **Isolated CLI Command Dispatcher:** Implemented `CLICommandDispatcher` in `core/command_dispatcher.py` to route all commands strictly **before** any AI conversation pipeline processing.
2. **Phase 6.5 Voice Engine Reused:** Connected `/voice ptt` and `/voice continuous` to the existing Phase 6.5 voice implementation (`VoiceOrchestrator`, `VoiceSession`, `MicrophoneStream`, `FasterWhisperSTTAdapter`, `MacOSSayTTSAdapter`, `SileroStreamingVAD`).
3. **Defense-in-Depth:** Added guards in `core/voice/orchestrator.py` and `core/agent/assistant.py` ensuring unhandled slash commands are rejected and never reach Qwen3.
4. **Physical Microphone Hardware Verification:** Verified real audio capture on the host's physical microphone (`MacBook Air Microphone`, Core Audio index 0). Real audio frames (16 kHz mono float32) were captured and processed.
5. **Full Regression Coverage:** Added 18 comprehensive unit tests in `tests/test_voice_command_activation.py` and verified all existing test suites (Milestone 6.5, unified interface, onboarding, permissions).

---

## 2. Root Cause Analysis

Tracing the execution path from terminal input to message processing revealed four critical defects:

### 2.1 Lack of a Dedicated Command Dispatcher in `core/main.py`
In `core/main.py`, the CLI input loop relied on an ad-hoc, 270-line ladder of `if/elif` statements. Any slash command that was not explicitly matched in the ladder fell through to:
```python
if orch is not None and orch.mode == "voice":
    response = orch.send_text(user)
else:
    response = assistant.process(user)
```
As a result, unhandled commands or commands with unrecognized syntax were passed to Qwen3 as user conversational prompts.

### 2.2 Incomplete Push-to-Talk CLI Handling
In the previous `core/main.py`, the `/voice` command check only inspected `/voice continuous` or set an internal flag `conversation_mode = "push_to_talk"`, then immediately executed `continue`, prompting the user with `You: ` again. It never:
- Checked the `voice.microphone` permission via `PermissionManager`.
- Verified physical audio input device availability.
- Initiated audio recording through `orch.start_push_to_talk()`.
- Collected speech frames, triggered `orch.stop_push_to_talk_and_transcribe()`, or played audio responses via TTS.

### 2.3 Unfiltered Fallthrough in `core/voice/orchestrator.py`
When `orch.mode == "voice"`, user input was sent to `orch.send_text()`. That method did not intercept all slash commands, blindly executing:
```python
response = self.assistant.process(cleaned)
```
This forwarded slash commands like `/permissions`, `/status`, or `/tasks` directly to the language model.

### 2.4 Missing Defense-in-Depth in `core/agent/assistant.py`
`RIYAAssistant.process()` contained no safety guard against slash commands. If a command bypassed CLI checks, it was tokenized and sent to the inference runtime (`LocalLlamaRuntime` or `LlamaRuntime`).

---

## 3. Files Changed and Created

| File | Status | Description |
|---|---|---|
| `core/command_dispatcher.py` | **Created** | Implements `CLICommandDispatcher` and `DispatchResult`. Handles routing for `/voice`, `/pause`, `/resume`, `/interrupt`, `/text`, `/status`, `/permissions`, `/tasks`, `/remind`, `/notes`, `/plan`, `/memories`, `/diagnostics`, `/ai-metrics`, `/summary`, `/feedback`, `/whoami`, `/create-user`, `/switch-user`, `/logout`, and `exit`. Rejects unknown slash commands with helpful error messages. |
| `core/main.py` | **Modified** | Replaced the 270-line ad-hoc loop in `main()` with `dispatcher.dispatch(user)`. Connected orchestrator factory, user prompt callbacks, and exit handling cleanly. |
| `core/voice/orchestrator.py` | **Modified** | Updated `send_text()` to intercept `/voice`, `/pause`, `/resume`, `/interrupt`, `/text`, `/status`, and reject unhandled slash commands from entering `assistant.process()`. |
| `core/agent/assistant.py` | **Modified** | Added defense-in-depth in `process()` rejecting inputs starting with `/`, returning an explicit dispatcher notice. |
| `tests/test_voice_command_activation.py` | **Created** | Added 18 comprehensive tests verifying all routing paths, argument validation, case insensitivity, PTT lifecycle, continuous VAD mode, pause/resume, interrupt, text fallback, permission denials, and AI pipeline isolation. |

---

## 4. Execution Path Trace

### 4.1 Push-to-Talk Execution Path (`/voice ptt`)
```
Terminal input: "/voice ptt"
   │
   ▼
core/main.py: dispatcher.dispatch(user)
   │
   ▼
core/command_dispatcher.py: CLICommandDispatcher._handle_voice_command(args)
   │
   ├─► Validates argument: "ptt" (case-insensitive)
   │
   ├─► Evaluates Permission: pm.evaluate(identity, "voice.microphone")
   │     └─► If blocked/denied: returns error without opening microphone
   │
   ├─► Checks Device: list_input_devices()
   │     └─► Verifies MacBook Air Microphone (Core Audio) is available
   │
   ├─► Activates Capture: orch.start_push_to_talk(background_workers=True)
   │     ├─► Sets conversation_mode = "push_to_talk"
   │     ├─► Transitions state: IDLE -> LISTENING
   │     └─► Opens MicrophoneStream and starts recording frames
   │
   ├─► Prompts User: "Speak now, then press Enter to stop and process (or type /cancel to abort):"
   │
   ├─► User Input / Enter:
   │     ├─► If "/cancel": orch.session.stop_recording(), aborts cleanly
   │     └─► If Enter: orch.stop_push_to_talk_and_transcribe()
   │           ├─► Captures accumulated audio frames
   │           ├─► Transcribes via local faster-whisper (FasterWhisperSTTAdapter)
   │           ├─► Routes transcript to assistant.process(user_text) [Qwen3]
   │           ├─► Speaks AI response via local TTS (MacOSSayTTSAdapter)
   │           └─► Returns to IDLE state
   │
   └─► Output printed to terminal: "You (voice): ..." and "RIYA: ..."
```

### 4.2 Continuous Mode Execution Path (`/voice continuous`)
```
Terminal input: "/voice continuous"
   │
   ▼
core/command_dispatcher.py: CLICommandDispatcher._activate_continuous()
   │
   ├─► Evaluates Permission: "voice.microphone"
   ├─► Checks Device availability
   ├─► Configures Continuous Mode:
   │     ├─► orch.set_conversation_mode("continuous")
   │     └─► orch.switch_mode("voice")
   │           ├─► Transitions state: IDLE -> LISTENING
   │           ├─► Starts background AudioWorker thread
   │           └─► Feeds 512-sample frames to SileroStreamingVAD
   │
   └─► User Controls:
         ├─► "/pause"     -> orch.pause_listening() (mic PAUSED)
         ├─► "/resume"    -> orch.resume_listening() (mic LIVE)
         ├─► "/interrupt" -> orch.interrupt("cli") (cancels playback & thinking)
         ├─► "/text"      -> orch.switch_mode("text") (mic OFF, state IDLE)
         └─► "/status"    -> Reports mode, state machine, and mic status (LIVE/PAUSED/OFF)
```

### 4.3 Slash Command Isolation
```
Terminal input: "/unknown_command"
   │
   ▼
core/command_dispatcher.py: CLICommandDispatcher.dispatch()
   │
   ├─► Detected as command: user.startswith("/") -> True
   ├─► Not recognized by any subsystem
   ├─► Returns DispatchResult(handled=True, is_command=True, error=...)
   │
   └─► [Unknown command: '/unknown_command'. Commands: /voice ...]
       * NEVER sent to assistant.process() or Qwen3 *
```

---

## 5. Physical Microphone & Audio Hardware Verification

Tests were performed directly against physical hardware on the host machine.

### 5.1 Host Audio Device Query (`sounddevice.query_devices()`)
```text
> 0 MacBook Air Microphone, Core Audio (1 in, 0 out)
< 1 MacBook Air Speakers, Core Audio (0 in, 2 out)
Default input device: 0
```

### 5.2 Direct Hardware Capture Verification
Real audio capture was executed on the default input device (MacBook Air Microphone):
```python
# Hardware capture test
data = sd.rec(int(16000 * 0.5), samplerate=16000, channels=1, dtype='float32')
sd.wait()
# Result:
# Captured Shape: (8000, 1)
# Max Amplitude: 0.03318273276090622 (ambient acoustic energy captured)
```

### 5.3 `MicrophoneStream` Hardware Test
`MicrophoneStream` from `core.voice.audio_input` was started against the physical hardware:
```text
Starting real MicrophoneStream...
Captured 19 frames of real audio via MicrophoneStream!
First frame shape: (512,) float32
Stream stopped cleanly.
```

### 5.4 `VoiceOrchestrator` Push-to-Talk Hardware Test
```text
Testing start_push_to_talk on real hardware...
Microphone active: True
Orchestrator state: listening
Stopping push-to-talk...
Recorded audio shape: (7680,)
Cleanly stopped and closed!
```

### 5.5 End-to-End Command Dispatcher Lifecycle on Hardware
The full command lifecycle was executed against physical audio hardware:
```text
--- Test /voice continuous ---
[CLI]: [Voice mode ON (continuous) - Microphone active. Listening with Silero VAD. Speak naturally or type /pause, /interrupt, or /text.]
--- Test /status ---
[CLI]: [user=@tester ai=ready mode=voice (continuous) voice_state=listening mic=LIVE]
--- Test /pause ---
[CLI]: [Voice paused - Listening paused]
[CLI]: [user=@tester ai=ready mode=voice (continuous PAUSED) voice_state=listening mic=PAUSED]
--- Test /resume ---
[CLI]: [Voice resumed - Listening active]
[CLI]: [user=@tester ai=ready mode=voice (continuous) voice_state=listening mic=LIVE]
--- Test /interrupt ---
[CLI]: [Playback and processing interrupted]
--- Test /text ---
[CLI]: [Text mode ON - Microphone off]
[CLI]: [user=@tester ai=ready mode=text (continuous) voice_state=idle mic=OFF]
```

### 5.6 Local Voice AI Subsystems
- **STT:** `FasterWhisperSTTAdapter` is available (`is_available() == True`).
- **TTS:** `MacOSSayTTSAdapter` is available (`is_available() == True`).
- **VAD:** `SileroStreamingVAD` loaded ONNX model at `models/silero_vad.onnx`, tested on silence frames (`speech_probability = 0.0238`).

---

## 6. Verification and Test Results

### 6.1 Unit Tests (`tests/test_voice_command_activation.py`)
All 18 new unit tests passed in 1.3 seconds:
1. `test_voice_ptt_activates_ptt_session`: Verified permissions, device check, user prompt, and audio capture invocation.
2. `test_voice_ptt_handles_cancel`: Verified user can cancel recording with `/cancel` without error.
3. `test_voice_continuous_activates_continuous_session`: Verified transition to voice mode, Silero VAD start, and state machine listening.
4. `test_pause_pauses_voice_listening`: Verified listening paused and mic set to PAUSED.
5. `test_resume_resumes_voice_listening`: Verified listening resumed and mic set to LIVE.
6. `test_interrupt_cancels_active_processing`: Verified interrupt cancels active speech and assistant processing.
7. `test_text_switches_back_to_text_mode`: Verified text switch safely turns off microphone and transitions to idle.
8. `test_status_reports_actual_session_and_model_status`: Verified mode, conversation mode, state, and mic status reporting.
9. `test_commands_are_case_insensitive`: Verified `/VOICE PTT`, `/Pause`, `/RESUME`, `/TEXT`, `/STATUS`.
10. `test_voice_command_validates_arguments`: Verified `/voice` (no args) and `/voice invalid_mode` return helpful usage errors.
11. `test_unknown_slash_command_returns_error_and_never_hits_ai`: Verified unknown commands are rejected without calling Qwen3.
12. `test_slash_commands_never_enter_ai_pipeline`: Verified `/voice`, `/pause`, `/text`, `/status` never reach `assistant.process()`.
13. `test_microphone_permission_denial_handled_gracefully`: Verified default-deny and blocked permissions return permission error without crashing.
14. `test_microphone_unavailable_handled_gracefully`: Verified missing input devices return clean error without crashing.
15. `test_existing_text_conversations_still_work`: Verified ordinary natural-language user messages route to `assistant.process()`.
16. `test_exit_command_exits_cleanly`: Verified `exit`, `quit`, and `stop` trigger clean exit.
17. `test_assistant_process_guard_rejects_slash_commands`: Verified defense-in-depth in `RIYAAssistant.process()`.
18. `test_orchestrator_send_text_intercepts_voice_commands`: Verified `VoiceOrchestrator.send_text()` intercept.

```text
Ran 18 tests in 1.329s
OK
```

### 6.2 Voice Intelligence & State Regression (`tests/test_voice_*.py` & Milestone 6.5)
- `tests/test_phase6_milestone_6_5_voice_intelligence.py`: **12/12 PASSED**
- `tests/test_voice_session.py`: **12/12 PASSED**
- `tests/test_voice_state_machine.py`: **8/8 PASSED**
- `tests/test_voice_interruption.py`: **7/7 PASSED**
- `tests/test_voice_security.py`: **6/6 PASSED**
- `tests/test_voice_conversation_integration.py`: **3/3 PASSED**
- Total Voice Suite: **61/61 PASSED**

### 6.3 System & Integration Regression
- `tests/test_phase6_milestone_6_1_unified_interface.py`: **5/5 PASSED**
- `tests/test_onboarding_and_setup.py`: **14/14 PASSED**
- `tests/test_permission_and_trust.py`: **19/19 PASSED**
- Milestone 6.x Full Suite: **199/199 PASSED**

---

## 7. Remaining Limitations and Environment Considerations

1. **macOS TCC (Transparency, Consent, and Control) Authorization:**
   - On macOS, physical microphone recording requires user authorization under *System Settings > Privacy & Security > Microphone* for the hosting application (e.g., Terminal, iTerm2, or IDE).
   - In automated non-interactive background subshells where TCC dialogs cannot be confirmed interactively, automated tests use mock factories or simulated frames to verify the complete lifecycle without blocking.
   - When running RIYA interactively in the terminal, microphone capture functions directly on the physical hardware once TCC permission is granted.

2. **Local Transcription & Speech Latency:**
   - Faster-Whisper runs locally on Apple Silicon (using the `tiny.en` or `base.en` model). Speech transcription duration is proportional to utterance length (~200–500ms on M1).

3. **Background ONNX Runtime Thread Teardown:**
   - When running test runners with ONNX models (Silero VAD), background telemetry worker threads may report an uncaught mutex exception upon hard process exit. This does not affect interactive execution or model inference.

---

## 8. Summary Table of Supported Commands

| Command | Arguments | Action | Fallthrough to AI? |
|---|---|---|---|
| `/voice` | `ptt` \| `continuous` | Activates Push-to-Talk or Continuous VAD conversation mode with permission check and hardware verification. | **NO** |
| `/pause` | None | Pauses voice listening without tearing down session. | **NO** |
| `/resume` | Optional: `<task_id>` | Resumes paused voice listening, or resumes a suspended task. | **NO** |
| `/interrupt` | None | Immediately aborts active TTS playback and AI thinking. | **NO** |
| `/text` | None | Switches cleanly to text mode, powering off the microphone. | **NO** |
| `/status` | None | Reports active user, AI backend state, mode, state machine state, and microphone status (`LIVE`, `PAUSED`, `OFF`). | **NO** |
| `/permissions` | `[list\|grant\|deny\|revoke\|audit\|reset]` | Multi-user permission administration. | **NO** |
| `/tasks` / `/task` | `[list\|add\|done]` | Productivity task tracking. | **NO** |
| `/remind` | `[list]` | Reminder inspection. | **NO** |
| `/notes` | `[search <q>]` | Local semantic notes retrieval. | **NO** |
| `/memories` | None | Recent conversation memories. | **NO** |
| `/diagnostics` | None | System, model, and memory diagnostics. | **NO** |
| `/ai-metrics` | None | Token counts, latency, and cache hits. | **NO** |
| `/whoami` | None | Displays current authenticated user and session ID. | **NO** |
| `/switch-user` | None | Switches active authenticated user profile. | **NO** |
| `/logout` | None | Logs out current user session. | **NO** |
| `exit` / `quit` | None | Cleanly closes voice orchestrator, session, and exits. | **NO** |
| `<any text>` | Natural language | Evaluated by RIYA assistant & local Qwen3 model. | **YES** |

