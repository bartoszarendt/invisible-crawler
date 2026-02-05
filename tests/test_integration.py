"""Integration tests for InvisibleCrawler.

Tests the full pipeline from spider to database using a mock HTTP server.
"""

import io
from pathlib import Path

import pytest
from PIL import Image
from pytest_httpserver import HTTPServer

from storage.db import get_cursor


class TestIntegrationPipeline:
    """End-to-end integration tests for the crawling pipeline."""

    @pytest.fixture
    def create_test_image(self) -> callable:
        """Factory fixture to create test images."""

        def _create_image(width: int = 256, height: int = 256, format: str = "JPEG") -> bytes:
            """Create a test image with specified dimensions."""
            img = Image.new("RGB", (width, height), color="blue")
            buffer = io.BytesIO()
            if format == "JPEG":
                img.save(buffer, format="JPEG", quality=85)
            else:
                img.save(buffer, format=format)
            return buffer.getvalue()

        return _create_image

    @pytest.fixture
    def sample_html_page(self) -> str:
        """Create sample HTML page with images."""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta property="og:image" content="/images/og-image.jpg">
        </head>
        <body>
            <h1>Test Page</h1>
            <img src="/images/photo1.jpg" alt="Photo 1">
            <img src="/images/photo2.png" alt="Photo 2">
            <img src="/images/photo3.webp" alt="Photo 3">
            <a href="/page2">Next Page</a>
        </body>
        </html>
        """

    def test_mock_server_setup(self, httpserver: HTTPServer) -> None:
        """Test that the mock HTTP server is working."""
        httpserver.expect_request("/").respond_with_data("Hello, World!")

        import requests

        response = requests.get(httpserver.url_for("/"))
        assert response.status_code == 200
        assert response.text == "Hello, World!"

    def test_image_fetch_from_mock_server(
        self, httpserver: HTTPServer, create_test_image: callable
    ) -> None:
        """Test ImageFetcher can download images from mock server."""
        from processor.fetcher import ImageFetcher

        # Create and serve a test image
        image_data = create_test_image(256, 256, "JPEG")
        httpserver.expect_request("/test-image.jpg").respond_with_data(
            image_data, content_type="image/jpeg"
        )

        # Fetch the image
        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/test-image.jpg"))

        assert result.success is True
        assert result.width == 256
        assert result.height == 256
        assert result.format == "JPEG"
        assert result.sha256_hash is not None

    def test_image_fetch_with_validation_failure(self, httpserver: HTTPServer) -> None:
        """Test ImageFetcher rejects images below minimum size."""
        from processor.fetcher import ImageFetcher

        # Create a tiny image (below 256x256 threshold)
        img = Image.new("RGB", (100, 100), color="red")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        small_image = buffer.getvalue()

        httpserver.expect_request("/small-image.jpg").respond_with_data(
            small_image, content_type="image/jpeg"
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/small-image.jpg"))

        assert result.success is False
        assert "too small" in result.error_message.lower()

    def test_database_storage_after_fetch(
        self, httpserver: HTTPServer, create_test_image: callable
    ) -> None:
        """Test that fetched image metadata is stored in database."""
        import hashlib

        from processor.fetcher import ImageFetcher

        # Create and serve test image
        image_data = create_test_image(256, 256, "JPEG")
        image_hash = hashlib.sha256(image_data).hexdigest()
        httpserver.expect_request("/db-test.jpg").respond_with_data(
            image_data, content_type="image/jpeg"
        )

        # Fetch image
        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/db-test.jpg"))
        assert result.success is True

        # Store in database (simulate pipeline behavior)
        test_url = httpserver.url_for("/db-test.jpg")
        with get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO images (url, sha256_hash, width, height, format, download_success)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (url) DO UPDATE SET
                    sha256_hash = EXCLUDED.sha256_hash,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height,
                    format = EXCLUDED.format,
                    download_success = EXCLUDED.download_success
                RETURNING id
                """,
                (test_url, image_hash, 256, 256, "JPEG", True),
            )
            image_id = cursor.fetchone()[0]

        # Verify image was stored
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT url, sha256_hash, width, height FROM images WHERE id = %s",
                (image_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == test_url
            assert row[1] == image_hash
            assert row[2] == 256
            assert row[3] == 256

        # Cleanup
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM provenance WHERE image_id = %s", (image_id,))
            cursor.execute("DELETE FROM images WHERE id = %s", (image_id,))

    def test_deduplication_by_hash(
        self, httpserver: HTTPServer, create_test_image: callable
    ) -> None:
        """Test that duplicate images (by hash) are not stored twice."""
        import hashlib

        from processor.fetcher import ImageFetcher

        # Create test image
        image_data = create_test_image(256, 256, "JPEG")
        image_hash = hashlib.sha256(image_data).hexdigest()

        # Serve same image at two different URLs
        httpserver.expect_request("/image-a.jpg").respond_with_data(
            image_data, content_type="image/jpeg"
        )
        httpserver.expect_request("/image-b.jpg").respond_with_data(
            image_data, content_type="image/jpeg"
        )

        fetcher = ImageFetcher()

        # Fetch both URLs
        result1 = fetcher.fetch(httpserver.url_for("/image-a.jpg"))
        result2 = fetcher.fetch(httpserver.url_for("/image-b.jpg"))

        assert result1.success is True
        assert result2.success is True
        assert result1.sha256_hash == result2.sha256_hash == image_hash

        # Store first image
        url1 = httpserver.url_for("/image-a.jpg")
        url2 = httpserver.url_for("/image-b.jpg")

        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO images (url, sha256_hash, download_success) VALUES (%s, %s, %s) RETURNING id",
                (url1, image_hash, True),
            )
            image_id = cursor.fetchone()[0]

            # Try to store second image with same hash
            cursor.execute(
                "SELECT id FROM images WHERE sha256_hash = %s",
                (image_hash,),
            )
            existing = cursor.fetchone()
            assert existing is not None

            # Add provenance for second URL
            cursor.execute(
                "INSERT INTO provenance (image_id, source_page_url, source_domain) VALUES (%s, %s, %s)",
                (existing[0], url2, "localhost"),
            )

        # Cleanup
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM provenance WHERE image_id = %s", (image_id,))
            cursor.execute("DELETE FROM images WHERE id = %s", (image_id,))

    def test_crawl_log_storage(self) -> None:
        """Test that crawl operations are logged to database."""
        with get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_log (page_url, domain, status, images_found, crawl_type)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                ("https://example.com/page1", "example.com", 200, 5, "discovery"),
            )
            log_id = cursor.fetchone()[0]

        # Verify log entry
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT page_url, domain, status, images_found FROM crawl_log WHERE id = %s",
                (log_id,),
            )
            row = cursor.fetchone()
            assert row[0] == "https://example.com/page1"
            assert row[1] == "example.com"
            assert row[2] == 200
            assert row[3] == 5

        # Cleanup
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM crawl_log WHERE id = %s", (log_id,))


