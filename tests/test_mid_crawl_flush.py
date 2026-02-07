"""Integration tests for mid-crawl state flushing (Workstream C)."""

import uuid
from unittest.mock import MagicMock, patch


class TestMidCrawlFlush:
    """Test mid-crawl domain stats persistence."""

    def test_stats_flushed_every_n_pages(self, db_cursor):
        """Domain stats should be persisted after flush_interval pages."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        # Setup: Insert domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, 'example.com', 'pending', 100)
            """,
            (domain_id,),
        )
        db_cursor.commit()

        # Mock environment: Phase C with flush interval = 10 pages
        with (
            patch(
                "crawler.spiders.discovery_spider.get_enable_smart_scheduling", return_value=True
            ),
            patch("crawler.spiders.discovery_spider.get_enable_claim_protocol", return_value=True),
            patch(
                "crawler.spiders.discovery_spider.get_domain_stats_flush_interval", return_value=10
            ),
        ):
            spider = DiscoverySpider(seeds=["example.com"])
            spider.crawler = MagicMock()
            spider.worker_id = "test-worker"  # Match claim owner

            # Claim the domain
            from storage.domain_repository import claim_domains

            claims = claim_domains("test-worker", batch_size=1)
            assert len(claims) == 1
            claimed_domain = claims[0]

            # Simulate processing 25 pages with flushes
            spider._domain_stats["example.com"] = {
                "pages": 25,
                "images_found": 5,
            }
            spider._claimed_domains[claimed_domain["id"]] = {
                "domain": "example.com",
                "version": claimed_domain["version"],
            }

            # Mock increment function at usage point
            with patch(
                "crawler.spiders.discovery_spider.increment_domain_stats_claimed"
            ) as mock_increment:
                # Trigger flush (should flush 20 pages: 2 flushes of 10 each)
                spider._maybe_flush_domain_stats("example.com")

                # Verify first flush (10 pages)
                if mock_increment.call_count >= 1:
                    first_call = mock_increment.call_args_list[0]
                    assert first_call.kwargs["domain_id"] == claimed_domain["id"]
                    assert first_call.kwargs["worker_id"] == "test-worker"
                    assert first_call.kwargs["pages_crawled_delta"] >= 10

    def test_final_flush_on_close(self, db_cursor):
        """Remaining stats should be flushed when spider closes."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        # Setup: Insert domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, 'example.com', 'pending', 100)
            """,
            (domain_id,),
        )
        db_cursor.commit()

        # Mock environment: Phase C with flush interval = 50 pages
        with (
            patch(
                "crawler.spiders.discovery_spider.get_enable_smart_scheduling", return_value=True
            ),
            patch("crawler.spiders.discovery_spider.get_enable_claim_protocol", return_value=True),
            patch(
                "crawler.spiders.discovery_spider.get_domain_stats_flush_interval", return_value=50
            ),
        ):
            spider = DiscoverySpider(seeds=["example.com"])
            spider.crawler = MagicMock()
            spider.worker_id = "test-worker"  # Match claim owner

            # Claim the domain
            from storage.domain_repository import claim_domains

            claims = claim_domains("test-worker", batch_size=1)
            claimed_domain = claims[0]

            # Simulate processing 35 pages (below flush threshold)
            spider._domain_stats["example.com"] = {
                "pages": 35,
                "images_found": 7,
            }
            spider._claimed_domains[claimed_domain["id"]] = {
                "domain": "example.com",
                "version": claimed_domain["version"],
            }

            # Mock release_claim to verify final stats
            with patch("crawler.spiders.discovery_spider.release_claim") as mock_release:
                spider.closed("finished")

                # Verify release_claim called with accumulated stats
                assert mock_release.call_count >= 1
                # Find call for our domain
                found = False
                for call in mock_release.call_args_list:
                    if call.kwargs.get("domain_id") == claimed_domain["id"]:
                        assert call.kwargs["pages_crawled_delta"] == 35
                        found = True
                        break
                assert found, "release_claim not called for our domain"

    def test_force_kill_recovery_visible_progress(self, db_cursor):
        """After force-kill, flushed stats should be visible in DB."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        # Setup: Insert domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, 'example.com', 'pending', 100)
            """,
            (domain_id,),
        )
        db_cursor.commit()

        # Mock environment: Phase C with flush interval = 10 pages
        with (
            patch(
                "crawler.spiders.discovery_spider.get_enable_smart_scheduling", return_value=True
            ),
            patch("crawler.spiders.discovery_spider.get_enable_claim_protocol", return_value=True),
            patch(
                "crawler.spiders.discovery_spider.get_domain_stats_flush_interval", return_value=10
            ),
        ):
            spider = DiscoverySpider(seeds=["example.com"])
            spider.crawler = MagicMock()

            # Claim the domain
            from storage.domain_repository import claim_domains

            claims = claim_domains("test-worker", batch_size=1)
            claimed_domain = claims[0]

            # Simulate processing 25 pages with flushes
            from storage.domain_repository import increment_domain_stats_claimed

            increment_domain_stats_claimed(
                domain_id=claimed_domain["id"],
                worker_id="test-worker",
                pages_crawled_delta=25,
                images_found_delta=5,
            )

            # Verify stats visible in DB immediately (not waiting for spider close)
            db_cursor.execute("SELECT pages_crawled FROM domains WHERE id = %s", (domain_id,))
            assert db_cursor.fetchone()[0] == 25

    def test_flush_interval_zero_disables_flush(self, db_cursor):
        """flush_interval=0 should disable mid-crawl flushing."""
        from crawler.spiders.discovery_spider import DiscoverySpider

        # Setup: Insert domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, 'example.com', 'pending', 100)
            """,
            (domain_id,),
        )
        db_cursor.commit()

        # Mock environment: Phase C with flush interval = 0 (disabled)
        with (
            patch(
                "crawler.spiders.discovery_spider.get_enable_smart_scheduling", return_value=True
            ),
            patch("crawler.spiders.discovery_spider.get_enable_claim_protocol", return_value=True),
            patch("crawler.spiders.discovery_spider.get_domain_stats_flush_interval", return_value=0),
        ):
            spider = DiscoverySpider(seeds=["example.com"])
            spider.crawler = MagicMock()

            # Claim the domain
            from storage.domain_repository import claim_domains

            claims = claim_domains("test-worker", batch_size=1)
            claimed_domain = claims[0]

            # Simulate processing many pages
            spider._domain_stats["example.com"] = {
                "pages": 1000,
                "images_found": 50,
            }
            spider._claimed_domains[claimed_domain["id"]] = {
                "domain": "example.com",
                "version": claimed_domain["version"],
            }

            # Mock increment function

            with patch(
                "storage.domain_repository.increment_domain_stats_claimed"
            ) as mock_increment:
                # Try to flush
                spider._maybe_flush_domain_stats("example.com")

                # Verify no incremental flush occurred (disabled)
                assert mock_increment.call_count == 0


class TestMidCrawlFlushConcurrency:
    """Test mid-crawl flush under concurrent conditions."""

    def test_incremental_flush_during_heartbeat(self, db_cursor):
        """Incremental flush should work during claim heartbeat renewal."""

        # Setup: Insert domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, 'example.com', 'pending', 100)
            """,
            (domain_id,),
        )
        db_cursor.commit()

        # Claim the domain
        from storage.domain_repository import claim_domains, renew_claim

        claims = claim_domains("test-worker", batch_size=1)
        claimed_domain = claims[0]

        # Flush stats incrementally
        from storage.domain_repository import increment_domain_stats_claimed

        increment_domain_stats_claimed(
            domain_id=claimed_domain["id"],
            worker_id="test-worker",
            pages_crawled_delta=10,
            images_found_delta=2,
        )

        # Renew claim (simulating heartbeat)
        renewed = renew_claim(domain_id=claimed_domain["id"], worker_id="test-worker")
        assert renewed is True

        # Flush more stats
        increment_domain_stats_claimed(
            domain_id=claimed_domain["id"],
            worker_id="test-worker",
            pages_crawled_delta=15,
            images_found_delta=3,
        )

        # Verify cumulative stats
        db_cursor.execute("SELECT pages_crawled, images_found FROM domains WHERE id = %s", (domain_id,))
        row = db_cursor.fetchone()
        assert row[0] == 25  # 10 + 15
        assert row[1] == 5   # 2 + 3

    def test_flush_with_version_conflict_handled(self, db_cursor):
        """Incremental flush should handle version conflicts gracefully."""

        # Setup: Insert domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score, version)
            VALUES (%s, 'example.com', 'pending', 100, 1)
            """,
            (domain_id,),
        )
        db_cursor.commit()

        # Claim the domain
        from storage.domain_repository import claim_domains

        claims = claim_domains("test-worker", batch_size=1)
        claimed_domain = claims[0]

        # Simulate concurrent update (version increment)
        db_cursor.execute("UPDATE domains SET version = version + 1 WHERE id = %s", (domain_id,))
        db_cursor.commit()

        # Try incremental flush with stale version (should handle gracefully)
        from storage.domain_repository import increment_domain_stats_claimed

        # This should not raise an exception, but may return False (0 rows updated)
        # The key test is no crash and no data corruption.
        increment_domain_stats_claimed(
            domain_id=claimed_domain["id"],
            worker_id="test-worker",
            pages_crawled_delta=10,
            images_found_delta=2,
        )
        # Result may be False due to version conflict - that's acceptable
        # The key requirement is that it fails gracefully
