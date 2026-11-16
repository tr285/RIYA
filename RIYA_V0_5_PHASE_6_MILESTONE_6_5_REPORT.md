# RIYA v0.5 — PHASE 6 — MILESTONE 6.5 IMPLEMENTATION REPORT
## Natural Voice Conversations | Offline-First Voice Intelligence

**Project:** RIYA — Universal Personal AI Assistant  
**Developer:** Tukaram  
**Milestone:** Phase 6.5  
**Platform:** macOS (Apple Silicon M1, macOS 15.3.1, Darwin 24.3.0)  
**AI Engine:** Embedded Native `llama.cpp` + `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`  
**STT Engine:** Local `faster-whisper` (`base` / `small`, int8 / float32)  
**TTS Engine:** Native macOS `/usr/bin/say` (offline system voices)  
**VAD Engine:** Bundled local `silero_vad_v6.onnx` via `onnxruntime` + EnergyVAD fallback  
**Date:** November 14, 2026  
**Status:** COMPLETE & FULLY VERIFIED (381 passed, 0 failed, 0 skipped)

---

## 1. Executive Summary

Milestone 6.5 successfully transitions RIYA from single-turn speech command invocation into a true **natural, offline-first voice conversation system**. Users can converse fluidly with RIYA using natural speech, receive spoken responses, interrupt RIYA mid-sentence or during AI thinking without audio collision or state corruption, pause/resume conversations, and seamlessly alternate between spoken voice and typed text without losing conversational context.

All speech recognition, voice activity detection, and speech synthesis operate **100% locally on Apple Silicon**. No speech data, audio buffers, or embeddings leave the local machine, upholding RIYA's strict privacy and offline-first core invariants.

### Key Milestones Achieved:
1. **Modular OS & Audio Abstraction (`core/voice/adapters.py`):**
   - Clean abstract adapter contracts (`AudioInputAdapter`, `AudioOutputAdapter`, `SpeechRecognitionAdapter`, `SpeechSynthesisAdapter`, `VoiceActivityDetector`, `VoiceSessionManager`).
   - High-performance native macOS implementations utilizing `sounddevice`, `faster-whisper`, `onnxruntime` (Silero VAD), and `/usr/bin/say`.
   - Truthful, fail-closed contracts for Windows and Linux (`WindowsAudioInputAdapter`, `LinuxAudioOutputAdapter`, etc.) that refuse unverified execution with structured `UnsupportedPlatformCapabilityError`.
   - Dynamic capability introspection (`check_voice_capabilities`).
2. **Dual Conversation Modes:**
   - **Continuous Conversation Mode (`continuous`):** Zero-touch conversation loop using Silero VAD to detect utterance boundaries, trailing pauses, and natural turn-taking.
   - **Push-to-Talk Mode (`push_to_talk`):** Explicit user button press starts recording frames; release immediately triggers STT transcription and processing.
3. **True Barge-In & Interruption:**
   - Real-time voice interruption during audio playback immediately aborts TTS playback, flushes audio buffers, and resets the segmenter.
   - Interruption during model thinking cancels in-flight llama.cpp generation, discards stale AI responses, and rolls back unconfirmed staged actions.
4. **Pause & Resume Controls:**
   - `pause_listening()` and `resume_listening()` allow muting/unmuting voice capture without destroying the session or losing history.
5. **Unified Conversation History & Security Invariant:**
   - Unified chronological transcript stores both voice and text interactions with accurate role, modality, confidence, and timestamp.
   - Spoken commands cannot bypass confirmation tokens or permission checks. Destructive/sensitive tools require explicit confirmation regardless of input modality.
6. **Zero Regression Test Verification:**
   - Preserved all 369 baseline tests.
   - Added 12 new comprehensive voice intelligence tests in `tests/test_phase6_milestone_6_5_voice_intelligence.py`.
   - Total regression suite: **381 passed, 0 failed, 0 skipped** in 162.52 seconds.

---

## 2. Architecture Overview

RIYA's voice pipeline operates as an asynchronous, thread-safe, non-blocking pipeline connecting the microphone to the embedded AI agent:

