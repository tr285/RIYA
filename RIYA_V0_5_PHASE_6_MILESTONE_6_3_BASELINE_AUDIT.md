# RIYA v0.5 — Phase 6 — Milestone 6.3 Baseline Audit
## Intelligent File Management — Repository Audit & Architecture Design

**Project:** RIYA — Universal Personal AI Assistant  
**Developer:** Tukaram  
**Milestone:** 6.3 — Intelligent File Management  
**Previous Milestone:** 6.2 — Cross-Platform OS Abstraction & Native macOS Integration (`234 passed, 0 failed, 0 skipped`)  
**Current Database Schema Before Milestone 6.3:** `v6` (`0006_phase5_tasks_skills_and_automation`)  
**Target Database Schema for Milestone 6.3:** `v7` (`0007_phase6_intelligent_file_management`)

---

## 1. Existing Capabilities (Reused Baseline)

### 1.1 Sandboxed Filesystem Primitives (`core/tools/filesystem/files.py`)
- **Allowed-Root Enforcement (`_allowed_roots`, `_resolve_path`, `_path_context`):**
  - Resolves paths relative to the authenticated user's isolated `ExecutionContext.workspace_dir` and `allowed_roots` (or `settings.FILESYSTEM_ALLOWED_ROOTS` when running without user context).
  - Explicitly blocks access to other users' storage roots under `data/users/<other_user>` (`_is_protected_user_store_path`).
  - Blocks sensitive filenames and suffixes (`.env`, `.env.*`, `riya.db`, `riya.db-wal`, `riya.db-shm`, `voice_settings.json`, `voice_recordings`, `.pem`, `.key`, `.p12`, `.pfx`, `.kdbx`, `.db`, `.sqlite`, `.sqlite3`).
- **Symlink & TOCTOU Protection (`_open_directory`, `_open_parent_directory`, `_entry_stat`):**
  - Traverses directories component-by-component using `os.open(..., O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent_fd)` and `os.stat(..., dir_fd=parent_fd, follow_symlinks=False)`.
  - Converts `ELOOP`/`ENOTDIR` to `FilesystemAccessError("Access denied: unsafe path component: ...")` and `EACCES`/`EPERM` to `FilesystemAccessError("Access denied by operating-system or macOS TCC privacy permissions...")`.
- **Existing Tools:**
  - `list_files(path=".")` — Non-recursive listing (up to `MAX_LIST_ENTRIES = 200`), skipping symlinks and sensitive files.
  - `find_files(name, path=None)` — Recursive substring filename search (`MAX_SEARCH_DEPTH = 20`, `MAX_SEARCH_ENTRIES = 10_000`, `MAX_SEARCH_MATCHES = 50`).
  - `create_folder(path)` — Descriptor-safe folder creation inside allowed roots.
  - `read_file(path)` — Descriptor-safe UTF-8 text reading up to `MAX_READ_FILE_BYTES = 64 * 1024`.
  - `write_file(path, content)` — Descriptor-safe UTF-8 file writing (`0o600`) up to `MAX_WRITE_CHARS = 64 * 1024` with post-write `verify_file_content`.
  - `inspect_file_metadata_structured(path)` & `inspect_file_metadata(path)` — Symlink-safe metadata inspection (`name`, `path`, `allowed_root`, `type`, `size_bytes`, `mode_octal`, `modified_at`, `created_at`, `is_symlink`, `readable`, `writable`).

### 1.2 Cross-Platform OS Abstraction (`core/device/`)
- `PlatformInterface` and `PlatformAdapter` (`core/device/platform.py`) define `FilesystemAdapter` (`discover_files`, `search_files`, `inspect_metadata`), `PlatformCapabilities`, and `PlatformOperationResult`.
- `MacOSPlatform` (`core/device/macos.py`) implements native macOS file discovery, search, and metadata inspection.
- `WindowsPlatform` (`core/device/windows.py`) and `LinuxPlatform` (`core/device/linux.py`) implement the `PlatformAdapter` contract.

