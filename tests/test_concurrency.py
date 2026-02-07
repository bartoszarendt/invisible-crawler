"""Tests for multi-worker concurrency (Phase C).

Tests that multiple workers can safely claim domains without conflicts
or duplicate work.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from storage.domain_repository import claim_domains, release_claim


class TestMultiWorkerConcurrency:
    """Test multi-worker claim protocol."""

    def test_two_workers_claim_different_domains(self, db_cursor):
        """Two workers should claim disjoint sets of domains."""
        # Setup: Insert 10 domains
        domain_ids = []
        for i in range(10):
            domain_id = uuid.uuid4()
            domain_ids.append(domain_id)
            db_cursor.execute(
                """
                INSERT INTO domains (id, domain, status, priority_score)
                VALUES (%s, %s, 'pending', %s)
                """,
                (domain_id, f"domain{i}.com", i * 10),
            )
            db_cursor.commit()

        # Two workers claim 5 domains each
        worker1_claims = []
        worker2_claims = []

        def worker_claim(worker_id, results_list, batch_size):
            claims = claim_domains(worker_id, batch_size=batch_size)
            results_list.extend(claims)

        # Run both workers concurrently
        threads = [
            threading.Thread(target=worker_claim, args=("worker-1", worker1_claims, 5)),
            threading.Thread(target=worker_claim, args=("worker-2", worker2_claims, 5)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify no overlap
        worker1_ids = {c["id"] for c in worker1_claims}
        worker2_ids = {c["id"] for c in worker2_claims}

        assert len(worker1_ids & worker2_ids) == 0, "Workers claimed same domains"
        assert len(worker1_claims) + len(worker2_claims) == 10, "Not all domains claimed"

    def test_worker_cannot_claim_already_claimed(self, db_cursor):
        """Worker should not be able to claim domain already claimed."""
        # Setup: Insert and claim a domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score, claimed_by, claim_expires_at)
            VALUES (%s, %s, 'pending', 100, 'worker-1', CURRENT_TIMESTAMP + INTERVAL '30 minutes')
            """,
            (domain_id, "claimed.com"),
        )
        db_cursor.commit()

        # Try to claim from different worker
        claims = claim_domains("worker-2", batch_size=10)

        assert len(claims) == 0

    def test_concurrent_claim_race_condition(self, db_cursor):
        """Multiple workers racing for same domain - only one wins."""
        # Setup: Insert single domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, %s, 'pending', 100)
            """,
            (domain_id, "single.com"),
        )
        db_cursor.commit()

        results = []

        def try_claim(worker_id):
            claims = claim_domains(worker_id, batch_size=1)
            if claims:
                results.append((worker_id, claims[0]["id"]))

        # 5 workers try to claim the same domain simultaneously
        threads = [threading.Thread(target=try_claim, args=(f"worker-{i}",)) for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one worker should succeed
        assert len(results) == 1

    def test_release_allows_reclaim(self, db_cursor):
        """Released domain can be claimed by another worker."""
        # Setup: Insert and claim domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at, version, pages_crawled)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '30 minutes', 1, 0)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Worker 1 releases
        success = release_claim(
            str(domain_id),
            "worker-1",
            expected_version=1,
            pages_crawled_delta=10,
            status="active",
        )
        assert success is True

        # Worker 2 can now claim
        claims = claim_domains("worker-2", batch_size=10)
        assert len(claims) == 1
        assert claims[0]["claimed_by"] == "worker-2"

    def test_version_increments_on_claim(self, db_cursor):
        """Version should increment when domain is claimed."""
        # Setup: Insert domain with version 0
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score, version)
            VALUES (%s, %s, 'pending', 100, 0)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Claim the domain
        claims = claim_domains("worker-1", batch_size=10)

        assert len(claims) == 1
        assert claims[0]["version"] == 1

        # Verify in DB
        db_cursor.execute(
            "SELECT version FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] == 1


class TestLeaseExpiry:
    """Test lease expiry and cleanup."""

    def test_expired_claim_can_be_reclaimed(self, db_cursor):
        """Expired claim can be claimed by another worker."""
        # Setup: Insert expired claim
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
            VALUES (%s, %s, 'pending', 'worker-1', CURRENT_TIMESTAMP - INTERVAL '5 minutes')
            """,
            (domain_id, "expired.com"),
        )
        db_cursor.commit()

        # Different worker can claim
        claims = claim_domains("worker-2", batch_size=10)

        assert len(claims) == 1
        assert claims[0]["claimed_by"] == "worker-2"

    def test_lease_duration_enforced(self, db_cursor):
        """Active lease prevents other workers from claiming."""
        # Setup: Insert active claim
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
            VALUES (%s, %s, 'pending', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '25 minutes')
            """,
            (domain_id, "active.com"),
        )
        db_cursor.commit()

        # Different worker cannot claim
        claims = claim_domains("worker-2", batch_size=10)

        assert len(claims) == 0


class TestNoDuplicateWork:
    """Test that ensures no duplicate work across workers."""

    def test_simulate_multi_worker_crawl(self, db_cursor):
        """Simulate 3 workers crawling 100 domains with no duplicates."""
        # Setup: Insert 100 domains
        domain_ids = []
        for i in range(100):
            domain_id = uuid.uuid4()
            domain_ids.append(domain_id)
            db_cursor.execute(
                """
                INSERT INTO domains (id, domain, status, priority_score)
                VALUES (%s, %s, 'pending', %s)
                """,
                (domain_id, f"domain{i}.com", i),
            )
            db_cursor.commit()

        # Track which domains each worker claimed
        worker_claims = {"worker-1": [], "worker-2": [], "worker-3": []}

        def worker_crawl(worker_id):
            while True:
                claims = claim_domains(worker_id, batch_size=10)
                if not claims:
                    break
                worker_claims[worker_id].extend(claims)

                # Simulate some work then release
                for claim in claims:
                    time.sleep(0.01)  # Simulate 10ms of work
                    release_claim(
                        claim["id"],
                        worker_id,
                        claim["version"],
                        pages_crawled_delta=5,
                        status="exhausted",
                    )

        # Run 3 workers concurrently
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker_crawl, f"worker-{i}") for i in range(1, 4)]
            for future in futures:
                future.result()

        # Verify no duplicate claims
        all_claimed = []
        for worker_id, claims in worker_claims.items():
            all_claimed.extend([c["id"] for c in claims])

        assert len(all_claimed) == len(set(all_claimed)), "Duplicate work detected"
        assert len(all_claimed) == 100, f"Expected 100 domains, got {len(all_claimed)}"

    def test_worker_crash_recovery(self, db_cursor):
        """Domain claimed by crashed worker can be reclaimed after expiry."""
        # Setup: Insert domain with short expiry
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
            VALUES (%s, %s, 'active', 'crashed-worker', CURRENT_TIMESTAMP - INTERVAL '1 minute')
            """,
            (domain_id, "crash.com"),
        )
        db_cursor.commit()

        # New worker can claim
        claims = claim_domains("recovery-worker", batch_size=10)

        assert len(claims) == 1
        assert claims[0]["claimed_by"] == "recovery-worker"
