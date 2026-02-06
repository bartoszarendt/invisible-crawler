"""Tests for per-domain budget enforcement (Phase B).

Tests that verify per-domain budgets work correctly, including:
- Budget enforcement per domain
- Pending URL tracking
- Integration with global max_pages
"""

from unittest.mock import patch

import pytest
from scrapy.http import HtmlResponse, Request

from crawler.spiders.discovery_spider import DiscoverySpider
from tests.fixtures import SAMPLE_HTML


class TestPerDomainBudget:
    """Tests for per-domain budget enforcement."""

    @pytest.fixture
    def spider_with_budget(self) -> DiscoverySpider:
        """Create spider with per-domain budget enabled."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            with patch("env_config.get_default_max_pages_per_run", return_value=5):
                spider = DiscoverySpider(seeds="config/test_seeds.txt")
                spider.enable_per_domain_budget = True
                spider.max_pages_per_run = 5
                return spider

    @pytest.fixture
    def spider_without_budget(self) -> DiscoverySpider:
        """Create spider with per-domain budget disabled (Phase A behavior)."""
        with patch("env_config.get_enable_per_domain_budget", return_value=False):
            spider = DiscoverySpider(seeds="config/test_seeds.txt")
            spider.enable_per_domain_budget = False
            spider.max_pages = 10  # Global limit
            return spider

    def test_per_domain_budget_enabled_initializes_tracking(
        self, spider_with_budget: DiscoverySpider
    ) -> None:
        """Per-domain budget mode initializes tracking dicts."""
        assert spider_with_budget.enable_per_domain_budget is True
        assert spider_with_budget.max_pages_per_run == 5
        assert spider_with_budget._domain_pages_crawled == {}
        assert spider_with_budget._domain_pending_urls == {}

    def test_per_domain_budget_disabled_no_tracking(
        self, spider_without_budget: DiscoverySpider
    ) -> None:
        """Disabled mode doesn't use per-domain tracking."""
        assert spider_without_budget.enable_per_domain_budget is False
        assert spider_without_budget._domain_pages_crawled == {}
        assert spider_without_budget._domain_pending_urls == {}

    def test_parse_tracks_per_domain_page_count(self, spider_with_budget: DiscoverySpider) -> None:
        """Parse tracks pages crawled per domain."""
        request = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=b"<html><body><a href='/page1'>Link</a></body></html>",
            headers={"Content-Type": "text/html"},
        )

        # First parse
        list(spider_with_budget.parse(response))
        assert spider_with_budget._domain_pages_crawled.get("example.com") == 1

        # Second parse
        list(spider_with_budget.parse(response))
        assert spider_with_budget._domain_pages_crawled.get("example.com") == 2

    def test_parse_tracks_different_domains_separately(
        self, spider_with_budget: DiscoverySpider
    ) -> None:
        """Page counts are tracked separately per domain."""
        # Parse example.com
        request1 = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response1 = HtmlResponse(
            url="https://example.com/",
            request=request1,
            body=b"<html><body><a href='/page1'>Link</a></body></html>",
            headers={"Content-Type": "text/html"},
        )
        list(spider_with_budget.parse(response1))

        # Parse other.com
        request2 = Request(url="https://other.com/", meta={"domain": "other.com", "depth": 0})
        response2 = HtmlResponse(
            url="https://other.com/",
            request=request2,
            body=b"<html><body><a href='/page1'>Link</a></body></html>",
            headers={"Content-Type": "text/html"},
        )
        list(spider_with_budget.parse(response2))
        list(spider_with_budget.parse(response2))

        assert spider_with_budget._domain_pages_crawled.get("example.com") == 1
        assert spider_with_budget._domain_pages_crawled.get("other.com") == 2

    def test_budget_enforcement_stops_following_links(
        self, spider_with_budget: DiscoverySpider
    ) -> None:
        """When budget reached, no more links are followed for that domain."""
        spider_with_budget.max_pages_per_run = 2
        spider_with_budget._domain_pages_crawled["example.com"] = 2  # Budget already reached

        request = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=b"<html><body><a href='/page1'>Link</a><a href='/page2'>Link2</a></body></html>",
            headers={"Content-Type": "text/html"},
        )

        items = list(spider_with_budget.parse(response))

        # Should still extract images but not follow links
        link_requests = [item for item in items if isinstance(item, Request) and "page" in item.url]
        assert len(link_requests) == 0

    def test_pending_urls_tracked_when_budget_reached(
        self, spider_with_budget: DiscoverySpider
    ) -> None:
        """Pending URLs are tracked when budget is reached mid-parse."""
        spider_with_budget.max_pages_per_run = 1

        request = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=b"<html><body><a href='/page1'>Link</a><a href='/page2'>Link2</a></body></html>",
            headers={"Content-Type": "text/html"},
        )

        list(spider_with_budget.parse(response))

        # After first page, budget is reached, but URLs should be tracked
        assert "example.com" in spider_with_budget._domain_pending_urls
        pending = spider_with_budget._domain_pending_urls["example.com"]
        assert len(pending) == 2
        assert all("url" in entry and "depth" in entry for entry in pending)

    def test_global_budget_used_when_per_domain_disabled(
        self, spider_without_budget: DiscoverySpider
    ) -> None:
        """When per-domain disabled, global max_pages is used."""
        spider_without_budget.max_pages = 5
        spider_without_budget.pages_crawled = 5  # Global budget reached

        request = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=b"<html><body><a href='/page1'>Link</a></body></html>",
            headers={"Content-Type": "text/html"},
        )

        items = list(spider_without_budget.parse(response))

        # Should not follow links when global budget reached
        link_requests = [item for item in items if isinstance(item, Request) and "page" in item.url]
        assert len(link_requests) == 0

    def test_unlimited_pages_when_max_pages_zero(
        self, spider_without_budget: DiscoverySpider
    ) -> None:
        """max_pages=0 means unlimited crawling (backward compatibility)."""
        spider_without_budget.max_pages = 0
        spider_without_budget.pages_crawled = 1000

        request = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=b"<html><body><a href='/page1'>Link</a></body></html>",
            headers={"Content-Type": "text/html"},
        )

        items = list(spider_without_budget.parse(response))

        # Should follow links when unlimited
        link_requests = [item for item in items if isinstance(item, Request) and "page" in item.url]
        assert len(link_requests) == 1

    def test_unlimited_pages_per_run_when_zero(self, spider_with_budget: DiscoverySpider) -> None:
        """max_pages_per_run=0 means unlimited per domain."""
        spider_with_budget.max_pages_per_run = 0
        spider_with_budget._domain_pages_crawled["example.com"] = 1000

        request = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=b"<html><body><a href='/page1'>Link</a></body></html>",
            headers={"Content-Type": "text/html"},
        )

        items = list(spider_with_budget.parse(response))

        # Should follow links when unlimited per domain
        link_requests = [item for item in items if isinstance(item, Request) and "page" in item.url]
        assert len(link_requests) == 1

    def test_mixed_mode_budgets_per_domain(self) -> None:
        """Different domains can have different page counts within same crawl."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            spider = DiscoverySpider(seeds="config/test_seeds.txt")
            spider.enable_per_domain_budget = True
            spider.max_pages_per_run = 5

            # Domain A: 3 pages crawled
            request_a = Request(url="https://a.com/", meta={"domain": "a.com", "depth": 0})
            response_a = HtmlResponse(
                url="https://a.com/",
                request=request_a,
                body=b"<html><body><a href='/page1'>Link</a></body></html>",
                headers={"Content-Type": "text/html"},
            )
            for _ in range(3):
                list(spider.parse(response_a))

            # Domain B: 1 page crawled
            request_b = Request(url="https://b.com/", meta={"domain": "b.com", "depth": 0})
            response_b = HtmlResponse(
                url="https://b.com/",
                request=request_b,
                body=b"<html><body><a href='/page1'>Link</a></body></html>",
                headers={"Content-Type": "text/html"},
            )
            list(spider.parse(response_b))

            assert spider._domain_pages_crawled.get("a.com") == 3
            assert spider._domain_pages_crawled.get("b.com") == 1

            # Both should still be under budget and follow links
            items_a = list(spider.parse(response_a))
            items_b = list(spider.parse(response_b))

            links_a = [i for i in items_a if isinstance(i, Request) and "page" in i.url]
            links_b = [i for i in items_b if isinstance(i, Request) and "page" in i.url]

            assert len(links_a) == 1  # a.com still under budget
            assert len(links_b) == 1  # b.com still under budget


class TestPerDomainBudgetWithRealHTML:
    """Tests using realistic HTML fixtures."""

    @pytest.fixture
    def spider(self) -> DiscoverySpider:
        """Create spider with per-domain budget enabled."""
        with patch("env_config.get_enable_per_domain_budget", return_value=True):
            with patch("env_config.get_default_max_pages_per_run", return_value=100):
                spider = DiscoverySpider(seeds="config/test_seeds.txt")
                spider.enable_per_domain_budget = True
                spider.max_pages_per_run = 100
                return spider

    def test_budget_enforcement_with_multiple_links(self, spider: DiscoverySpider) -> None:
        """Budget enforcement works with realistic HTML containing many links."""
        spider.max_pages_per_run = 2

        request = Request(url="https://example.com/", meta={"domain": "example.com", "depth": 0})
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=SAMPLE_HTML.encode("utf-8"),
            headers={"Content-Type": "text/html"},
        )

        # Parse once (page 1)
        items1 = list(spider.parse(response))
        link_requests_1 = [
            item for item in items1 if isinstance(item, Request) and item.callback == spider.parse
        ]

        # Parse again (page 2 - at budget limit)
        items2 = list(spider.parse(response))
        link_requests_2 = [
            item for item in items2 if isinstance(item, Request) and item.callback == spider.parse
        ]

        # Parse again (page 3 - over budget, should not follow links)
        items3 = list(spider.parse(response))
        link_requests_3 = [
            item for item in items3 if isinstance(item, Request) and item.callback == spider.parse
        ]

        # First two parses should yield links
        assert len(link_requests_1) > 0
        assert len(link_requests_2) > 0

        # Third parse should track pending but not yield link requests
        assert len(link_requests_3) == 0

        # But pending URLs should be tracked
        assert "example.com" in spider._domain_pending_urls
        assert len(spider._domain_pending_urls["example.com"]) > 0
