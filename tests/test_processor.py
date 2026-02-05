"""Tests for the image processor modules."""

import io

import pytest
from PIL import Image

from processor.fetcher import ImageFetcher, ImageFetchResult
from processor.fingerprint import ImageFingerprinter, compute_sha256


class TestImageFetcher:
    """Test cases for ImageFetcher."""

    @pytest.fixture
    def fetcher(self) -> ImageFetcher:
        """Create a fetcher instance for testing."""
        return ImageFetcher(
            min_file_size=100,
            max_file_size=10 * 1024 * 1024,  # 10MB
            min_width=256,
            min_height=256,
        )

    @pytest.fixture
    def sample_image_bytes(self) -> bytes:
        """Create sample image bytes for testing (256x256 to meet min size requirements)."""
        img = Image.new("RGB", (256, 256), color="red")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()

    def test_fetcher_initialization(self, fetcher: ImageFetcher) -> None:
        """Test fetcher initializes with correct settings."""
        assert fetcher.min_file_size == 100
        assert fetcher.max_file_size == 10 * 1024 * 1024
        assert fetcher.min_dimensions == (256, 256)
        assert fetcher.timeout == 30

    def test_parse_image_dimensions_success(
        self, fetcher: ImageFetcher, sample_image_bytes: bytes
    ) -> None:
        """Test successful image dimension parsing."""
        width, height, img_format = fetcher._parse_image_dimensions(sample_image_bytes)

        assert width == 256
        assert height == 256
        assert img_format == "JPEG"

    def test_parse_image_dimensions_failure(self, fetcher: ImageFetcher) -> None:
        """Test handling of invalid image content."""
        width, height, img_format = fetcher._parse_image_dimensions(b"not an image")

        assert width is None
        assert height is None
        assert img_format is None

    def test_fetch_result_success(self) -> None:
        """Test successful ImageFetchResult creation."""
        result = ImageFetchResult(
            success=True,
            url="https://example.com/image.jpg",
            content=b"image data",
            content_type="image/jpeg",
            file_size=1000,
            width=100,
            height=100,
            format="JPEG",
            sha256_hash="abc123",
        )

        assert result.success is True
        assert result.url == "https://example.com/image.jpg"
        assert result.file_size == 1000

    def test_fetch_result_failure(self) -> None:
        """Test failed ImageFetchResult creation."""
        result = ImageFetchResult(
            success=False,
            url="https://example.com/image.jpg",
            error_message="404 Not Found",
        )

        assert result.success is False
        assert result.error_message == "404 Not Found"
        assert result.content is None


class TestImageFingerprinter:
    """Test cases for ImageFingerprinter (Phase 2 extensibility)."""

    @pytest.fixture
    def fingerprinter(self) -> ImageFingerprinter:
        """Create a fingerprinter instance for testing."""
        return ImageFingerprinter(normalize_size=(64, 64))

    @pytest.fixture
    def sample_jpeg_bytes(self) -> bytes:
        """Create sample JPEG image bytes."""
        img = Image.new("RGB", (100, 100), color="blue")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()

    @pytest.fixture
    def sample_png_bytes(self) -> bytes:
        """Create sample PNG image bytes."""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_fingerprinter_initialization(self, fingerprinter: ImageFingerprinter) -> None:
        """Test fingerprinter initializes with correct settings."""
        assert fingerprinter.normalize_size == (64, 64)

    def test_binary_hash(self, fingerprinter: ImageFingerprinter, sample_jpeg_bytes: bytes) -> None:
        """Test binary SHA-256 hash generation."""
        hash1 = fingerprinter.binary_hash(sample_jpeg_bytes)
        hash2 = fingerprinter.binary_hash(sample_jpeg_bytes)

        assert len(hash1) == 64
        assert hash1 == hash2

    def test_binary_hash_different_content(self, fingerprinter: ImageFingerprinter) -> None:
        """Test different content produces different hashes."""
        img1 = Image.new("RGB", (100, 100), color="red")
        img2 = Image.new("RGB", (100, 100), color="blue")

        buffer1 = io.BytesIO()
        buffer2 = io.BytesIO()
        img1.save(buffer1, format="JPEG")
        img2.save(buffer2, format="JPEG")

        hash1 = fingerprinter.binary_hash(buffer1.getvalue())
        hash2 = fingerprinter.binary_hash(buffer2.getvalue())

        assert hash1 != hash2

    def test_get_image_info(
        self, fingerprinter: ImageFingerprinter, sample_jpeg_bytes: bytes
    ) -> None:
        """Test extraction of image metadata."""
        info = fingerprinter.get_image_info(sample_jpeg_bytes)

        assert info["width"] == 100
        assert info["height"] == 100
        assert info["format"] == "JPEG"
        assert info["mode"] == "RGB"

    def test_get_image_info_invalid(self, fingerprinter: ImageFingerprinter) -> None:
        """Test handling of invalid image data."""
        with pytest.raises(ValueError, match="Invalid image content"):
            fingerprinter.get_image_info(b"not an image")

    def test_normalize_image(
        self, fingerprinter: ImageFingerprinter, sample_jpeg_bytes: bytes
    ) -> None:
        """Test image normalization to PNG."""
        normalized = fingerprinter.normalize_image(sample_jpeg_bytes)

        # Verify it's a valid PNG
        img = Image.open(io.BytesIO(normalized))
        assert img.format == "PNG"
        assert img.mode == "RGB"
        assert img.width == 100
        assert img.height == 100

    def test_normalize_image_rgba(
        self, fingerprinter: ImageFingerprinter, sample_png_bytes: bytes
    ) -> None:
        """Test normalization converts RGBA to RGB."""
        normalized = fingerprinter.normalize_image(sample_png_bytes)

        img = Image.open(io.BytesIO(normalized))
        assert img.format == "PNG"
        assert img.mode == "RGB"  # Should be converted from RGBA

    def test_normalize_image_invalid(self, fingerprinter: ImageFingerprinter) -> None:
        """Test normalization fails gracefully for invalid data."""
        with pytest.raises(ValueError, match="Image normalization failed"):
            fingerprinter.normalize_image(b"not an image")

    def test_normalized_hash(
        self, fingerprinter: ImageFingerprinter, sample_jpeg_bytes: bytes
    ) -> None:
        """Test normalized hash generation."""
        hash1 = fingerprinter.normalized_hash(sample_jpeg_bytes)
        hash2 = fingerprinter.normalized_hash(sample_jpeg_bytes)

        assert len(hash1) == 64
        assert hash1 == hash2

    def test_normalized_hash_consistency(self, fingerprinter: ImageFingerprinter) -> None:
        """Test same visual content produces same normalized hash."""
        # Create same image twice with different encodings
        img1 = Image.new("RGB", (100, 100), color="green")
        img2 = Image.new("RGB", (100, 100), color="green")

        buffer1 = io.BytesIO()
        buffer2 = io.BytesIO()
        img1.save(buffer1, format="PNG", compress_level=1)
        img2.save(buffer2, format="PNG", compress_level=9)

        hash1 = fingerprinter.normalized_hash(buffer1.getvalue())
        hash2 = fingerprinter.normalized_hash(buffer2.getvalue())

        assert hash1 == hash2

    def test_compute_sha256_helper(self) -> None:
        """Test compute_sha256 convenience function."""
        data = b"test content"
        hash1 = compute_sha256(data)
        hash2 = compute_sha256(data)

        assert len(hash1) == 64
        assert hash1 == hash2