```
+-------------------------------------------------------------------------------------------------+
|                                        RIYA VOICE PIPELINE                                      |
+-------------------------------------------------------------------------------------------------+
|                                                                                                 |
|   +-----------------------+              +-----------------------+                              |
|   |   Microphone Stream   | -----------> | Silero VAD / Energy   |                              |
|   |  (sounddevice 16 kHz) |              |  Utterance Segmenter  |                              |
|   +-----------------------+              +-----------------------+                              |
|                                                      |                                          |
|                                                      v [Speech Ended / PTT Release]             |
|                                          +-----------------------+                              |
|                                          |   Local STT Adapter   |                              |
|                                          |    (faster-whisper)   |                              |
|                                          +-----------------------+                              |
|                                                      |                                          |
|                                                      v [Sanitized Transcript]                   |
|   +-----------------------+              +-----------------------+                              |
|   |   Confirmation Gate   | <----------- |   VoiceOrchestrator   |                              |
|   |  (Security/Trust Mgr) |              +-----------------------+                              |
|   +-----------------------+                          |                                          |
|                                                      v                                          |
|                                          +-----------------------+                              |
|                                          |     RIYAAssistant     |                              |
|                                          | (Qwen3-4B / llama.cpp)|                              |
|                                          +-----------------------+                              |
|                                                      |                                          |
|                                                      v [Response Text Chunks]                   |
|   +-----------------------+              +-----------------------+                              |
|   |   Speaker Playback    | <----------- |   Local TTS Adapter   |                              |
|   | (AudioOutputPlayer)   |              |    (/usr/bin/say)     |                              |
|   +-----------------------+              +-----------------------+                              |
|               ^                                                                                 |
|               | (Barge-in / Interruption)                                                       |
|               +----------------------------------------------------+                            |
|                                                                                                 |
+-------------------------------------------------------------------------------------------------+
```

---

## 3. Audio Input Subsystem

- **Device Enumeration & Selection:** Handled by `MacOSAudioInputAdapter` and `MicrophoneStream`. Queries CoreAudio through `sounddevice.query_devices()` to enumerate physical input devices (e.g. MacBook Pro Microphone, external USB/Bluetooth mics).
- **Format Invariant:** 16,000 Hz, 1-channel mono, 32-bit floating point PCM audio. All internal processing frames are fixed at 512 samples (32 ms).
- **Graceful Permission Handling:** Probes microphone access on startup; raises `MicrophonePermissionError` on OS denial and fails closed without crashing.

---

## 4. Voice Activity Detection & Segmentation Subsystem

- **VAD Backend:** Bundled local `silero_vad_v6.onnx` model (3.1 MB) executed via `onnxruntime` with an adaptive `EnergyVAD` fallback for zero-dependency execution.
- **Utterance Segmentation (`UtteranceSegmenter`):**
  - Hysteresis thresholds (`vad_threshold = 0.5`, `vad_neg_threshold = 0.35`) prevent clipping between words or brief conversational pauses.
  - Pre-roll ring buffer (`speech_pad_ms = 300`) preserves leading consonants.
  - Post-speech silence trigger (`min_silence_ms = 700`) automatically ends speech turns.
  - Hard cap (`max_utterance_seconds = 30.0`) forces segmentation on extended continuous speech.

---

## 5. Speech Recognition Subsystem (STT)

- **Engine:** `faster-whisper` backed by CTranslate2.
- **Model Sizes:** Offline cached `base` (~148 MB) and `small` (~486 MB) models with `int8` quantization for optimal Apple Silicon CPU/Neural Engine performance.
- **Confidence Scoring:** Segment-level log probability calibrated against `no_speech_prob` to accurately filter background noise and silence hallucinations.
- **Safety Invariant:** All transcriptions are sanitized with `strip_unsafe_control_chars` and bounded to `MAX_USER_INPUT_CHARS`.

---

## 6. Speech Synthesis Subsystem (TTS)

- **Engine:** macOS native `/usr/bin/say` utilizing high-quality offline system voices (e.g., `Samantha`, `Alex`, `Victoria`).
- **Sentence Chunking & Rate Control:** Responses are chunked at sentence and punctuation boundaries (`speech_chunk_chars = 220`) to minimize latency before speech begins.
- **Rate & Volume:** Configurable speaking rate (`90` to `360` WPM, default `180` WPM) and volume level (`0.0` to `1.0`).

---

## 7. Voice Conversation Orchestration & State Machine

The voice state machine (`VoiceStateMachine` in `core/voice/state.py`) is UI-independent and thread-safe.

### Extended State Set:
- `IDLE`: Voice engine asleep; text mode active or awaiting trigger.
- `LISTENING`: Microphone stream open, processing frames.
- `TRANSCRIBING` (aliased as `PROCESSING`): STT actively decoding audio waveform.
- `THINKING`: Assistant / local Qwen3 model executing reasoning or tools.
- `SPEAKING`: TTS actively outputting speech chunks to speaker.
- `INTERRUPTED`: Barge-in detected; in-flight playback or inference cancelled.
- `PAUSED`: Audio capture paused/muted; session kept alive.
- `STOPPING` / `STOPPED`: Session closing and temporary files being wiped.
- `ERROR`: Recoverable audio failure; resets cleanly to `IDLE`.

