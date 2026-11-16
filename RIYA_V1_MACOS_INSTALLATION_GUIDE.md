# RIYA v1.0 — MACOS INSTALLATION & ONBOARDING GUIDE

**Product:** RIYA — Universal Personal AI Assistant  
**Platform:** macOS 12.0+ (Apple Silicon Recommended: M1 / M2 / M3 / M4)  
**Release:** v1.0.0 Production  

---

## 1. System Requirements

Before installing RIYA, verify that your Mac meets the following hardware and software requirements:

- **Operating System:** macOS Monterey (12.0) or later (macOS Sonoma 14+ recommended).
- **Processor:** Apple Silicon (M1, M2, M3, M4) recommended for native Apple Metal GPU acceleration. Intel Macs fall back to multi-threaded CPU inference.
- **Memory (RAM):** 8 GB minimum; 16 GB unified memory recommended for optimal multi-turn performance.
- **Storage:** 4 GB available disk space (includes the 2.5 GB bundled Qwen3 neural model and local FasterWhisper speech models).
- **Audio:** Built-in microphone and speakers (or connected headset).

---

## 2. Installation Steps

### Step 1: Mount the Disk Image
1. Locate the downloaded installer file: `RIYA-v1.0.0-AppleSilicon.dmg`.
2. Double-click the `.dmg` file. A Finder window titled **"RIYA Installer"** will open.

### Step 2: Install RIYA into Applications
1. In the installer window, drag the **RIYA** application icon and drop it directly onto the **Applications** folder shortcut.
2. Finder will copy the application bundle into `/Applications`.
3. Eject the disk image by clicking the eject icon next to "RIYA Installer" in Finder's sidebar.

```
+--------------------------------------------------------+
|                    RIYA Installer                      |
|                                                        |
|      [ RIYA.app ]  ---------->  [ Applications ]       |
|                                                        |
|  Tip: Drag RIYA into Applications to complete install  |
+--------------------------------------------------------+
```

### Step 3: First Launch & Gatekeeper Verification
1. Open **Applications** in Finder or press `Cmd + Space` to open Spotlight and search for **RIYA**.
2. **First-Time macOS Gatekeeper Note:** Because this release is signed with local ad-hoc developer signatures:
   - If macOS displays: *"RIYA cannot be opened because it is from an unidentified developer"*:
   - Open **System Settings** > **Privacy & Security**.
   - Scroll down to the **Security** section and click **"Open Anyway"**.
   - Alternatively, right-click (or Control-click) `RIYA.app` in `/Applications` and select **Open**.

---

## 3. Guided First-Time Onboarding Wizard

Upon first launch, RIYA displays an interactive 8-step setup wizard to configure your private offline environment:

### Step 1: Welcome & Offline Promise
- Explains RIYA's strict privacy boundaries: all neural inference runs locally on your Apple Silicon GPU. No telemetry or audio is uploaded to the cloud.
- Click **"Get Started"** to proceed.

### Step 2: Local Account Setup
- Create a local username and secure password, or sign into an existing account.
- Passwords are encrypted with SHA-256 + salt; credentials never leave your Mac.

### Step 3: Local Neural Network Selection
- RIYA automatically detects the bundled `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` model.
- Automatically selects **Apple Metal GPU** backend for hardware acceleration.
- Click **"Verify Model"** to run an instant offline verification probe.

### Step 4: Microphone & Audio Permissions
- Click **"Configure Microphone"**. macOS will prompt:
  > *"RIYA would like to access the microphone."*
- Click **"OK"** to permit local speech recognition.
- Click **"Test Microphone"** to verify real-time voice capture.

### Step 5: Audio Output & Voice Style
- Select your preferred speech speed and response style:
  - `concise` (Fast, direct answers — recommended)
  - `balanced` (Natural conversational responses)
  - `expressive` (Detailed explanations with expressive prosody)
- Click **"Test Speaker"** to verify local text-to-speech synthesis.

### Step 6: Trust & Permissions Review
- Review how RIYA safeguards your Mac. High-impact operations (file modifications, system automation) default to **explicit confirmation**.
- All capabilities start ungranted by default.

### Step 7: System Readiness Diagnostics
- The wizard executes an automatic readiness scan verifying:
  - Local SQLite database integrity (`data/riya.db`).
  - Neural network initialization and Metal buffer allocations.
  - Microphone and speaker device streams.
  - Security gates and cryptographic token generators.

### Step 8: Launch RIYA
- Click **"Finish & Launch RIYA"**. The modern desktop interface opens, ready for text and voice conversation!

---

## 4. Using RIYA Desktop

### Primary Controls & Shortcuts
| Action | Keyboard Shortcut | UI Control |
| :--- | :--- | :--- |
| **New Conversation** | `Cmd + N` | Sidebar `+ New Conversation` |
| **Open Preferences** | `Cmd + ,` | Sidebar `⚙️ Preferences` |
| **Toggle Dark/Light Theme** | `Cmd + T` | Header `🌙 Dark / ☀️ Light` button |
| **Toggle Voice Mode** | `Cmd + Shift + V` | Input bar `🎙️ Start Voice` |
| **Push-to-Talk (Hold)** | Press & Hold | Input bar `Push to Talk` |
| **Pause / Resume Voice** | `Cmd + Shift + P` | Input bar `Pause / Resume` |
| **Interrupt Speech / AI** | `Cmd + .` | Input bar `⏹️ Interrupt` |
| **Send Message** | `Return` | Input bar `Send ↑` |
| **Multi-line Input** | `Shift + Return` | Text input box |
| **Quit RIYA** | `Cmd + Q` | Menu bar `RIYA > Quit` |

---

## 5. Safe Uninstallation Guide

To uninstall RIYA while preserving your personal memories and configurations:

### Option A: Standard Application Removal (Preserves User Data)
1. Quit RIYA (`Cmd + Q`).
2. Open `/Applications` in Finder.
3. Drag `RIYA.app` to the Trash.
4. *Your local database, custom memories, and notes remain preserved in `~/Projects/RIYA/data/riya.db` and will be automatically restored if you reinstall RIYA.*

### Option B: Complete Removal (Includes Database & Models)
If you wish to completely remove all traces of RIYA:
1. Delete the application bundle from `/Applications`.
2. Delete the local data directory:
   ```bash
   rm -rf ~/Projects/RIYA/data/riya.db*
   ```
3. Remove models and voice temp caches:
   ```bash
   rm -rf ~/Projects/RIYA/models/*.gguf
   rm -rf ~/Projects/RIYA/data/voice_temp
   ```

*Note: Deleting `data/riya.db` will permanently erase all stored memories, user profiles, and conversation histories.*

