"""Tests for async image fetching module."""

import io
from unittest.mock import MagicMock, Mock

import pytest
from PIL import Image
from scrapy.http import Response, Request

from processor.async_fetcher import (
    ScrapyImageDownloader,
    create_fetch_result_from_scrapy_response,
)
from processor.fetcher import ImageFetchResult


class TestScrapyImageDownloader:
    """Test cases for ScrapyImageDownloader."""

    @pytest.fixture
    def downloader(self) -> ScrapyImageDownloader:
        """Create a downloader instance for testing."""
        return ScrapyImageDownloader(
            min_file_size=50,  # Lower threshold for testing
            max_file_size=10 * 1024 * 1024,
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

    def test_downloader_initialization(self, downloader: ScrapyImageDownloader) -> None:
        """Test downloader initializes with correct settings."""
        assert downloader.min_file_size == 50
        assert downloader.max_file_size == 10 * 1024 * 1024
        assert downloader.min_dimensions == (256, 256)

    def test_process_response_success(
        self, downloader: ScrapyImageDownloader, sample_image_bytes: bytes
    ) -> None:
        """Test successful image response processing."""
        # Create mock response
        request = Request(url="https://example.com/image.jpg")
        response = Response(
            url="https://example.com/image.jpg",
            request=request,
            body=sample_image_bytes,
            headers={b"Content-Type": [b"image/jpeg"]},
            status=200,
        )

        result = downloader.process_response("https://example.com/image.jpg", response)

        assert result.success is True
        assert result.url == "https://example.com/image.jpg"
        assert result.content_type == "image/jpeg"
        assert result.width == 256
        assert result.height == 256
        assert result.format == "JPEG"
        assert result.sha256_hash is not None
        assert len(result.sha256_hash) == 64

    def test_process_response_http_error(self, downloader: ScrapyImageDownloader) -> None:
        """Test handling of HTTP error responses."""
        request = Request(url="https://example.com/image.jpg")
        response = Response(
            url="https://example.com/image.jpg",
            request=request,
            body=b"",
            status=404,
        )

        result = downloader.process_response("https://example.com/image.jpg", response)

        assert result.success is False
        assert result.error_message is not None
        assert "HTTP error: 404" in result.error_message

    def test_process_response_invalid_content_type(
        self, downloader: ScrapyImageDownloader, sample_image_bytes: bytes
    ) -> None:
        """Test rejection of invalid content types."""
        request = Request(url="https://example.com/image.jpg")
        response = Response(
            url="https://example.com/image.jpg",
            request=request,
            body=sample_image_bytes,
            headers={b"Content-Type": [b"application/pdf"]},
            status=200,
        )

        result = downloader.process_response("https://example.com/image.jpg", response)

        assert result.success is False
        assert result.error_message is not None
        assert "Invalid content type" in result.error_message

    def test_process_response_file_too_large(self, downloader: ScrapyImageDownloader) -> None:
        """Test rejection of files above maximum size."""
        # Create an image that's within dimension limits but exceeds max file size
        # by using a large raw byte string
        large_content = b"\x00" * (downloader.max_file_size + 100)

        request = Request(url="https://example.com/image.bin")
        response = Response(
            url="https://example.com/image.bin",
            request=request,
            body=large_content,
            headers={b"Content-Type": [b"image/jpeg"]},
            status=200,
        )

        result = downloader.process_response("https://example.com/image.bin", response)

        assert result.success is False
        assert result.error_message is not None
        assert "File too large" in result.error_message

    def test_process_response_dimensions_too_small(self, downloader: ScrapyImageDownloader) -> None:
        """Test rejection of images below minimum dimensions."""
        # Create a small image (below 256x256 threshold)
        img = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        small_image = buffer.getvalue()

        request = Request(url="https://example.com/image.jpg")
        response = Response(
            url="https://example.com/image.jpg",
            request=request,
            body=small_image,
            headers={b"Content-Type": [b"image/jpeg"]},
            status=200,
        )

        result = downloader.process_response("https://example.com/image.jpg", response)

        assert result.success is False
        assert result.error_message is not None
        assert "Image too small" in result.error_message

    def test_process_response_consistent_hashing(
        self, downloader: ScrapyImageDownloader, sample_image_bytes: bytes
    ) -> None:
        """Test that same content produces same hash."""
        request = Request(url="https://example.com/image.jpg")
        response1 = Response(
            url="https://example.com/image.jpg",
            request=request,
            body=sample_image_bytes,
            headers={b"Content-Type": [b"image/jpeg"]},
            status=200,
        )
        response2 = Response(
            url="https://example.com/image.jpg",
            request=request,
            body=sample_image_bytes,
            headers={b"Content-Type": [b"image/jpeg"]},
            status=200,
        )

        result1 = downloader.process_response("https://example.com/image.jpg", response1)
        result2 = downloader.process_response("https://example.com/image.jpg", response2)

        assert result1.sha256_hash == result2.sha256_hash

    def test_create_fetch_result_from_scrapy_response(self, sample_image_bytes: bytes) -> None:
        """Test convenience function for creating fetch results."""
        request = Request(url="https://example.com/image.jpg")
        response = Response(
            url="https://example.com/image.jpg",
            request=request,
            body=sample_image_bytes,
            headers={b"Content-Type": [b"image/jpeg"]},
            status=200,
        )

        result = create_fetch_result_from_scrapy_response("https://example.com/image.jpg", response)

        assert result.success is True
        assert result.width == 256
        assert result.height == 256


class TestAsyncFetcherIntegration:
    """Integration tests for async fetching."""

    def test_fetch_result_compatibility(self) -> None:
        """Test that async and sync fetch results are compatible."""
        # Both modules should use the same ImageFetchResult class
        from processor.async_fetcher import ImageFetchResult as AsyncResult
        from processor.fetcher import ImageFetchResult as SyncResult

        # They should be the same class
        assert AsyncResult is SyncResult

    def test_image_fetch_result_creation(self) -> None:
        """Test ImageFetchResult can be created with all fields."""
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

    def test_image_fetch_result_failure(self) -> None:
        """Test ImageFetchResult for failed fetch."""
        result = ImageFetchResult(
            success=False,
            url="https://example.com/image.jpg",
            error_message="Network error",
        )

        assert result.success is False
        assert result.error_message == "Network error"
        assert result.content is None
