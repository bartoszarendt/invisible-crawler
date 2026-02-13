"""Persistent dupefilter for URL fingerprinting.

Provides a Redis-backed request filter that persists across restarts,
enabling true resumability for long-running crawls.
"""

import hashlib
import logging
from collections.abc import Iterator
from typing import Any

from scrapy.dupefilters import BaseDupeFilter
from scrapy.utils.request import fingerprint

logger = logging.getLogger(__name__)


class PersistentRFPDupeFilter(BaseDupeFilter):
    """Request fingerprint dupefilter backed by Redis.

    Persists seen fingerprints to Redis to survive restarts.
    Uses SHA-256 fingerprints for consistency.
    """

    def __init__(self, redis_client: Any, key_prefix: str = "dupefilter") -> None:
        """Initialize the dupefilter.

        Args:
            redis_client: Redis client instance.
            key_prefix: Prefix for Redis keys.
        """
        self.redis = redis_client
        self.key_prefix = key_prefix
        self.fingerprints_key = f"{key_prefix}:fingerprints"

    @classmethod
    def from_settings(cls, settings: Any) -> "PersistentRFPDupeFilter":
        """Create dupefilter from Scrapy settings.

        Args:
            settings: Scrapy settings object.

        Returns:
            Configured dupefilter instance.
        """
        import redis

        redis_url = settings.get("REDIS_URL", "redis://localhost:6379/0")
        key_prefix = settings.get("DUPEFILTER_KEY_PREFIX", "dupefilter")
        client = redis.from_url(redis_url)  # type: ignore[no-untyped-call]
        return cls(client, key_prefix)

    def _get_fingerprint(self, request: Any) -> str:
        """Get fingerprint for a request.

        Args:
            request: Scrapy Request object.

        Returns:
            Hex-encoded SHA-256 fingerprint.
        """
        fp = fingerprint(request)
        return hashlib.sha256(fp).hexdigest()

    def request_seen(self, request: Any) -> bool:
        """Check if request has been seen before.

        Args:
            request: Scrapy Request object.

        Returns:
            True if request was seen before, False otherwise.
        """
        fp = self._get_fingerprint(request)

        if self.redis.sismember(self.fingerprints_key, fp):
            return True

        self.redis.sadd(self.fingerprints_key, fp)
        return False

    def open(self) -> None:
        """Called when spider opens.

        Initializes Redis connection if needed.
        """
        logger.info(f"Opened persistent dupefilter: {self.fingerprints_key}")

    def close(self, reason: str) -> None:
        """Called when spider closes.

        Args:
            reason: Why the spider closed.
        """
        logger.info(f"Closed persistent dupefilter: {reason}")

    def clear(self) -> None:
        """Clear all fingerprints."""
        self.redis.delete(self.fingerprints_key)
        logger.info("Cleared persistent dupefilter")

    def get_fingerprints(self) -> Iterator[str]:
        """Get all known fingerprints.

        Yields:
            Fingerprint strings.
        """
        for fp in self.redis.smembers(self.fingerprints_key):
            yield fp.decode("utf-8") if isinstance(fp, bytes) else fp
