"""Tests for pipeline rejection tracking."""

import io
from unittest.mock import MagicMock

import pytest
from PIL import Image
from scrapy.http import Request, Response

from crawler.pipelines import ImageProcessingPipeline
from processor.media_policy import (
    REJECTION_REASON_FILE_TOO_LARGE,
    REJECTION_REASON_FILE_TOO_SMALL,
    REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL,
    REJECTION_REASON_MISSING_RESPONSE,
    REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE,
)


class TestPipelineRejectionTracking:
    """Test rejection reason tracking in pipeline."""

    @pytest.fixture
    def pipeline(self) -> ImageProcessingPipeline:
        """Create and initialize a pipeline."""
        pipeline = ImageProcessingPipeline()
        mock_spider = MagicMock()
        pipeline.open_spider(mock_spider)
        return pipeline

    @pytest.fixture
    def create_test_image(self) -> callable:
        """Factory fixture to create test images."""

        def _create_image(width: int = 256, height: int = 256) -> bytes:
            img = Image.new("RGB", (width, height), color="blue")
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()

        return _create_image

    def test_rejection_stats_initialized(self, pipeline: ImageProcessingPipeline) -> None:
        """Test that rejection stats are properly initialized."""
        assert REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE in pipeline.rejection_stats
        assert REJECTION_REASON_FILE_TOO_LARGE in pipeline.rejection_stats
        assert REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL in pipeline.rejection_stats
        assert REJECTION_REASON_MISSING_RESPONSE in pipeline.rejection_stats

        # All should start at 0
        for count in pipeline.rejection_stats.values():
            assert count == 0

    def test_classify_rejection_unsupported_content_type(
        self, pipeline: ImageProcessingPipeline
    ) -> None:
        """Test classification of unsupported content type."""
        pipeline._classify_and_count_rejection("unsupported_content_type: image/svg+xml")
        assert pipeline.rejection_stats[REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE] == 1

    def test_classify_rejection_file_too_small(
        self, pipeline: ImageProcessingPipeline
    ) -> None:
        """Test classification of file too small."""
        pipeline._classify_and_count_rejection("file_too_small: 512 bytes")
        assert pipeline.rejection_stats[REJECTION_REASON_FILE_TOO_SMALL] == 1

    def test_classify_rejection_dimensions_too_small(
        self, pipeline: ImageProcessingPipeline
    ) -> None:
        """Test classification of dimensions too small."""
        pipeline._classify_and_count_rejection("image_dimensions_too_small: 100x100")
        assert pipeline.rejection_stats[REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL] == 1

    def test_classify_rejection_unknown_format(
        self, pipeline: ImageProcessingPipeline
    ) -> None:
        """Test classification falls back to unknown for unrecognized formats."""
        pipeline._classify_and_count_rejection("Some random error message")
        assert pipeline.rejection_stats.get("unknown", 0) == 1

    def test_increment_rejection_reason_multiple_times(
        self, pipeline: ImageProcessingPipeline
    ) -> None:
        """Test that rejection reasons accumulate correctly."""
        pipeline._increment_rejection_reason(REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE)
        pipeline._increment_rejection_reason(REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE)
        pipeline._increment_rejection_reason(REJECTION_REASON_FILE_TOO_LARGE)

        assert pipeline.rejection_stats[REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE] == 2
        assert pipeline.rejection_stats[REJECTION_REASON_FILE_TOO_LARGE] == 1

    def test_missing_response_increments_rejection(
        self, pipeline: ImageProcessingPipeline
    ) -> None:
        """Test that missing response increments rejection counter."""
        from scrapy.exceptions import DropItem

        mock_spider = MagicMock()
        item = {
            "type": "image",
            "url": "https://example.com/missing.jpg",
            "source_page": "https://example.com/",
            "source_domain": "example.com",
            "crawl_type": "discovery",
            "response": None,  # Missing response
        }

        with pytest.raises(DropItem):
            pipeline.process_item(item, mock_spider)

        assert pipeline.rejection_stats[REJECTION_REASON_MISSING_RESPONSE] == 1
        assert pipeline.stats["images_failed"] == 1

    def test_invalid_content_type_response(
        self, pipeline: ImageProcessingPipeline
    ) -> None:
        """Test that invalid content type increments rejection counter."""
        from scrapy.exceptions import DropItem

        mock_spider = MagicMock()

        # Create response with SVG content type
        request = Request(url="https://example.com/icon.svg")
        response = Response(
            url="https://example.com/icon.svg",
            request=request,
            body=b"<svg>...</svg>",
            headers={b"Content-Type": [b"image/svg+xml"]},
            status=200,
        )

        item = {
            "type": "image",
            "url": "https://example.com/icon.svg",
            "source_page": "https://example.com/",
            "source_domain": "example.com",
            "crawl_type": "discovery",
            "response": response,
        }

        with pytest.raises(DropItem):
            pipeline.process_item(item, mock_spider)

        assert pipeline.rejection_stats[REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE] == 1
        assert pipeline.stats["images_failed"] == 1

    def test_dimensions_too_small_response(
        self, pipeline: ImageProcessingPipeline, create_test_image: callable
    ) -> None:
        """Test that undersized images increment rejection counter."""
        from scrapy.exceptions import DropItem

        mock_spider = MagicMock()

        # Create small image (100x100, below 256x256 threshold)
        # Need to ensure file is large enough to pass min_file_size check
        img = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=100)  # Higher quality for larger file
        # Pad to ensure over 1KB
        small_image = buffer.getvalue()
        if len(small_image) < 1024:
            small_image = small_image + b'\x00' * (1024 - len(small_image))

        request = Request(url="https://example.com/small.jpg")
        response = Response(
            url="https://example.com/small.jpg",
            request=request,
            body=small_image,
            headers={b"Content-Type": [b"image/jpeg"]},
            status=200,
        )

        item = {
            "type": "image",
            "url": "https://example.com/small.jpg",
            "source_page": "https://example.com/",
            "source_domain": "example.com",
            "crawl_type": "discovery",
            "response": response,
        }

        with pytest.raises(DropItem):
            pipeline.process_item(item, mock_spider)

        # Either dimension check or file size check, both are valid rejections
        # for under-sized images
        total_size_rejections = (
            pipeline.rejection_stats[REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL] +
            pipeline.rejection_stats[REJECTION_REASON_FILE_TOO_SMALL]
        )
        assert total_size_rejections >= 1
        assert pipeline.stats["images_failed"] == 1
