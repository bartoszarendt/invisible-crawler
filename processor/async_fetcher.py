"""Async image fetching module for InvisibleCrawler.

Provides asynchronous image downloading using Twisted's HTTP client
to avoid blocking Scrapy's reactor during image fetches.
"""

import hashlib
import logging
from io import BytesIO
from typing import Any

from PIL import Image
from scrapy.http import Response
from twisted.internet import defer
from twisted.web.client import Agent, readBody
from twisted.web.http_headers import Headers

from processor.fetcher import ImageFetchResult

logger = logging.getLogger(__name__)


class AsyncImageFetcher:
    """Asynchronously fetches and validates images from URLs.

    Uses Twisted's HTTP client to perform non-blocking downloads
    that integrate with Scrapy's Twisted reactor.

    Attributes:
        min_file_size: Minimum file size in bytes (default: 1024 = 1KB).
        max_file_size: Maximum file size in bytes (default: 50MB).
        min_dimensions: Minimum width/height in pixels (default: 256x256).
        timeout: Request timeout in seconds (default: 30).
        agent: Twisted Agent for HTTP requests.
    """

    # Allowed content types
    ALLOWED_CONTENT_TYPES = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "image/bmp",
        "image/tiff",
    }

    def __init__(
        self,
        reactor: Any,
        min_file_size: int = 1024,  # 1KB minimum
        max_file_size: int = 50 * 1024 * 1024,  # 50MB maximum
        min_width: int = 256,
        min_height: int = 256,
        timeout: int = 30,
    ) -> None:
        """Initialize the async fetcher with validation thresholds.

        Args:
            reactor: Twisted reactor instance.
            min_file_size: Minimum file size in bytes.
            max_file_size: Maximum file size in bytes.
            min_width: Minimum image width in pixels.
            min_height: Minimum image height in pixels.
            timeout: HTTP request timeout in seconds.
        """
        self.min_file_size = min_file_size
        self.max_file_size = max_file_size
        self.min_dimensions = (min_width, min_height)
        self.timeout = timeout
        self.agent = Agent(reactor)

    @defer.inlineCallbacks
    def fetch(self, url: str) -> defer.Deferred[ImageFetchResult]:
        """Fetch and validate an image from a URL asynchronously.

        Args:
            url: The image URL to fetch.

        Returns:
            Deferred that fires with ImageFetchResult.
        """
        logger.debug(f"Async fetching image: {url}")

        try:
            # Make async HTTP request
            response = yield self._request(url)
        except defer.TimeoutError:
            defer.returnValue(
                ImageFetchResult(
                    success=False,
                    url=url,
                    error_message="Request timeout",
                )
            )
            return
        except Exception as e:
            defer.returnValue(
                ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=f"Request failed: {e}",
                )
            )
            return

        # Check HTTP status
        if response.code != 200:
            defer.returnValue(
                ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=f"HTTP error: {response.code}",
                )
            )
            return

        # Validate content type from headers
        content_type_headers = response.headers.getRawHeaders(b"Content-Type", [])
        content_type = ""
        if content_type_headers:
            content_type = content_type_headers[0].decode("utf-8", errors="ignore").lower()
            content_type = content_type.split(";")[0].strip()

        if content_type and content_type not in self.ALLOWED_CONTENT_TYPES:
            defer.returnValue(
                ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=f"Invalid content type: {content_type}",
                )
            )
            return

        # Read response body
        try:
            body = yield readBody(response)
        except Exception as e:
            defer.returnValue(
                ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=f"Failed to read response body: {e}",
                )
            )
            return

        # Validate file size
        file_size = len(body)
        if file_size < self.min_file_size:
            defer.returnValue(
                ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=f"File too small: {file_size} bytes",
                )
            )
            return

        if file_size > self.max_file_size:
            defer.returnValue(
                ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=f"File too large: {file_size} bytes",
                )
            )
            return

        # Generate SHA-256 hash
        sha256_hash = hashlib.sha256(body).hexdigest()

        # Parse image dimensions
        width, height, img_format = self._parse_image_dimensions(body)

        # Validate dimensions if parseable
        if width and height:
            if width < self.min_dimensions[0] or height < self.min_dimensions[1]:
                defer.returnValue(
                    ImageFetchResult(
                        success=False,
                        url=url,
                        error_message=f"Image too small: {width}x{height}",
                    )
                )
                return

        logger.debug(f"Successfully fetched image: {url} ({file_size} bytes)")

        defer.returnValue(
            ImageFetchResult(
                success=True,
                url=url,
                content=body,
                content_type=content_type,
                file_size=file_size,
                width=width,
                height=height,
                format=img_format,
                sha256_hash=sha256_hash,
            )
        )

    def _request(self, url: str) -> defer.Deferred[Any]:
        """Make HTTP GET request using Twisted Agent.

        Args:
            url: URL to request.

        Returns:
            Deferred that fires with the response.
        """
        # Set up headers
        headers = Headers(
            {
                b"User-Agent": [b"InvisibleCrawler/0.1 (Async Image Fetcher)"],
                b"Accept": [b"image/*,*/*;q=0.8"],
            }
        )

        # Make request
        d = self.agent.request(b"GET", url.encode("utf-8"), headers, None)
        return d

    def _parse_image_dimensions(self, content: bytes) -> tuple[int | None, int | None, str | None]:
        """Parse image dimensions from binary content.

        Args:
            content: Raw image bytes.

        Returns:
            Tuple of (width, height, format) or (None, None, None) on failure.
        """
        try:
            img = Image.open(BytesIO(content))
            return img.width, img.height, img.format
        except Exception as e:
            logger.debug(f"Could not parse image dimensions: {e}")
            return None, None, None


