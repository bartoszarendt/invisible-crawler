"""Tests for resume from checkpoint functionality (Phase B).

Tests that verify domains can resume crawling from saved checkpoints,
including checkpoint loading, clearing, and graceful fallback behavior.
"""

from unittest.mock import patch

import pytest
from scrapy.http import Request

from crawler.spiders.discovery_spider import DiscoverySpider


class TestResumeFromCheckpoint:
    """Tests for checkpoint resume functionality."""

    @pytest.fixture
    def spider_with_resume(self) -> DiscoverySpider:
        """Create spider with per-domain budget and tracking enabled."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            with patch("env_config.get_enable_domain_tracking", return_value=True):
                with patch("env_config.get_default_max_pages_per_run", return_value=100):
                    spider = DiscoverySpider(seeds="config/test_seeds.txt")
                    spider.enable_per_domain_budget = True
                    spider.enable_domain_tracking = True
                    spider.max_pages_per_run = 100
                    return spider

    def test_yield_start_requests_without_checkpoint(
        self, spider_with_resume: DiscoverySpider
    ) -> None:
        """Without checkpoint, yields root URL."""
        with patch("crawler.spiders.discovery_spider.get_domain", return_value=None):
            requests = list(
                spider_with_resume._yield_start_requests("https://example.com", "example.com")
            )

        assert len(requests) == 1
        assert requests[0].url == "https://example.com"
        assert requests[0].meta["depth"] == 0
        assert requests[0].meta["domain"] == "example.com"

    def test_no_checkpoint_yields_root(self, spider_with_resume: DiscoverySpider) -> None:
        """Domain without checkpoint yields root URL normally."""
        mock_domain_row = {
            "domain": "example.com",
            "frontier_checkpoint_id": None,
        }

        with patch("crawler.spiders.discovery_spider.get_domain", return_value=mock_domain_row):
            requests = list(
                spider_with_resume._yield_start_requests("https://example.com", "example.com")
            )

        assert len(requests) == 1
        assert requests[0].url == "https://example.com"

    def test_domain_not_in_db_yields_root(self, spider_with_resume: DiscoverySpider) -> None:
        """Domain not in database yields root URL."""
        with patch("crawler.spiders.discovery_spider.get_domain", return_value=None):
            requests = list(
                spider_with_resume._yield_start_requests("https://example.com", "example.com")
            )

        assert len(requests) == 1
        assert requests[0].url == "https://example.com"

    def test_budget_disabled_ignores_checkpoints(self) -> None:
        """When per-domain budget disabled, checkpoints are ignored."""
        with patch("env_config.get_enable_per_domain_budget", return_value=False):
            with patch("env_config.get_enable_domain_tracking", return_value=True):
                spider = DiscoverySpider(seeds="config/test_seeds.txt")
                spider.enable_per_domain_budget = False
                spider.enable_domain_tracking = True

                mock_domain_row = {
                    "domain": "example.com",
                    "frontier_checkpoint_id": "example.com:run-123",
                }

                with patch(
                    "crawler.spiders.discovery_spider.get_domain", return_value=mock_domain_row
                ):
                    requests = list(
                        spider._yield_start_requests("https://example.com", "example.com")
                    )

        # Should yield root URL even though checkpoint exists
        assert len(requests) == 1
        assert requests[0].url == "https://example.com"

    def test_tracking_disabled_ignores_checkpoints(self) -> None:
        """When domain tracking disabled, checkpoints are ignored."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            with patch("env_config.get_enable_domain_tracking", return_value=False):
                spider = DiscoverySpider(seeds="config/test_seeds.txt")
                spider.enable_per_domain_budget = True
                spider.enable_domain_tracking = False

                requests = list(spider._yield_start_requests("https://example.com", "example.com"))

        # Should yield root URL - no checkpoint lookup attempted
        assert len(requests) == 1
        assert requests[0].url == "https://example.com"