class TestEndToEnd:
    """End-to-end tests running the full crawler stack."""

    @pytest.fixture
    def create_test_image(self) -> callable:
        """Factory fixture to create test images."""

        def _create_image(width: int = 256, height: int = 256) -> bytes:
            img = Image.new("RGB", (width, height), color="green")
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
            return buffer.getvalue()

        return _create_image

    def test_full_pipeline_single_page(
        self, httpserver: HTTPServer, create_test_image: callable, tmp_path: Path
    ) -> None:
        """Test spider + fetcher + database integration (simulates pipeline behavior)."""

        # Create test images
        image1_data = create_test_image(256, 256)
        image2_data = create_test_image(256, 256)

        # Set up mock server with HTML page and images
        html_content = """
        <!DOCTYPE html>
        <html>
        <body>
            <h1>Test Page</h1>
            <img src="/images/test1.jpg" alt="Test 1">
            <img src="/images/test2.jpg" alt="Test 2">
        </body>
        </html>
        """

        httpserver.expect_request("/").respond_with_data(html_content, content_type="text/html")
        httpserver.expect_request("/images/test1.jpg").respond_with_data(
            image1_data, content_type="image/jpeg"
        )
        httpserver.expect_request("/images/test2.jpg").respond_with_data(
            image2_data, content_type="image/jpeg"
        )

        # Create seed file
        seed_file = tmp_path / "test_seeds.txt"
        seed_file.write_text(httpserver.url_for("/"))

        # Simulate spider discovery (extract image URLs)
        from scrapy.http import HtmlResponse, Request

        from crawler.spiders.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(seeds=str(seed_file), max_pages=1)

        # Mock response
        request = Request(url=httpserver.url_for("/"))
        response = HtmlResponse(
            url=httpserver.url_for("/"),
            request=request,
            body=html_content.encode("utf-8"),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

        # Parse and collect image items
        image_items = []
        for item in spider.parse(response):
            if isinstance(item, dict) and item.get("type") == "image":
                image_items.append(item)

        assert len(image_items) == 2

        # Simulate pipeline processing
        from processor.fetcher import ImageFetcher

        fetcher = ImageFetcher()
        stored_count = 0

        for item in image_items:
            result = fetcher.fetch(item["url"])
            if result.success:
                # Store in database
                with get_cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO images (url, sha256_hash, width, height, format, download_success)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (url) DO UPDATE SET
                            sha256_hash = EXCLUDED.sha256_hash,
                            width = EXCLUDED.width,
                            height = EXCLUDED.height,
                            format = EXCLUDED.format,
                            download_success = EXCLUDED.download_success
                        RETURNING id
                        """,
                        (
                            item["url"],
                            result.sha256_hash,
                            result.width,
                            result.height,
                            result.format,
                            True,
                        ),
                    )
                    image_id = cursor.fetchone()[0]

                    # Add provenance
                    cursor.execute(
                        "INSERT INTO provenance (image_id, source_page_url, source_domain) VALUES (%s, %s, %s)",
                        (image_id, item["source_page"], item["source_domain"]),
                    )
                    stored_count += 1

        assert stored_count == 2

        # Verify both images were stored (different URLs, different content)
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM images WHERE url LIKE %s",
                (f"{httpserver.url_for('/')}%",),
            )
            count = cursor.fetchone()[0]
            assert count == 2

        # Cleanup
        with get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM provenance WHERE source_domain = %s",
                (httpserver.host,),
            )
            cursor.execute(
                "DELETE FROM images WHERE url LIKE %s",
                (f"{httpserver.url_for('/')}%",),
            )
