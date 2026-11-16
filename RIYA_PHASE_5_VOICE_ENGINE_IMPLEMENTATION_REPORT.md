# RIYA v0.4 — Phase 5: Real-Time Voice Conversation Engine Implementation Report

**Project:** RIYA v0.4 (Offline-First Personal AI Assistant)  
**Location:** `/Users/tukaramgore/Projects/RIYA`  
**Target Platform:** macOS (Apple Silicon M1 verified)  
**Developer:** Tukaram  
**Report Date:** 2026-11-14  

---

## 1. Executive Summary

Phase 5 replaced RIYA's fixed-duration single-shot voice prototype with a modular, offline-first real-time voice conversation system integrated directly with `RIYAAssistant`, `ConversationContext`, `ToolExecutor`, `SecurityManager`, and `ConfirmationManager`.

The voice subsystem now supports:
- Explicit-activation real-time microphone capture (`16 kHz` mono float32) with device selection, permission error classification, and live level metering.
- Streaming local Voice Activity Detection using the bundled Silero v6 ONNX model (`silero_vad_v6.onnx`) with an adaptive energy fallback.
- Local offline Speech-to-Text via `faster-whisper` (`local_files_only=True` by default, shared model cache).
- Chunked local Text-to-Speech via macOS `/usr/bin/say` + PCM buffered playback through `sounddevice.OutputStream`, with explicit honest capability declarations (`supports_emotional_prosody = False`).
- Sub-utterance real-time user barge-in / interruption during `SPEAKING` and cancellation during `THINKING`.
- Seamless Mode A (Text), Mode B (Voice), and Mode C (Text ↔ Voice switching) over the same `RIYAAssistant` and `ConversationContext`.
- CLI voice/text switching commands in `core/main.py` plus a Tkinter desktop UI and framework-independent `VoiceUIController`.

After final edits, **137 automated tests pass** (`112` existing baseline tests + `25` new Phase 5 tests, `0` failed, `0` skipped) and `python -m compileall -q core tests` exited with code `0`.

---

## 2. Existing Voice Architecture Discovered

Before Phase 5 edits, `core/voice/` contained 4 minimal files (`225` lines total across `core/voice/*.py`, `core/main.py`, and `core/config/settings.py`):
- `core/voice/__init__.py`: empty (`0` bytes).
- `core/voice/microphone.py`: `Microphone(sample_rate=16000, channels=1)` blocking `sd.rec()`/`sd.wait()` fixed-duration recorder writing to disk.
- `core/voice/stt.py`: `LocalSTT` instantiating a fresh `WhisperModel` per instance without `local_files_only`, without caching, and printing `Detected language:` to stdout.
- `core/voice/tts.py`: Phase-3-hardened `/usr/bin/say` wrapper (`LocalTTS(voice="Samantha")`, stdin `-f -`, `shell=False`, `timeout=30`), synchronous only, without chunking, WAV synthesis, rate, volume, or playback interruption.
- `core/voice/assistant.py`: `VoiceAssistant` calling `LocalLlamaRuntime` directly (bypassing `RIYAAssistant`, tools, memory, confirmation, and `ConversationContext`).
- `apps/*` (`apps/macos`, `apps/web`, etc.) and `core/ui` contained no existing frontend code.

---

## 3. Final Architecture

```text
   [Mode A: Text UI / CLI]                     [Mode B: Real-Time Microphone]
            │                                                │
            │                                  MicrophoneStream (16 kHz, 512 samples)
            │                                                │
            │                                  UtteranceSegmenter (Silero ONNX / Energy)
            │                                    ├─ Normal turn: speech_start -> speech_ended
            │                                    └─ Barge-in during SPEAKING -> interrupt()
            │                                                │
            │                                       LocalSTT (faster-whisper, offline)
            │                                                │
            └──────────────► VoiceOrchestrator ◄─────────────┘
                            (VoiceStateMachine + VoiceSession + VoiceConfig)
                                      │
                        Voice Confirmation Confidence Gate (when pending_action set)
                                      │
                                      ▼
                        RIYAAssistant.process() [Shared ConversationContext]
                                      │
                 ┌────────────────────┼────────────────────┐
                 ▼                    ▼                    ▼
           MemoryService       CommandExecutor        AgentPlanner
                 │                    │                    │
                 └────────────► ToolExecutor ◄─────────────┘
                         (SecurityManager + ConfirmationManager)
                                      │
                                      ▼
                     AudioOutputPlayer + LocalTTS + personality.py
                      (Chunked PCM playback + immediate stop())
```