class TestCheckpointSaveOnClose:
    """Tests for checkpoint saving in spider closed handler."""

    @pytest.fixture
    def spider_with_budget(self) -> DiscoverySpider:
        """Create spider with budget enabled and pending URLs."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            with patch("env_config.get_enable_domain_tracking", return_value=True):
                spider = DiscoverySpider(seeds="config/test_seeds.txt")
                spider.enable_per_domain_budget = True
                spider.enable_domain_tracking = True
                spider.max_pages_per_run = 5
                spider.crawl_run_id = None  # Skip crawl run DB updates
                spider._domain_pages_crawled = {"example.com": 5}  # Budget reached
                spider._domain_pending_urls = {
                    "example.com": [
                        {"url": "https://example.com/page1", "depth": 1},
                        {"url": "https://example.com/page2", "depth": 2},
                    ]
                }
                return spider

    def test_no_checkpoint_without_pending_urls(self, spider_with_budget: DiscoverySpider) -> None:
        """No checkpoint saved if no pending URLs."""
        spider_with_budget._domain_pending_urls = {"example.com": []}

        # Should not raise exception
        spider_with_budget.closed("finished")

    def test_no_checkpoint_if_budget_not_reached(self, spider_with_budget: DiscoverySpider) -> None:
        """No checkpoint saved if budget wasn't reached."""
        spider_with_budget._domain_pages_crawled = {"example.com": 3}  # Under budget

        # Should not raise exception
        spider_with_budget.closed("finished")

    def test_no_checkpoint_if_budget_disabled(self, spider_with_budget: DiscoverySpider) -> None:
        """No checkpoint saved if per-domain budget disabled."""
        spider_with_budget.enable_per_domain_budget = False

        # Should not raise exception
        spider_with_budget.closed("finished")

    def test_no_checkpoint_if_tracking_disabled(self, spider_with_budget: DiscoverySpider) -> None:
        """No checkpoint saved if domain tracking disabled."""
        spider_with_budget.enable_domain_tracking = False

        # Should not raise exception
        spider_with_budget.closed("finished")

    def test_checkpoint_save_graceful_on_redis_error(
        self, spider_with_budget: DiscoverySpider
    ) -> None:
        """Redis errors during checkpoint save are logged but don't crash."""
        with patch("redis.from_url", side_effect=Exception("Redis down")):
            # Should not raise exception
            spider_with_budget.closed("finished")

    def test_multiple_domains_tracked_separately(self, spider_with_budget: DiscoverySpider) -> None:
        """Each domain with pending URLs is tracked separately."""
        spider_with_budget._domain_pages_crawled = {
            "example.com": 5,
            "other.com": 5,
        }
        spider_with_budget._domain_pending_urls = {
            "example.com": [{"url": "https://example.com/page1", "depth": 1}],
            "other.com": [{"url": "https://other.com/page1", "depth": 1}],
        }

        # Should not raise exception
        spider_with_budget.closed("finished")


class TestResumeState:
    """Tests for resume state tracking."""

    def test_pending_urls_accumulated_correctly(self) -> None:
        """Pending URLs are accumulated as crawl progresses."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            with patch("env_config.get_enable_domain_tracking", return_value=True):
                spider = DiscoverySpider(seeds="config/test_seeds.txt")
                spider.enable_per_domain_budget = True
                spider.enable_domain_tracking = True
                spider.max_pages_per_run = 2

                request = Request(
                    url="https://example.com/", meta={"domain": "example.com", "depth": 0}
                )
                from scrapy.http import HtmlResponse

                response = HtmlResponse(
                    url="https://example.com/",
                    request=request,
                    body=b"<html><body><a href='/page1'>Link</a><a href='/page2'>Link</a></body></html>",
                    headers={"Content-Type": "text/html"},
                )

                # Parse multiple times
                list(spider.parse(response))
                list(spider.parse(response))
                list(spider.parse(response))

                # Should have pending URLs tracked
                assert "example.com" in spider._domain_pending_urls
                pending = spider._domain_pending_urls["example.com"]
                # Each page has 2 links, parsed 3 times = 6 pending URLs tracked
                assert len(pending) == 6

    def test_per_domain_pages_tracked_correctly(self) -> None:
        """Pages crawled are tracked per domain."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            spider = DiscoverySpider(seeds="config/test_seeds.txt")
            spider.enable_per_domain_budget = True
            spider.max_pages_per_run = 10

            request = Request(
                url="https://example.com/", meta={"domain": "example.com", "depth": 0}
            )
            from scrapy.http import HtmlResponse

            response = HtmlResponse(
                url="https://example.com/",
                request=request,
                body=b"<html><body>Test</body></html>",
                headers={"Content-Type": "text/html"},
            )

            # Parse 3 times
            list(spider.parse(response))
            list(spider.parse(response))
            list(spider.parse(response))

            assert spider._domain_pages_crawled.get("example.com") == 3