class ScrapyImageDownloader:
    """Downloads images using Scrapy's built-in downloader.

    This approach leverages Scrapy's existing infrastructure:
    - Connection pooling via Scrapy's downloader
    - Retry logic and error handling
    - Politeness settings (delays, rate limits)
    - Middleware support (robots.txt, user-agent rotation, etc.)

    This is the recommended approach for Phase 2 as it provides
    the best integration with Scrapy's ecosystem.
    """

    # Allowed content types
    ALLOWED_CONTENT_TYPES = {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "image/bmp",
        "image/tiff",
    }

    def __init__(
        self,
        min_file_size: int = 1024,
        max_file_size: int = 50 * 1024 * 1024,
        min_width: int = 256,
        min_height: int = 256,
    ) -> None:
        """Initialize the downloader with validation thresholds.

        Args:
            min_file_size: Minimum file size in bytes.
            max_file_size: Maximum file size in bytes.
            min_width: Minimum image width in pixels.
            min_height: Minimum image height in pixels.
        """
        self.min_file_size = min_file_size
        self.max_file_size = max_file_size
        self.min_dimensions = (min_width, min_height)

    def process_response(self, url: str, response: Response) -> ImageFetchResult:
        """Process a Scrapy Response object into an ImageFetchResult.

        Args:
            url: The image URL.
            response: Scrapy Response object from image download.

        Returns:
            ImageFetchResult with parsed metadata.
        """
        # Check for download errors
        if response.status != 200:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=f"HTTP error: {response.status}",
            )

        # Validate content type
        content_type = response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore")
        content_type = content_type.split(";")[0].strip().lower()

        if content_type and content_type not in self.ALLOWED_CONTENT_TYPES:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=f"Invalid content type: {content_type}",
            )

        # Get content
        content = response.body
        file_size = len(content)

        # Validate file size
        if file_size < self.min_file_size:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=f"File too small: {file_size} bytes",
            )

        if file_size > self.max_file_size:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=f"File too large: {file_size} bytes",
            )

        # Generate SHA-256 hash
        sha256_hash = hashlib.sha256(content).hexdigest()

        # Parse image dimensions
        width, height, img_format = self._parse_image_dimensions(content)

        # Validate dimensions
        if width and height:
            if width < self.min_dimensions[0] or height < self.min_dimensions[1]:
                return ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=f"Image too small: {width}x{height}",
                )

        return ImageFetchResult(
            success=True,
            url=url,
            content=content,
            content_type=content_type,
            file_size=file_size,
            width=width,
            height=height,
            format=img_format,
            sha256_hash=sha256_hash,
        )

    def _parse_image_dimensions(self, content: bytes) -> tuple[int | None, int | None, str | None]:
        """Parse image dimensions from binary content.

        Args:
            content: Raw image bytes.

        Returns:
            Tuple of (width, height, format) or (None, None, None) on failure.
        """
        try:
            img = Image.open(BytesIO(content))
            return img.width, img.height, img.format
        except Exception as e:
            logger.debug(f"Could not parse image dimensions: {e}")
            return None, None, None


def create_fetch_result_from_scrapy_response(url: str, response: Response) -> ImageFetchResult:
    """Create an ImageFetchResult from a Scrapy response.

    Convenience function for processing image responses from Scrapy downloads.

    Args:
        url: The image URL.
        response: Scrapy Response object.

    Returns:
        ImageFetchResult with success status and metadata.
    """
    downloader = ScrapyImageDownloader()
    return downloader.process_response(url, response)