### 1.3 Security, Permissions & Confirmations (`core/security/`)
- `PermissionManager` (`core/security/permissions.py`) defines `filesystem.read` (`ALLOW` inside user workspace), `filesystem.create` (`ASK`/confirmation), `filesystem.modify` (`ASK`/confirmation), and `filesystem.directory` (scoped directory grants for paths outside the user's default workspace).
- `SecurityManager` (`core/security/manager.py`) and `ConfirmationManager` (`core/security/confirmation.py`) enforce single-use, session-bound, parameter-fingerprinted confirmation tokens (`ConfirmationGrant`) for mutating or destructive operations.

### 1.4 Local AI & Unified Assistant Interface (`core/ai/`, `core/agent/`, `core/voice/ui.py`)
- `LocalLlamaRuntime` (`core/ai/local_runtime.py`) runs the local `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` model on Apple Silicon Metal (`MTL0`) offline.
- `RIYAAssistant` (`core/agent/assistant.py`) and `VoiceUIController` (`core/voice/ui.py`) surface structured tool outcomes (`successful`, `denied`, `unsupported`, `failed`), confirmation banners (`Approve` / `Reject`), and audit logs.
- Installed local Python packages available for document extraction without network access:
  - `pypdf` (PDF extraction)
  - `openpyxl` (`.xlsx` spreadsheet inspection)
  - `PIL` / Pillow (PNG/JPEG/WEBP image metadata & dimensions)
  - `chardet` / `charset_normalizer` (text encoding detection)
  - Python stdlib `zipfile` + `xml.etree.ElementTree` (`.docx` OOXML text extraction without needing `python-docx`), `csv`, `json`, `hashlib`, `mimetypes`, `shutil`.

---

## 2. Missing Capabilities Required for Milestone 6.3

1. **Intelligent File Discovery (`search_files_advanced` / `FileDiscoveryEngine`):**
   - Multi-criteria search supporting filename query, extension filter (`.pdf`, `.py`, etc.), file-type category filter (`document`, `spreadsheet`, `code`, `image`, `audio`, `video`, `data`, `archive`), folder filter, creation/modification date range (`modified_after`, `modified_before`, `modified_within_days`), size range (`min_size_bytes`, `max_size_bytes`), recursive vs non-recursive toggle, hidden-file toggle (`include_hidden=False` default), natural-language query parsing, and relevance/recency/size ranking.
   - Content/keyword search across supported readable/extractable documents (`find_files_by_content`).
2. **Extensible Document Content Extraction (`DocumentExtractor`):**
   - Support for PDF (`.pdf` via `pypdf` + safe fallback), Word OOXML (`.docx` via safe `zipfile` + `ElementTree` with zip-bomb limits), legacy `.doc` (explicitly reported as unsupported binary legacy format unless plain text is safely extractable, never faked), `.txt`, `.md`, `.csv`, `.xlsx` (via `openpyxl` read-only mode), `.xls` (explicitly reported as unsupported legacy binary spreadsheet if `xlrd` is absent), `.json`, `.xml`, source code (`.py`, `.java`, `.js`, `.ts`, `.html`, `.css`), image metadata (`.png`, `.jpg`, `.jpeg`, `.webp` via `PIL.Image` header inspection), and audio/video metadata inspection (`.mp3`, `.wav`, `.mp4` via `wave`/file header & stat metadata).
   - Encoding detection (`utf-8`, `utf-8-sig`, `charset_normalizer`/`chardet`, `latin-1` fallback), corrupted-file resilience, large-file size caps, and prompt-injection sanitization.
3. **Local Document Summarization (`DocumentSummarizer` / `summarize_document`):**
   - Chunked document summarization using RIYA's local `Qwen3` (`LocalLlamaRuntime`) with map-reduce chunk synthesis, preserving key facts/dates/numbers/names, untrusted-document prompt-injection framing, and explicit offline error reporting when the local model is unloaded/unavailable (never falling back to cloud AI).
4. **Duplicate File Detection (`DuplicateDetector` / `find_duplicate_files`):**
   - Two-phase duplicate detection: (1) group regular files by exact `st_size > 0`, (2) stream SHA-256 hashes (`64 KiB` chunks) only for same-size candidates, grouping identical SHA-256 digests with wasted-space calculation and never deleting files automatically.
5. **Intelligent File Organization & Safe File Mutations (`FileOrganizer`):**
   - Single-file operations: `copy_file`, `move_file`, `rename_file`, `delete_file` — all enforcing allowed roots, symlink rejection, destination conflict detection (never silently overwriting existing files), atomic/verified execution, and confirmation gates.
   - Bulk organization (`organize_files`): modes `by_extension`, `by_type`, `by_date`, or `custom_category`; dry-run preview generation (`preview_only=True`) showing source path, destination path, operation, affected file count, and conflicts; explicit confirmation required before executing any file moves.
6. **File Metadata & Storage Insights (`file_insights`):**
   - Optional SHA-256 and MIME type in `inspect_file_metadata_structured`.
   - Directory/workspace insights: largest files, recently modified files, oldest files, file-type distribution, and folder storage usage breakdown.
7. **Persistent Per-User File Index & Operation Log (SQLite Migration `v7`):**
   - Incremental, user-isolated `file_index_entries` (metadata + `mtime_ns` + `size_bytes` + optional `sha256`, never storing full private document contents) and `file_operation_logs` (audit trail of file creates, copies, moves, renames, organizations, and deletions with rollback metadata).
8. **Cross-Platform Adapter Support (`MacOSPlatform`, `WindowsPlatform`, `LinuxPlatform`):**
   - Route file-management operations through `FilesystemAdapter` on `PlatformAdapter` so `MacOSPlatform`, `WindowsPlatform`, and `LinuxPlatform` support portable, sandboxed Python `os`/`Path` filesystem operations within allowed roots while explicitly reporting any OS-specific unsupported capabilities.

---

## 3. Existing Security Mechanisms & New Milestone 6.3 Hardening

| Security Requirement | Enforcement Mechanism |
|---|---|
| Allowed-root directory enforcement | `_resolve_path` + `_path_context` + `PermissionManager` (`filesystem.directory` required outside user workspace) |
| Path traversal protection | Canonical path resolution + component-by-component `O_NOFOLLOW` directory descriptor walk |
| Symlink protection | Pre-resolution `is_symlink()` check on all path components + `O_NOFOLLOW` + `os.stat(follow_symlinks=False)` |
| Prompt injection in documents | Extracted document text wrapped in `<untrusted_document_content>` tags and sanitized via `sanitize_untrusted_text`; planner/system prompts explicitly forbid obeying instructions inside documents |
| Destination conflict detection | `copy_file`, `move_file`, `rename_file`, and `organize_files` check destination existence via `follow_symlinks=False` and refuse to overwrite existing files unless explicitly confirmed |
| Destructive & bulk operation approval | `copy_file`, `move_file`, `rename_file`, `organize_files` (`execute=True`), and `delete_file` require `ConfirmationManager` one-time tokens; `filesystem.delete` defaults to `DENY` until explicitly granted by the authenticated user |
| User isolation | All `ExecutionContext` roots, `file_index_entries`, `file_operation_logs`, and UI tool results are keyed by `identity.user_id` |

---

## 4. Required Modifications & Module Design

### New Modules to Create
1. `core/tools/filesystem/types.py` — Supported file extension catalog (`pdf`, `docx`, `doc`, `txt`, `md`, `csv`, `xlsx`, `xls`, `json`, `xml`, `py`, `java`, `js`, `ts`, `html`, `css`, `png`, `jpg`, `jpeg`, `webp`, `mp3`, `wav`, `mp4`), category mappings, MIME resolution, and structured dataclasses.
2. `core/tools/filesystem/discovery.py` — `FileDiscoveryEngine` (`search_files_advanced`, `find_files_by_content`, `analyze_directory_insights`, incremental `FileIndexStore` integration).
3. `core/tools/filesystem/extraction.py` — `DocumentExtractor` (safe, bounded extraction for PDF, DOCX, TXT/MD, CSV, XLSX, JSON, XML, source code, and image/audio/video metadata + prompt-injection tagging).
4. `core/tools/filesystem/summarizer.py` — `DocumentSummarizer` (chunking, local Qwen3 map-reduce summarization, factual extraction fallback disabled when model unavailable, zero cloud calls).
5. `core/tools/filesystem/duplicates.py` — `DuplicateDetector` (size pre-filter + streaming 64 KiB SHA-256 hashing + duplicate group report).
6. `core/tools/filesystem/organization.py` — `FileOrganizer` (`copy_file`, `move_file`, `rename_file`, `delete_file`, `organize_files` with preview/conflict detection and operation logging).
7. `core/tools/filesystem/index_store.py` — Per-user SQLite `FileIndexStore` and `FileOperationLogger` with pre-action path revalidation.
8. `tests/test_phase6_milestone_6_3_intelligent_file_management.py` — Comprehensive Milestone 6.3 test suite covering discovery, extraction, local Qwen3 summarization, duplicate detection, organization, security, and cross-platform adapters.

### Existing Modules to Update (with `.before_v05_phase6_m63` backups)
1. `core/database/migrations.py` — Add reversible Migration `0007_phase6_intelligent_file_management` (`file_index_entries` and `file_operation_logs` tables, `CURRENT_SCHEMA_VERSION = 7`).
2. `core/tools/filesystem/files.py` — Extend `inspect_file_metadata_structured` with optional `include_sha256` and `mime_type`, and re-export Milestone 6.3 file management tool functions.
3. `core/device/platform.py`, `core/device/macos.py`, `core/device/windows.py`, `core/device/linux.py` — Extend `FilesystemAdapter` and platform adapters to expose intelligent file discovery, extraction, duplicate detection, insights, and organization through the platform abstraction.
4. `core/security/permissions.py` & `core/security/manager.py` — Add `filesystem.delete` (`DENY` by default, `sensitive=True`, `protected_by_default=True`) and map new tools (`search_files_advanced`, `extract_document_text`, `summarize_document`, `find_duplicate_files`, `file_insights`, `copy_file`, `move_file`, `rename_file`, `organize_files`, `delete_file`).
5. `core/agent/tool_registry.py`, `core/agent/tool_executor.py`, `core/agent/agent.py`, `core/agent/planner.py`, `core/agent/intent.py`, `core/agent/executor.py`, `core/agent/assistant.py` — Register, validate, verify, and route all Milestone 6.3 file-management capabilities.
6. `core/voice/ui.py` — Surface rich file discovery results, document summaries, duplicate groups, organization previews, and operation statuses (`Planned`, `Awaiting approval`, `Running`, `Successful`, `Failed`, `Denied`, `Unsupported`).

---

## 5. Backwards-Compatibility Risks & Mitigations

1. **Semantic Tool Retrieval Stability (`core/agent/planner.py`):**
   - *Risk:* Adding new file-management tools to `ToolRegistry` can shift top-$k$ embeddings in `SemanticToolEngine` for unrelated conversational prompts.
   - *Mitigation:* Gate the new Milestone 6.3 file-management tools in `AgentPlanner._OS_EXTENSION_TOOLS` / `_FILE_MANAGEMENT_TOOLS` behind explicit file/document/folder/duplicate/organize/summarize cues so general conversational prompts receive the exact same top-8 baseline tools.
2. **Existing Tool Signatures (`list_files`, `find_files`, `create_folder`, `read_file`, `write_file`, `inspect_file_metadata`):**
   - *Risk:* Existing Phase 1–6.2 tests assert exact return strings and parameter schemas on these 6 tools.
   - *Mitigation:* Keep existing tool signatures and output formats 100% intact; add advanced options either as optional default-preserving parameters or dedicated tools (`search_files_advanced`, `extract_document_text`, `summarize_document`, `find_duplicate_files`, `file_insights`, `copy_file`, `move_file`, `rename_file`, `organize_files`, `delete_file`).
3. **Database Schema Migration (`v6` -> `v7`):**
   - *Risk:* Existing migration tests verify `apply_migrations` and `rollback_migration` down to version 1.
   - *Mitigation:* Implement `_up_0007_phase6_intelligent_file_management` and `_down_0007_phase6_intelligent_file_management` transactionally with full index and table cleanup on rollback, and back up `data/riya.db` to `data/riya.db.before_v05_phase6_m63`.

---

## 6. Controlled 9-Stage Implementation Plan

- **Stage 1:** Complete this baseline audit (`RIYA_V0_5_PHASE_6_MILESTONE_6_3_BASELINE_AUDIT.md`) and create `.before_v05_phase6_m63` backups.
- **Stage 2:** Implement SQLite Migration `v7` (`file_index_entries`, `file_operation_logs`), `FileIndexStore`, and cross-platform `FilesystemAdapter` extensions (`core/device/platform.py`, `macos.py`, `windows.py`, `linux.py`).
- **Stage 3:** Implement `core/tools/filesystem/types.py` and `core/tools/filesystem/discovery.py` (intelligent file discovery, natural-language filter parsing, ranking, content search, metadata & storage insights).
- **Stage 4:** Implement `core/tools/filesystem/extraction.py` and `core/tools/filesystem/summarizer.py` (PDF, DOCX, TXT, MD, CSV, XLSX, JSON, XML, code, image/audio/video metadata, prompt-injection containment, and local Qwen3 chunked summarization).
- **Stage 5:** Implement `core/tools/filesystem/duplicates.py` and `core/tools/filesystem/organization.py` (streaming SHA-256 duplicate detection, `copy_file`, `move_file`, `rename_file`, `delete_file`, and preview-first `organize_files`).
- **Stage 6:** Integrate permissions (`filesystem.delete`, `filesystem.create`, `filesystem.modify`, `filesystem.read`), `SecurityManager`, `ToolRegistry`, `ToolExecutor` verification hooks, `IntentDetector`, `AgentPlanner`, `RIYAAssistant`, and `VoiceUIController`.
- **Stage 7:** Create and run `tests/test_phase6_milestone_6_3_intelligent_file_management.py` covering all discovery, extraction, summarization, duplicate, organization, and security test cases.
- **Stage 8:** Run full Phase 1–6.3 regression suite (`pytest -q`) and real local Qwen3 integration verification.
- **Stage 9:** Produce `RIYA_V0_5_PHASE_6_MILESTONE_6_3_REPORT.md` with actual test outputs and Milestone 6.4 readiness assessment.
