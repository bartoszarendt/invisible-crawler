"""Image fetching module for InvisibleCrawler.

Handles downloading images from URLs with validation and error handling.
"""

import hashlib
import logging
import os
from dataclasses import dataclass
from io import BytesIO

import requests
from PIL import Image

from processor.media_policy import (
    REJECTION_REASON_FILE_TOO_LARGE,
    REJECTION_REASON_FILE_TOO_SMALL,
    REJECTION_REASON_HTTP_ERROR,
    REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL,
    REJECTION_REASON_INVALID_IMAGE_PAYLOAD,
    format_rejection_reason,
    validate_content_type,
)

logger = logging.getLogger(__name__)

# User-Agent can be configured via environment variable for consistency with Scrapy settings
DEFAULT_USER_AGENT = "InvisibleCrawler/0.1 (Image fetcher for research)"
USER_AGENT = os.getenv("CRAWLER_USER_AGENT", DEFAULT_USER_AGENT)


@dataclass
class ImageFetchResult:
    """Result of fetching an image.

    Attributes:
        success: Whether the fetch was successful.
        url: The URL that was fetched.
        content: Raw binary content (if successful).
        content_type: MIME type from HTTP headers.
        file_size: Size in bytes.
        width: Image width in pixels (if parseable).
        height: Image height in pixels (if parseable).
        format: Image format (e.g., 'JPEG', 'PNG').
        sha256_hash: SHA-256 hash of the content.
        phash_hash: Perceptual hash (pHash) for similarity matching.
        dhash_hash: Difference hash (dHash) for similarity matching.
        error_message: Description of failure (if unsuccessful).
    """

    success: bool
    url: str
    content: bytes | None = None
    content_type: str | None = None
    file_size: int = 0
    width: int | None = None
    height: int | None = None
    format: str | None = None
    sha256_hash: str | None = None
    phash_hash: str | None = None
    dhash_hash: str | None = None
    error_message: str | None = None


class ImageFetcher:
    """Fetches and validates images from URLs.

    Implements conservative fetching with validation:
    - Content-type whitelisting
    - Size thresholds (min/max)
    - Dimension validation
    - Timeout and retry logic

    Attributes:
        min_file_size: Minimum file size in bytes (default: 1024 = 1KB).
        max_file_size: Maximum file size in bytes (default: 50MB).
        min_dimensions: Minimum width/height in pixels (default: 100x100).
        timeout: Request timeout in seconds (default: 30).
        session: Reusable requests Session for connection pooling.
    """

    # Removed: ALLOWED_CONTENT_TYPES now imported from media_policy

    def __init__(
        self,
        min_file_size: int = 1024,  # 1KB minimum
        max_file_size: int = 50 * 1024 * 1024,  # 50MB maximum
        min_width: int = 256,
        min_height: int = 256,
        timeout: int = 30,
    ) -> None:
        """Initialize the fetcher with validation thresholds.

        Args:
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
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
            }
        )

    def fetch(self, url: str) -> ImageFetchResult:
        """Fetch and validate an image from a URL.

        Args:
            url: The image URL to fetch.

        Returns:
            ImageFetchResult with success status and metadata.
        """
        logger.debug(f"Fetching image: {url}")

        try:
            # Make HTTP request with streaming for large files
            response = self.session.get(
                url,
                timeout=self.timeout,
                stream=True,
                allow_redirects=True,
            )
            response.raise_for_status()

        except requests.exceptions.Timeout:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=format_rejection_reason(REJECTION_REASON_HTTP_ERROR, "timeout"),
            )
        except requests.exceptions.ConnectionError as e:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=format_rejection_reason(REJECTION_REASON_HTTP_ERROR, f"connection_error: {type(e).__name__}"),
            )
        except requests.exceptions.HTTPError as e:
            # Extract status code if available
            status = getattr(e.response, "status_code", "unknown") if hasattr(e, "response") else "unknown"
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=format_rejection_reason(REJECTION_REASON_HTTP_ERROR, f"status_{status}"),
            )
        except requests.exceptions.RequestException as e:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=format_rejection_reason(REJECTION_REASON_HTTP_ERROR, f"request_exception: {type(e).__name__}"),
            )

        # Validate content type (strict: reject if missing or unsupported)
        content_type = response.headers.get("Content-Type", "")
        is_valid, error_reason = validate_content_type(content_type)
        if not is_valid:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=error_reason,
            )

        # Check file size from headers (if available)
        content_length = response.headers.get("Content-Length")
        if content_length:
            file_size = int(content_length)
            if file_size < self.min_file_size:
                return ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=format_rejection_reason(REJECTION_REASON_FILE_TOO_SMALL, f"{file_size} bytes"),
                )
            if file_size > self.max_file_size:
                return ImageFetchResult(
                    success=False,
                    url=url,
                    error_message=format_rejection_reason(REJECTION_REASON_FILE_TOO_LARGE, f"{file_size} bytes"),
                )

        # Read content with size validation
        try:
            content = self._read_content_with_limit(response)
        except ValueError as e:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=str(e),
            )

        # Validate minimum file size
        if len(content) < self.min_file_size:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=format_rejection_reason(REJECTION_REASON_FILE_TOO_SMALL, f"{len(content)} bytes"),
            )

        # Generate SHA-256 hash
        sha256_hash = hashlib.sha256(content).hexdigest()

        # Parse image dimensions and validate payload
        width, height, img_format = self._parse_image_dimensions(content)

        # If we cannot parse dimensions, reject as invalid payload
        if width is None or height is None:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=format_rejection_reason(REJECTION_REASON_INVALID_IMAGE_PAYLOAD, "cannot parse dimensions"),
            )

        # Validate dimensions
        if width < self.min_dimensions[0] or height < self.min_dimensions[1]:
            return ImageFetchResult(
                success=False,
                url=url,
                error_message=format_rejection_reason(REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL, f"{width}x{height}"),
            )

        logger.debug(f"Successfully fetched image: {url} ({len(content)} bytes)")

        return ImageFetchResult(
            success=True,
            url=url,
            content=content,
            content_type=content_type,
            file_size=len(content),
            width=width,
            height=height,
            format=img_format,
            sha256_hash=sha256_hash,
        )

    def _read_content_with_limit(self, response: requests.Response) -> bytes:
        """Read response content with size limit.

        Args:
            response: Requests Response object.

        Returns:
            Content as bytes.

        Raises:
            ValueError: If content exceeds max_file_size.
        """
        chunks = []
        total_size = 0

        for chunk in response.iter_content(chunk_size=8192):
            chunks.append(chunk)
            total_size += len(chunk)

            if total_size > self.max_file_size:
                raise ValueError(f"File exceeds maximum size: {self.max_file_size} bytes")

        return b"".join(chunks)

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

    def close(self) -> None:
        """Close the HTTP session."""
        self.session.close()
