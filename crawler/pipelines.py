"""Scrapy pipelines for InvisibleCrawler.

Pipelines process scraped items (images) and persist them to storage.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from scrapy.exceptions import DropItem
from scrapy.spiders import Spider

from processor.fetcher import ImageFetcher
from storage.db import get_cursor

logger = logging.getLogger(__name__)


class ImageProcessingPipeline:
    """Pipeline for processing discovered images.

    This pipeline receives image metadata from the spider,
    downloads the image, generates fingerprints, and stores
    metadata in the database.

    Attributes:
        stats: Dictionary tracking pipeline statistics.
        fetcher: ImageFetcher instance for downloading images.
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
        self.fetcher: ImageFetcher | None = None
        self.discovery_refresh_after_days = _get_int_env("DISCOVERY_REFRESH_AFTER_DAYS", 0)

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
        self.fetcher = ImageFetcher()

    def close_spider(self, spider: Spider) -> None:
        """Called when spider closes.

        Args:
            spider: The spider instance.
        """
        if self.fetcher:
            self.fetcher.close()

        # Close database connection pool to release resources
        from storage.db import close_all_connections

        close_all_connections()

        logger.info("=" * 50)
        logger.info("ImageProcessingPipeline Statistics:")
        for key, value in self.stats.items():
            logger.info(f"  {key}: {value}")
        logger.info("=" * 50)

    def process_item(self, item: dict[str, Any], spider: Spider) -> dict[str, Any]:
        """Process a scraped image item.

        Downloads the image, generates SHA-256 fingerprint,
        and stores metadata in database.

        Args:
            item: Dictionary containing image metadata.
            spider: The spider that yielded the item.

        Returns:
            The processed item.

        Raises:
            DropItem: If item should be discarded.
        """
        if item.get("type") != "image":
            return item

        self.stats["images_received"] += 1

        try:
            # Download and process the image
            result = self._process_image(item)

            if result["status"] == "downloaded":
                self.stats["images_downloaded"] += 1
                self.stats["total_bytes_downloaded"] += result.get("file_size", 0)
            elif result["status"] == "skipped":
                self.stats["images_skipped"] += 1
            elif result["status"] == "deduplicated":
                self.stats["images_deduplicated"] += 1
            elif result["status"] == "failed":
                self.stats["images_failed"] += 1

            logger.debug(f"Processed image: {item.get('url')} ({result['status']})")

        except Exception as e:
            self.stats["images_failed"] += 1
            logger.error(f"Failed to process image {item.get('url')}: {e}")
            # Don't raise DropItem on interruption-related errors
            if "shutdown" in str(e).lower() or "interrupt" in str(e).lower():
                logger.info(f"Gracefully handling interruption for {item.get('url')}")
                return item
            raise DropItem(f"Failed to process image: {e}")

        return item

    def _process_image(self, item: dict[str, Any]) -> dict[str, Any]:
        """Download and process a single image.

        Args:
            item: Dictionary containing image metadata.

        Returns:
            Dictionary with processing result.
        """
        url = item.get("url", "")
        source_page = item.get("source_page", "")
        source_domain = item.get("source_domain", "")
        crawl_type = item.get("crawl_type", "discovery")

        if not self.fetcher:
            raise RuntimeError("ImageFetcher not initialized")

        if crawl_type == "discovery":
            existing = self._get_existing_image_by_url(url)
            if existing:
                image_id, last_seen_at = existing
                if not self._should_refresh(last_seen_at):
                    self._ensure_provenance(image_id, source_page, source_domain)
                    return {
                        "status": "skipped",
                        "image_id": str(image_id),
                        "reason": "already_seen",
                    }

        # Fetch the image
        fetch_result = self.fetcher.fetch(url)

        if not fetch_result.success:
            logger.warning(f"Failed to fetch image {url}: {fetch_result.error_message}")
            return {"status": "failed", "error": fetch_result.error_message}

        # Store image metadata in database
        return self._store_image_metadata(
            url=url,
            source_page=source_page,
            source_domain=source_domain,
            fetch_result=fetch_result,
        )

    def _store_image_metadata(
        self,
        url: str,
        source_page: str,
        source_domain: str,
        fetch_result: Any,
    ) -> dict[str, Any]:
        """Store image metadata in the database.

        Args:
            url: Image URL.
            source_page: Page where image was found.
            source_domain: Domain of source page.
            fetch_result: ImageFetchResult with image data.

        Returns:
            Dictionary with storage result.
        """
        try:
            with get_cursor() as cursor:
                # Check if image already exists by SHA-256 hash
                cursor.execute(
                    "SELECT id FROM images WHERE sha256_hash = %s", (fetch_result.sha256_hash,)
                )
                existing = cursor.fetchone()

                if existing:
                    # Image already exists (exact duplicate)
                    image_id = existing[0]

                    # Update last_seen timestamp
                    cursor.execute(
                        "UPDATE images SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (image_id,),
                    )

                    logger.debug(f"Image already exists (hash match): {url} -> {image_id}")
                    status = "deduplicated"
                else:
                    # Insert new image record
                    cursor.execute(
                        """
                        INSERT INTO images (
                            url, sha256_hash, width, height, format,
                            content_type, file_size_bytes, download_success
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                        ),
                    )
                    image_id = cursor.fetchone()[0]
                    logger.debug(f"Inserted new image: {url} -> {image_id}")
                    status = "downloaded"

                # Add provenance record
                self._ensure_provenance(image_id, source_page, source_domain)

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
                    logger.warning(f"Image {image_id} not found in database, skipping provenance insertion")
                    return

                cursor.execute(
                    """
                    INSERT INTO provenance (image_id, source_page_url, source_domain)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (image_id, source_page_url) DO NOTHING
                    """,
                    (image_id, source_page, source_domain),
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


def _get_int_env(name: str, default: int) -> int:
    """Parse an int environment variable with a safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
