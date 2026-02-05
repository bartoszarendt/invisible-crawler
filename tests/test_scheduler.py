"""Tests for Redis URL frontier scheduler."""

import os
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from scrapy.http import Request
from scrapy.spiders import Spider

from crawler.scheduler import (
    DomainPriorityQueue,
    InvisibleRedisScheduler,
    check_redis_available,
)


class MockRedis:
    """Mock Redis client for testing."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.sets: dict[str, set] = {}
        self.hashes: dict[str, dict] = {}

    def ping(self) -> bool:
        return True

    def sadd(self, key: str, *values: Any) -> int:
        if key not in self.sets:
            self.sets[key] = set()
        added = 0
        for value in values:
            if value not in self.sets[key]:
                self.sets[key].add(value)
                added += 1
        return added

    def srem(self, key: str, value: Any) -> int:
        if key in self.sets and value in self.sets[key]:
            self.sets[key].remove(value)
            return 1
        return 0

    def hincrby(self, key: str, field: str, increment: int = 1) -> int:
        if key not in self.hashes:
            self.hashes[key] = {}
        current = int(self.hashes[key].get(field, 0))
        new_value = current + increment
        self.hashes[key][field] = str(new_value)
        return new_value

    def hget(self, key: str, field: str) -> str | None:
        if key in self.hashes:
            return self.hashes[key].get(field)
        return None

    def delete(self, key: str) -> int:
        removed = 0
        if key in self.data:
            del self.data[key]
            removed += 1
        if key in self.sets:
            del self.sets[key]
            removed += 1
        if key in self.hashes:
            del self.hashes[key]
            removed += 1
        return removed


class TestInvisibleRedisScheduler:
    """Test cases for InvisibleRedisScheduler."""

    @pytest.fixture
    def mock_redis(self) -> MockRedis:
        """Create mock Redis client."""
        return MockRedis()

    @pytest.fixture
    def scheduler(self, mock_redis: MockRedis) -> InvisibleRedisScheduler:
        """Create scheduler with mock Redis."""
        return InvisibleRedisScheduler(
            server=mock_redis,
            persist=True,
            flush_on_start=False,
        )

    @pytest.fixture
    def mock_spider(self) -> Spider:
        """Create mock spider."""
        spider = MagicMock(spec=Spider)
        spider.name = "test_spider"
        spider.logger = MagicMock()
        spider.settings = MagicMock()
        spider.settings.get.return_value = None
        return spider

    def test_scheduler_initialization(self, scheduler: InvisibleRedisScheduler) -> None:
        """Test scheduler initializes with correct settings."""
        assert scheduler.persist is True
        assert scheduler.flush_on_start is False
        assert scheduler.stats["urls_scheduled"] == 0
        assert scheduler.stats["urls_deduplicated"] == 0
        assert scheduler.stats["domains_tracked"] == 0

    def test_enqueue_request_new_url(
        self, scheduler: InvisibleRedisScheduler, mock_spider: Spider
    ) -> None:
        """Test enqueuing a new request."""
        scheduler.open(mock_spider)

        request = Request(url="https://example.com/page1")
        result = scheduler.enqueue_request(request)

        assert result is True
        assert scheduler.stats["urls_scheduled"] == 1

    def test_enqueue_request_duplicate(
        self, scheduler: InvisibleRedisScheduler, mock_spider: Spider
    ) -> None:
        """Test that duplicate requests are filtered."""
        scheduler.open(mock_spider)

        # Mock dupefilter to mark second request as seen
        scheduler.df = MagicMock()
        scheduler.df.request_seen = Mock(side_effect=[False, True])

        request1 = Request(url="https://example.com/page1")
        request2 = Request(url="https://example.com/page1")

        result1 = scheduler.enqueue_request(request1)
        result2 = scheduler.enqueue_request(request2)

        assert result1 is True
        assert result2 is False
        assert scheduler.stats["urls_deduplicated"] == 1

    def test_track_domain(
        self, scheduler: InvisibleRedisScheduler, mock_spider: Spider, mock_redis: MockRedis
    ) -> None:
        """Test domain tracking."""
        scheduler.open(mock_spider)
        scheduler.spider = mock_spider

        scheduler._track_domain("example.com")
        scheduler._track_domain("example.com")  # Duplicate should not increment
        scheduler._track_domain("other.com")

        assert scheduler.stats["domains_tracked"] == 2
        assert "example.com" in mock_redis.sets.get("test_spider:domains", set())
        assert "other.com" in mock_redis.sets.get("test_spider:domains", set())

    def test_priority_for_refresh_crawls(
        self, scheduler: InvisibleRedisScheduler, mock_spider: Spider
    ) -> None:
        """Test that refresh crawls get lower priority."""
        scheduler.open(mock_spider)

        discovery_request = Request(
            url="https://example.com/page1", meta={"crawl_type": "discovery"}
        )
        refresh_request = Request(url="https://example.com/page2", meta={"crawl_type": "refresh"})

        # Both should be enqueued
        scheduler.enqueue_request(discovery_request)
        scheduler.enqueue_request(refresh_request)

        assert scheduler.stats["urls_scheduled"] == 2


class TestDomainPriorityQueue:
    """Test cases for DomainPriorityQueue."""

    @pytest.fixture
    def mock_redis(self) -> MockRedis:
        """Create mock Redis client."""
        return MockRedis()

    @pytest.fixture
    def mock_spider(self) -> Spider:
        """Create mock spider."""
        spider = MagicMock(spec=Spider)
        spider.name = "test_spider"
        return spider

    def test_domain_count_tracking(self, mock_redis: MockRedis, mock_spider: Spider) -> None:
        """Test per-domain request counting."""
        queue = DomainPriorityQueue(
            server=mock_redis,
            spider=mock_spider,
            key="test:queue",
        )

        # Push requests for different domains
        request1 = Request(url="https://example.com/page1")
        request2 = Request(url="https://example.com/page2")
        request3 = Request(url="https://other.com/page1")

        queue.push(request1)
        queue.push(request2)
        queue.push(request3)

        # Check counts
        assert queue.get_domain_count("example.com") == 2
        assert queue.get_domain_count("other.com") == 1
        assert queue.get_domain_count("unknown.com") == 0


class TestRedisAvailability:
    """Test cases for Redis availability checking."""

    @patch("redis.from_url")
    def test_redis_available(self, mock_from_url: MagicMock) -> None:
        """Test detecting available Redis."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_from_url.return_value = mock_client

        result = check_redis_available("redis://localhost:6379/0")

        assert result is True
        mock_client.ping.assert_called_once()

    @patch("redis.from_url")
    def test_redis_unavailable(self, mock_from_url: MagicMock) -> None:
        """Test detecting unavailable Redis."""
        import redis

        mock_from_url.side_effect = redis.ConnectionError("Connection refused")

        result = check_redis_available("redis://localhost:6379/0")

        assert result is False

    @patch("redis.from_url")
    def test_redis_default_url(self, mock_from_url: MagicMock) -> None:
        """Test using default Redis URL from environment."""
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_from_url.return_value = mock_client

        with patch.dict(os.environ, {"REDIS_URL": "redis://redis.example.com:6379/1"}):
            result = check_redis_available()

        assert result is True
        mock_from_url.assert_called_once()
