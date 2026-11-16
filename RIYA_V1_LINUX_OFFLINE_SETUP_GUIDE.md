# RIYA v1.0 — Linux Offline Setup & Air-Gapped Operation Guide
## Completely Offline AI Intelligence, Model Provisioning & Voice Execution

**Product:** RIYA — Universal Offline-First Personal AI Assistant  
**Target Platform:** Linux (Ubuntu 24.04 LTS / Debian / AppImage)  
**Security Level:** Air-Gapped / Zero External Telemetry  

---

## 1. Offline-First Guarantee

RIYA is engineered from the ground up to operate completely without internet connectivity:
- **No Cloud LLM Calls:** All natural-language reasoning is performed locally by embedded llama.cpp runtime.
- **No Remote Telemetry:** No user prompts, system metrics, error traces, or transcripts are ever transmitted to any remote server.
- **No Silent Fallbacks:** RIYA never silently downgrades to remote APIs if a local model is missing. It truthfully notifies you and stays offline.
- **Local Persistence:** All memory vectors and user identity profiles reside in local SQLite databases (`~/.local/share/riya/data/riya.db`).

---

## 2. Model Provisioning Workflows

Local neural inference requires a verified GGUF quantized model. RIYA supports two methods:

### Method A: Resumable Automated Setup (Requires one-time internet access)
If your Linux workstation has internet access during initial setup, run:
```bash
riya --setup
```
1. RIYA checks available disk space (ensures > 3.5 GB free space).
2. Prompts for your explicit consent to download the official Qwen3 4B Instruct model (~2.49 GB).
3. Downloads via streaming HTTP Range requests (resumable if interrupted).
4. Verifies the SHA-256 cryptographic checksum (`8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc`).
5. Atomically installs the model into `~/.local/share/riya/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`.

---

### Method B: Manual Air-Gapped Setup (100% Offline from Day 1)
For classified, secure, or completely air-gapped systems without internet access:

#### Step 1: Obtain the Model File on an External Transfer Drive
On any internet-connected computer, download the verified GGUF model:
- **Model URL:** `https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`
- **Filename:** `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`
- **Expected SHA-256:** `8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc`

#### Step 2: Verify Checksum on Transfer Media
```bash
sha256sum Qwen3-4B-Instruct-2507-Q4_K_M.gguf
# Must match: 8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc
```

#### Step 3: Copy to RIYA Models Directory on Air-Gapped Machine
```bash
mkdir -p ~/.local/share/riya/models
cp /media/transfer/Qwen3-4B-Instruct-2507-Q4_K_M.gguf ~/.local/share/riya/models/
```

#### Step 4: Verify Local Model Detection
Run RIYA diagnostics:
```bash
riya --diagnostics
```
Expected output:
```
AI Model : ✓ Qwen3-4B-Instruct-2507-Q4_K_M.gguf (2.5 GB)
Inference Backend : CPU / Vulkan
Status : Operational
```

---

## 3. Offline Voice Pipeline Configuration

RIYA's voice stack runs 100% locally on your Linux machine using three decoupled components:

### 1. Voice Activity Detection (Silero VAD)
- **Model:** `silero_vad.onnx` (bundled directly inside the application core).
- **Execution:** Runs via ONNX Runtime on CPU with zero network lookups.

### 2. Speech-to-Text Recognition (faster-whisper)
- For air-gapped setups, transfer the pre-cached HuggingFace directory:
  ```bash
  mkdir -p ~/.cache/huggingface/hub/
  cp -r /media/transfer/models--Systran--faster-whisper-base ~/.cache/huggingface/hub/
  ```

### 3. Speech Synthesis (Text-to-Speech)
- Uses native Linux binaries (`piper` or `espeak-ng`):
  ```bash
  sudo apt install espeak-ng
  # or portable Piper neural binary in ~/.local/bin/piper
  ```

---

## 4. Air-Gapped System Verification Checklist

Before deploying in a network-isolated environment, verify the following:

- [ ] Network cables unplugged and Wi-Fi disabled (`nmcli radio all off`).
- [ ] `riya --version` reports `RIYA 1.0.0`.
- [ ] `riya --diagnostics` passes all mandatory checks.
- [ ] `riya` interactive CLI responds to prompt "Hello, who are you?" locally in < 2 seconds.
- [ ] `/voice ptt` captures local audio and speaks response offline.
- [ ] `/permissions list` displays configured security policies.
- [ ] `netstat -tunlp` or `ss -tunlp` confirms zero listening or outbound network sockets created by RIYA.

