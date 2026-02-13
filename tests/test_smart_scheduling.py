"""Tests for smart scheduling (Phase C).

Tests that the spider correctly queries domains table and selects
candidates based on priority score and status.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import Request

from crawler.spiders.discovery_spider import DiscoverySpider


class TestSmartScheduling:
    """Test smart scheduling integration."""

    @pytest.fixture
    def spider(self):
        """Create a spider instance with Phase C enabled."""
        with (
            patch(
                "crawler.spiders.discovery_spider.get_enable_smart_scheduling", return_value=True
            ),
            patch("crawler.spiders.discovery_spider.get_enable_claim_protocol", return_value=True),
        ):
            spider = DiscoverySpider()
            spider.crawler = MagicMock()
            return spider

    def test_spider_claims_domains_on_start(self, spider):
        """Spider should claim domains when starting."""
        mock_domain = {
            "id": str(uuid.uuid4()),
            "domain": "example.com",
            "version": 1,
            "frontier_checkpoint_id": None,
        }

        with patch(
            "crawler.spiders.discovery_spider.claim_domains",
            return_value=[mock_domain],
        ):
            requests = list(spider.start_requests())

        assert len(requests) == 1
        assert isinstance(requests[0], Request)
        assert requests[0].url == "https://example.com"
        assert requests[0].meta["domain"] == "example.com"
        assert requests[0].meta["domain_id"] == mock_domain["id"]

    def test_spider_tracks_claimed_domains(self, spider):
        """Spider should track claimed domains for heartbeat/release."""
        mock_domain = {
            "id": str(uuid.uuid4()),
            "domain": "example.com",
            "version": 1,
            "frontier_checkpoint_id": None,
        }

        with patch(
            "crawler.spiders.discovery_spider.claim_domains",
            return_value=[mock_domain],
        ):
            list(spider.start_requests())

        assert mock_domain["id"] in spider._claimed_domains
        assert spider._claimed_domains[mock_domain["id"]]["domain"] == "example.com"

    def test_spider_resumes_from_checkpoint(self, spider):
        """Spider should resume domains with frontier checkpoints."""
        mock_domain = {
            "id": str(uuid.uuid4()),
            "domain": "example.com",
            "version": 1,
            "frontier_checkpoint_id": "example.com:run-123",
        }

        checkpoint_urls = [
            {"url": "https://example.com/page1", "depth": 1},
            {"url": "https://example.com/page2", "depth": 1},
        ]

        with (
            patch(
                "crawler.spiders.discovery_spider.claim_domains",
                return_value=[mock_domain],
            ),
            patch(
                "crawler.spiders.discovery_spider.load_checkpoint",
                return_value=checkpoint_urls,
            ),
            patch(
                "crawler.spiders.discovery_spider.delete_checkpoint",
            ),
            patch(
                "crawler.spiders.discovery_spider.clear_frontier_checkpoint",
            ),
        ):
            requests = list(spider.start_requests())

        assert len(requests) == 2
        assert requests[0].url == "https://example.com/page1"
        assert requests[1].url == "https://example.com/page2"

    def test_spider_no_domains_available(self, spider):
        """Spider should produce no requests when no domains available to claim."""
        with patch(
            "crawler.spiders.discovery_spider.claim_domains",
            return_value=[],
        ):
            requests = list(spider.start_requests())

        # Phase C does not fall back to seeds; returns empty when nothing to claim
        assert len(requests) == 0

    def test_spider_claims_multiple_domains(self, spider):
        """Spider should claim multiple domains at once."""
        mock_domains = [
            {
                "id": str(uuid.uuid4()),
                "domain": f"example{i}.com",
                "version": 1,
                "frontier_checkpoint_id": None,
            }
            for i in range(3)
        ]

        with patch(
            "crawler.spiders.discovery_spider.claim_domains",
            return_value=mock_domains,
        ):
            requests = list(spider.start_requests())

        assert len(requests) == 3


class TestClaimRelease:
    """Test claim release in closed handler."""

    @pytest.fixture
    def spider(self):
        """Create a spider with claimed domains."""
        with (
            patch(
                "crawler.spiders.discovery_spider.get_enable_smart_scheduling", return_value=True
            ),
            patch("crawler.spiders.discovery_spider.get_enable_claim_protocol", return_value=True),
        ):
            spider = DiscoverySpider()
            spider.crawler = MagicMock()
            spider.crawl_run_id = uuid.uuid4()
            spider._claimed_domains = {str(uuid.uuid4()): {"domain": "example.com", "version": 1}}
            spider._domain_stats = {
                "example.com": {"pages": 10, "images_found": 5, "errors": 0, "links_discovered": 3}
            }
            # Mock DB-dependent method for computing images_stored
            spider._compute_domain_images_stored = MagicMock(return_value={"example.com": 3})
            return spider

    def test_releases_claims_on_close(self, spider):
        """Should release all claims when spider closes."""
        with patch(
            "crawler.spiders.discovery_spider.release_claim",
            return_value=True,
        ) as mock_release:
            spider.closed("finished")

        assert mock_release.called

    def test_releases_with_correct_stats(self, spider):
        """Should release with accumulated stats."""
        with patch(
            "crawler.spiders.discovery_spider.release_claim",
            return_value=True,
        ) as mock_release:
            spider.closed("finished")

        call_args = mock_release.call_args
        assert call_args.kwargs["pages_crawled_delta"] == 10
        assert call_args.kwargs["pages_discovered_delta"] == 13  # 10 pages + 3 links
        assert call_args.kwargs["images_found_delta"] == 5
        assert call_args.kwargs["images_stored_delta"] == 3
        assert call_args.kwargs["total_error_count_delta"] == 0
        assert call_args.kwargs["consecutive_error_count"] == 0  # pages > 0

    def test_retries_on_version_conflict(self, spider):
        """Should retry release on version conflict."""
        with patch(
            "crawler.spiders.discovery_spider.release_claim",
            side_effect=[False, False, True],  # Fail twice, succeed on third
        ) as mock_release:
            spider.closed("finished")

        assert mock_release.call_count == 3

    def test_no_double_count_on_close(self, spider):
        """Should not double-count stats when claim protocol enabled (Workstream B)."""
        # Setup: Multi-domain scenario with both claimed and non-claimed domains
        domain_id_1 = str(uuid.uuid4())
        str(uuid.uuid4())

        spider._claimed_domains = {domain_id_1: {"domain": "claimed.com", "version": 1}}
        spider._domain_stats = {
            "claimed.com": {"pages": 10, "images_found": 5, "errors": 0, "links_discovered": 3},
            "unclaimed.com": {"pages": 8, "images_found": 2, "errors": 1, "links_discovered": 4},
        }
        spider._compute_domain_images_stored = MagicMock(
            return_value={"claimed.com": 3, "unclaimed.com": 1}
        )

        with (
            patch(
                "crawler.spiders.discovery_spider.release_claim",
                return_value=True,
            ),
            patch("crawler.spiders.discovery_spider.update_domain_stats") as mock_update_stats,
        ):
            spider.closed("finished")

        # Verify: release_claim was called for claimed domain
        # Verify: update_domain_stats was called ONLY for unclaimed domain (not double-counted)
        assert mock_update_stats.call_count == 1
        call_args = mock_update_stats.call_args
        assert call_args.kwargs["domain"] == "unclaimed.com"
        assert call_args.kwargs["pages_crawled_delta"] == 8

    def test_exhausted_status_preserved(self, spider):
        """Should preserve terminal status from release_claim (Workstream B)."""
        domain_id = str(uuid.uuid4())
        spider._claimed_domains = {domain_id: {"domain": "example.com", "version": 1}}
        spider._domain_stats = {
            "example.com": {"pages": 100, "images_found": 50, "errors": 0, "links_discovered": 0}
        }
        spider._domain_frontier_queue = {}  # No pending URLs = exhausted
        spider._compute_domain_images_stored = MagicMock(return_value={"example.com": 40})

        with (
            patch(
                "crawler.spiders.discovery_spider.release_claim",
                return_value=True,
            ) as mock_release,
            patch("crawler.spiders.discovery_spider.update_domain_stats") as mock_update_stats,
        ):
            spider.closed("finished")

        # Verify: release_claim was called with status='exhausted'
        call_args = mock_release.call_args
        assert call_args.kwargs["status"] == "exhausted"

        # Verify: update_domain_stats was NOT called for this domain (no overwrite)
        assert mock_update_stats.call_count == 0
