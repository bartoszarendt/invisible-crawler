"""Logging configuration for InvisibleCrawler.

Provides structured logging with JSON formatting support for production
and human-readable formatting for development.
"""

import json
import logging
import sys
from datetime import datetime
from typing import Any


class StructuredLogFormatter(logging.Formatter):
    """Structured JSON formatter for production logging.

    Outputs log records as JSON objects with consistent fields:
    - timestamp: ISO format timestamp
    - level: Log level name
    - logger: Logger name
    - message: Log message
    - context: Additional structured context data
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Args:
            record: LogRecord to format.

        Returns:
            JSON formatted log string.
        """
        log_data: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
            ):
                log_data[key] = value

        return json.dumps(log_data, default=str)


class CrawlStatistics:
    """Collects and reports crawl statistics.

    Tracks key metrics during crawling operations:
    - Pages crawled
    - Images discovered
    - Images downloaded
    - Images failed
    - Bytes downloaded
    - Errors encountered

    Attributes:
        start_time: When statistics collection started.
        pages_crawled: Number of pages successfully crawled.
        pages_failed: Number of pages that failed to crawl.
        images_discovered: Total images found across all pages.
        images_downloaded: Successfully downloaded images.
        images_failed: Failed image downloads.
        images_deduplicated: Duplicate images found.
        total_bytes: Total bytes downloaded.
        errors: List of error messages encountered.
    """

    def __init__(self) -> None:
        """Initialize statistics collector."""
        self.start_time: datetime = datetime.utcnow()
        self.pages_crawled: int = 0
        self.pages_failed: int = 0
        self.images_discovered: int = 0
        self.images_downloaded: int = 0
        self.images_skipped: int = 0
        self.images_failed: int = 0
        self.images_deduplicated: int = 0
        self.total_bytes: int = 0
        self.errors: list[str] = []

    def record_page_crawled(self, url: str, images_found: int) -> None:
        """Record a successfully crawled page.

        Args:
            url: Page URL that was crawled.
            images_found: Number of images found on the page.
        """
        self.pages_crawled += 1
        self.images_discovered += images_found

    def record_page_failed(self, url: str, error: str) -> None:
        """Record a failed page crawl.

        Args:
            url: Page URL that failed.
            error: Error message.
        """
        self.pages_failed += 1
        self.errors.append(f"Page failed {url}: {error}")

    def record_image_downloaded(self, bytes_downloaded: int) -> None:
        """Record a successful image download.

        Args:
            bytes_downloaded: Size of downloaded image in bytes.
        """
        self.images_downloaded += 1
        self.total_bytes += bytes_downloaded

    def record_image_failed(self, error: str) -> None:
        """Record a failed image download.

        Args:
            error: Error message.
        """
        self.images_failed += 1
        self.errors.append(f"Image failed: {error}")

    def record_image_deduplicated(self) -> None:
        """Record a deduplicated image."""
        self.images_deduplicated += 1

    def record_image_skipped(self) -> None:
        """Record a skipped image (failed validation)."""
        self.images_skipped += 1

    def get_summary(self) -> dict[str, Any]:
        """Get statistics summary as dictionary.

        Returns:
            Dictionary with statistics summary.
        """
        duration = (datetime.utcnow() - self.start_time).total_seconds()

        return {
            "duration_seconds": round(duration, 2),
            "pages_crawled": self.pages_crawled,
            "pages_failed": self.pages_failed,
            "images_discovered": self.images_discovered,
            "images_downloaded": self.images_downloaded,
            "images_skipped": self.images_skipped,
            "images_failed": self.images_failed,
            "images_deduplicated": self.images_deduplicated,
            "total_bytes": self.total_bytes,
            "error_count": len(self.errors),
        }

    def log_summary(self, logger: logging.Logger) -> None:
        """Log statistics summary.

        Args:
            logger: Logger instance to use.
        """
        summary = self.get_summary()

        logger.info("=" * 60)
        logger.info("CRAWL STATISTICS SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Duration: {summary['duration_seconds']:.2f} seconds")
        logger.info(f"Pages crawled: {summary['pages_crawled']}")
        logger.info(f"Pages failed: {summary['pages_failed']}")
        logger.info(f"Images discovered: {summary['images_discovered']}")
        logger.info(f"Images downloaded: {summary['images_downloaded']}")
        logger.info(f"Images skipped: {summary['images_skipped']}")
        logger.info(f"Images failed: {summary['images_failed']}")
        logger.info(f"Images deduplicated: {summary['images_deduplicated']}")
        logger.info(f"Total bytes: {summary['total_bytes']:,}")
        logger.info(f"Errors: {summary['error_count']}")
        logger.info("=" * 60)


def setup_logging(
    level: int = logging.INFO,
    json_format: bool = False,
    log_file: str | None = None,
) -> None:
    """Set up logging configuration.

    Args:
        level: Logging level (default: INFO).
        json_format: Use JSON formatting for structured logs.
        log_file: Optional file path to write logs to.
    """
    handlers: list[logging.Handler] = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if json_format:
        console_handler.setFormatter(StructuredLogFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    handlers.append(console_handler)

    # File handler
    if log_file:
        file_handler = logging.FileHandler(log_file)
        if json_format:
            file_handler.setFormatter(StructuredLogFormatter())
        else:
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
        handlers.append(file_handler)

    # Configure root logger
    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,
    )

    # Set third-party loggers to WARNING to reduce noise
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("scrapy").setLevel(logging.WARNING)
