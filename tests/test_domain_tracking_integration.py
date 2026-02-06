"""Integration tests for domain tracking.

Tests end-to-end domain tracking functionality including:
- Spider integration with domain tracking
- Feature flag behavior
- Domain stats accumulation across runs
- Backfill script integration
"""

from unittest.mock import MagicMock, patch

import pytest


class TestDomainTrackingIntegration:
    """Integration tests for domain tracking in spider."""

    @pytest.fixture
    def seed_file(self, tmp_path):
        """Create a temporary seed file."""
        seed_file = tmp_path / "test_seeds.txt"
        seed_file.write_text("example.com\ntest.org\n")
        return str(seed_file)

    @patch("crawler.spiders.discovery_spider.get_enable_domain_tracking")
    @patch("crawler.spiders.discovery_spider.upsert_domain")
    @patch("crawler.spiders.discovery_spider.canonicalize_domain")
    def test_spider_upserts_domains_when_enabled(
        self, mock_canonicalize, mock_upsert, mock_get_enabled, seed_file
    ):
        """Test that spider upserts domains when tracking is enabled."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        mock_get_enabled.return_value = True
        mock_canonicalize.return_value = "example.com"

        spider = DiscoverySpider(seeds=seed_file)

        # Simulate starting requests
        list(spider.start_requests())

        # Should have upserted domains
        assert mock_upsert.called
        # Check that upsert was called (with actual path which may vary)
        assert mock_upsert.called
        call_kwargs = mock_upsert.call_args.kwargs
        assert call_kwargs["domain"] == "example.com"
        assert call_kwargs["seed_rank"] is None

    @patch("crawler.spiders.discovery_spider.get_enable_domain_tracking")
    @patch("crawler.spiders.discovery_spider.upsert_domain")
    def test_spider_skips_upsert_when_disabled(self, mock_upsert, mock_get_enabled, seed_file):
        """Test that spider skips upsert when tracking is disabled."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        mock_get_enabled.return_value = False

        spider = DiscoverySpider(seeds=seed_file)
        list(spider.start_requests())

        # Should NOT have upserted domains
        assert not mock_upsert.called

    @patch("crawler.spiders.discovery_spider.get_enable_domain_tracking")
    def test_spider_tracks_domain_stats_during_crawl(self, mock_get_enabled):
        """Test that spider tracks per-domain stats during crawl."""
        from scrapy.http import HtmlResponse

        from crawler.spiders.discovery_spider import DiscoverySpider

        mock_get_enabled.return_value = True

        spider = DiscoverySpider(seeds="test_seeds.txt")

        # Create a mock response
        html = '<html><body><img src="test.jpg"></body></html>'
        request = MagicMock()
        request.meta = {"domain": "example.com", "depth": 0}
        response = HtmlResponse(
            url="https://example.com/page",
            body=html.encode(),
            request=request,
        )

        # Parse the response
        list(spider.parse(response))

        # Check that domain stats were tracked
        assert "example.com" in spider._domain_stats
        assert spider._domain_stats["example.com"]["pages"] == 1
        assert spider._domain_stats["example.com"]["images_found"] == 1

    @patch("crawler.spiders.discovery_spider.get_enable_domain_tracking")
    @patch("crawler.spiders.discovery_spider.update_domain_stats")
    @patch("crawler.spiders.discovery_spider.canonicalize_domain")
    def test_spider_updates_domain_stats_on_close(
        self, mock_canonicalize, mock_update_stats, mock_get_enabled
    ):
        """Test that spider updates domain stats when closed."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        mock_get_enabled.return_value = True
        mock_canonicalize.return_value = "example.com"

        spider = DiscoverySpider(seeds="test_seeds.txt")
        spider.crawl_run_id = "test-run-id"

        # Simulate domain stats from crawl (images_stored not tracked by spider)
        spider._domain_stats = {"example.com": {"pages": 5, "images_found": 20}}

        # Close spider
        spider.closed("finished")

        # Should have updated domain stats
        mock_update_stats.assert_called_once_with(
            domain="example.com",
            pages_crawled_delta=5,
            images_found_delta=20,
            status="active",
            last_crawl_run_id="test-run-id",
        )

    @patch("crawler.spiders.discovery_spider.get_enable_domain_tracking")
    @patch("crawler.spiders.discovery_spider.update_domain_stats")
    def test_spider_skips_stats_update_when_no_domains(self, mock_update_stats, mock_get_enabled):
        """Test that spider skips update when no domains were crawled."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        mock_get_enabled.return_value = True

        spider = DiscoverySpider(seeds="test_seeds.txt")
        spider._domain_stats = {}

        spider.closed("finished")

        # Should NOT have called update_domain_stats
        assert not mock_update_stats.called

    @patch("crawler.spiders.discovery_spider.get_enable_domain_tracking")
    def test_spider_sets_blocked_status_for_blocked_domains(self, mock_get_enabled):
        """Test that spider sets blocked status for blocked domains."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        mock_get_enabled.return_value = True

        spider = DiscoverySpider(seeds="test_seeds.txt")
        spider.crawl_run_id = "test-run-id"
        spider._domain_stats = {"blocked.com": {"pages": 1, "images_found": 0}}
        spider._blocked_domains_canonical.add("blocked.com")

        with patch("crawler.spiders.discovery_spider.update_domain_stats") as mock_update:
            spider.closed("finished")

            # Should have called with blocked status
            call_args = mock_update.call_args
            assert call_args.kwargs["status"] == "blocked"


class TestFeatureFlagBehavior:
    """Test feature flag behavior."""

    def test_enable_domain_tracking_default_true(self):
        """Test that domain tracking is enabled by default."""
        from env_config import get_enable_domain_tracking

        # Should default to True when env var not set
        with patch.dict("os.environ", {}, clear=True):
            result = get_enable_domain_tracking()
            assert result is True

    def test_enable_domain_tracking_respects_env_var(self):
        """Test that feature flag respects ENABLE_DOMAIN_TRACKING env var."""
        from env_config import get_enable_domain_tracking

        with patch.dict("os.environ", {"ENABLE_DOMAIN_TRACKING": "false"}):
            result = get_enable_domain_tracking()
            assert result is False

    def test_strip_subdomains_default_false(self):
        """Test that subdomain stripping defaults to False."""
        from env_config import get_domain_canonicalization_strip_subdomains

        with patch.dict("os.environ", {}, clear=True):
            result = get_domain_canonicalization_strip_subdomains()
            assert result is False


class TestBackfillIntegration:
    """Test backfill script integration."""

    @patch("storage.domain_repository.backfill_domains_from_crawl_log")
    def test_backfill_domains_command(self, mock_backfill):
        """Test backfill command calls repository function."""
        import argparse

        from crawler.cli import backfill_domains_command

        mock_backfill.return_value = {
            "domains_created": 100,
            "images_stored_updated": 50,
        }

        args = argparse.Namespace(dry_run=False)
        result = backfill_domains_command(args)

        assert result == 0
        mock_backfill.assert_called_once()

    @patch("storage.domain_repository.backfill_domains_from_crawl_log")
    def test_backfill_dry_run(self, mock_backfill):
        """Test backfill dry run doesn't execute."""
        import argparse

        from crawler.cli import backfill_domains_command

        args = argparse.Namespace(dry_run=True)
        result = backfill_domains_command(args)

        assert result == 0
        mock_backfill.assert_not_called()


