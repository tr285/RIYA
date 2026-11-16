"""Automated verification suite for model provisioning, hardware recommendation, and macOS packaging."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.ai.provisioning import (
    ChecksumVerificationError,
    DownloadCancelledError,
    DownloadProgress,
    HardwareDetector,
    HardwareProfile,
    HardwareRecommender,
    InsufficientDiskSpaceError,
    ModelCatalog,
    ModelMetadata,
    ProvisioningSecurityError,
    SecureModelDownloader,
)
from core.config.paths import (
    get_app_support_dir,
    get_cache_dir,
    get_database_path,
    get_logs_dir,
    get_model_search_paths,
    get_models_dir,
    get_recordings_dir,
    get_user_data_dir,
    is_development_mode,
    is_running_in_bundle,
)


class TestModelProvisioningAndPackaging:
    """Test suite covering Phase 4 path separation, model provisioning, and packaging."""

    # 1. Path Separation & Storage Architecture
    def test_path_resolvers_and_environment_overrides(self, monkeypatch: pytest.MonkeyPatch):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Test default resolvers return valid existing paths
            data_dir = get_user_data_dir()
            assert data_dir.exists()
            db_path = get_database_path()
            assert db_path.name == "riya.db"

            models_dir = get_models_dir()
            assert models_dir.exists()

            logs_dir = get_logs_dir()
            assert logs_dir.exists()

            cache_dir = get_cache_dir()
            assert cache_dir.exists()

            recordings_dir = get_recordings_dir()
            assert recordings_dir.exists()

            # Test environment variable overrides
            custom_data = (tmp_path / "custom_data").resolve()
            monkeypatch.setenv("RIYA_DATA_DIR", str(custom_data))
            assert get_user_data_dir() == custom_data
            assert custom_data.exists()

            custom_models = (tmp_path / "custom_models").resolve()
            monkeypatch.setenv("RIYA_MODELS_DIR", str(custom_models))
            assert get_models_dir() == custom_models
            assert custom_models.exists()

            custom_db = (tmp_path / "custom.db").resolve()
            monkeypatch.setenv("RIYA_DATABASE_PATH", str(custom_db))
            assert get_database_path() == custom_db

    def test_bundle_mode_detection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("RIYA_BUNDLE_APP", "1")
        assert is_running_in_bundle() is True
        assert is_development_mode() is False

        monkeypatch.delenv("RIYA_BUNDLE_APP", raising=False)
        monkeypatch.setenv("RIYA_ENV", "production")
        assert is_development_mode() is False

    def test_model_search_paths_include_defaults(self):
        paths = get_model_search_paths()
        assert len(paths) >= 1
        assert any(p.exists() for p in paths)

    # 2. Model Catalog Metadata Integrity
    def test_model_catalog_metadata_and_integrity(self):
        models = ModelCatalog.list_all()
        assert len(models) >= 3

        default_model = ModelCatalog.get_default()
        assert default_model.is_default is True
        assert "qwen3" in default_model.model_id.lower()
        assert default_model.filename.endswith(".gguf")

        for meta in models:
            assert meta.size_bytes > 1_000_000_000  # > 1 GB
            assert meta.size_gb > 1.0
            assert meta.size_mb > 1000.0
            assert len(meta.sha256_checksum) == 64
            assert meta.context_length >= 2048
            assert meta.recommended_min_ram_gb >= 4.0
            assert meta.source_url.startswith("https://huggingface.co/")

    def test_model_catalog_lookup_functions(self):
        default_meta = ModelCatalog.get_default()
        by_id = ModelCatalog.get_by_id(default_meta.model_id)
        assert by_id == default_meta

        by_filename = ModelCatalog.get_by_filename(default_meta.filename)
        assert by_filename == default_meta

        matched = ModelCatalog.match_local_file(f"/some/path/{default_meta.filename}")
        assert matched == default_meta

        assert ModelCatalog.get_by_id("non-existent-model-id") is None
        assert ModelCatalog.get_by_filename("non-existent.gguf") is None

    # 3. Hardware Detector and Recommender
    def test_hardware_detector_profile(self):
        profile = HardwareDetector.get_profile()
        assert profile.total_ram_bytes > 0
        assert profile.total_ram_gb > 0
        assert profile.cpu_cores >= 1
        assert isinstance(profile.metal_supported, bool)
        assert profile.free_disk_bytes >= 0

    def test_hardware_recommender_logic(self):
        # Case 1: 8 GB Mac
        profile_8gb = HardwareProfile(
            platform_system="Darwin",
            machine_arch="arm64",
            chip_brand="Apple M1",
            total_ram_bytes=8 * 1024**3,
            total_ram_gb=8.0,
            cpu_cores=8,
            metal_supported=True,
            free_disk_bytes=50 * 1024**3,
            free_disk_gb=50.0,
        )
        rec_8gb = HardwareRecommender.recommend(profile_8gb)
        assert rec_8gb.recommended_model.recommended_min_ram_gb <= 8.0
        assert "8.0 GB" in rec_8gb.reason
        assert rec_8gb.is_gpu_accelerated is True

        # Case 2: 32 GB Mac (Pro / Max)
        profile_32gb = HardwareProfile(
            platform_system="Darwin",
            machine_arch="arm64",
            chip_brand="Apple M3 Max",
            total_ram_bytes=32 * 1024**3,
            total_ram_gb=32.0,
            cpu_cores=16,
            metal_supported=True,
            free_disk_bytes=200 * 1024**3,
            free_disk_gb=200.0,
        )
        rec_32gb = HardwareRecommender.recommend(profile_32gb)
        assert rec_32gb.recommended_model.recommended_min_ram_gb <= 16.0
        assert "32.0 GB" in rec_32gb.reason

    # 4. Secure Downloader Security & Validation
    def test_downloader_url_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = SecureModelDownloader(target_dir=Path(tmp_dir))

            # Valid URLs
            downloader.validate_url("https://huggingface.co/owner/repo/resolve/main/model.gguf")
            downloader.validate_url("https://hf.co/owner/repo/resolve/main/model.gguf")
            downloader.validate_url("https://cdn-lfs.huggingface.co/repos/model.gguf")

            # Insecure HTTP
            with pytest.raises(ProvisioningSecurityError, match="Insecure scheme"):
                downloader.validate_url("http://huggingface.co/model.gguf")

            # Untrusted Domains
            with pytest.raises(ProvisioningSecurityError, match="Unauthorized host"):
                downloader.validate_url("https://malicious-site.com/model.gguf")

            with pytest.raises(ProvisioningSecurityError, match="Unauthorized host"):
                downloader.validate_url("https://github.com/owner/repo/raw/main/model.gguf")

    def test_downloader_disk_space_guard(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = SecureModelDownloader(target_dir=Path(tmp_dir), safety_margin_bytes=1024)
            # Check for unreasonable size (e.g. 500 Terabytes)
            with pytest.raises(InsufficientDiskSpaceError, match="Insufficient disk space"):
                downloader.check_disk_space(500 * 1024**4)

    def test_downloader_checksum_verification(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test.bin"
            content = b"RIYA Cryptographic SHA-256 Model Verification"
            test_file.write_bytes(content)
            expected_hash = hashlib.sha256(content).hexdigest()

            assert SecureModelDownloader.verify_checksum(test_file, expected_hash) is True
            assert SecureModelDownloader.verify_checksum(test_file, "0" * 64) is False
            assert SecureModelDownloader.verify_checksum(Path(tmp_dir) / "nonexistent.bin", expected_hash) is False

    def test_downloader_streaming_and_atomic_install(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = Path(tmp_dir)
            downloader = SecureModelDownloader(target_dir=target_path)

            payload = b"RIYA Neural Weights Simulation" * 1000
            payload_sha256 = hashlib.sha256(payload).hexdigest()

            meta = ModelMetadata(
                model_id="mock-model",
                name="Mock Model",
                architecture="qwen3",
                filename="mock-model.gguf",
                size_bytes=len(payload),
                sha256_checksum=payload_sha256,
                context_length=2048,
                recommended_min_ram_gb=4.0,
                source_url="https://huggingface.co/riya/mock-model/resolve/main/mock-model.gguf",
                description="Mock test model",
            )

            progress_reports: list[DownloadProgress] = []

            class MockResponse:
                def __init__(self, data: bytes):
                    self.bio = io.BytesIO(data)
                    self.status = 200

                def read(self, chunk_size: int = 1024):
                    return self.bio.read(chunk_size)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            with patch("urllib.request.urlopen", return_value=MockResponse(payload)):
                installed_file = downloader.download_model(
                    meta,
                    progress_callback=lambda p: progress_reports.append(p),
                )

            assert installed_file.exists()
            assert installed_file.name == "mock-model.gguf"
            assert not (target_path / "mock-model.gguf.download").exists()
            assert installed_file.read_bytes() == payload
            assert len(progress_reports) >= 1
            assert progress_reports[-1].percent == 100.0

    def test_downloader_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloader = SecureModelDownloader(target_dir=Path(tmp_dir))

            meta = ModelMetadata(
                model_id="test-cancel",
                name="Test Cancel",
                architecture="qwen3",
                filename="test-cancel.gguf",
                size_bytes=10000,
                sha256_checksum="0" * 64,
                context_length=2048,
                recommended_min_ram_gb=4.0,
                source_url="https://huggingface.co/riya/test/resolve/main/test.gguf",
                description="Test cancellation",
            )

            def _cancelling_read(_sz):
                downloader.cancel()
                return b"some_chunk_data"

            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.side_effect = _cancelling_read
            mock_resp.__enter__.return_value = mock_resp

            with patch("urllib.request.urlopen", return_value=mock_resp):
                with pytest.raises(DownloadCancelledError, match="cancelled by user"):
                    downloader.download_model(meta)

            assert downloader.is_cancelled is True

    # 5. Packaged Bundle & DMG Verification
    def test_dist_app_bundle_structure(self):
        repo_root = Path(__file__).resolve().parents[1]
        app_path = repo_root / "dist" / "RIYA.app"
        assert app_path.exists(), "dist/RIYA.app bundle must exist"

        launcher = app_path / "Contents" / "MacOS" / "RIYA"
        assert launcher.exists(), "MacOS/RIYA launcher must exist"
        assert os.access(launcher, os.X_OK), "MacOS/RIYA must be executable"

        info_plist = app_path / "Contents" / "Info.plist"
        assert info_plist.exists(), "Info.plist must exist"

        icon = app_path / "Contents" / "Resources" / "AppIcon.icns"
        assert icon.exists(), "AppIcon.icns must exist"

        app_core = app_path / "Contents" / "Resources" / "app" / "core"
        assert app_core.exists(), "Bundled core/ package must exist"

    def test_dist_dmg_installer_exists(self):
        repo_root = Path(__file__).resolve().parents[1]
        dmg_path = repo_root / "dist" / "RIYA-v1.0.0-AppleSilicon.dmg"
        assert dmg_path.exists(), "dist/RIYA-v1.0.0-AppleSilicon.dmg must exist"
        assert dmg_path.stat().st_size > 50 * 1024 * 1024  # > 50 MB
