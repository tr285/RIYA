# RIYA v1.0 — Linux Uninstallation & Data Preservation Guide
## Safe Package Removal and Explicit User Data Management

**Product:** RIYA — Universal Offline-First Personal AI Assistant  
**Target Platform:** Ubuntu / Debian and AppImage portable environments  
**Version:** 1.0.0  

---

## 1. Core Data Preservation Policy

When uninstallation occurs, RIYA strictly enforces the **Principle of User Data Sovereignty**:

> **CRITICAL GUARANTEE:**  
> Uninstalling or purging the RIYA package (`apt remove` or `apt purge`) **NEVER** deletes your personal databases, conversation memories, downloaded multi-gigabyte neural models, or custom settings.

System-level package managers are granted write access only to `/opt/riya/`, `/usr/bin/riya`, and `/usr/share/`. All personal files reside safely in standard Freedesktop XDG directories under your home folder (`~/.local/share/riya`).

---

## 2. Option A: Uninstalling the Debian Package (.deb)

### Standard Uninstallation (Removes Application Binaries)
To remove the application while keeping system configuration links:
```bash
sudo apt remove riya
```
*What this removes:*
- `/opt/riya/` (Application code and launcher)
- `/usr/bin/riya` (Symlink)
- `/usr/share/applications/riya.desktop` (Menu launcher)
- `/usr/share/icons/hicolor/*/apps/riya.png` (Application icons)

*What is PRESERVED:*
- `~/.local/share/riya/data/riya.db` (User conversations, profiles, memory vectors)
- `~/.local/share/riya/models/` (Downloaded GGUF models like Qwen3 4B)
- `~/.local/share/riya/logs/` (Diagnostic logs)
- `~/.config/riya/` (User preferences)

### Complete Package Purge
```bash
sudo apt purge riya
```
Even during `apt purge`, the `postrm` maintainer script preserves your user home directories. It cleans up only system-wide caches and triggers `update-desktop-database` and `gtk-update-icon-cache`.

---

## 3. Option B: Removing the Portable AppImage

Because the AppImage is portable and does not modify system directories, removal is immediate:

```bash
# 1. Remove the AppImage executable
rm -f path/to/RIYA-1.0.0-x86_64.AppImage
rm -f ~/.local/bin/riya  # If copied to local bin

# 2. (Optional) Remove temporary extracted runtime cache
rm -rf ~/.cache/riya/appimage_runtimes/
```
Your personal databases and models in `~/.local/share/riya/` remain untouched.

---

## 4. Optional: Explicit Manual Deletion of User Data

If you explicitly wish to permanently erase all personal AI data, memories, and downloaded models from your computer, perform the following manual steps:

```bash
# WARNING: This permanently erases all local AI memories, profiles, and databases!
rm -rf ~/.local/share/riya/data/
rm -rf ~/.local/share/riya/logs/
rm -rf ~/.cache/riya/

# To delete downloaded neural models (~2.5 GB):
rm -rf ~/.local/share/riya/models/

# To remove the entire user RIYA folder:
rm -rf ~/.local/share/riya/
```

---

## 5. Summary Table

| Action | Command | Application Binaries | Desktop Entry | User Database | Downloaded Models |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Upgrade** | `sudo apt install ./riya_1.0.1_amd64.deb` | Upgraded | Upgraded | **Preserved** | **Preserved** |
| **Remove** | `sudo apt remove riya` | Removed | Removed | **Preserved** | **Preserved** |
| **Purge** | `sudo apt purge riya` | Removed | Removed | **Preserved** | **Preserved** |
| **Manual Data Erase** | `rm -rf ~/.local/share/riya` | Unaffected | Unaffected | Permanently Deleted | Permanently Deleted |

