# RIYA v0.5 — Phase 6 — Milestone 6.5 Baseline Audit
## Natural Voice Conversations | Offline-First Voice Intelligence — Repository Audit & Architecture Plan

**Project:** RIYA — Universal Personal AI Assistant  
**Developer:** Tukaram  
**Milestone:** Phase 6.5 — Natural Voice Conversations  
**Previous Milestones:** Phase 6.1 (Unified Interface), Phase 6.2 (OS Abstraction), Phase 6.3 (Intelligent File Management), Phase 6.4 (Application Control)  
**Baseline Test Results:** `369 passed, 0 failed, 0 skipped` (verified in 159.59s)  
**Database Schema:** `v7` (`0007_phase6_intelligent_file_management`)  
**Development Platform:** macOS Apple Silicon (M1) with Darwin native integration  
**AI Engine:** Local `LocalLlamaRuntime` with Qwen3-4B-Instruct-2507-Q4_K_M.gguf via embedded `llama.cpp` Metal bridge

---

## 1. Existing Architecture & Reused Foundations

RIYA already contains a modular voice engine under `core/voice/` developed across earlier phases:
- **`core/voice/audio_input.py` (`MicrophoneStream`):**
  - Thread-safe real-time audio capture via `sounddevice.InputStream`.
  - Converts arbitrary hardware sample rates and multi-channel input to 16 kHz mono float32 (`resample_mono`).
  - Pre-flight permission probing with `permission_probe_seconds` (default 1.5s) to detect macOS TCC denials before opening long-running streams.
  - Device enumeration (`list_input_devices`, `list_output_devices`) returning `AudioDeviceInfo`.
- **`core/voice/audio_output.py` (`AudioOutputPlayer`):**
  - Chunked, streaming playback of synthesized speech using `sounddevice` and `soundfile`.
  - Instant interruption (`stop()` and `_stop_event.set()`) to support natural barge-in without audio buffering lag.
- **`core/voice/stt.py` (`LocalSTT`):**
  - Fast, local, offline speech recognition using `faster-whisper` (`WhisperModel` on CTranslate2).
  - Model caching (`_MODEL_CACHE`) reusing loaded instances across turns on Apple Silicon.
  - Supports both in-memory NumPy float32 audio arrays and local WAV audio files (`str | Path`).
  - `local_files_only=True` prevents unexpected network downloads at runtime.
- **`core/voice/tts.py` (`LocalTTS`):**
  - Offline local text-to-speech powered by macOS `/usr/bin/say`.
  - Parameterized non-shell command construction (`_build_say_command`).
  - Voice selection (`Samantha`, `Albert`, `Aman`, etc.) and rate control (`rate_wpm` 90–360).
- **`core/voice/vad.py` (`SileroStreamingVAD` & `EnergyVAD`):**
  - Stateful 16 kHz, 512-sample frame ONNX runtime detector using bundled `silero_vad_v6.onnx`.
  - Fallback adaptive RMS `EnergyVAD` when ONNX is absent or energy mode is forced.
  - `UtteranceSegmenter` managing pre-roll padding, minimum speech duration, trailing silence detection, and maximum utterance bounds.
- **`core/voice/session.py` (`VoiceSession`):**
  - Manages single active voice session lock, recording lifecycle, and temporary file cleanup.
  - User-scoped transcript entries (`TranscriptEntry`) preserving speech metadata (modality, confidence, language).
- **`core/voice/state.py` (`VoiceStateMachine`):**
  - Thread-safe state transition graph (`IDLE`, `LISTENING`, `TRANSCRIBING`, `THINKING`, `SPEAKING`, `INTERRUPTED`, `ERROR`, `STOPPING`).
- **`core/voice/orchestrator.py` (`VoiceOrchestrator`):**
  - Master coordinator connecting microphone capture, VAD segmentation, STT transcription, `RIYAAssistant.process()`, and TTS playback.
- **`core/voice/ui.py` (`VoiceUIController`):**
  - Visual status snapshots, 24-bar waveform animation envelope, and multi-turn transcript hydration.

---

## 2. Audio & Speech Dependencies Audit

Inspection of the Python environment (`python -c "..."`) on this macOS host reveals:

| Dependency | Status | Notes |
|---|---|---|
| `sounddevice` | **Available** | PortAudio wrapper for real-time microphone capture and playback |
| `faster_whisper` | **Available** | CTranslate2-based offline Whisper implementation |
| `onnxruntime` | **Available (v1.30.0)** | High-performance inference engine for local Silero VAD |
| `silero_vad_v6.onnx` | **Available on Disk** | Located at `/opt/anaconda3/lib/python3.13/site-packages/faster_whisper/assets/silero_vad_v6.onnx` |
| Cached STT Models | **Available on Disk** | `~/.cache/huggingface/hub/models--Systran--faster-whisper-small` and `models--Systran--faster-whisper-base` |
| Cached AI Models | **Available on Disk** | `~/.cache/huggingface/hub/models--lmstudio-community--Qwen3-4B-Instruct-2507-GGUF` |
| Cached Embeddings | **Available on Disk** | `~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2` |
| `numpy` & `scipy` | **Available** | Array manipulation, resampling, and DSP |
| `wave` & `soundfile` | **Available** | Audio file I/O for test fixtures and temporary utterance files |
| `/usr/bin/say` | **Available** | Native macOS speech synthesis with 20+ English voices |
| `torch` | **Available** | PyTorch available for fallback computation if needed |

**Conclusion:** Zero external network downloads or cloud API subscriptions are required. All necessary models and binaries are already installed locally on this machine.

---

## 3. Local AI Integration & Conversation Lifecycle

1. **Inference Pipeline:**
   - Spoken audio is recorded via `MicrophoneStream`.
   - `UtteranceSegmenter` segments speech via local `SileroStreamingVAD`.
   - `LocalSTT` transcribes audio frames to a structured `TranscriptionResult(text, language, confidence, duration_seconds)`.
   - Transcript text is routed directly to `RIYAAssistant.process(transcript_text)` (the identical pipeline used for typed input).
   - `RIYAAssistant` invokes `LocalLlamaRuntime` with Qwen3 on Apple Silicon Metal acceleration, maintaining conversational memory and multi-turn context.
   - The generated response text is split into natural sentences by `split_into_speech_chunks` and synthesized via `LocalTTS` / `AudioOutputPlayer`.
2. **Context & Multi-Turn Unification:**
   - Both typed text and voice messages append to the same underlying conversation context (`ExecutionContext` and `RIYAAssistant._conversation_context`).
   - Follow-up voice questions correctly resolve pronouns and references to earlier typed messages and vice versa.

---

## 4. Permission & Security Architecture

1. **Microphone Access Control:**
   - `voice.microphone` permission spec in `core/security/permissions.py` (sensitive, protected by default).
   - System-level macOS TCC permission is checked on stream initialization (`_PERMISSION_ERROR_TOKENS`). If permission is denied, RIYA fails closed with `MicrophonePermissionError` and never attempts clandestine capture.
2. **Confirmation Gate Preservation:**
   - High-risk and mutating actions (`delete_file`, `close_application`, `desktop_terminate_app`, etc.) requiring user confirmation through `ConfirmationManager` **cannot be bypassed via voice**.
   - Spoken confirmations ("yes", "confirm", "proceed") require high transcription confidence (`min_confirmation_confidence >= 0.55`).
   - Injected confirmation tokens in speech transcripts are rejected.
3. **Data Privacy:**
   - `retain_recordings = False` by default. Utterance audio exists only as transient memory buffers and is wiped after turn completion.

---

## 5. Gap Analysis for Milestone 6.5

While the core modules exist, the following critical requirements from the Phase 6.5 specification must be implemented and hardened:

1. **Formal Cross-Platform Voice Adapter Interfaces (Section 13):**
   - Need reusable abstract interfaces in `core/voice/adapters.py`:
     - `AudioInputAdapter` (ABC)
     - `AudioOutputAdapter` (ABC)
     - `SpeechRecognitionAdapter` (ABC)
     - `SpeechSynthesisAdapter` (ABC)
     - `VoiceActivityDetector` (ABC)
     - `VoiceSessionManager` (ABC)
   - Concrete implementations for macOS (`MacOSAudioInputAdapter`, `MacOSAudioOutputAdapter`, `FasterWhisperSTTAdapter`, `MacOSSayTTSAdapter`, `SileroVADAdapter`, `DefaultVoiceSessionManager`).
   - Honest, structured unsupported adapters for Windows and Linux returning `status="unsupported"` without simulating functionality.