### State Machine (`core/voice/state.py`)
Implements all 8 required states (`IDLE`, `LISTENING`, `TRANSCRIBING`, `THINKING`, `SPEAKING`, `INTERRUPTED`, `ERROR`, `STOPPING`), thread-safe `RLock` synchronization, listener isolation outside locks, and strict transition enforcement raising `InvalidStateTransition` on disallowed edges.

---

## 4. STT Engine Selected and Rationale

- **Selected Engine:** `faster-whisper` (`1.2.1`, CTranslate2 `4.8.2` backend) with CPU `int8` inference and default `base` (`~148 MB`) or configurable `small` (`~486 MB`) / `tiny` (`~75 MB`).
- **Rationale:** Already present and compatible with Python 3.13.9 on Apple Silicon (`arm64`), offline-capable (`local_files_only=True`), low memory footprint via thread-safe model singleton caching (`_MODEL_CACHE`), supports numpy waveforms directly without disk temp files per utterance, and supports multilingual models.
- **Measured Offline Benchmark (probe WAV `3.90s`, M1 CPU `int8`):**
  - `base`: load `0.92s`, transcribe `0.52s–0.67s`.
  - `small`: load `0.84s`, transcribe `2.15s–2.23s`.
- **Safeguards:** Silence hallucination filtering (`no_speech_prob > 0.85` & `avg_logprob < -1.0`), `STTModelUnavailableError` on uncached models (zero automatic network downloads), control-char stripping, `max_transcript_chars` capping, and removal of stdout transcript/language printing.

---

## 5. TTS Engine Selected and Rationale

- **Selected Engine:** Native macOS `/usr/bin/say` wrapped by `LocalTTS`, paired with `AudioOutputPlayer` (`soundfile` + `sounddevice.OutputStream`) for buffered PCM chunk playback.
- **Rationale:** Piper / `pyttsx3` / `AVFoundation` Python bindings are not installed in this environment; `/usr/bin/say` is pre-installed offline on macOS (`184` system voices inspected including `Samantha`, `Daniel`, `Aman`, `Rishi`, `Tara`, `Lekha`), requires zero extra downloads or network requests, and preserves Phase 3's validated argv/stdin execution security (`shell=False`, regex voice validation, `-f -`).
- **Capability Honesty:** `LocalTTS.supports_emotional_prosody = False`. Expressive personality profiles (`friendly`, `calm`, `excited`, `empathetic`, `professional` in `core/voice/personality.py`) control rate cadence (`style_rate_wpm`), markdown-to-speech cleanup, and sentence/clause chunking without falsely claiming neural emotional prosody and without altering security confirmation prompts (`Proceed?`).
- **Design Pluggability:** `AudioOutputPlayer` accepts any TTS implementation exposing `.synthesize_to_wav()` (`supports_wav_synthesis=True`) or `.speak()`, allowing a future local neural engine (e.g., Piper or Kokoro) to drop in cleanly.

---

## 6. VAD Implementation

