"""Tests for domain claim protocol (Phase C).

Tests the core concurrency primitives:
- claim_domains: Atomic domain claim acquisition
- renew_claim: Lease renewal
- release_claim: Claim release with optimistic locking
- expire_stale_claims: Cleanup of expired claims
"""

import uuid

from storage.domain_repository import (
    claim_domains,
    expire_stale_claims,
    release_claim,
    renew_claim,
)


class TestClaimDomains:
    """Test domain claim acquisition."""

    def test_claim_single_domain(self, db_cursor):
        """Should claim an available domain."""
        # Setup: Insert a domain
        domain_id = str(uuid.uuid4())
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s::uuid, %s, 'pending', 100)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Claim the domain
        claimed = claim_domains("worker-1", batch_size=10)

        assert len(claimed) == 1
        assert claimed[0]["domain"] == "example.com"
        assert claimed[0]["claimed_by"] == "worker-1"
        assert claimed[0]["version"] == 1

    def test_claim_prioritizes_active_domains(self, db_cursor):
        """Should prioritize active domains over pending."""
        # Setup: Insert domains with different statuses
        active_id = uuid.uuid4()
        pending_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, %s, 'active', 50), (%s, %s, 'pending', 100)
            """,
            (active_id, "active-domain.com", pending_id, "pending-domain.com"),
        )
        db_cursor.commit()

        # Claim with limit 1
        claimed = claim_domains("worker-1", batch_size=1)

        assert len(claimed) == 1
        assert claimed[0]["domain"] == "active-domain.com"

    def test_claim_orders_by_priority(self, db_cursor):
        """Should claim highest priority domains first."""
        # Setup: Insert domains with different priorities
        low_id = uuid.uuid4()
        high_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, %s, 'pending', 10), (%s, %s, 'pending', 100)
            """,
            (low_id, "low.com", high_id, "high.com"),
        )
        db_cursor.commit()

        # Claim both
        claimed = claim_domains("worker-1", batch_size=10)

        assert len(claimed) == 2
        assert claimed[0]["domain"] == "high.com"
        assert claimed[1]["domain"] == "low.com"

    def test_claim_respects_batch_size(self, db_cursor):
        """Should respect batch_size limit."""
        # Setup: Insert 5 domains
        for i in range(5):
            domain_id = uuid.uuid4()
            db_cursor.execute(
                """
                INSERT INTO domains (id, domain, status, priority_score)
                VALUES (%s, %s, 'pending', %s)
                """,
                (domain_id, f"domain{i}.com", i * 10),
            )
            db_cursor.commit()

        # Claim with batch_size=2
        claimed = claim_domains("worker-1", batch_size=2)

        assert len(claimed) == 2

    def test_claim_skips_blocked_domains(self, db_cursor):
        """Should not claim blocked or unreachable domains."""
        # Setup: Insert domains with different statuses
        blocked_id = uuid.uuid4()
        unreachable_id = uuid.uuid4()
        pending_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score)
            VALUES (%s, %s, 'blocked', 100),
                   (%s, %s, 'unreachable', 100),
                   (%s, %s, 'pending', 100)
            """,
            (
                blocked_id,
                "blocked.com",
                unreachable_id,
                "unreachable.com",
                pending_id,
                "pending.com",
            ),
        )
        db_cursor.commit()

        # Claim all available
        claimed = claim_domains("worker-1", batch_size=10)

        assert len(claimed) == 1
        assert claimed[0]["domain"] == "pending.com"

    def test_claim_skips_already_claimed(self, db_cursor):
        """Should not claim domains already claimed by another worker."""
        # Setup: Insert a claimed domain
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
        claimed = claim_domains("worker-2", batch_size=10)

        assert len(claimed) == 0

    def test_claim_can_claim_expired(self, db_cursor):
        """Should allow claiming expired domains."""
        # Setup: Insert an expired claim
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, priority_score, claimed_by, claim_expires_at)
            VALUES (%s, %s, 'pending', 100, 'worker-1', CURRENT_TIMESTAMP - INTERVAL '1 minute')
            """,
            (domain_id, "expired.com"),
        )
        db_cursor.commit()

        # Claim from different worker
        claimed = claim_domains("worker-2", batch_size=10)

        assert len(claimed) == 1
        assert claimed[0]["domain"] == "expired.com"
        assert claimed[0]["claimed_by"] == "worker-2"


