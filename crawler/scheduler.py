"""Redis-based URL frontier scheduler for InvisibleCrawler.

Provides persistent URL queueing with per-domain priority queues,
deduplication, and crawl resume capabilities.
"""

import logging
from typing import Any
from urllib.parse import urlparse

from scrapy.http import Request
from scrapy.settings import Settings
from scrapy.spiders import Spider
from scrapy.utils.misc import load_object
from scrapy_redis.queue import PriorityQueue
from scrapy_redis.scheduler import Scheduler as RedisScheduler

from crawler.redis_keys import domains_key
from env_config import get_redis_url

logger = logging.getLogger(__name__)


class InvisibleRedisScheduler(RedisScheduler):
    """Redis-based scheduler with per-domain queue support.

    Extends scrapy-redis Scheduler to provide:
    - Per-domain priority queues for better crawl control
    - Separate lanes for discovery vs refresh crawls
    - Queue depth observability

    Note: Redis is required for this scheduler. Use check_redis_available()
    to verify connectivity before starting crawls.

    Attributes:
        redis_url: Redis connection URL.
        persist: Whether to persist queues on spider close.
        flush_on_start: Whether to flush queues on spider start.
    """

    def __init__(
        self,
        server: Any,
        persist: bool = True,
        flush_on_start: bool = False,
        queue_key: str = "%(spider)s:requests",
        queue_cls: str = "crawler.scheduler.DomainPriorityQueue",
        dupefilter_key: str = "%(spider)s:dupefilter",
        dupefilter_cls: str = "scrapy_redis.dupefilter.RFPDupeFilter",
        idle_before_close: int = 0,
        serializer: Any = None,
        **kwargs: Any,
    ):
        """Initialize the Redis scheduler.

        Args:
            server: Redis server connection.
            persist: Whether to persist queues on spider close.
            flush_on_start: Whether to flush queues on spider start.
            queue_key: Redis key pattern for queues.
            queue_cls: Queue class to use.
            dupefilter_key: Redis key pattern for dupefilter.
            dupefilter_cls: DupeFilter class to use.
            idle_before_close: Idle time before closing spider.
            serializer: Serializer for queue items.
            **kwargs: Additional arguments.
        """
        super().__init__(
            server=server,
            persist=persist,
            flush_on_start=flush_on_start,
            queue_key=queue_key,
            queue_cls=queue_cls,
            dupefilter_key=dupefilter_key,
            dupefilter_cls=dupefilter_cls,
            idle_before_close=idle_before_close,
            serializer=serializer,
        )
        self.stats: dict[str, int] = {
            "urls_scheduled": 0,
            "urls_deduplicated": 0,
            "domains_tracked": 0,
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> "InvisibleRedisScheduler":
        """Create scheduler instance from Scrapy settings.

        Args:
            settings: Scrapy settings object.

        Returns:
            New scheduler instance.
        """
        kwargs = {
            "persist": settings.getbool("SCHEDULER_PERSIST", True),
            "flush_on_start": settings.getbool("SCHEDULER_FLUSH_ON_START", False),
            "idle_before_close": settings.getint("SCHEDULER_IDLE_BEFORE_CLOSE", 0),
            "serializer": load_object(
                settings.get("SCHEDULER_SERIALIZER", "scrapy_redis.picklecompat")
            ),
        }

        # Optional load balancing with priority queue
        queue_cls = load_object(
            settings.get("SCHEDULER_QUEUE_CLASS", "crawler.scheduler.DomainPriorityQueue")
        )
        kwargs["queue_cls"] = queue_cls

        # Load dupefilter class
        dupefilter_cls = load_object(
            settings.get("DUPEFILTER_CLASS", "scrapy_redis.dupefilter.RFPDupeFilter")
        )
        kwargs["dupefilter_cls"] = dupefilter_cls

        # Load queue and dupefilter keys
        kwargs["queue_key"] = settings.get("SCHEDULER_QUEUE_KEY", "%(spider)s:requests")
        kwargs["dupefilter_key"] = settings.get("DUPEFILTER_KEY", "%(spider)s:dupefilter")

        # Get Redis connection
        from scrapy_redis import connection

        server = connection.from_settings(settings)

        return cls(server=server, **kwargs)

    @classmethod
    def from_crawler(cls, crawler: Any) -> "InvisibleRedisScheduler":
        """Create scheduler instance from crawler.

        Args:
            crawler: Scrapy crawler instance.

        Returns:
            New scheduler instance.
        """
        instance = cls.from_settings(crawler.settings)
        instance.stats = {
            "urls_scheduled": 0,
            "urls_deduplicated": 0,
            "domains_tracked": 0,
        }
        return instance

    def open(self, spider: Spider) -> None:
        """Open the scheduler for the given spider.

        Args:
            spider: The spider instance.
        """
        super().open(spider)
        spider.logger.info(f"Redis scheduler opened. Persist: {self.persist}")

        # Log queue status
        queue_length = len(self.queue)
        spider.logger.info(f"Queue length: {queue_length}")

    def close(self, reason: str) -> None:
        """Close the scheduler.

        Args:
            reason: Why the spider is closing.
        """
        logger.info("=" * 50)
        logger.info("Redis Scheduler Statistics:")
        for key, value in self.stats.items():
            logger.info(f"  {key}: {value}")
        logger.info("=" * 50)

        if not self.persist:
            self.flush()

    def enqueue_request(self, request: Request) -> bool:
        """Add request to queue if not already seen.

        Args:
            request: Scrapy Request to schedule.

        Returns:
            True if request was added, False if duplicate.
        """
        if not request.dont_filter and self.df.request_seen(request):
            self.stats["urls_deduplicated"] += 1
            return False

        if self.stats:
            self.stats["urls_scheduled"] += 1

        # Track domain
        domain = urlparse(request.url).netloc
        if domain:
            self._track_domain(domain)

        # Adjust priority based on request meta (scrapy-redis uses request.priority)
        if request.meta.get("crawl_type") == "refresh":
            # Lower priority for refresh crawls
            request.priority -= 10

        # Push to queue (uses request.priority internally)
        self.queue.push(request)

        return True

    def next_request(self) -> Request | None:
        """Get next request from queue.

        Returns:
            Next Request or None if queue is empty.
        """
        request = self.queue.pop()
        return request

    def has_pending_requests(self) -> bool:
        """Check if there are pending requests.

        Returns:
            True if queue is not empty.
        """
        return len(self.queue) > 0

    def _track_domain(self, domain: str) -> None:
        """Track that a domain is being crawled.

        Args:
            domain: Domain netloc.
        """
        # Use Redis set to track unique domains
        key = domains_key(self.spider.name)
        added = self.server.sadd(key, domain)
        if added:
            self.stats["domains_tracked"] += 1

    def get_queue_depth(self) -> int:
        """Get current queue depth.

        Returns:
            Number of requests in queue.
        """
        return len(self.queue)

    def get_domain_queue_depth(self, domain: str) -> int:
        """Get queue depth for a specific domain.

        Args:
            domain: Domain netloc.

        Returns:
            Number of requests for domain.
        """
        # This requires the queue to support domain-specific counts
        # For now, return total queue depth
        if hasattr(self.queue, "get_domain_count"):
            return self.queue.get_domain_count(domain)
        return len(self.queue)

    def flush_domain(self, domain: str) -> int:
        """Remove all pending requests for a domain.

        Args:
            domain: Domain to flush.

        Returns:
            Number of requests removed.
        """
        # Note: This is a placeholder. Full implementation would
        # require iterating through queue and removing domain-specific URLs.
        key = domains_key(self.spider.name)
        self.server.srem(key, domain)
        return 0


class DomainPriorityQueue(PriorityQueue):
    """Priority queue with per-domain tracking.

    Extends scrapy-redis PriorityQueue to maintain per-domain
    request counts for observability.
    """

    def __init__(self, server: Any, spider: Spider, key: str, serializer: Any = None) -> None:
        """Initialize domain priority queue.

        Args:
            server: Redis server connection.
            spider: Scrapy spider instance.
            key: Redis key for the queue.
            serializer: Serializer for queue items.
        """
        super().__init__(server, spider, key, serializer)
        self.domain_counts_key = f"{key}:domain_counts"

    def push(self, request: Request) -> None:
        """Push request onto queue.

        Args:
            request: Scrapy Request to push (priority is read from request.priority).
        """
        # scrapy-redis PriorityQueue uses request.priority attribute
        super().push(request)

        # Track per-domain count
        domain = urlparse(request.url).netloc
        if domain:
            self.server.hincrby(self.domain_counts_key, domain, 1)

    def pop(self, timeout: int = 0) -> Request | None:
        """Pop request from queue.

        Args:
            timeout: Timeout in seconds (0 = non-blocking).

        Returns:
            Request or None if queue is empty.
        """
        request = super().pop(timeout)

        if request:
            # Decrement per-domain count
            domain = urlparse(request.url).netloc
            if domain:
                self.server.hincrby(self.domain_counts_key, domain, -1)

        return request

    def get_domain_count(self, domain: str) -> int:
        """Get pending request count for domain.

        Args:
            domain: Domain netloc.

        Returns:
            Number of pending requests.
        """
        count = self.server.hget(self.domain_counts_key, domain)
        return int(count) if count else 0

    def clear(self) -> None:
        """Clear queue and domain counts."""
        super().clear()
        self.server.delete(self.domain_counts_key)


def check_redis_available(url: str | None = None) -> bool:
    """Check if Redis is available at the given URL.

    Args:
        url: Redis URL (defaults to REDIS_URL env var).

    Returns:
        True if Redis is reachable, False otherwise.
    """
    import redis

    url = url or get_redis_url()
    try:
        client = redis.from_url(url, socket_connect_timeout=2)
        client.ping()
        return True
    except redis.ConnectionError:
        logger.warning(f"Redis not available at {url}")
        return False
    except Exception as e:
        logger.warning(f"Redis check failed: {e}")
        return False
