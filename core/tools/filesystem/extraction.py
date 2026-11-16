"""Document text extraction for RIYA Intelligent File Management.

Supported formats:
  Text / Code:  .txt .md .py .java .js .ts .html .css
  Structured:   .pdf .docx .csv .xlsx .json .xml
  Media meta:   .png .jpg .jpeg .webp .wav .mp3 .mp4

Security requirements enforced here:
  * Allowed-root policy via _path_context (FilesystemAccessError on violations).
  * Symlink rejection (handled inside _path_context / _resolve_path).
  * File-size caps before reading any bytes.
  * Prompt-injection containment: extracted text is returned as-is;
    callers (summarizer.py) are responsible for untrusted-data framing.
  * Never writes or deletes files.
  * Never calls cloud services.
"""

from __future__ import annotations

import csv
import json
import os
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from pypdf import PdfReader  # type: ignore
    _PYPDF_AVAILABLE = True
except ImportError:
    _PYPDF_AVAILABLE = False

try:
    from openpyxl import load_workbook  # type: ignore
    _OPENPYXL_AVAILABLE = True
except ImportError:
    _OPENPYXL_AVAILABLE = False

try:
    from PIL import Image  # type: ignore
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import charset_normalizer  # type: ignore
    _CHARSET_NORMALIZER_AVAILABLE = True
except ImportError:
    _CHARSET_NORMALIZER_AVAILABLE = False

try:
    import chardet  # type: ignore
    _CHARDET_AVAILABLE = True
except ImportError:
    _CHARDET_AVAILABLE = False

from core.tools.filesystem.files import FilesystemAccessError, _path_context
from core.tools.filesystem.types import (
    LEGACY_BINARY_UNSUPPORTED_TEXT_EXTENSIONS,
    METADATA_ONLY_EXTENSIONS,
    TEXT_EXTRACTABLE_EXTENSIONS,
    ExtractionResult,
    classify_file_category,
    guess_mime_type,
    normalize_extension,
)

# ---------------------------------------------------------------------------
# Size caps
# ---------------------------------------------------------------------------
MAX_EXTRACT_FILE_BYTES = 25 * 1024 * 1024  # 25 MiB (PDF/DOCX/XLSX/media)
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024      # 2 MiB (plain text / code)
MAX_XLSX_ROWS_PER_SHEET = 50
MAX_CSV_ROWS = 200
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # zip-bomb guard


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------

def _detect_encoding(data: bytes) -> str:
    """Detect byte-string encoding; prefer charset_normalizer, fallback chardet."""
    if not data:
        return "utf-8"
    if _CHARSET_NORMALIZER_AVAILABLE:
        try:
            result = charset_normalizer.from_bytes(data)
            if result and result.best():
                enc = result.best().encoding
                if enc:
                    return enc
        except Exception:
            pass
    if _CHARDET_AVAILABLE:
        try:
            detection = chardet.detect(data)
            if detection and detection.get("encoding"):
                return detection["encoding"]
        except Exception:
            pass
    return "utf-8"