Implemented in `core/voice/vad.py`:
- **Primary Backend (`SileroStreamingVAD`):** Uses `onnxruntime` (`1.30.0`, `CPUExecutionProvider`, 1 thread) directly against `silero_vad_v6.onnx` (`1.25 MB`, already bundled inside `faster-whisper`), preserving stateful `h`/`c` tensors (`[1, 1, 128]`) and 64-sample context overlap across 512-sample (`32 ms`) 16 kHz frames. Validated against `faster_whisper.vad.SileroVADModel` with `maxdiff = 0.0` at `~0.11 ms/frame` on M1.
- **Fallback Backend (`EnergyVAD`):** Lightweight adaptive RMS noise-floor estimator used when configured (`vad_engine="energy"`) or if ONNX fails to initialize.
- **Turn-Taking & Pause Tolerance (`UtteranceSegmenter`):** Dual-threshold hysteresis (`vad_threshold=0.5`, `vad_neg_threshold=0.35`), configurable `min_speech_ms=250`, `min_silence_ms=700` (preserves natural breathing pauses), pre-roll buffer (`speech_pad_ms=300`), and `max_utterance_seconds=30.0` hard cap.

---

## 7. Audio Input / Output Implementation

- **Input (`core/voice/audio_input.py`, `core/voice/microphone.py`):**
  - `list_input_devices()` and `list_output_devices()` enumerate PortAudio hardware cleanly.
  - `MicrophoneStream` remains completely inert until explicit `start()`, handles resampling (`resample_mono`) from hardware sample rates (e.g., `48000 Hz` MacBook Air Microphone) to `16000 Hz`, drops oldest frames if queue saturates, computes real-time RMS level (`0.0..1.0`), and classifies OS permission errors or exact-zero dead input (`MicrophonePermissionError`), missing devices (`MicrophoneUnavailableError`), and stream failures (`AudioCaptureError`).
- **Output (`core/voice/audio_output.py`):**
  - `AudioOutputPlayer` splits speech cleanly on sentence/clause/word boundaries (`split_into_speech_chunks`, never mid-word), synthesizes per-chunk WAVs inside a private `0o700` temp directory, deletes each WAV immediately in `finally:`, applies user volume scaling (`0.0..1.0`), streams in ~25 ms blocks to the configured output device while reporting real-time `current_level`, and updates `spoken_chunks` synchronously.

---

## 8. Interruption Implementation

Real-time interruption / barge-in works across two coordinated threads in `VoiceOrchestrator`:
1. While `_turn_worker_loop` plays assistant chunks sequentially through `AudioOutputPlayer`, `_audio_capture_loop` continues reading 32 ms mic frames into `UtteranceSegmenter.process_frame(..., assistant_speaking=True)`.
2. `notify_playback_started()` applies an initial `echo_guard_ms` cooldown (`250 ms`), after which sustained high-probability user speech (`barge_in_threshold=0.8`, `barge_in_min_speech_ms=400`) triggers `barge_in_triggered=True` and immediately captures the interrupting words (including pre-roll).
3. `VoiceOrchestrator.interrupt()` atomically increments `_turn_id`, sets `_turn_cancel_event`, invokes `player.stop()` (aborting the PCM output stream and clearing remaining chunks within one 25 ms block) and `assistant.cancel()`, and transitions `SPEAKING -> INTERRUPTED -> LISTENING` (or `THINKING -> INTERRUPTED -> LISTENING`).
4. Any in-flight inference returning with an older `turn_id` is discarded before appending stale assistant audio or speaking stale text.

---

## 9. Voice / Text Switching

`VoiceOrchestrator` manages Mode A (`"text"`), Mode B (`"voice"`), and Mode C (`switch_mode("text" | "voice")`):
- `send_text()` and `handle_utterance_audio()` both call `self.assistant.process(...)` on the **exact same `RIYAAssistant` instance and `ConversationContext`**, avoiding duplicate entries or split voice memory.
- Switching `"voice" -> "text"` halts playback, closes the microphone stream (`microphone_active == False`), clears temporary audio, and keeps conversation history intact.
- `core/main.py` exposes `--voice`, `--ui`, and interactive slash commands `/voice`, `/text`, `/interrupt`, `/status`, and `/settings`.
- Note on model streaming: Because RIYA's local `LocalLlamaRuntime` (`core/ai/local_runtime.py` + `riya_llama.cpp`) produces full responses synchronously per call rather than token streaming, RIYA honestly chunks the completed response at sentence boundaries via `split_into_speech_chunks()` rather than fabricating token-by-token streaming.