class TestRenewClaim:
    """Test claim renewal (heartbeat)."""

    def test_renew_valid_claim(self, db_cursor):
        """Should renew an active claim."""
        # Setup: Insert a claimed domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at, version)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes', 1)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Renew the claim
        success = renew_claim(str(domain_id), "worker-1")

        assert success is True

        # Verify version incremented
        db_cursor.execute(
            "SELECT version, claim_expires_at FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] == 2  # Version incremented

    def test_renew_wrong_worker(self, db_cursor):
        """Should not allow renewal by wrong worker."""
        # Setup: Insert a claimed domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at, version)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes', 1)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Try to renew from different worker
        success = renew_claim(str(domain_id), "worker-2")

        assert success is False

    def test_renew_expired_claim(self, db_cursor):
        """Should not renew an expired claim."""
        # Setup: Insert an expired claim
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at, version)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP - INTERVAL '5 minutes', 1)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Try to renew
        success = renew_claim(str(domain_id), "worker-1")

        assert success is False


class TestReleaseClaim:
    """Test claim release with optimistic locking."""

    def test_release_with_correct_version(self, db_cursor):
        """Should release claim with correct version."""
        # Setup: Insert a claimed domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at, version, pages_crawled)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes', 5, 0)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Release with correct version
        success = release_claim(
            str(domain_id),
            "worker-1",
            expected_version=5,
            pages_crawled_delta=10,
            status="exhausted",
        )

        assert success is True

        # Verify domain updated
        db_cursor.execute(
            "SELECT claimed_by, claim_expires_at, version, pages_crawled, status FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] is None  # claimed_by cleared
        assert row[1] is None  # claim_expires_at cleared
        assert row[2] == 6  # version incremented
        assert row[3] == 10  # pages_crawled updated
        assert row[4] == "exhausted"  # status updated

    def test_release_version_mismatch(self, db_cursor):
        """Should fail release with wrong version (optimistic lock)."""
        # Setup: Insert a claimed domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at, version)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes', 5)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Try to release with wrong version
        success = release_claim(
            str(domain_id),
            "worker-1",
            expected_version=4,  # Wrong version
            status="exhausted",
        )

        assert success is False

    def test_release_wrong_worker(self, db_cursor):
        """Should not allow release by wrong worker."""
        # Setup: Insert a claimed domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at, version)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes', 5)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Try to release from different worker
        success = release_claim(
            str(domain_id),
            "worker-2",
            expected_version=5,
            status="exhausted",
        )

        assert success is False


class TestExpireStaleClaims:
    """Test cleanup of expired claims."""

    def test_expire_expired_claims(self, db_cursor):
        """Should expire claims past their expiry time."""
        # Setup: Insert expired claims
        for i in range(3):
            domain_id = uuid.uuid4()
            db_cursor.execute(
                """
                INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
                VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP - INTERVAL '5 minutes')
                """,
                (domain_id, f"expired{i}.com"),
            )
            db_cursor.commit()

        # Insert one active claim
        active_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes')
            """,
            (active_id, "active.com"),
        )
        db_cursor.commit()

        # Expire stale claims
        count = expire_stale_claims()

        assert count == 3

        # Verify active claim still exists
        db_cursor.execute(
            "SELECT claimed_by FROM domains WHERE id = %s",
            (active_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] == "worker-1"

    def test_expire_no_stale_claims(self, db_cursor):
        """Should return 0 when no stale claims exist."""
        # Setup: Insert only active claims
        for i in range(2):
            domain_id = uuid.uuid4()
            db_cursor.execute(
                """
                INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
                VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes')
                """,
                (domain_id, f"active{i}.com"),
            )
            db_cursor.commit()

        # Expire stale claims
        count = expire_stale_claims()

        assert count == 0


class TestForceReleaseClaims:
    """Test force-release functionality (Workstream D)."""

    def test_force_release_worker_claims(self, db_cursor):
        """Should force-release all claims for a specific worker."""
        from storage.domain_repository import force_release_worker_claims

        # Setup: Insert claims for multiple workers
        for i, worker in enumerate(["worker-1", "worker-1", "worker-2"]):
            domain_id = uuid.uuid4()
            db_cursor.execute(
                """
                INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
                VALUES (%s, %s, 'active', %s, CURRENT_TIMESTAMP + INTERVAL '20 minutes')
                """,
                (domain_id, f"domain{i}.com", worker),
            )
        db_cursor.commit()

        # Force-release worker-1 claims
        count = force_release_worker_claims("worker-1")

        assert count == 2

        # Verify worker-1 claims released
        db_cursor.execute(
            "SELECT COUNT(*) FROM domains WHERE claimed_by = 'worker-1'"
        )
        assert db_cursor.fetchone()[0] == 0

        # Verify worker-2 claim still active
        db_cursor.execute(
            "SELECT COUNT(*) FROM domains WHERE claimed_by = 'worker-2'"
        )
        assert db_cursor.fetchone()[0] == 1

    def test_force_release_all_claims(self, db_cursor):
        """Should force-release all active claims (emergency recovery)."""
        from storage.domain_repository import force_release_all_claims

        # Setup: Insert claims for multiple workers
        for i, worker in enumerate(["worker-1", "worker-2", "worker-3"]):
            domain_id = uuid.uuid4()
            db_cursor.execute(
                """
                INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
                VALUES (%s, %s, 'active', %s, CURRENT_TIMESTAMP + INTERVAL '20 minutes')
                """,
                (domain_id, f"domain{i}.com", worker),
            )
        db_cursor.commit()

        # Force-release all claims
        count = force_release_all_claims()

        assert count == 3

        # Verify all claims released
        db_cursor.execute(
            "SELECT COUNT(*) FROM domains WHERE claimed_by IS NOT NULL"
        )
        assert db_cursor.fetchone()[0] == 0

    def test_incremental_stats_update(self, db_cursor):
        """Should incrementally update stats for claimed domains (Workstream C)."""
        from storage.domain_repository import increment_domain_stats_claimed

        # Setup: Insert a claimed domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at,
                                pages_crawled, images_found, images_stored, total_error_count)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes',
                    10, 5, 3, 0)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Incrementally update stats (mid-crawl flush)
        success = increment_domain_stats_claimed(
            domain_id=domain_id,
            worker_id="worker-1",  # Must match claim owner
            pages_crawled_delta=15,
            images_found_delta=8,
            images_stored_delta=6,
            total_error_count_delta=1,
        )

        assert success is True

        # Verify stats incremented atomically
        db_cursor.execute(
            """
            SELECT pages_crawled, images_found, images_stored, total_error_count
            FROM domains WHERE id = %s
            """,
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] == 25  # 10 + 15
        assert row[1] == 13  # 5 + 8
        assert row[2] == 9   # 3 + 6
        assert row[3] == 1   # 0 + 1

    def test_incremental_stats_requires_claim(self, db_cursor):
        """Should fail incremental update if domain not claimed (safety check)."""
        from storage.domain_repository import increment_domain_stats_claimed

        # Setup: Insert an unclaimed domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled)
            VALUES (%s, %s, 'pending', 0)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Try to increment stats on unclaimed domain
        success = increment_domain_stats_claimed(
            domain_id=domain_id,
            worker_id="worker-1",
            pages_crawled_delta=10,
        )

        assert success is False  # Safety check: only update claimed domains

        # Verify stats not updated
        db_cursor.execute(
            "SELECT pages_crawled FROM domains WHERE id = %s",
            (domain_id,),
        )
        assert db_cursor.fetchone()[0] == 0