class TestDomainStatsAccumulation:
    """Test that domain stats accumulate across multiple runs."""

    @patch("storage.domain_repository.get_cursor")
    def test_stats_accumulate_with_plus_equals(self, mock_get_cursor):
        """Test that stats use += for accumulation."""
        from storage.domain_repository import update_domain_stats

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("domain-uuid",)
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        update_domain_stats(domain="example.com", pages_crawled_delta=10)

        # Check that the query uses += (pages_crawled = pages_crawled + %s)
        call_args = mock_cursor.execute.call_args
        query = call_args[0][0]
        assert "pages_crawled = pages_crawled + %s" in query

    @patch("storage.domain_repository.get_cursor")
    def test_multiple_updates_accumulate(self, mock_get_cursor):
        """Test that multiple updates accumulate correctly."""
        from storage.domain_repository import update_domain_stats

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("domain-uuid",)
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        # First update
        update_domain_stats(domain="example.com", pages_crawled_delta=5)

        # Second update
        update_domain_stats(domain="example.com", pages_crawled_delta=3)

        # Both should have used += with their respective deltas
        calls = mock_cursor.execute.call_args_list
        assert len(calls) == 2
        assert calls[0][0][1][0] == 5  # First delta
        assert calls[1][0][1][0] == 3  # Second delta