---

## 10. UI Implementation

Implemented in `core/voice/ui.py`:
- **`VoiceUIController` & `VoiceVisualSnapshot`:** UI-framework-independent controller providing visual status strings across all 8 `VoiceState` values, live 24-bar `waveform_bars` envelopes synced to microphone/speaker RMS levels, transcript entries, `can_interrupt` status, `MIC LIVE` / `MIC OFF` indicator, honest `(code - untested)` language labels derived from `VERIFIED_LANGUAGES`, and persisted settings application.
- **Tkinter Desktop Interface (`launch_voice_gui`):** Features conversation transcript feed (`You [text/voice]`, `RIYA [text/voice]`), text box + Send, `Start Voice`/`Stop Voice` toggle, `Interrupt` button, `MIC LIVE`/`MIC OFF` badge, animated 24-bar canvas waveform throttled to ~15 fps only while active (`350 ms` idle polling to minimize M1 battery use), and a Settings window covering input device, output device, language, voice, response style, speed (WPM), volume, and voice response toggle.

---

## 11. Files Created and Modified

### Files Created
1. `core/voice/errors.py`
2. `core/voice/state.py`
3. `core/voice/config.py`
4. `core/voice/vad.py`
5. `core/voice/audio_input.py`
6. `core/voice/personality.py`
7. `core/voice/audio_output.py`
8. `core/voice/session.py`
9. `core/voice/orchestrator.py`
10. `core/voice/ui.py`
11. `tests/test_voice_state_machine.py`
12. `tests/test_voice_session.py`
13. `tests/test_voice_stt.py`
14. `tests/test_voice_tts.py`
15. `tests/test_voice_interruption.py`
16. `tests/test_voice_security.py`
17. `tests/test_voice_conversation_integration.py`
18. `RIYA_PHASE_5_VOICE_ENGINE_IMPLEMENTATION_REPORT.md`

### Files Modified (with `*.before_phase5` non-overwriting backups)
1. `core/voice/__init__.py` (previously empty `0` bytes)
2. `core/voice/microphone.py` (`core/voice/microphone.py.before_phase5`)
3. `core/voice/stt.py` (`core/voice/stt.py.before_phase5`)
4. `core/voice/tts.py` (`core/voice/tts.py.before_phase5`)
5. `core/voice/assistant.py` (`core/voice/assistant.py.before_phase5`)
6. `core/main.py` (`core/main.py.before_phase5`)
7. `core/tools/filesystem/files.py` (`core/tools/filesystem/files.py.before_phase5`)
8. `pyproject.toml` (`pyproject.toml.before_phase5`)
*(Note: `core/ai/local_runtime.py.before_phase5` was created pre-edit; `core/ai/local_runtime.py` required no modifications.)*

---

## 12. Dependencies Added and Reasons

- **No new pip packages were installed into the environment during Phase 5** (all required libraries were already present in `/opt/anaconda3/bin/python` 3.13.9 `arm64`: `sounddevice 0.5.6`, `soundfile 0.14.0`, `faster_whisper 1.2.1`, `onnxruntime 1.30.0`, `numpy 2.3.5`, `tkinter`).
- **Updated `pyproject.toml` `[project.optional-dependencies] voice`:** Added `"onnxruntime>=1.16"` alongside existing `sounddevice>=0.4`, `soundfile>=0.12`, and `faster-whisper>=1.0` so package metadata accurately reflects `SileroStreamingVAD`.

---

## 13. Model Sizes and Storage Requirements

