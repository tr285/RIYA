"""Secure, resumable streaming model downloader with SHA-256 integrity verification.

Enforces HTTPS allowlisting, disk space pre-flight validation, HTTP Range resumption,
and atomic installation of GGUF model files.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable

from core.ai.provisioning.catalog import ModelMetadata
from core.config.paths import get_models_dir


# Security allowlist: Only official HuggingFace domains are permitted for model downloads
_ALLOWED_HOSTS: frozenset[str] = frozenset({
    "huggingface.co",
    "hf.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.huggingface.co",
    "cdn-lfs-eu-1.huggingface.co",
})

_DEFAULT_SAFETY_MARGIN_BYTES = 1024 * 1024 * 1024  # 1 GB headroom
_CHUNK_SIZE = 128 * 1024  # 128 KB chunks


class ProvisioningSecurityError(Exception):
    """Raised when an untrusted or insecure download URL is rejected."""


class InsufficientDiskSpaceError(Exception):
    """Raised when the target drive lacks sufficient free space for the model."""


class ChecksumVerificationError(Exception):
    """Raised when the SHA-256 hash of the downloaded model does not match metadata."""


class DownloadCancelledError(Exception):
    """Raised when a download was cancelled by the user."""


@dataclass(frozen=True)
class DownloadProgress:
    downloaded_bytes: int
    total_bytes: int
    percent: float
    speed_bytes_sec: float
    eta_seconds: float
    is_resuming: bool = False


class SecureModelDownloader:
    """Manages secure, verified streaming downloads of neural models to local storage."""

    def __init__(
        self,
        target_dir: Path | None = None,
        safety_margin_bytes: int = _DEFAULT_SAFETY_MARGIN_BYTES,
    ):
        self.target_dir = target_dir or get_models_dir()
        self.safety_margin_bytes = safety_margin_bytes
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Signal active download to abort cleanly."""
        self._cancel_event.set()

    def reset_cancel(self) -> None:
        """Reset the cancellation flag for a new download."""
        self._cancel_event.clear()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def validate_url(self, url: str) -> None:
        """Enforce HTTPS protocol and domain allowlist."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() != "https":
            raise ProvisioningSecurityError(
                f"Insecure scheme '{parsed.scheme}'. Only HTTPS model sources are permitted."
            )
        hostname = (parsed.hostname or "").lower()
        if hostname not in _ALLOWED_HOSTS and not any(hostname.endswith("." + h) for h in _ALLOWED_HOSTS):
            raise ProvisioningSecurityError(
                f"Unauthorized host '{hostname}'. Model downloads must originate from official trusted repositories."
            )

    def check_disk_space(self, required_bytes: int) -> None:
        """Ensure destination disk has required space plus safety margin."""
        self.target_dir.mkdir(parents=True, exist_ok=True)
        try:
            free = shutil.disk_usage(self.target_dir).free
        except OSError as exc:
            raise InsufficientDiskSpaceError(f"Cannot determine free disk space: {exc}") from exc

        needed = required_bytes + self.safety_margin_bytes
        if free < needed:
            needed_gb = round(needed / float(1024**3), 2)
            free_gb = round(free / float(1024**3), 2)
            raise InsufficientDiskSpaceError(
                f"Insufficient disk space in {self.target_dir}. "
                f"Available: {free_gb} GB, Required: {needed_gb} GB (including 1GB safety buffer)."
            )

    def download_model(
        self,
        metadata: ModelMetadata,
        progress_callback: Callable[[DownloadProgress], None] | None = None,
        force_redownload: bool = False,
    ) -> Path:
        """
        Download and verify a model from the catalog.

        Returns the verified Path to the installed .gguf file.
        """
        with self._lock:
            self.reset_cancel()
            self.validate_url(metadata.source_url)
            self.target_dir.mkdir(parents=True, exist_ok=True)

            final_path = self.target_dir / metadata.filename
            temp_path = self.target_dir / f"{metadata.filename}.download"

            # 1. If final file already exists and valid, return early unless force
            if final_path.is_file() and not force_redownload:
                if self.verify_checksum(final_path, metadata.sha256_checksum):
                    return final_path

            # 2. Check disk space pre-flight
            existing_partial_size = temp_path.stat().st_size if temp_path.exists() else 0
            needed_bytes = max(0, metadata.size_bytes - existing_partial_size)
            self.check_disk_space(needed_bytes)

            # 3. Stream download with range header resumption
            self._stream_to_file(
                url=metadata.source_url,
                temp_path=temp_path,
                expected_total_bytes=metadata.size_bytes,
                progress_callback=progress_callback,
            )

            # 4. Verify SHA-256 checksum on completed temporary file
            if not self.verify_checksum(temp_path, metadata.sha256_checksum):
                if temp_path.exists():
                    temp_path.unlink()
                raise ChecksumVerificationError(
                    f"Cryptographic SHA-256 verification failed for {metadata.filename}. "
                    "Temporary file removed to prevent corrupted inference."
                )

            # 5. Atomic installation (rename .download -> .gguf)
            os.replace(temp_path, final_path)
            return final_path

    def _stream_to_file(
        self,
        url: str,
        temp_path: Path,
        expected_total_bytes: int,
        progress_callback: Callable[[DownloadProgress], None] | None,
    ) -> None:
        start_offset = 0
        if temp_path.exists():
            start_offset = temp_path.stat().st_size
            if start_offset >= expected_total_bytes:
                # Already complete or oversized, restart cleanly
                temp_path.unlink()
                start_offset = 0

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "RIYA-Desktop-Assistant/1.0.0 (AppleSilicon; macOS)",
            },
        )
        if start_offset > 0:
            req.add_header("Range", f"bytes={start_offset}-")

        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                status = getattr(resp, "status", 200)
                is_resuming = (status == 206 and start_offset > 0)
                mode = "ab" if is_resuming else "wb"
                downloaded = start_offset if is_resuming else 0

                start_time = time.monotonic()
                last_time = start_time
                bytes_since_last = 0
                speed = 0.0

                with temp_path.open(mode) as f:
                    while True:
                        if self._cancel_event.is_set():
                            raise DownloadCancelledError("Model download was cancelled by user.")

                        chunk = resp.read(_CHUNK_SIZE)
                        if not chunk:
                            break

                        f.write(chunk)
                        downloaded += len(chunk)
                        bytes_since_last += len(chunk)

                        now = time.monotonic()
                        elapsed_segment = now - last_time
                        if elapsed_segment >= 0.25:
                            speed = bytes_since_last / elapsed_segment
                            last_time = now
                            bytes_since_last = 0

                        if progress_callback:
                            percent = round((downloaded / float(expected_total_bytes)) * 100, 1)
                            eta = (
                                round((expected_total_bytes - downloaded) / speed, 1)
                                if speed > 0
                                else 0.0
                            )
                            progress_callback(
                                DownloadProgress(
                                    downloaded_bytes=downloaded,
                                    total_bytes=expected_total_bytes,
                                    percent=min(100.0, percent),
                                    speed_bytes_sec=speed,
                                    eta_seconds=eta,
                                    is_resuming=is_resuming,
                                )
                            )

        except urllib.error.HTTPError as exc:
            if exc.code == 416:  # Requested Range Not Satisfiable
                if temp_path.exists():
                    temp_path.unlink()
                return self._stream_to_file(url, temp_path, expected_total_bytes, progress_callback)
            raise

    @classmethod
    def verify_checksum(cls, file_path: Path, expected_sha256: str) -> bool:
        """Compute SHA-256 hash of file in chunks and compare."""
        if not file_path.is_file():
            return False
        hasher = hashlib.sha256()
        with file_path.open("rb") as f:
            for block in iter(lambda: f.read(512 * 1024), b""):
                hasher.update(block)
        return hasher.hexdigest().lower() == expected_sha256.strip().lower()

