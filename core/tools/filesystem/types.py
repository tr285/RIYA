"""Supported file formats, category mappings, and structured models for RIYA Intelligent File Management."""

from __future__ import annotations

from dataclasses import dataclass, field
import mimetypes
from pathlib import Path
from typing import Any


SUPPORTED_DISCOVERY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".txt",
        ".md",
        ".csv",
        ".xlsx",
        ".xls",
        ".json",
        ".xml",
        ".py",
        ".java",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".mp3",
        ".wav",
        ".mp4",
    }
)

TEXT_EXTRACTABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".txt",
        ".md",
        ".csv",
        ".xlsx",
        ".json",
        ".xml",
        ".py",
        ".java",
        ".js",
        ".ts",
        ".html",
        ".css",
    }
)

METADATA_ONLY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".mp3",
        ".wav",
        ".mp4",
    }
)

LEGACY_BINARY_UNSUPPORTED_TEXT_EXTENSIONS: dict[str, str] = {
    ".doc": "Legacy binary Word (.doc) format is not supported for text extraction without external converters; convert to .docx or .pdf.",
    ".xls": "Legacy binary Excel (.xls) format is not supported for spreadsheet extraction without external converters; convert to .xlsx or .csv.",
}

EXTENSION_TO_CATEGORY: dict[str, str] = {
    ".pdf": "documents",
    ".docx": "documents",
    ".doc": "documents",
    ".txt": "documents",
    ".md": "documents",
    ".csv": "spreadsheets",
    ".xlsx": "spreadsheets",
    ".xls": "spreadsheets",
    ".json": "data",
    ".xml": "data",
    ".py": "code",
    ".java": "code",
    ".js": "code",
    ".ts": "code",
    ".html": "code",
    ".css": "code",
    ".png": "images",
    ".jpg": "images",
    ".jpeg": "images",
    ".webp": "images",
    ".mp3": "audio",
    ".wav": "audio",
    ".mp4": "video",
}

CATEGORY_ALIASES: dict[str, str] = {
    "document": "documents",
    "documents": "documents",
    "doc": "documents",
    "docs": "documents",
    "pdf": "documents",
    "spreadsheet": "spreadsheets",
    "spreadsheets": "spreadsheets",
    "excel": "spreadsheets",
    "data": "data",
    "code": "code",
    "source": "code",
    "source_code": "code",
    "script": "code",
    "scripts": "code",
    "python": "code",
    "image": "images",
    "images": "images",
    "photo": "images",
    "photos": "images",
    "picture": "images",
    "pictures": "images",
    "audio": "audio",
    "music": "audio",
    "sound": "audio",
    "video": "video",
    "videos": "video",
    "movie": "video",
    "movies": "video",
}

EXPLICIT_MIME_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".json": "application/json",
    ".xml": "application/xml",
    ".py": "text/x-python",
    ".java": "text/x-java-source",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".html": "text/html",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
}


def normalize_extension(ext: str | None) -> str:
    if not ext or not isinstance(ext, str):
        return ""
    cleaned = ext.strip().lower()
    if not cleaned:
        return ""
    if not cleaned.startswith("."):
        cleaned = "." + cleaned
    return cleaned


def classify_file_category(path_or_ext: str | Path) -> str:
    ext = (
        path_or_ext.suffix.lower()
        if isinstance(path_or_ext, Path)
        else normalize_extension(
            Path(path_or_ext).suffix if "." in str(path_or_ext) else str(path_or_ext)
        )
    )
    return EXTENSION_TO_CATEGORY.get(ext, "other")


def guess_mime_type(path: str | Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in EXPLICIT_MIME_MAP:
        return EXPLICIT_MIME_MAP[ext]
    guessed, _ = mimetypes.guess_type(str(p))
    return guessed or "application/octet-stream"


@dataclass(frozen=True)
class FileRecord:
    """Structured metadata record for a discovered or inspected file."""

    name: str
    path: str
    parent_dir: str
    extension: str
    category: str
    type: str  # "file" | "directory"
    size_bytes: int
    modified_at: str
    created_at: str
    mtime_ns: int
    mime_type: str = "application/octet-stream"
    mode_octal: str = ""
    readable: bool = True
    writable: bool = True
    sha256: str | None = None
    score: float = 0.0
    matched_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "parent_dir": self.parent_dir,
            "extension": self.extension,
            "category": self.category,
            "type": self.type,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "created_at": self.created_at,
            "mtime_ns": self.mtime_ns,
            "mime_type": self.mime_type,
            "mode_octal": self.mode_octal,
            "readable": self.readable,
            "writable": self.writable,
        }
        if self.sha256 is not None:
            payload["sha256"] = self.sha256
        if self.score:
            payload["score"] = round(self.score, 3)
        if self.matched_snippet:
            payload["matched_snippet"] = self.matched_snippet
        return payload


@dataclass(frozen=True)
class ExtractionResult:
    """Structured output from DocumentExtractor."""

    status: str  # "success" | "unsupported" | "failed"
    path: str
    name: str
    extension: str
    category: str
    mime_type: str
    size_bytes: int
    encoding: str = "utf-8"
    text: str = ""
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": self.path,
            "name": self.name,
            "extension": self.extension,
            "category": self.category,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "encoding": self.encoding,
            "text": self.text,
            "truncated": self.truncated,
            "metadata": dict(self.metadata),
            "warnings": list(self.warnings),
            "error": self.error,
        }
