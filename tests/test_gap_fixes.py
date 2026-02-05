"""Tests for Gap Fixes (2026-02-06).

Tests specifically verify the 6 gaps identified and fixed:
1. Refresh crawl type propagation
2. SVG filtering at spider URL filter level
3. Content-Type bypass prevention
4. Download counter consistency
5. Structured rejection reasons
6. Lint cleanliness (verified separately)
"""

import io

from PIL import Image
from pytest_httpserver import HTTPServer
from scrapy.http import HtmlResponse, Request, Response

from processor.media_policy import (
    REJECTION_REASON_INVALID_IMAGE_PAYLOAD,
    REJECTION_REASON_MISSING_CONTENT_TYPE,
    REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE,
)


class TestSVGFiltering:
    """Test that SVG URLs are strictly blocked at spider level."""

    def test_svg_with_extension_rejected(self) -> None:
        """SVG URLs with .svg extension are rejected."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(seeds=None)

        # These should all be rejected
        assert not spider._is_valid_image_url("https://example.com/logo.svg")
        assert not spider._is_valid_image_url("https://example.com/images/icon.svg")
        assert not spider._is_valid_image_url("https://example.com/img/graphic.svg?v=1")

    def test_svg_with_image_path_rejected(self) -> None:
        """SVG URLs are rejected even if path contains 'image' or '/img/'."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(seeds=None)

        # Old code would accept these due to heuristic "/img/" or "image" check
        # New code strictly requires allowed extension
        assert not spider._is_valid_image_url("https://example.com/images/logo.svg")
        assert not spider._is_valid_image_url("https://example.com/img/icon.svg")

    def test_allowed_extensions_accepted(self) -> None:
        """URLs with allowed extensions are accepted."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(seeds=None)

        assert spider._is_valid_image_url("https://example.com/photo.jpg")
        assert spider._is_valid_image_url("https://example.com/image.png")
        assert spider._is_valid_image_url("https://example.com/graphic.webp")
        assert spider._is_valid_image_url("https://example.com/photo.jpeg")

    def test_query_string_allowed_extension(self) -> None:
        """URLs with allowed extensions in query string are accepted."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(seeds=None)

        assert spider._is_valid_image_url("https://example.com/image?file=photo.jpg")
        assert spider._is_valid_image_url("https://example.com/cdn?img=test.png&v=1")


