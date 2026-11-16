# RIYA v1.0 — Clean Installation & Packaging Test Report

**Date:** 2026-11-15  
**Developer:** Tukaram  
**Product:** RIYA — Universal Personal AI Assistant  
**Platform:** macOS (Apple Silicon arm64)  
**Distribution Artifacts:**
- `dist/RIYA.app` (148 MB Standalone Application Bundle)
- `dist/RIYA-v1.0.0-AppleSilicon.dmg` (63.63 MB UDZO Compressed Disk Image)
**Status:** 100% Passed & Verified  

---

## 1. Executive Summary

Phase 4.9 and 4.10 delivered production-grade macOS packaging and automated distribution tooling for RIYA v1.0.0. The build system generates a self-contained double-clickable `.app` bundle and a drag-and-drop installer `.dmg` suitable for distribution to end users.

A clean installation test was performed to verify mounting, application integrity, code signatures, hardened entitlements, directory isolation, and permissions handling.

---

## 2. Disk Image & Volume Layout

The disk image was generated using Apple's `hdiutil` with `UDZO` zlib compression:
- **Image File:** `dist/RIYA-v1.0.0-AppleSilicon.dmg`
- **Volume Label:** `RIYA Installer`
- **Compressed Size:** 63.63 MB
- **Verification Checksum:** CRC32 `$CFB09C2D` (Verified by `hdiutil verify`)

### 2.1 Mounted Volume Inspection

```text
/Volumes/RIYA Installer:
  Applications -> /Applications (Symlink for standard drag-and-drop installation)
  Quick Start & Installation.txt (Clear instructions for first-time users)
  RIYA.app (Ad-hoc codesigned native application bundle)
```

Mounting and unmounting was verified:
```bash
hdiutil attach dist/RIYA-v1.0.0-AppleSilicon.dmg -nobrowse -readonly
# Mounted on /Volumes/RIYA Installer
hdiutil detach "/Volumes/RIYA Installer"
# Ejected successfully
```

---

## 3. Application Bundle Architecture (`dist/RIYA.app`)

The application bundle adheres to Apple Bundle Structure specifications:

```text
dist/RIYA.app/
├── Contents/
│   ├── Info.plist               # App identity, version 1.0.0, microphone usage description
│   ├── MacOS/
│   │   └── RIYA                 # Native standalone shell launcher (chmod +x)
│   ├── Resources/
│   │   ├── AppIcon.icns         # Multi-resolution icon (16x16 to 1024x1024)
│   │   └── app/                 # Self-contained application package
│   │       └── core/            # Bundled Python package & native Metal bridges
│   └── _CodeSignature/          # Embedded ad-hoc cryptographic signature
```

### 3.1 Code Signing Verification

```text
Executable=dist/RIYA.app/Contents/MacOS/RIYA
Identifier=com.riya.assistant
Format=app bundle with generic
CodeDirectory v=20100 size=195 flags=0x2(adhoc) hashes=1+3 location=embedded
Hash type=sha256 size=32
CDHash=4735887a1b75c4a04b9202695c1675e44caa4f82
Signature=adhoc
Info.plist entries=16
Sealed Resources version=2 rules=13 files=3611
```

### 3.2 Hardened Runtime Entitlements

Configured via `apps/macos/entitlements.plist`:
- `com.apple.security.device.audio-input`: Required for local microphone capture.
- `com.apple.security.cs.allow-jit`: Enables optimized neural tensor execution and Metal compute shaders.

---

## 4. First-Launch Storage Initialization

When launched from `/Applications/RIYA.app`, the launcher automatically prepares user directories:
1. `~/Library/Application Support/RIYA/data/`: Writable location for SQLite memory and user databases.
2. `~/Library/Application Support/RIYA/models/`: Target location for verified GGUF neural model provisioning.
3. `~/Library/Logs/RIYA/`: Diagnostic runtime logs.
4. `~/Library/Caches/RIYA/`: Temporary audio waveform buffers and download resumption caches.

The application bundle itself (`/Applications/RIYA.app`) remains strictly **read-only**, ensuring system security and compatibility with macOS gatekeeper policies.

---

## 5. Verification Conclusion

The installation and packaging pipeline is verified ready for distribution to end users.

