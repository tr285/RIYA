# RIYA v1.0 — MACOS TROUBLESHOOTING & RECOVERY GUIDE

**Product:** RIYA — Universal Personal AI Assistant  
**Platform:** macOS (Apple Silicon & Intel)  
**Release:** v1.0.0 Production  

---

## 1. Quick Diagnostic Checklist

If you experience unexpected behavior, run through this quick checklist:

1. **Check System Diagnostics:**
   - In RIYA, click **📊 System Status** in the sidebar.
   - Look for any check marked `[FAILED]` or `[WARNING]`.
2. **Verify Microphone State:**
   - Look at the top status bar: Is the badge **MIC LIVE** (green), **MIC PAUSED** (amber), or **MIC OFF** (grey)?
3. **Verify AI Engine State:**
   - Does the top chip read **AI: Ready (Apple Metal GPU)** or **AI: Limited Mode**?
4. **Inspect Console Logs:**
   - Run RIYA from terminal to view real-time diagnostics:
     ```bash
     python -m core.main --diagnostics
     ```

---

## 2. macOS Gatekeeper & App Launch Issues

### Issue 2.1: "RIYA cannot be opened because it is from an unidentified developer"
- **Cause:** macOS Gatekeeper blocks applications signed with local ad-hoc developer certificates that have not been notarized by Apple Developer servers.
- **Solution A (Recommended):**
  1. Open **System Settings** on your Mac.
  2. Navigate to **Privacy & Security**.
  3. Scroll down to the **Security** header.
  4. Under *"RIYA was blocked from use because it is not from an identified developer"*, click **"Open Anyway"**.
  5. Enter your macOS administrator password to confirm.
- **Solution B (Terminal Command):**
  Clear quarantine extended attributes directly:
  ```bash
  xattr -cr /Applications/RIYA.app
  ```

### Issue 2.2: Application crashes or alerts "Python 3 environment not found"
- **Cause:** The launcher script could not locate a valid Python 3 installation with Tkinter GUI support.
- **Solution:**
  1. Verify Python 3 with Tkinter is installed:
     ```bash
     python3 -c "import tkinter; print('Tkinter OK')"
     ```
  2. If using Homebrew or Conda, explicitly point RIYA to your preferred executable by setting `RIYA_PYTHON`:
     ```bash
     export RIYA_PYTHON="/opt/homebrew/bin/python3"
     ```
  3. Alternatively, install Python 3 via Homebrew:
     ```bash
     brew install python-tk@3.13
     ```

---

## 3. Microphone & Voice Recognition Issues

### Issue 3.1: Voice Mode says "MIC OFF" or fails with `MicrophonePermissionError`
- **Cause:** macOS Privacy & Security has blocked RIYA from accessing the physical recording hardware.
- **Solution:**
  1. Open **System Settings** > **Privacy & Security** > **Microphone**.
  2. Locate **RIYA** (or your Terminal/Python environment) in the list.
  3. Toggle the switch to **ON** (Enabled).
  4. If RIYA does not appear in the list, reset the macOS TCC microphone permission cache:
     ```bash
     tccutil reset Microphone com.riya.assistant
     ```
  5. Relaunch RIYA. macOS will display the permission prompt again.

### Issue 3.2: Push-to-Talk records but transcribes empty text
- **Cause:** Physical input device is set to an inactive or muted audio interface, or input level is too low.
- **Solution:**
  1. In RIYA, click **⚙️ Preferences** in the sidebar.
  2. Switch to the **Voice** tab.
  3. Inspect the **Audio Input (Microphone)** dropdown.
  4. Ensure your active microphone (e.g. `[0] MacBook Pro Microphone`) is selected.
  5. Speak clearly within 1–2 feet of your Mac.

### Issue 3.3: Audio feedback or echo during continuous listening
- **Cause:** Built-in speakers are feeding synthesized speech back into the microphone.
- **Solution:**
  - Wear headphones or connected AirPods when using **Continuous Voice** mode.
  - Or switch to **Push-to-Talk** (`Cmd + Shift + V` -> Press to Talk) which automatically mutes the input stream during speech playback.

---

## 4. AI Engine & Model Inference Issues

### Issue 4.1: Status shows "AI: Limited Mode (No model loaded)"
- **Cause:** The bundled GGUF model file was moved, deleted, or corrupted.
- **Solution:**
  1. Verify the model file exists in the `models/` directory:
     ```bash
     ls -la models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
     ```
  2. The file size should be approximately **2,497,280,448 bytes (2.5 GB)**.
  3. If missing, restore the file from your backup archive or re-download the official Qwen3 GGUF model.

### Issue 4.2: Inference is slow (CPU fallback instead of Metal GPU)
- **Cause:** Native `libriya_llama.dylib` was compiled without Metal support or execution is falling back to CPU.
- **Solution:**
  1. Open **⚙️ Preferences** > **AI Models**.
  2. Check **Active Backend**. It should read **Apple Metal GPU**.
  3. If it reads CPU fallback, verify your Mac uses Apple Silicon (`uname -m` outputs `arm64`).
  4. Ensure Metal shading compiler is accessible (`xcrun -sdk macosx metal -v`).

---

## 5. Memory & Database Recovery

### Issue 5.1: RIYA displays "Recovery Required" on startup
- **Cause:** Database file `data/riya.db` suffered an ungraceful crash or SQLite lock error.
- **Solution:**
  1. RIYA includes an automated recovery system in the onboarding wizard. Click **"Recover from Backup"**.
  2. The recovery manager will automatically locate the most recent verified backup in `archive/backup_files/` (or `data/riya.db.before_*`) and restore your memories.
  3. To manually check database integrity:
     ```bash
     sqlite3 data/riya.db "PRAGMA integrity_check;"
     ```
     Should return `ok`.

### Issue 5.2: Forgotten user password
- **Cause:** Local user password entered incorrectly during sign-in.
- **Solution:**
  - In compliance with privacy design, RIYA does not store unhashed credentials.
  - If you lose access, create a new local user profile through the onboarding screen. Your previous user profile remains isolated and encrypted on disk.

---

## 6. Performance & Storage Optimization

### Cleaning Voice Temp Caches
Voice recordings and temporary WAV files are stored in `data/voice_temp/`. RIYA cleans these up automatically upon turn completion, but if unexpected shutdowns left orphaned files:
```bash
rm -rf data/voice_temp/*.wav
```

### Resetting Preferences
If you wish to reset all voice and appearance preferences to defaults without affecting your stored memories:
1. Open **⚙️ Preferences** > **Voice**.
2. Click **Reset to Defaults**.

---

*Need additional help? Check `RIYA_V1_MACOS_INSTALLATION_GUIDE.md` or submit an issue to the developer.*