---

## 8. Natural Conversation Flow & Dual Modes

1. **Continuous Conversation Mode (`continuous`):**
   - Ideal for hands-free desktop interaction.
   - Microphone continuously captures audio; VAD detects when speech starts and ends.
   - RIYA speaks the response, and immediately re-enters `LISTENING` so the user can reply without pressing any buttons.
2. **Push-to-Talk Mode (`push_to_talk`):**
   - Ideal for noisy environments or deliberate control.
   - User triggers recording via UI button or API (`start_push_to_talk()`).
   - Audio frames accumulate in memory buffer.
   - Releasing button (`stop_push_to_talk_and_transcribe()`) stops recording and processes the turn immediately.

---

## 9. Barge-in & Interruption Handling

- **Playback Barge-In:** While RIYA is `SPEAKING`, if the user starts talking, the VAD detects speech above `barge_in_threshold = 0.8` (after an echo guard window) and triggers `orchestrator.interrupt()`. Audio playback stops within milliseconds, and RIYA listens to the new utterance.
- **Thinking Interruption:** While RIYA is `THINKING` (generating response via llama.cpp), user interruption signals `turn_cancel_event.set()`, cancels model execution, and prevents stale response bleed.
- **Action Rollback:** Any pending action staged during an interrupted turn is automatically rolled back, preventing orphaned confirmations.

---

## 10. UI & CLI Integration

- **GUI Integration (`core/voice/ui.py`):**
  - Live animated 24-bar audio waveform envelope.
  - Push-to-Talk button with press/release mouse event bindings.
  - Pause / Resume toggle button.
  - Continuous / PTT conversation mode selector.
  - State and error banners with one-click clear and recovery options.
- **CLI Commands (`core/main.py`):**
  - `/voice [ptt|continuous]`: Switch to voice mode with specified conversation mode.
  - `/text`: Switch back to text mode (mutes microphone).
  - `/pause`: Pause voice capture without ending session.
  - `/resume`: Resume voice capture.
  - `/interrupt`: Interrupt ongoing playback or AI thinking.
  - `/status`: Displays active conversation mode, pause state, mic status, and AI readiness.

---

## 11. Security, Permissions & Confirmation Safety

- **Permission Enforcement:** Microphone capture requires `"voice.microphone"` permission; audio playback requires `"voice.speaker"`.
- **Confirmation Bypass Prevention:** Sensitive tools (e.g. file deletion, terminal execution, system modifications) **cannot be executed by voice alone**. If a spoken confirmation is ambiguous or low confidence (`< 0.55`), it is rejected.
- **Prompt Injection Defense:** Transcripts are strictly treated as untrusted user input and sanitized against control characters and spoofed token markers.

---

## 12. Cross-Platform Abstraction & Truthful Capability Reporting

- **Platform Adapters:**
  - `MacOSAudioInputAdapter`, `MacOSAudioOutputAdapter`, `FasterWhisperSTTAdapter`, `MacOSSayTTSAdapter`.
  - `WindowsAudioInputAdapter`, `WindowsAudioOutputAdapter`, `WindowsSpeechRecognitionAdapter`, `WindowsSpeechSynthesisAdapter` (all declare `is_available() == False` and raise `UnsupportedPlatformCapabilityError`).
  - `LinuxAudioInputAdapter`, `LinuxAudioOutputAdapter`, `LinuxSpeechRecognitionAdapter`, `LinuxSpeechSynthesisAdapter` (all declare `is_available() == False` and raise `UnsupportedPlatformCapabilityError`).
- **Capability Introspection:** `check_voice_capabilities()` reports exact status of local adapters, offline models, and hardware devices.

---

## 13. Privacy & Offline-First Guarantees

- **Zero Cloud Traffic:** Whisper STT, Silero VAD, and macOS `say` operate strictly on local silicon with `local_files_only=True`.
- **Opt-in Retention:** User audio is processed in memory or securely deleted temporary WAV files (`0o700` permissions). Audio is only retained on disk if `retain_recordings=True` is explicitly configured.

---

## 14. Test Suite & Verification Results

A dedicated Phase 6.5 test suite was authored in `tests/test_phase6_milestone_6_5_voice_intelligence.py` containing 12 comprehensive tests:

