"""Scrapy pipelines for InvisibleCrawler.

Pipelines process scraped items (images) and persist them to storage.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from scrapy.exceptions import DropItem
from scrapy.spiders import Spider

from env_config import (
    get_discovery_refresh_after_days,
    get_image_min_height,
    get_image_min_width,
)
from processor.async_fetcher import ScrapyImageDownloader
from processor.fetcher import ImageFetcher, ImageFetchResult
from processor.media_policy import (
    REJECTION_REASON_FILE_TOO_LARGE,
    REJECTION_REASON_FILE_TOO_SMALL,
    REJECTION_REASON_HTTP_ERROR,
    REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL,
    REJECTION_REASON_INVALID_IMAGE_PAYLOAD,
    REJECTION_REASON_MISSING_CONTENT_TYPE,
    REJECTION_REASON_MISSING_RESPONSE,
    REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE,
)
from storage.db import get_cursor

logger = logging.getLogger(__name__)


class ImageProcessingPipeline:
    """Pipeline for processing discovered images.

    This pipeline receives image metadata from the spider,
    downloads the image (using async fetching via Scrapy's downloader),
    generates fingerprints, and stores metadata in the database.

    Attributes:
        stats: Dictionary tracking pipeline statistics.
        downloader: ScrapyImageDownloader for processing responses.
        sync_fetcher: ImageFetcher for synchronous fallback.
        discovery_refresh_after_days: Days before refreshing existing images.
    """

    def __init__(self) -> None:
        """Initialize the pipeline."""
        self.stats: dict[str, Any] = {
            "images_received": 0,
            "images_downloaded": 0,
            "images_skipped": 0,
            "images_failed": 0,
            "images_deduplicated": 0,
            "total_bytes_downloaded": 0,
        }
        # Rejection reason counters (structured metrics)
        self.rejection_stats: dict[str, int] = {
            REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE: 0,
            REJECTION_REASON_MISSING_CONTENT_TYPE: 0,
            REJECTION_REASON_FILE_TOO_SMALL: 0,
            REJECTION_REASON_FILE_TOO_LARGE: 0,
            REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL: 0,
            REJECTION_REASON_INVALID_IMAGE_PAYLOAD: 0,
            REJECTION_REASON_HTTP_ERROR: 0,
            REJECTION_REASON_MISSING_RESPONSE: 0,
        }
        self.downloader: ScrapyImageDownloader | None = None
        self.sync_fetcher: ImageFetcher | None = None
        self.discovery_refresh_after_days = get_discovery_refresh_after_days()
        self.image_min_width = get_image_min_width()
        self.image_min_height = get_image_min_height()

    @classmethod
    def from_crawler(cls, crawler: Any) -> "ImageProcessingPipeline":
        """Create pipeline instance from crawler.

        Args:
            crawler: Scrapy Crawler instance.

        Returns:
            New pipeline instance.
        """
        return cls()

    def open_spider(self, spider: Spider) -> None:
        """Called when spider opens.

        Args:
            spider: The spider instance.
        """
        logger.info("ImageProcessingPipeline opened")
        self.stats = {
            "images_received": 0,
            "images_downloaded": 0,
            "images_skipped": 0,
            "images_failed": 0,
            "images_deduplicated": 0,
            "total_bytes_downloaded": 0,
        }
        # Reset rejection counters
        self.rejection_stats = {
            REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE: 0,
            REJECTION_REASON_MISSING_CONTENT_TYPE: 0,
            REJECTION_REASON_FILE_TOO_SMALL: 0,
            REJECTION_REASON_FILE_TOO_LARGE: 0,
            REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL: 0,
            REJECTION_REASON_INVALID_IMAGE_PAYLOAD: 0,
            REJECTION_REASON_HTTP_ERROR: 0,
            REJECTION_REASON_MISSING_RESPONSE: 0,
        }
        # Use async downloader that integrates with Scrapy
        self.downloader = ScrapyImageDownloader(
            min_width=self.image_min_width,
            min_height=self.image_min_height,
        )
        # Keep sync fetcher for testing/standalone use
        self.sync_fetcher = ImageFetcher(
            min_width=self.image_min_width,
            min_height=self.image_min_height,
        )

    def close_spider(self, spider: Spider) -> None:
        """Called when spider closes.

        Args:
            spider: The spider instance.
        """
        if self.sync_fetcher:
            self.sync_fetcher.close()

        # Close database connection pool to release resources
        from storage.db import close_all_connections

        close_all_connections()

        logger.info("=" * 50)
        logger.info("ImageProcessingPipeline Statistics:")
        for key, value in self.stats.items():
            logger.info(f"  {key}: {value}")
        logger.info("Rejection Reasons:")
        for reason, count in self.rejection_stats.items():
            if count > 0:
                logger.info(f"  {reason}: {count}")
        logger.info("=" * 50)

    def process_item(self, item: dict[str, Any], spider: Spider) -> Any:
        """Process a scraped image item.

        Receives items with already-downloaded image responses from spider.
        Validates, fingerprints, and stores metadata.

        Args:
            item: Dictionary containing image metadata and response.
            spider: The spider that yielded the item.

        Returns:
            The processed item.

        Raises:
            DropItem: If item should be discarded.
        """
        if item.get("type") != "image":
            return item

        self.stats["images_received"] += 1

        url = item.get("url", "")
        source_page = item.get("source_page", "")
        source_domain = item.get("source_domain", "")
        crawl_type = item.get("crawl_type", "discovery")
        response = item.get("response")  # Spider attaches Scrapy Response

        if not response:
            self.stats["images_failed"] += 1
            self._increment_rejection_reason(REJECTION_REASON_MISSING_RESPONSE)
            logger.error(f"Image item missing response: {url}")
            raise DropItem("Missing response object")

        # Check if we should skip this image (discovery mode only)
        if crawl_type == "discovery":
            existing = self._get_existing_image_by_url(url)
            if existing:
                image_id, last_seen_at = existing
                if not self._should_refresh(last_seen_at):
                    self._ensure_provenance(image_id, source_page, source_domain)
                    self.stats["images_skipped"] += 1
                    logger.debug(f"Skipped image (already seen): {url}")
                    return item

        # Process the response into ImageFetchResult
        if self.downloader is None:
            raise RuntimeError("Downloader not initialized")

        fetch_result = self.downloader.process_response(url, response)

        if not fetch_result.success:
            self.stats["images_failed"] += 1
            # Classify and count rejection reason
            self._classify_and_count_rejection(fetch_result.error_message)
            logger.warning(f"Failed to validate image {url}: {fetch_result.error_message}")
            raise DropItem(f"Image validation failed: {fetch_result.error_message}")

        # Store image metadata in database
        try:
            result = self._store_image_metadata(
                url=url,
                source_page=source_page,
                source_domain=source_domain,
                fetch_result=fetch_result,
                crawl_run_id=item.get("crawl_run_id"),
                crawl_type=crawl_type,
            )

            if result["status"] == "downloaded":
                self.stats["images_downloaded"] += 1
                self.stats["total_bytes_downloaded"] += fetch_result.file_size
            elif result["status"] == "deduplicated":
                self.stats["images_deduplicated"] += 1

        except Exception as e:
            self.stats["images_failed"] += 1
            logger.error(f"Failed to store image {url}: {e}")
            raise DropItem(f"Failed to store image: {e}")

        return item

    def _store_image_metadata(
        self,
        url: str,
        source_page: str,
        source_domain: str,
        fetch_result: ImageFetchResult,
        crawl_run_id: Any = None,
        crawl_type: str = "discovery",
    ) -> dict[str, Any]:
        """Store image metadata in the database.

        Args:
            url: Image URL.
            source_page: Page where image was found.
            source_domain: Domain of source page.
            fetch_result: ImageFetchResult with image data.
            crawl_run_id: Optional crawl run ID for stats tracking.
            crawl_type: Type of crawl (discovery or refresh).

        Returns:
            Dictionary with storage result.
        """
        try:
            with get_cursor() as cursor:
                # First check by URL to handle URL/hash conflicts properly
                cursor.execute(
                    "SELECT id, sha256_hash FROM images WHERE url = %s", (url,)
                )
                url_existing = cursor.fetchone()

                if url_existing:
                    # URL exists - check if hash changed (refresh scenario)
                    existing_id, existing_hash = url_existing
                    if existing_hash == fetch_result.sha256_hash:
                        # Same URL, same hash - just update last_seen
                        cursor.execute(
                            "UPDATE images SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s",
                            (existing_id,),
                        )
                        image_id = existing_id
                        status = "deduplicated"
                    else:
                        # Same URL, different hash - update metadata (content changed)
                        cursor.execute(
                            """
                            UPDATE images
                            SET sha256_hash = %s,
                                width = %s,
                                height = %s,
                                format = %s,
                                content_type = %s,
                                file_size_bytes = %s,
                                phash_hash = %s,
                                dhash_hash = %s,
                                last_seen_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """,
                            (
                                fetch_result.sha256_hash,
                                fetch_result.width,
                                fetch_result.height,
                                fetch_result.format,
                                fetch_result.content_type,
                                fetch_result.file_size,
                                fetch_result.phash_hash,
                                fetch_result.dhash_hash,
                                existing_id,
                            ),
                        )
                        image_id = existing_id
                        status = "downloaded"  # Count as new download since content changed
                        logger.info(f"Updated image with changed hash: {url} -> {image_id}")
                else:
                    # Check if image exists by hash (different URL, same content)
                    cursor.execute(
                        "SELECT id FROM images WHERE sha256_hash = %s", (fetch_result.sha256_hash,)
                    )
                    hash_existing = cursor.fetchone()

                    if hash_existing:
                        # Different URL, same hash - just link provenance
                        image_id = hash_existing[0]
                        cursor.execute(
                            "UPDATE images SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s",
                            (image_id,),
                        )
                        status = "deduplicated"
                        logger.debug(f"Image exists with different URL (hash match): {url} -> {image_id}")
                    else:
                        # Completely new image
                        cursor.execute(
                            """
                            INSERT INTO images (
                                url, sha256_hash, width, height, format,
                                content_type, file_size_bytes, download_success,
                                phash_hash, dhash_hash
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id
                            """,
                            (
                                url,
                                fetch_result.sha256_hash,
                                fetch_result.width,
                                fetch_result.height,
                                fetch_result.format,
                                fetch_result.content_type,
                                fetch_result.file_size,
                                True,
                                fetch_result.phash_hash,
                                fetch_result.dhash_hash,
                            ),
                        )
                        image_id = cursor.fetchone()[0]
                        status = "downloaded"
                        logger.debug(f"Inserted new image: {url} -> {image_id}")

                # Add provenance record within same transaction
                cursor.execute(
                    """
                    INSERT INTO provenance (image_id, source_page_url, source_domain, discovery_type)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (image_id, source_page_url) DO UPDATE
                    SET discovered_at = CURRENT_TIMESTAMP,
                        discovery_type = EXCLUDED.discovery_type
                    """,
                    (image_id, source_page, source_domain, crawl_type),
                )

                # Increment crawl_log.images_downloaded for this page (if crawl_run_id provided)
                if crawl_run_id and status == "downloaded":
                    cursor.execute(
                        """
                        UPDATE crawl_log
                        SET images_downloaded = images_downloaded + 1
                        WHERE page_url = %s AND crawl_run_id = %s
                        """,
                        (source_page, crawl_run_id),
                    )
                    rows_updated = cursor.rowcount
                    if rows_updated == 0:
                        logger.debug(
                            f"No crawl_log entry found for page {source_page} (run {crawl_run_id})"
                        )

                return {
                    "status": status,
                    "image_id": str(image_id),
                    "file_size": fetch_result.file_size,
                }

        except Exception as e:
            logger.error(f"Database error storing image {url}: {e}")
            raise

    def _get_existing_image_by_url(self, url: str) -> tuple[Any, Any] | None:
        """Return existing image id and last_seen_at for a URL if present."""
        try:
            with get_cursor() as cursor:
                cursor.execute(
                    "SELECT id, last_seen_at FROM images WHERE url = %s",
                    (url,),
                )
                row = cursor.fetchone()
                if row:
                    return row[0], row[1]
        except Exception as e:
            logger.warning(f"Failed to check existing image for {url}: {e}")
        return None

    def _ensure_provenance(self, image_id: Any, source_page: str, source_domain: str) -> None:
        """Insert provenance row if it does not already exist."""
        try:
            with get_cursor() as cursor:
                # First verify the image still exists (defensive check)
                cursor.execute("SELECT 1 FROM images WHERE id = %s", (image_id,))
                exists = cursor.fetchone()
                if not exists:
                    logger.warning(
                        f"Image {image_id} not found in database, skipping provenance insertion"
                    )
                    return

                cursor.execute(
                    """
                    INSERT INTO provenance (image_id, source_page_url, source_domain, discovery_type)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (image_id, source_page_url) DO UPDATE
                    SET discovered_at = CURRENT_TIMESTAMP,
                        discovery_type = EXCLUDED.discovery_type
                    """,
                    (image_id, source_page, source_domain, "discovery"),
                )
                logger.debug(f"Successfully inserted provenance for image {image_id}")
        except Exception as e:
            logger.warning(f"Failed to insert provenance for {image_id}: {e}")
            # Don't re-raise the exception to avoid crashing the pipeline

    def _should_refresh(self, last_seen_at: datetime | None) -> bool:
        """Determine if an image should be refreshed based on last_seen_at."""
        if self.discovery_refresh_after_days <= 0 or last_seen_at is None:
            return False
        threshold = datetime.now(UTC) - timedelta(days=self.discovery_refresh_after_days)
        return last_seen_at < threshold

    def _increment_rejection_reason(self, reason: str) -> None:
        """Increment rejection counter for a specific reason.

        Args:
            reason: Structured rejection reason constant.
        """
        if reason in self.rejection_stats:
            self.rejection_stats[reason] += 1
        else:
            # Track unknown reasons as well
            self.rejection_stats[reason] = 1
            logger.debug(f"New rejection reason tracked: {reason}")

    def _classify_and_count_rejection(self, error_message: str | None) -> None:
        """Classify and count rejection reason from error message.

        Parses structured error messages like 'unsupported_content_type: image/svg+xml'
        and increments the appropriate counter.

        Args:
            error_message: Error message from fetch result.
        """
        if not error_message:
            self._increment_rejection_reason("unknown")
            return

        # Extract structured reason (format: "reason: details")
        parts = error_message.split(":", 1)
        reason = parts[0].strip().lower()

        # Map to known rejection reasons
        known_reasons = {
            REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE,
            REJECTION_REASON_MISSING_CONTENT_TYPE,
            REJECTION_REASON_FILE_TOO_SMALL,
            REJECTION_REASON_FILE_TOO_LARGE,
            REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL,
            REJECTION_REASON_INVALID_IMAGE_PAYLOAD,
            REJECTION_REASON_HTTP_ERROR,
            REJECTION_REASON_MISSING_RESPONSE,
        }

        if reason in known_reasons:
            self._increment_rejection_reason(reason)
        else:
            # Fallback: try to infer from message
            msg_lower = error_message.lower()
            if "content type" in msg_lower or "content-type" in msg_lower:
                self._increment_rejection_reason(REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE)
            elif "too small" in msg_lower and "dimension" in msg_lower:
                self._increment_rejection_reason(REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL)
            elif "too small" in msg_lower:
                self._increment_rejection_reason(REJECTION_REASON_FILE_TOO_SMALL)
            elif "too large" in msg_lower:
                self._increment_rejection_reason(REJECTION_REASON_FILE_TOO_LARGE)
            elif "http" in msg_lower or "status" in msg_lower:
                self._increment_rejection_reason(REJECTION_REASON_HTTP_ERROR)
            else:
                self._increment_rejection_reason("unknown")