| Component | Model / Asset | Approximate Size | Location / Notes |
|---|---|---|---|
| **VAD** | `silero_vad_v6.onnx` | `1.25 MB` | Bundled inside `faster_whisper/assets/` |
| **STT (Default)** | `faster-whisper-base` (`int8`) | `~148 MB` (`147.9 MB` cached) | Local Hugging Face cache; no runtime download |
| **STT (Optional)** | `faster-whisper-small` (`int8`) | `~486 MB` (`486.2 MB` cached) | Local Hugging Face cache; slower (`~2.2s` vs `~0.6s`) |
| **STT (Uncached)** | `tiny` (`~75 MB`), `medium` (`~1.5 GB`), `large-v3-turbo` (`~1.6 GB`) | Not installed | Explicit `STTModelUnavailableError` if selected offline |
| **TTS** | macOS `/usr/bin/say` voices (`184` local voices) | OS-native (`0 B` extra) | Offline Apple speech synthesis |
| **LLM** | `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` | Existing local model | Unchanged in `models/` |

---

## 14. Offline Verification

- `LocalSTT` defaults to `local_files_only=True`; attempting to load an uncached model raises `STTModelUnavailableError` rather than initiating a network download.
- Executed real end-to-end audio/VAD/STT/TTS smoke test under `HF_HUB_OFFLINE=1` with zero network access (`faster-whisper` `base`, `silero_vad_v6.onnx`, `/usr/bin/say`, `AudioOutputPlayer`) — succeeded cleanly (`0.52s` STT, working Silero VAD, working speaker playback & interruption).
- Automated `tests/test_voice_stt.py` asserts `local_files_only=True` is forwarded to `WhisperModel`.

---

## 15. Privacy and Security Verification

- **No Hidden Recording:** `MicrophoneStream` and `VoiceSession` never open audio streams until `start()` / `switch_mode("voice")` is explicitly invoked; `microphone_active` tracks true stream status.
- **Temp Storage Cleanup & Opt-in Retention:** `VoiceSession` uses a `0o700` temp directory deleted automatically on `stop()`/`close()`/`recover_from_error()`, `AudioOutputPlayer` deletes chunk WAV files immediately in `finally:`, and `maybe_save_recording()` writes permanent files **only** when `retain_recordings=True`.
- **Tool Authorization Boundary:** Transcribed text passes strictly through `RIYAAssistant.process()` -> `AgentPlanner` -> `ToolExecutor` -> `SecurityManager` -> `ConfirmationManager`. Verified in `tests/test_voice_security.py`: low-confidence confirmations (`< min_confirmation_confidence`), ambiguous voice phrases, `voice_confirmation_enabled=False`, and spoken `confirmation_token=...` strings cannot authorize protected actions.
- **Filesystem Protection:** `core/tools/filesystem/files.py` now blocks/hides `voice_settings.json` (`0o600` atomic writes) and `voice_recordings` from AI filesystem tools (`read_file`, `list_files`, `find_files`).
- **No Debug Transcript Leaks:** `LocalSTT` no longer prints recognized metadata to stdout.

---

## 16. Automated Test Results

All automated tests use mocks for hardware/LLM/model operations where noted (except `test_real_bundled_silero_onnx_vad_offline` which runs the bundled `silero_vad_v6.onnx` directly):

- **Focused Phase 5 Voice Suites (`25` tests):**
  `pytest -v tests/test_voice_state_machine.py tests/test_voice_session.py tests/test_voice_stt.py tests/test_voice_tts.py tests/test_voice_interruption.py tests/test_voice_security.py tests/test_voice_conversation_integration.py`
  -> **`25 passed in 1.15s`** (`test_voice_state_machine`: 5, `test_voice_session`: 7, `test_voice_stt`: 3, `test_voice_tts`: 3, `test_voice_interruption`: 2, `test_voice_security`: 3, `test_voice_conversation_integration`: 2).
- **Full Suite (`pytest -q`):** **`137 passed in 1.21s`** (`112` existing baseline + `25` new Phase 5 tests; `0` failed, `0` skipped, exit code `0`).
- **Python Bytecode Compilation (`python -m compileall -q core tests`):** exit code `0`.

---

## 17. Actual Hardware Test Results