2. **Explicit Dual Voice Modes (Section 7):**
   - **Mode A: Push-to-Talk (`push_to_talk`):**
     - User explicitly triggers start-recording, speaks, releases/stops recording, and receives a spoken response.
   - **Mode B: Continuous Conversation (`continuous`):**
     - Real-time continuous listening with VAD-triggered auto-turn processing, returning to `LISTENING` after playback.
   - Distinct controls, state transitions, and UI indicators for both modes.
3. **Enhanced State Machine (Section 10):**
   - Formalize extended states: `IDLE`, `LISTENING`, `PROCESSING`, `THINKING`, `SPEAKING`, `INTERRUPTED`, `PAUSED`, `ERROR`, `STOPPED`.
   - Support `PROCESSING` as a distinct state between speech capture and inference.
4. **Offline Capability Diagnostics & Capability Reporting (Section 12):**
   - Implement `check_voice_capabilities()` providing structured diagnostics for STT availability, TTS voice availability, VAD model presence, microphone permissions, and offline readiness.
5. **Robust Audio-File Input for Headless Testing (Section 4 & 15):**
   - Comprehensive test fixtures using synthesized WAV files to verify STT, VAD, and conversation loops without requiring hardware microphones in CI/CD.

---

## 6. Phase 6.5 Implementation Plan

```
                                    VoiceSessionManager
                                             │
             ┌───────────────────────────────┴───────────────────────────────┐
             ▼                                                               ▼
   Mode A: Push-to-Talk                                         Mode B: Continuous Conversation
(Explicit Record Start/Stop)                                        (VAD Auto-Segmentation)
             │                                                               │
             └───────────────────────────────┬───────────────────────────────┘
                                             ▼
                                     AudioInputAdapter
                                             │
                                             ▼
                                   VoiceActivityDetector
                                (Silero VAD / Energy VAD)
                                             │
                                             ▼
                                 SpeechRecognitionAdapter
                              (faster-whisper / LocalSTT)
                                             │
                                             ▼
                                  RIYAAssistant (Qwen3)
                           (Conversational Memory & Tools)
                                             │
                                             ▼
                                  SpeechSynthesisAdapter
                                (LocalTTS / /usr/bin/say)
                                             │
                                             ▼
                                    AudioOutputAdapter
                               (Chunked, Interruptible Player)
```

### Execution Stages
- **Stage 1 (Complete):** Audit repository, verify dependencies, run baseline tests (`369 passed`), create audit artifact.
- **Stage 2:** Implement cross-platform adapter interfaces in `core/voice/adapters.py` (AudioInputAdapter, AudioOutputAdapter, SpeechRecognitionAdapter, SpeechSynthesisAdapter, VoiceActivityDetector, VoiceSessionManager).
- **Stage 3:** Implement concrete macOS adapters and truthful unsupported Windows/Linux adapters.
- **Stage 4:** Enhance `VoiceSession` and `VoiceOrchestrator` with explicit `push_to_talk` and `continuous` modes, extended state machine (`PROCESSING`, `PAUSED`, `STOPPED`), and offline capability diagnostics.
- **Stage 5:** Update `VoiceUIController` and CLI integration to support push-to-talk, continuous toggle, and live state reporting.
- **Stage 6:** Build comprehensive unit, integration, and security test suite in `tests/test_phase6_milestone_6_5_voice_intelligence.py`.
- **Stage 7:** Full regression run ensuring all 369 baseline tests pass alongside new voice tests.
- **Stage 8:** Write final implementation report `RIYA_V0_5_PHASE_6_MILESTONE_6_5_REPORT.md`.

---

## 7. Potential Compatibility & Security Risks

1. **Microphone Permission Denials:** On macOS, missing TCC permissions can cause hard crashes if not handled gracefully. The `MicrophoneStream` pre-flight probe mitigates this by catching PortAudio errors and mapping them to `MicrophonePermissionError`.
2. **Audio Overlap & Stale Responses during Barge-in:** When a user interrupts while RIYA is speaking or thinking, speech synthesis and active inference must be cancelled immediately, and the discarded response must not bleed into the next turn.
3. **High Resource Contention on Apple Silicon:** Running Qwen3 and faster-whisper concurrently requires careful memory management. Using model caching and single-threaded ONNX VAD prevents CPU/memory exhaustion.
4. **Command Injection via Transcripts:** Voice transcripts are strictly sanitized using `strip_unsafe_control_chars` and passed through the standard `ToolExecutor` security and confirmation pipeline.
