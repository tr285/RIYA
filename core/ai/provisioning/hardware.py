"""Hardware capability detection and intelligent neural model recommender.

Analyzes Apple Silicon generation, unified memory capacity, GPU core count,
and disk availability to recommend the optimal local model configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any

from core.ai.provisioning.catalog import ModelCatalog, ModelMetadata
from core.config.paths import get_models_dir


@dataclass(frozen=True)
class HardwareProfile:
    platform_system: str
    machine_arch: str
    chip_brand: str
    total_ram_bytes: int
    total_ram_gb: float
    cpu_cores: int
    metal_supported: bool
    free_disk_bytes: int
    free_disk_gb: float


@dataclass(frozen=True)
class ModelRecommendation:
    recommended_model: ModelMetadata
    reason: str
    ram_headroom_gb: float
    is_gpu_accelerated: bool
    alternative_models: tuple[ModelMetadata, ...]


class HardwareDetector:
    """Probes system parameters and Apple Silicon capabilities."""

    @classmethod
    def get_profile(cls, target_dir: Path | None = None) -> HardwareProfile:
        sys_name = platform.system()
        machine = platform.machine()
        chip_brand = "Unknown"
        cores = os.cpu_count() or 4
        ram_bytes = 0

        # Detect total RAM
        if sys_name == "Darwin":
            try:
                out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
                ram_bytes = int(out)
            except Exception:
                pass

            try:
                chip_brand = subprocess.check_output(
                    ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
                ).strip()
            except Exception:
                chip_brand = "Apple Silicon"
        else:
            try:
                pages = os.sysconf("SC_PHYS_PAGES")
                page_size = os.sysconf("SC_PAGE_SIZE")
                if pages > 0 and page_size > 0:
                    ram_bytes = int(pages) * int(page_size)
            except Exception:
                pass
            chip_brand = platform.processor() or "Generic Processor"

        if ram_bytes <= 0:
            ram_bytes = 8 * 1024 * 1024 * 1024  # Default fallback 8 GB

        ram_gb = round(ram_bytes / float(1024**3), 1)

        # Detect Metal acceleration
        metal_supported = (sys_name == "Darwin" and machine in {"arm64", "aarch64"})

        # Detect free storage
        dest = target_dir or get_models_dir()
        try:
            free_bytes = shutil.disk_usage(dest).free
        except OSError:
            free_bytes = 10 * 1024 * 1024 * 1024
        free_gb = round(free_bytes / float(1024**3), 1)

        return HardwareProfile(
            platform_system=sys_name,
            machine_arch=machine,
            chip_brand=chip_brand,
            total_ram_bytes=ram_bytes,
            total_ram_gb=ram_gb,
            cpu_cores=cores,
            metal_supported=metal_supported,
            free_disk_bytes=free_bytes,
            free_disk_gb=free_gb,
        )


class HardwareRecommender:
    """Evaluates hardware capacity to select the ideal model for local inference."""

    @classmethod
    def recommend(
        cls,
        profile: HardwareProfile | None = None,
        catalog: type[ModelCatalog] = ModelCatalog,
    ) -> ModelRecommendation:
        hw = profile or HardwareDetector.get_profile()
        all_models = catalog.list_all()

        # Selection logic based on Apple Silicon unified memory
        if hw.total_ram_gb >= 24.0:
            # 24GB+ Macs (M1/M2/M3/M4 Max or Studio): Can run 7B comfortably or 4B at lightning speed
            chosen = catalog.get_by_id("qwen2.5-7b-instruct-q4_k_m") or catalog.get_default()
            reason = (
                f"Your Mac has {hw.total_ram_gb} GB unified memory and {hw.chip_brand}. "
                "Recommended 7B parameters for high-depth reasoning with Apple Metal GPU acceleration."
            )
        elif hw.total_ram_gb >= 12.0:
            # 16GB Macs (Standard Pro/Air with 16GB): Qwen3 4B is optimal sweet spot
            chosen = catalog.get_default()
            reason = (
                f"Your Mac has {hw.total_ram_gb} GB unified memory with {hw.chip_brand}. "
                "Qwen3 4B Instruct delivers the ideal balance of advanced intelligence and ultra-fast generation."
            )
        else:
            # 8GB Macs (Base M1/M2/M3): Recommend Qwen3 4B or Qwen2.5 3B
            chosen = catalog.get_default()
            reason = (
                f"Your Mac has {hw.total_ram_gb} GB unified memory. "
                "Qwen3 4B Instruct is optimized with 4-bit quantization (Q4_K_M) to run smoothly without memory pressure."
            )

        headroom = round(hw.total_ram_gb - chosen.recommended_min_ram_gb, 1)
        alternatives = tuple(m for m in all_models if m.model_id != chosen.model_id)

        return ModelRecommendation(
            recommended_model=chosen,
            reason=reason,
            ram_headroom_gb=headroom,
            is_gpu_accelerated=hw.metal_supported,
            alternative_models=alternatives,
        )