Executed a controlled offline smoke run (`HF_HUB_OFFLINE=1`) on Apple MacBook Air M1 (`arm64`, macOS):
- **Verified Audio Devices:** `MacBook Air Microphone` (index `0`, `48000.0 Hz`), `MacBook Air Speakers` (index `1`, `88200.0 Hz`).
- **Real Microphone Capture Probe (`MicrophoneStream.probe_permission_and_read_level(0.25)`):** Opened hardware stream cleanly, captured live non-zero ambient RMS (`0.011509`), and stopped cleanly (`active_after_stop=False`, confirming macOS mic permission and clean shutdown).
- **Real Offline Silero VAD + LocalSTT:** Segmented a synthesized `3.90s` speech WAV via `SileroStreamingVAD` (`1` utterance, `72704` samples) and transcribed via cached `faster-whisper` (`base`, `int8`, `local_files_only=True`) in `0.52s` (`conf=0.722`).
- **Real Speaker Playback & Interruption:** Rendered speech via `/usr/bin/say` and played PCM via `AudioOutputPlayer` to `MacBook Air Speakers`, interrupted mid-speech after `0.25s` (`completed=False`, `cancelled=True`, `spoken_chunks=[]`, `pgrep -x say` returned `no_orphaned_say`).
- **Orchestrator & Temp Cleanup:** Switched `text -> voice -> text`, verified `ConversationContext` shared cleanly (`ctx_len=4`), and confirmed temp dir removal (`AFTER_STOP_TEMP_EXISTS=False`, `mic_active=False`, `state=idle`).
- **Honest Boundary on Hardware Test:** Live GGUF `Qwen3-4B` LLM inference (`assistant.ai` was mocked in the smoke run), multi-second spoken live human mic conversation loop, and interactive Tkinter window mouse-click walkthrough were **not** exercised in this scripted verification run.

---

## 18. Known Limitations

1. **No Token-Level LLM Streaming Yet:** `LocalLlamaRuntime` / `riya_llama.cpp` generates full replies per inference call; speech chunking begins after full text generation completes rather than streaming tokens incrementally from `llama.cpp`.
2. **macOS `/usr/bin/say` Acoustic Expressiveness:** Native `/usr/bin/say` varies voice and speaking rate (`-r`), not genuine neural acoustic emotion (`supports_emotional_prosody = False`).
3. **English-Only End-to-End Verification:** `hi` (Hindi) and `mr` (Marathi) language options are exposed in `VoiceConfig` and labeled `(untested)` in UI settings (`VERIFIED_LANGUAGES = {"en"}`), because real STT + TTS pronunciation fidelity for Hindi/Marathi has not yet been tested end-to-end.
4. **No Acoustic Echo Cancellation (AEC):** Barge-in relies on `echo_guard_ms=250` plus a strict high `barge_in_threshold=0.8` (`400 ms` sustained speech); using headphones eliminates speaker-to-mic bleed best, while high speaker volume at close range could otherwise trigger false barge-in after the 250 ms cooldown.
5. **macOS-First TTS/UI:** Non-macOS TTS fallback (e.g., Piper or `espeak-ng`) is not implemented yet.

---

## 19. Recommended Next Phase

1. **Native C++ Token Streaming & Abort Hook (`core/ai/native/bridge/riya_llama.cpp`):** Expose token callbacks + atomic cancellation from `llama.cpp` so RIYA can speak the first completed sentence while remaining tokens are still generating, and abort long generations instantly on barge-in.
2. **Optional Local Neural TTS Backend (e.g., Piper ONNX):** Add an offline neural voice engine implementation matching `LocalTTS` (`supports_wav_synthesis=True`, supporting natural English/Hindi/Marathi voices once locally downloaded with user consent).
3. **Acoustic Echo Cancellation / Reference Subtraction & Live Interactive Soak Test:** Add WebRTC/Speex AEC or adaptive reference suppression using the outgoing PCM playback buffer and run an interactive live human conversation + Tkinter GUI soak test across English, Hindi, and Marathi before removing `(untested)` labels.
