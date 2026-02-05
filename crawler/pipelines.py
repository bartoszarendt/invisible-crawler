"""Scrapy pipelines for InvisibleCrawler.

Pipelines process scraped items (images) and persist them to storage.
"""

import logging
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

        if not self.fetcher:
            raise RuntimeError("ImageFetcher not initialized")

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

                    logger.debug(f"Image already exists (hash match): {url}")
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
                    logger.debug(f"Inserted new image: {url}")
                    status = "downloaded"

                # Add provenance record
                cursor.execute(
                    """
                    INSERT INTO provenance (image_id, source_page_url, source_domain)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (image_id, source_page, source_domain),
                )

                return {
                    "status": status,
                    "image_id": str(image_id),
                    "file_size": fetch_result.file_size,
                }

        except Exception as e:
            logger.error(f"Database error storing image {url}: {e}")
            raise
