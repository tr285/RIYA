# RIYA v1.0 — Linux Troubleshooting & Diagnostic Guide
## Resolution of Common Linux Integration, Audio, and Runtime Issues

**Product:** RIYA — Universal Offline-First Personal AI Assistant  
**Target Platform:** Ubuntu 24.04 LTS / Debian / AppImage  
**Version:** 1.0.0  

---

## 1. Quick Diagnostic Sweep

Whenever an issue occurs, start by running RIYA's built-in Linux diagnostic command:
```bash
riya --diagnostics
```
This performs an automated check of Python version, database access, disk space, GGUF models, audio hardware, and system utilities.

---

## 2. Common Errors & Verified Solutions

### Issue 1: `[SECURITY WARNING] RIYA should not be executed as root.`
- **Cause:** You ran `sudo riya` or `sudo ./RIYA-1.0.0-x86_64.AppImage`.
- **Reason:** RIYA is a personal assistant designed to protect your host system. Running as root violates user-data scoping and creates files in root directories.
- **Solution:** Always run as a standard desktop user:
  ```bash
  riya --ui
  ```
  *(To bypass this guard in containerized test environments, pass `ALLOW_ROOT=1 riya`).*

---

### Issue 2: `No local AI model found. RIYA can start but AI inference will be unavailable.`
- **Cause:** The GGUF neural weights file has not been downloaded or copied to the models directory.
- **Solution:**
  - Automated download (requires ~2.5 GB internet download):
    ```bash
    riya --setup
    ```
  - Manual placement: Copy `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` to:
    ```bash
    mkdir -p ~/.local/share/riya/models
    cp /path/to/model.gguf ~/.local/share/riya/models/
    ```
  - Custom location: If your model is stored elsewhere (e.g. `/data/models`), set an environment variable:
    ```bash
    export RIYA_MODELS_DIR="/data/models"
    riya --ui
    ```

---

### Issue 3: `ModuleNotFoundError: No module named '_tkinter'`
- **Cause:** On Ubuntu/Debian, Tkinter is packaged separately from the core Python interpreter.
- **Solution:**
  ```bash
  sudo apt install python3-tk
  ```

---

### Issue 4: `OSError: PortAudio library not found` (or `sounddevice` import error)
- **Cause:** PortAudio C shared library is missing from your system.
- **Solution:**
  - On Ubuntu / Debian:
    ```bash
    sudo apt install libportaudio2 libsndfile1
    ```
  - On Fedora / RHEL:
    ```bash
    sudo dnf install portaudio libsndfile
    ```
  - On Arch Linux:
    ```bash
    sudo pacman -S portaudio libsndfile
    ```

---

### Issue 5: No speech output or `No TTS synthesizer found`
- **Cause:** Neither `piper`, `espeak-ng`, nor `spd-say` is installed on your Linux host.
- **Solution:**
  - Install eSpeak NG (fast, lightweight offline speech synthesizer):
    ```bash
    sudo apt install espeak-ng
    ```
  - Or install Piper neural TTS for human-like speech:
    Download the Linux x86_64 piper binary and place it in `~/.local/bin/piper`.

---

### Issue 6: Clipboard copy/paste does not work or secrets are not scrubbed
- **Cause:** Missing Freedesktop clipboard utilities.
- **Solution:**
  - For Wayland (Ubuntu 24.04 default):
    ```bash
    sudo apt install wl-clipboard
    ```
  - For X11:
    ```bash
    sudo apt install xclip
    ```

---

### Issue 7: Notifications are not appearing on desktop
- **Cause:** `notify-send` utility is missing or notification daemon is inactive.
- **Solution:**
  ```bash
  sudo apt install libnotify-bin
  # Verify with a test notification:
  notify-send "RIYA" "Desktop notifications are working!"
  ```

---

### Issue 8: AppImage will not open on Ubuntu 24.04 LTS
- **Cause:** Standard Type-2 AppImages depend on `libfuse2`, which Ubuntu 24.04 replaces with `fuse3`.
- **Solution:**
  RIYA's portable AppImage includes a self-extracting runtime that extracts to `~/.cache/riya/appimage_runtimes/` automatically, working **with or without FUSE**.
  If running a third-party AppImage tool:
  ```bash
  sudo apt install libfuse2t64  # On Ubuntu 24.04
  ```

---

### Issue 9: SQLite Database is Locked (`database is locked`)
- **Cause:** A previous instance of RIYA was abruptly terminated while writing to the SQLite database.
- **Solution:**
  Check for stale locks:
  ```bash
  # Check if another riya process is running:
  pgrep -l riya
  # Kill stale process if necessary:
  pkill -9 -f "core.main"
  ```
  The database uses Write-Ahead Logging (WAL) and automatically recovers on the next connection.

---

## 3. Contact & Logs Submission

To view the latest diagnostic logs:
```bash
cat ~/.local/share/riya/logs/riya.log
```
To submit a bug report or diagnostic dump, export the system report:
```bash
riya --diagnostics > riya_diagnostics.txt
```
*(Personal conversation memories and database rows are strictly excluded from diagnostics).*