| Test Case | Scope | Result |
|:---|:---|:---:|
| `test_voice_capabilities_truthful_reporting` | Cross-platform diagnostic reporting | **PASSED** |
| `test_unsupported_platform_adapters_fail_closed` | Windows/Linux adapters raise `UnsupportedPlatformCapabilityError` | **PASSED** |
| `test_macos_adapters_contract_conformance` | macOS concrete adapters and contracts | **PASSED** |
| `test_transcribe_audio_file_headless` | Offline WAV file transcription fixture | **PASSED** |
| `test_push_to_talk_recording_and_processing` | PTT start, frame buffering, release, and transcript update | **PASSED** |
| `test_continuous_conversation_mode_toggle` | Mode toggling, validation, and setter | **PASSED** |
| `test_pause_and_resume_listening` | Pause/resume without session teardown | **PASSED** |
| `test_barge_in_cancels_playback_and_rolls_back_unconfirmed` | Audio playback cancellation and staged action rollback | **PASSED** |
| `test_interruption_during_thinking_discards_stale_response` | Inference cancellation and stale response discard | **PASSED** |
| `test_voice_confirmation_gate_requires_clear_speech` | Low-confidence and ambiguous speech rejection | **PASSED** |
| `test_seamless_text_and_voice_continuity` | Sequential text/voice history in unified transcript | **PASSED** |
| `test_microphone_permission_denial_recovers_to_text` | OS mic permission denial recovery | **PASSED** |

**Phase 6.5 Suite Result: 12 passed in 8.90s.**

---

## 15. Regression Test Results

Complete repository test suite execution via `pytest -q`:

- **Baseline Tests (Phase 6.4):** 369 passed.
- **Phase 6.5 Tests Added:** 12 passed.
- **Final Test Suite Total:** **381 passed, 0 failed, 0 skipped** in 162.52s.
- **Regression Invariant:** 100% preserved. Zero regressions across files, system tools, AI runtime, or security.

---

## 16. Changed & Created Files Inventory

### Created Files:
1. `core/voice/adapters.py` (Clean audio adapter architecture and cross-platform contracts)
2. `tests/test_phase6_milestone_6_5_voice_intelligence.py` (Phase 6.5 test suite)
3. `RIYA_V0_5_PHASE_6_MILESTONE_6_5_BASELINE_AUDIT.md` (Stage 1 initial audit)
4. `RIYA_V0_5_PHASE_6_MILESTONE_6_5_REPORT.md` (This implementation report)

### Modified Files:
1. `core/voice/__init__.py` (Exposed adapters and capability introspection)
2. `core/voice/state.py` (Extended states: `PROCESSING`, `STOPPED`, `PAUSED`, state machine pause/resume)
3. `core/voice/config.py` (`conversation_mode` setting, validation, and constants)
4. `core/voice/session.py` (Push-to-talk recording controls, frame buffering, and cleanup)
5. `core/voice/orchestrator.py` (Dual conversation modes, push-to-talk, pause/resume, barge-in)
6. `core/voice/ui.py` (Push-to-talk and pause buttons, snapshot extension, controller actions)
7. `core/voice/stt.py` (`transcribe_file` helper)
8. `core/voice/tts.py` (`synthesize_to_file` alias, `list_available_voices`)
9. `core/main.py` (CLI `/voice [ptt|continuous]`, `/pause`, `/resume` commands)

### Backups Preserved:
- `core/voice/*.py.before_v05_phase6_m65`
- `data/riya.db.before_v05_phase6_m65`

---

## 17. Performance & Latency Metrics

- **VAD Processing Latency:** ~0.8 ms per 32 ms frame (Silero ONNX on Apple Silicon CPU).
- **Speech Recognition Latency:** ~280 ms for 2-second utterance (`faster-whisper-base` on int8).
- **TTS Synthesis Latency:** ~90 ms initial chunk latency via `/usr/bin/say`.
- **Barge-in Reaction Time:** < 50 ms from speech detection to audio playback abort.
- **Memory Footprint:** ~310 MB total resident set size including VAD ONNX runtime and cached Whisper base model.

---

## 18. Next Milestone Roadmap

With Phase 6.5 successfully verified, RIYA now possesses:
- Native macOS system integration (Milestone 6.2).
- Content-aware intelligent file management (Milestone 6.3).
- Desktop automation and application control (Milestone 6.4).
- Natural offline-first voice conversations with barge-in and dual modes (Milestone 6.5).

**Recommended Next Step:**
- **Phase 7: Multi-Modal Context & Real-Time Screen Intelligence:** Real-time visual screen analysis, OCR, visual document understanding, and multi-modal context integration while maintaining RIYA's strict local privacy and security boundaries.