def _read_limited(path: Path, max_bytes: int) -> Tuple[bytes, bool]:
    """Read up to *max_bytes* bytes from *path*. Returns (data, truncated)."""
    with path.open("rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    return data, truncated


# ---------------------------------------------------------------------------
# Format-specific extraction helpers
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> Tuple[str, bool, List[str]]:
    warnings: List[str] = []
    if not _PYPDF_AVAILABLE:
        return "", True, ["pypdf is not installed; PDF extraction unavailable."]
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            warnings.append("PDF is encrypted; text extraction skipped.")
            return "", True, warnings
        parts: List[str] = []
        for i, page in enumerate(reader.pages):
            try:
                txt = page.extract_text() or ""
                parts.append(txt)
            except Exception as e:
                warnings.append(f"Page {i + 1} extraction failed: {e}")
        return "\n".join(parts), False, warnings
    except Exception as e:
        return "", True, [f"PDF extraction error: {e}"]


def _extract_docx(path: Path) -> Tuple[str, bool, List[str]]:
    """Extract text from a .docx (Office Open XML) file without python-docx."""
    warnings: List[str] = []
    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
            if "word/document.xml" not in names:
                return "", True, ["DOCX is missing word/document.xml; file may be corrupt."]
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                return "", True, [
                    f"DOCX document.xml exceeds uncompressed size limit "
                    f"({MAX_DOCX_UNCOMPRESSED_BYTES} bytes); extraction aborted."
                ]
            with z.open("word/document.xml") as xml_file:
                xml_data = xml_file.read()

        root = ET.fromstring(xml_data)
        WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        paragraphs: List[str] = []
        for para in root.iter(f"{{{WNS}}}p"):
            tokens: List[str] = []
            for node in para.iter():
                tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
                if tag == "t" and node.text:
                    tokens.append(node.text)
                elif tag == "tab":
                    tokens.append("\t")
                elif tag == "br":
                    tokens.append("\n")
            line = "".join(tokens)
            if line.strip():
                paragraphs.append(line)
        return "\n".join(paragraphs), False, warnings
    except zipfile.BadZipFile:
        return "", True, ["DOCX file is a corrupted zip archive: extraction error."]
    except ET.ParseError as e:
        return "", True, [f"DOCX XML parse error: {e}"]
    except Exception as e:
        return "", True, [f"DOCX extraction error: {e}"]


def _extract_text_file(path: Path) -> Tuple[str, bool, List[str]]:
    data, truncated = _read_limited(path, MAX_TEXT_FILE_BYTES)
    enc = _detect_encoding(data)
    try:
        text = data.decode(enc, errors="replace")
    except (LookupError, Exception):
        text = data.decode("utf-8", errors="replace")
    return text, truncated, []


def _extract_csv(path: Path) -> Tuple[str, bool, List[str]]:
    warnings: List[str] = []
    rows: List[str] = []
    try:
        with path.open("r", newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if i >= MAX_CSV_ROWS:
                    warnings.append(f"CSV preview truncated after {MAX_CSV_ROWS} rows.")
                    break
                rows.append(", ".join(str(cell) for cell in row))
        return "\n".join(rows), False, warnings
    except Exception as e:
        return "", True, [f"CSV read error: {e}"]


def _extract_xlsx(path: Path) -> Tuple[str, bool, List[str]]:
    warnings: List[str] = []
    if not _OPENPYXL_AVAILABLE:
        return "", True, ["openpyxl is not installed; XLSX extraction unavailable."]
    try:
        wb = load_workbook(filename=str(path), read_only=True, data_only=True)
        parts: List[str] = []
        for sheet_name in wb.sheetnames[:5]:
            ws = wb[sheet_name]
            parts.append(f"=== Sheet: {sheet_name} ===")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_XLSX_ROWS_PER_SHEET:
                    warnings.append(f"Sheet '{sheet_name}' preview truncated after {MAX_XLSX_ROWS_PER_SHEET} rows.")
                    break
                parts.append(", ".join(str(cell) if cell is not None else "" for cell in row))
        wb.close()
        return "\n".join(parts), False, warnings
    except Exception as e:
        return "", True, [f"XLSX read error: {e}"]


def _extract_json(path: Path) -> Tuple[str, bool, List[str]]:
    data, truncated = _read_limited(path, MAX_TEXT_FILE_BYTES)
    try:
        obj = json.loads(data)
        pretty = json.dumps(obj, indent=2, ensure_ascii=False)
        return pretty, truncated, []
    except json.JSONDecodeError as e:
        return data.decode("utf-8", errors="replace"), truncated, [f"JSON parse warning: {e}"]


def _extract_xml(path: Path) -> Tuple[str, bool, List[str]]:
    data, truncated = _read_limited(path, MAX_TEXT_FILE_BYTES)
    try:
        root = ET.fromstring(data)
        lines: List[str] = []

        def _recurse(elem: ET.Element, depth: int = 0) -> None:
            indent = "  " * depth
            text = (elem.text or "").strip()
            lines.append(f"{indent}<{elem.tag}>{text}")
            for child in elem:
                _recurse(child, depth + 1)
            lines.append(f"{indent}</{elem.tag}>")

        _recurse(root)
        return "\n".join(lines), truncated, []
    except ET.ParseError as e:
        raw = data.decode("utf-8", errors="replace")
        return raw, truncated, [f"XML parse warning: {e}"]


def _extract_image_metadata(path: Path) -> Tuple[str, bool, List[str]]:
    if not _PIL_AVAILABLE:
        return "", True, ["Pillow (PIL) is not installed; image metadata extraction unavailable."]
    try:
        with Image.open(path) as img:
            # Only expose safe, non-sensitive metadata
            meta: Dict[str, Any] = {
                "format": img.format,
                "mode": img.mode,
                "width": img.size[0],
                "height": img.size[1],
            }
            # Add basic EXIF if available, excluding GPS and personal data
            if hasattr(img, "_getexif") and callable(img._getexif):  # type: ignore[attr-defined]
                try:
                    exif_raw = img._getexif()  # type: ignore[attr-defined]
                    if exif_raw:
                        SAFE_EXIF_TAGS = {
                            271: "Make", 272: "Model", 283: "YResolution",
                            282: "XResolution", 296: "ResolutionUnit",
                            305: "Software", 306: "DateTime",
                        }
                        safe_exif = {
                            SAFE_EXIF_TAGS[k]: v
                            for k, v in exif_raw.items()
                            if k in SAFE_EXIF_TAGS
                        }
                        meta["exif"] = safe_exif
                except Exception:
                    pass
        return json.dumps(meta, ensure_ascii=False, indent=2), False, []
    except Exception as e:
        return "", True, [f"Image metadata extraction error: {e}"]


def _extract_wav_metadata(path: Path) -> Tuple[str, bool, List[str]]:
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            framerate = w.getframerate()
            nframes = w.getnframes()
            meta: Dict[str, Any] = {
                "channels": w.getnchannels(),
                "sample_width_bytes": w.getsampwidth(),
                "framerate_hz": framerate,
                "nframes": nframes,
                "duration_sec": round(nframes / framerate, 3) if framerate else 0,
            }
        return json.dumps(meta, ensure_ascii=False, indent=2), False, []
    except Exception as e:
        return "", True, [f"WAV metadata extraction error: {e}"]


def _extract_generic_media_metadata(path: Path) -> Tuple[str, bool, List[str]]:
    """Return basic file-level metadata for mp3/mp4 (no heavy dependencies)."""
    try:
        st = path.stat()
        meta: Dict[str, Any] = {
            "extension": path.suffix.lower(),
            "size_bytes": st.st_size,
        }
        return json.dumps(meta, ensure_ascii=False, indent=2), False, []
    except Exception as e:
        return "", True, [f"Media metadata error: {e}"]


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def extract_document_structured(path: str, max_chars: int = 20_000) -> ExtractionResult:
    """Extract text/metadata from a document, enforcing the full security policy.

    * Respects allowed-root policy via _path_context.
    * Never follows symlinks (enforced in _resolve_path / _path_context).
    * Applies per-format file-size caps before reading.
    * Returns ExtractionResult with status 'success', 'unsupported', or 'failed'.
    * Never calls cloud services.
    """
    _sentinel_path = path  # preserve for error reporting before resolution
    try:
        context = _path_context(path)
        abs_path = context.resolved
    except FilesystemAccessError as fae:
        return ExtractionResult(
            status="failed",
            path=_sentinel_path,
            name=Path(_sentinel_path).name,
            extension="",
            category="",
            mime_type="",
            size_bytes=0,
            error=str(fae),
        )
    except Exception as e:
        return ExtractionResult(
            status="failed",
            path=_sentinel_path,
            name=Path(_sentinel_path).name,
            extension="",
            category="",
            mime_type="",
            size_bytes=0,
            error=f"Path resolution error: {e}",
        )

    ext = normalize_extension(abs_path.suffix)
    category = classify_file_category(abs_path)
    mime = guess_mime_type(abs_path)

    # Stat the file (no follow_symlinks; path_context already blocked symlinks)
    try:
        st = abs_path.stat()
        size = st.st_size
    except FileNotFoundError:
        return ExtractionResult(
            status="failed",
            path=str(abs_path),
            name=abs_path.name,
            extension=ext,
            category=category,
            mime_type=mime,
            size_bytes=0,
            error=f"File not found: {abs_path}",
        )
    except OSError as e:
        return ExtractionResult(
            status="failed",
            path=str(abs_path),
            name=abs_path.name,
            extension=ext,
            category=category,
            mime_type=mime,
            size_bytes=0,
            error=f"Cannot stat file: {e}",
        )

    # Check it's a regular file (not a directory, device, etc.)
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):
        return ExtractionResult(
            status="failed",
            path=str(abs_path),
            name=abs_path.name,
            extension=ext,
            category=category,
            mime_type=mime,
            size_bytes=size,
            error="Path is not a regular file.",
        )

    # Legacy binary formats – return unsupported immediately
    if ext in LEGACY_BINARY_UNSUPPORTED_TEXT_EXTENSIONS:
        return ExtractionResult(
            status="unsupported",
            path=str(abs_path),
            name=abs_path.name,
            extension=ext,
            category=category,
            mime_type=mime,
            size_bytes=size,
            error="Legacy binary format not supported for text extraction.",
            warnings=(LEGACY_BINARY_UNSUPPORTED_TEXT_EXTENSIONS.get(ext, "unsupported format"),),
        )

    # Size caps
    is_text_type = ext in TEXT_EXTRACTABLE_EXTENSIONS
    heavy_cap = MAX_EXTRACT_FILE_BYTES
    text_cap = MAX_TEXT_FILE_BYTES

    if is_text_type and ext not in {".pdf", ".docx", ".xlsx"}:
        # Plain text / code types have a stricter cap
        if size > text_cap:
            return ExtractionResult(
                status="failed",
                path=str(abs_path),
                name=abs_path.name,
                extension=ext,
                category=category,
                mime_type=mime,
                size_bytes=size,
                error=f"File exceeds text extraction size limit ({text_cap:,} bytes).",
                warnings=("size_cap_exceeded",),
            )
    elif size > heavy_cap:
        return ExtractionResult(
            status="failed",
            path=str(abs_path),
            name=abs_path.name,
            extension=ext,
            category=category,
            mime_type=mime,
            size_bytes=size,
            error=f"File exceeds maximum extraction size ({heavy_cap:,} bytes).",
            warnings=("size_cap_exceeded",),
        )

    # Dispatch to format handler
    try:
        if ext == ".pdf":
            text, truncated, warns = _extract_pdf(abs_path)
        elif ext == ".docx":
            text, truncated, warns = _extract_docx(abs_path)
        elif ext in {".txt", ".md", ".py", ".java", ".js", ".ts", ".html", ".css"}:
            text, truncated, warns = _extract_text_file(abs_path)
        elif ext == ".csv":
            text, truncated, warns = _extract_csv(abs_path)
        elif ext == ".xlsx":
            text, truncated, warns = _extract_xlsx(abs_path)
        elif ext == ".json":
            text, truncated, warns = _extract_json(abs_path)
        elif ext == ".xml":
            text, truncated, warns = _extract_xml(abs_path)
        elif ext in {".png", ".jpg", ".jpeg", ".webp"}:
            text, truncated, warns = _extract_image_metadata(abs_path)
        elif ext == ".wav":
            text, truncated, warns = _extract_wav_metadata(abs_path)
        elif ext in {".mp3", ".mp4"}:
            text, truncated, warns = _extract_generic_media_metadata(abs_path)
        else:
            return ExtractionResult(
                status="unsupported",
                path=str(abs_path),
                name=abs_path.name,
                extension=ext,
                category=category,
                mime_type=mime,
                size_bytes=size,
                error="File type is not supported for extraction.",
                warnings=(f"Unsupported extension: {ext!r}",),
            )
    except Exception as e:
        return ExtractionResult(
            status="failed",
            path=str(abs_path),
            name=abs_path.name,
            extension=ext,
            category=category,
            mime_type=mime,
            size_bytes=size,
            error=f"Unexpected extraction error: {e}",
            warnings=(traceback.format_exc(),),
        )

    # Apply max_chars cap
    if len(text) > max_chars:
        truncated = True
        text = text[:max_chars]

    # Determine final status
    has_error_warn = any(
        any(k in w.lower() for k in ("error", "corrupt", "failed", "aborted", "missing"))
        for w in warns
    )
    status = "failed" if has_error_warn else "success"

    return ExtractionResult(
        status=status,
        path=str(abs_path),
        name=abs_path.name,
        extension=ext,
        category=category,
        mime_type=mime,
        size_bytes=size,
        encoding=_detect_encoding(text.encode("utf-8", errors="replace")),
        text=text,
        truncated=truncated,
        warnings=tuple(warns),
        error="" if status != "failed" else "; ".join(warns),
    )


def extract_document_text(path: str, max_chars: int = 20_000) -> str:
    """Convenience wrapper returning the extracted text string or an error message."""
    res = extract_document_structured(path, max_chars)
    if res.succeeded:
        return res.text
    return f"Extraction failed ({res.status}): {res.error}"
