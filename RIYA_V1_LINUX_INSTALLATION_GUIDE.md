# RIYA v1.0 — Linux Installation Guide
## Native DEB Package & Portable AppImage Installation

**Product:** RIYA — Universal Offline-First Personal AI Assistant  
**Target Platform:** Ubuntu 24.04 LTS (Noble Numbat), Debian 12, Linux Mint 22, and compatible distributions  
**Architectures:** x86_64 (amd64) primary, aarch64 (arm64) supported  
**Version:** 1.0.0  
**License:** Personal Offline Use  

---

## 1. System Requirements

### Hardware Requirements
- **Processor:** 64-bit x86_64 (Intel Core i5/i7/i9 8th gen+, AMD Ryzen 2000+) or ARM64 (64-bit ARMv8.2+)
- **Memory (RAM):** 
  - Minimum: 8 GB RAM (for Qwen3 4B Q4_K_M model execution)
  - Recommended: 16 GB RAM
- **Storage:**
  - Application install: ~50 MB free disk space
  - Local neural model: ~3.5 GB free disk space (Qwen3 4B GGUF model: 2.5 GB + working space)
  - Memory & databases: 500 MB free disk space

### Operating System & Sound Server Compatibility
- **Distributions:** Ubuntu 24.04 LTS, Ubuntu 22.04 LTS, Debian 12 (Bookworm), Fedora 39+, Arch Linux
- **Display Server:** Wayland (default on Ubuntu 24.04) or X11
- **Sound Architecture:** PipeWire (`pipewire-pulse`) or PulseAudio or ALSA

---

## 2. Option A: Native Debian Package Installation (.deb)

The native `.deb` package provides system-wide installation, desktop menu integration, high-resolution application icons, and automatic dependency resolution via APT.

### Step 1: Download or Locate Package
The compiled package is located at:
```bash
dist/riya_1.0.0_amd64.deb
```

### Step 2: Install via APT (Recommended)
Installing via `apt install` automatically resolves and installs all required system libraries:
```bash
sudo apt update
sudo apt install ./dist/riya_1.0.0_amd64.deb
```

*Alternative using `dpkg`:*
```bash
sudo dpkg -i ./dist/riya_1.0.0_amd64.deb
sudo apt install -f  # Fix any missing system dependencies
```

### Step 3: Verify Installation
Verify that the executable is accessible from your system PATH:
```bash
riya --version
# Expected output: RIYA 1.0.0
```

Run system readiness diagnostics:
```bash
riya --diagnostics
```

### Step 4: Launch RIYA
You can launch RIYA in three ways:
1. **Desktop Application Launcher:** Open your desktop application menu (Super key), search for **RIYA**, and click the application icon.
2. **Desktop GUI from Terminal:**
   ```bash
   riya --ui
   # or
   riya-ui
   ```
3. **Interactive CLI / Voice Mode:**
   ```bash
   riya
   # or direct voice mode:
   riya --voice
   ```

---

## 3. Option B: Portable AppImage Installation (.AppImage)

The AppImage format is a self-contained portable executable that runs directly without needing root privileges or package manager installation.

### Step 1: Download or Locate AppImage
The portable AppImage is located at:
```bash
dist/RIYA-1.0.0-x86_64.AppImage
```

### Step 2: Grant Executable Permissions
```bash
chmod +x dist/RIYA-1.0.0-x86_64.AppImage
```

### Step 3: Run RIYA
Execute directly from the terminal or double-click the file in your desktop file manager:
```bash
./dist/RIYA-1.0.0-x86_64.AppImage
```

To run with specific flags:
```bash
./dist/RIYA-1.0.0-x86_64.AppImage --version
./dist/RIYA-1.0.0-x86_64.AppImage --ui
./dist/RIYA-1.0.0-x86_64.AppImage --setup
```

*(Optional) Install AppImage to User Binaries:*
```bash
mkdir -p ~/.local/bin
cp dist/RIYA-1.0.0-x86_64.AppImage ~/.local/bin/riya
# Ensure ~/.local/bin is in your PATH
```

---

## 4. Installed Locations & File Structure

| Item | System DEB Path | AppImage Location | Notes |
| :--- | :--- | :--- | :--- |
| **Application Core** | `/opt/riya/` | Extracted runtime cache | Read-only application code |
| **Executable Launcher** | `/usr/bin/riya` | `RIYA-1.0.0-x86_64.AppImage` | Symlink or wrapper |
| **Desktop Entry** | `/usr/share/applications/riya.desktop` | Extracted / user desktop | Freedesktop menu launcher |
| **Application Icons** | `/usr/share/icons/hicolor/*/apps/riya.png` | Embedded icon | 16x16 up to 512x512 |
| **User SQLite Database** | `~/.local/share/riya/data/riya.db` | `~/.local/share/riya/data/riya.db` | **Persistent mutable data** |
| **User Neural Models** | `~/.local/share/riya/models/` | `~/.local/share/riya/models/` | **Persistent neural weights** |
| **Application Logs** | `~/.local/share/riya/logs/` | `~/.local/share/riya/logs/` | **Diagnostic logs** |

---

## 5. Security Notes

- **Never Run as Root:** RIYA explicitly guards against running as root. Normal operation does not require and should never use `sudo riya`.
- **User Data Isolation:** Every Linux user account receives its own isolated database (`~/.local/share/riya/data/riya.db`) and models folder, preventing cross-user data leakage.
- **Offline Guarantee:** RIYA operates completely offline. No outgoing network requests are initiated during conversation or tool execution.

