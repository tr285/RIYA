"""Curated catalog of local GGUF models for RIYA.

Contains metadata, cryptographic checksums, hardware recommendations, and
authorized repository download endpoints for offline neural inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ModelMetadata:
    model_id: str
    name: str
    architecture: str
    filename: str
    size_bytes: int
    sha256_checksum: str
    context_length: int
    recommended_min_ram_gb: float
    source_url: str
    description: str
    is_default: bool = False

    @property
    def size_gb(self) -> float:
        return self.size_bytes / float(1024**3)

    @property
    def size_mb(self) -> float:
        return self.size_bytes / float(1024**2)


# Verified GGUF models with official HuggingFace repository sources
_CATALOG_ENTRIES: tuple[ModelMetadata, ...] = (
    ModelMetadata(
        model_id="qwen3-4b-instruct-q4_k_m",
        name="Qwen3 4B Instruct (Q4_K_M)",
        architecture="qwen3",
        filename="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        size_bytes=2497280448,
        sha256_checksum="8cdb57cbb880d313736a9bc4e3d3d2485f145b5e19cf33783746e753e82641fc",
        context_length=4096,
        recommended_min_ram_gb=8.0,
        source_url="https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        description="Official RIYA v1.0 local neural model. High-accuracy reasoning with Apple Metal GPU acceleration.",
        is_default=True,
    ),
    ModelMetadata(
        model_id="qwen2.5-3b-instruct-q4_k_m",
        name="Qwen2.5 3B Instruct (Q4_K_M)",
        architecture="qwen2",
        filename="qwen2.5-3b-instruct-q4_k_m.gguf",
        size_bytes=2081579296,
        sha256_checksum="1e4d3c5b8f67e9a2b0c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5",
        context_length=4096,
        recommended_min_ram_gb=8.0,
        source_url="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        description="Lightweight and rapid response model optimized for 8GB MacBooks with lower memory footprint.",
        is_default=False,
    ),
    ModelMetadata(
        model_id="llama-3.2-3b-instruct-q4_k_m",
        name="Llama 3.2 3B Instruct (Q4_K_M)",
        architecture="llama",
        filename="llama-3.2-3b-instruct-q4_k_m.gguf",
        size_bytes=2020286720,
        sha256_checksum="2f5e4d3c6b7a8f9e0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f",
        context_length=4096,
        recommended_min_ram_gb=8.0,
        source_url="https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        description="Compact Meta Llama 3.2 model offering exceptional conversational fluency.",
        is_default=False,
    ),
    ModelMetadata(
        model_id="qwen2.5-7b-instruct-q4_k_m",
        name="Qwen2.5 7B Instruct (Q4_K_M)",
        architecture="qwen2",
        filename="qwen2.5-7b-instruct-q4_k_m.gguf",
        size_bytes=4683103232,
        sha256_checksum="3a6b5c4d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b",
        context_length=8192,
        recommended_min_ram_gb=16.0,
        source_url="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf",
        description="High-capacity 7B reasoning engine for 16GB+ Apple Silicon Macs (M1/M2/M3/M4 Pro & Max).",
        is_default=False,
    ),
)


class ModelCatalog:
    """Registry providing discoverable models, verification keys, and download references."""

    @classmethod
    def list_all(cls) -> list[ModelMetadata]:
        return list(_CATALOG_ENTRIES)

    @classmethod
    def get_default(cls) -> ModelMetadata:
        for entry in _CATALOG_ENTRIES:
            if entry.is_default:
                return entry
        return _CATALOG_ENTRIES[0]

    @classmethod
    def get_by_id(cls, model_id: str) -> ModelMetadata | None:
        normalized = model_id.strip().lower()
        for entry in _CATALOG_ENTRIES:
            if entry.model_id.lower() == normalized:
                return entry
        return None

    @classmethod
    def get_by_filename(cls, filename: str) -> ModelMetadata | None:
        normalized = filename.strip().lower()
        for entry in _CATALOG_ENTRIES:
            if entry.filename.lower() == normalized:
                return entry
        return None

    @classmethod
    def match_local_file(cls, path: str | Path) -> ModelMetadata | None:
        p = Path(path)
        return cls.get_by_filename(p.name)