class TestContentTypeValidation:
    """Test that missing/unsupported Content-Type is strictly rejected."""

    def test_missing_content_type_rejected_sync(self, httpserver: HTTPServer) -> None:
        """Sync fetcher rejects responses with missing Content-Type header."""
        from processor.fetcher import ImageFetcher

        # Create valid JPEG but serve with no Content-Type
        img = Image.new("RGB", (300, 300), color="blue")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        image_data = buffer.getvalue()

        # Use headers parameter to ensure no Content-Type is sent
        httpserver.expect_request("/no-ct.jpg").respond_with_data(
            image_data, headers={}  # Explicitly no headers
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/no-ct.jpg"))

        assert result.success is False
        # Either missing_content_type or empty string should be rejected
        assert (REJECTION_REASON_MISSING_CONTENT_TYPE in result.error_message or
                "unsupported_content_type" in result.error_message)

    def test_svg_content_type_rejected_sync(self, httpserver: HTTPServer) -> None:
        """Sync fetcher rejects SVG content-type even with valid payload."""
        from processor.fetcher import ImageFetcher

        svg_payload = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="100" height="100"/></svg>'

        httpserver.expect_request("/test.svg").respond_with_data(
            svg_payload, content_type="image/svg+xml"
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/test.svg"))

        assert result.success is False
        assert REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE in result.error_message

    def test_missing_content_type_rejected_scrapy_downloader(self) -> None:
        """ScrapyImageDownloader rejects responses with missing Content-Type."""
        from processor.async_fetcher import ScrapyImageDownloader

        # Create valid JPEG
        img = Image.new("RGB", (300, 300), color="blue")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        image_data = buffer.getvalue()

        # Create response without Content-Type header
        response = Response(
            url="https://example.com/test.jpg",
            status=200,
            body=image_data,
            headers={},  # No Content-Type
        )

        downloader = ScrapyImageDownloader()
        result = downloader.process_response("https://example.com/test.jpg", response)

        assert result.success is False
        assert REJECTION_REASON_MISSING_CONTENT_TYPE in result.error_message

    def test_valid_content_type_accepted(self, httpserver: HTTPServer) -> None:
        """Valid content types are accepted."""
        from processor.fetcher import ImageFetcher

        img = Image.new("RGB", (300, 300), color="green")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        image_data = buffer.getvalue()

        httpserver.expect_request("/valid.jpg").respond_with_data(
            image_data, content_type="image/jpeg"
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/valid.jpg"))

        assert result.success is True


class TestInvalidPayloadRejection:
    """Test that invalid image payloads are rejected even with correct Content-Type."""

    def test_corrupted_payload_rejected_sync(self, httpserver: HTTPServer) -> None:
        """Sync fetcher rejects corrupted image payload."""
        from processor.fetcher import ImageFetcher

        # Serve garbage data with valid content-type
        corrupted_data = b"This is not a valid JPEG file" * 100  # Make it large enough

        httpserver.expect_request("/corrupted.jpg").respond_with_data(
            corrupted_data, content_type="image/jpeg"
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/corrupted.jpg"))

        assert result.success is False
        assert REJECTION_REASON_INVALID_IMAGE_PAYLOAD in result.error_message

    def test_corrupted_payload_rejected_scrapy_downloader(self) -> None:
        """ScrapyImageDownloader rejects corrupted image payload."""
        from processor.async_fetcher import ScrapyImageDownloader

        corrupted_data = b"Not a valid PNG" * 100

        response = Response(
            url="https://example.com/test.png",
            status=200,
            body=corrupted_data,
            headers={b"Content-Type": b"image/png"},
        )

        downloader = ScrapyImageDownloader()
        result = downloader.process_response("https://example.com/test.png", response)

        assert result.success is False
        assert REJECTION_REASON_INVALID_IMAGE_PAYLOAD in result.error_message


class TestRefreshCrawlTypePropagation:
    """Test that refresh crawl type is correctly propagated to image requests."""

    def test_refresh_mode_propagated_to_image_requests(self) -> None:
        """Spider in refresh mode passes crawl_type='refresh' to image requests."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(seeds=None, crawl_type="refresh")

        # Create mock HTML response with an image
        html = """
        <html>
            <body>
                <img src="https://example.com/test.jpg" />
            </body>
        </html>
        """
        request = Request(url="https://example.com/page")
        request.meta["domain"] = "example.com"
        request.meta["depth"] = 0

        response = HtmlResponse(
            url="https://example.com/page",
            body=html.encode("utf-8"),
            encoding="utf-8",
            request=request,
        )

        # Parse and collect image requests
        requests = list(spider.parse(response))

        # Find image request
        image_requests = [r for r in requests if isinstance(r, Request) and "test.jpg" in r.url]
        assert len(image_requests) == 1

        image_req = image_requests[0]
        assert image_req.meta.get("crawl_type") == "refresh"

    def test_discovery_mode_propagated_to_image_requests(self) -> None:
        """Spider in discovery mode passes crawl_type='discovery' to image requests."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        spider = DiscoverySpider(seeds=None, crawl_type="discovery")

        html = """
        <html>
            <body>
                <img src="https://example.com/test.jpg" />
            </body>
        </html>
        """
        request = Request(url="https://example.com/page")
        request.meta["domain"] = "example.com"
        request.meta["depth"] = 0

        response = HtmlResponse(
            url="https://example.com/page",
            body=html.encode("utf-8"),
            encoding="utf-8",
            request=request,
        )

        requests = list(spider.parse(response))
        image_requests = [r for r in requests if isinstance(r, Request) and "test.jpg" in r.url]
        assert len(image_requests) == 1

        image_req = image_requests[0]
        assert image_req.meta.get("crawl_type") == "discovery"


class TestCanonicalRejectionReasons:
    """Test that rejection reasons follow canonical structured format."""

    def test_file_too_small_canonical_format(self, httpserver: HTTPServer) -> None:
        """File size rejections use canonical format."""
        from processor.fetcher import ImageFetcher

        small_data = b"x" * 500  # Below 1024 byte minimum

        httpserver.expect_request("/small.jpg").respond_with_data(
            small_data, content_type="image/jpeg"
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/small.jpg"))

        assert result.success is False
        # Format: "file_too_small: 500 bytes"
        assert result.error_message.startswith("file_too_small:")
        assert "500 bytes" in result.error_message

    def test_dimensions_too_small_canonical_format(self, httpserver: HTTPServer) -> None:
        """Dimension rejections use canonical format."""
        from processor.fetcher import ImageFetcher

        # Create image larger than 1KB but smaller than 256x256
        img = Image.new("RGB", (128, 128), color="red")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        # Pad to ensure > 1KB
        image_data = buffer.getvalue()
        if len(image_data) < 1024:
            image_data += b"\x00" * (1024 - len(image_data) + 100)

        httpserver.expect_request("/small-dim.png").respond_with_data(
            image_data, content_type="image/png"
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/small-dim.png"))

        assert result.success is False
        # Format: "image_dimensions_too_small: 128x128"
        assert result.error_message.startswith("image_dimensions_too_small:")
        assert "128x128" in result.error_message

    def test_unsupported_content_type_canonical_format(self, httpserver: HTTPServer) -> None:
        """Content-type rejections use canonical format."""
        from processor.fetcher import ImageFetcher

        httpserver.expect_request("/test.gif").respond_with_data(
            b"GIF89a", content_type="image/gif"
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/test.gif"))

        assert result.success is False
        # Format: "unsupported_content_type: image/gif"
        assert result.error_message.startswith("unsupported_content_type:")
        assert "image/gif" in result.error_message


class TestHttpErrorCanonicalization:
    """Test that all HTTP/network errors use canonical http_error format."""

    def test_timeout_canonical_format(self) -> None:
        """Timeout errors use canonical http_error format."""
        from unittest.mock import patch

        from processor.fetcher import ImageFetcher

        fetcher = ImageFetcher()

        with patch.object(fetcher.session, "get", side_effect=__import__("requests").exceptions.Timeout()):
            result = fetcher.fetch("https://example.com/test.jpg")

        assert result.success is False
        assert result.error_message == "http_error: timeout"

    def test_connection_error_canonical_format(self) -> None:
        """Connection errors use canonical http_error format."""
        from unittest.mock import patch

        from processor.fetcher import ImageFetcher

        fetcher = ImageFetcher()

        with patch.object(fetcher.session, "get", side_effect=__import__("requests").exceptions.ConnectionError()):
            result = fetcher.fetch("https://example.com/test.jpg")

        assert result.success is False
        assert result.error_message.startswith("http_error: connection_error:")

    def test_http_error_canonical_format(self, httpserver: HTTPServer) -> None:
        """HTTP status errors use canonical http_error format."""
        from processor.fetcher import ImageFetcher

        httpserver.expect_request("/notfound.jpg").respond_with_data(
            b"Not Found", status=404
        )

        fetcher = ImageFetcher()
        result = fetcher.fetch(httpserver.url_for("/notfound.jpg"))

        assert result.success is False
        assert result.error_message.startswith("http_error: status_")
        assert "404" in result.error_message

    def test_scrapy_downloader_http_error_canonical(self) -> None:
        """ScrapyImageDownloader uses canonical format for HTTP errors."""
        from scrapy.http import Response

        from processor.async_fetcher import ScrapyImageDownloader

        response = Response(
            url="https://example.com/test.jpg",
            status=403,
            body=b"Forbidden",
            headers={b"Content-Type": b"image/jpeg"},
        )

        downloader = ScrapyImageDownloader()
        result = downloader.process_response("https://example.com/test.jpg", response)

        assert result.success is False
        assert result.error_message == "http_error: status_403"


class TestCrawlRunsPersistence:
    """Test that crawl_runs.images_downloaded is persisted to DB."""

    def test_spider_close_updates_crawl_runs(self) -> None:
        """Spider close persists images_downloaded to crawl_runs table."""
        from crawler.spiders.discovery_spider import DiscoverySpider
        from storage.db import get_cursor

        spider = DiscoverySpider(seeds=None)
        spider.pages_crawled = 5
        spider.images_found = 10

        # Create a crawl run
        with get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO crawl_runs (mode, status, seed_source)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                ("discovery", "running", "test"),
            )
            crawl_run_id = cursor.fetchone()[0]
            spider.crawl_run_id = crawl_run_id

            # Insert mock crawl_log entries with images_downloaded
            cursor.execute(
                """
                INSERT INTO crawl_log (page_url, domain, status, images_found, images_downloaded, crawl_run_id, crawl_type)
                VALUES
                    ('https://example.com/page1', 'example.com', 200, 3, 2, %s, 'discovery'),
                    ('https://example.com/page2', 'example.com', 200, 7, 5, %s, 'discovery')
                """,
                (crawl_run_id, crawl_run_id),
            )

        # Call spider close
        spider.closed("finished")

        # Verify crawl_runs was updated
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT pages_crawled, images_found, images_downloaded, status FROM crawl_runs WHERE id = %s",
                (crawl_run_id,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == 5  # pages_crawled
            assert row[1] == 10  # images_found
            assert row[2] == 7  # images_downloaded (2 + 5 from crawl_log)
            assert row[3] == "completed"

        # Verify spider log counter matches DB
        assert spider.images_downloaded == 7

        # Cleanup
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM crawl_log WHERE crawl_run_id = %s", (crawl_run_id,))
            cursor.execute("DELETE FROM crawl_runs WHERE id = %s", (crawl_run_id,))

