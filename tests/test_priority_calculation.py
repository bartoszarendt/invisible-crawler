"""Tests for priority calculation (Phase C).

Tests the priority scoring formula and recalculation logic.
"""

import uuid

import pytest

from storage.priority_calculator import recalculate_priorities


class TestRecalculatePriorities:
    """Test priority score recalculation."""

    def test_calculates_yield_rate(self, db_cursor):
        """Should calculate image yield rate."""
        # Setup: Insert a domain with known stats
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored, images_found, total_error_count)
            VALUES (%s, %s, 'active', 100, 50, 75, 5)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Recalculate priorities
        stats = recalculate_priorities()

        assert stats["updated"] == 1

        # Verify yield rate
        db_cursor.execute(
            "SELECT image_yield_rate FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] == pytest.approx(0.5, rel=1e-3)  # 50/100

    def test_calculates_error_rate(self, db_cursor):
        """Should calculate error rate."""
        # Setup: Insert a domain with errors
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored, total_error_count)
            VALUES (%s, %s, 'active', 100, 80, 10)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Verify error rate
        db_cursor.execute(
            "SELECT error_rate FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] == pytest.approx(0.1, rel=1e-3)  # 10/100

    def test_calculates_priority_score(self, db_cursor):
        """Should calculate composite priority score."""
        # Setup: Insert a domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored,
                                 pages_discovered, total_error_count, seed_rank)
            VALUES (%s, %s, 'active', 100, 50, 200, 5, 10)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Verify priority score is calculated
        db_cursor.execute(
            "SELECT priority_score FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] is not None
        assert row[0] > 0  # Should have positive score

    def test_skips_blocked_domains(self, db_cursor):
        """Should not update blocked or unreachable domains."""
        # Setup: Insert blocked and unreachable domains
        blocked_id = uuid.uuid4()
        unreachable_id = uuid.uuid4()
        active_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored)
            VALUES (%s, %s, 'blocked', 100, 50),
                   (%s, %s, 'unreachable', 100, 50),
                   (%s, %s, 'active', 100, 50)
            """,
            (blocked_id, "blocked.com", unreachable_id, "unreachable.com", active_id, "active.com"),
        )
        db_cursor.commit()

        # Recalculate
        stats = recalculate_priorities()

        assert stats["updated"] == 1  # Only active domain
        assert stats["active"] == 1

    def test_handles_zero_pages_crawled(self, db_cursor):
        """Should handle domains with no pages crawled."""
        # Setup: Insert domain with zero pages
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored, seed_rank)
            VALUES (%s, %s, 'pending', 0, 0, 100)
            """,
            (domain_id, "new-domain.com"),
        )
        db_cursor.commit()

        # Recalculate
        stats = recalculate_priorities()

        assert stats["updated"] == 1

        # Verify rates are NULL (division by zero avoided)
        db_cursor.execute(
            "SELECT image_yield_rate, error_rate FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] is None
        assert row[1] is None

    def test_updates_timestamp(self, db_cursor):
        """Should update priority_computed_at timestamp."""
        # Setup: Insert a domain
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored)
            VALUES (%s, %s, 'active', 100, 50)
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Verify timestamp updated
        db_cursor.execute(
            "SELECT priority_computed_at FROM domains WHERE id = %s",
            (domain_id,),
        )
        row = db_cursor.fetchone()
        assert row[0] is not None


class TestPriorityScoringFormula:
    """Test the specific priority scoring formula."""

    def test_seed_rank_contributes_to_score(self, db_cursor):
        """Higher seed rank (lower number) should contribute positively."""
        # Setup: Insert domains with different seed ranks
        high_rank_id = uuid.uuid4()
        low_rank_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored, seed_rank)
            VALUES (%s, %s, 'active', 10, 0, 1), (%s, %s, 'active', 10, 0, 100)
            """,
            (high_rank_id, "high-rank.com", low_rank_id, "low-rank.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Get scores
        db_cursor.execute(
            "SELECT domain, priority_score FROM domains WHERE id IN (%s, %s)",
            (high_rank_id, low_rank_id),
        )
        rows = {row[0]: row[1] for row in db_cursor.fetchall()}

        # High rank (rank 1) should have higher score
        assert rows["high-rank.com"] > rows["low-rank.com"]

    def test_image_yield_contributes_to_score(self, db_cursor):
        """Higher image yield should increase priority."""
        # Setup: Insert domains with different yields
        high_yield_id = uuid.uuid4()
        low_yield_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored, seed_rank)
            VALUES (%s, %s, 'active', 100, 50, 0), (%s, %s, 'active', 100, 5, 0)
            """,
            (high_yield_id, "high-yield.com", low_yield_id, "low-yield.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Get scores
        db_cursor.execute(
            "SELECT domain, priority_score FROM domains WHERE id IN (%s, %s)",
            (high_yield_id, low_yield_id),
        )
        rows = {row[0]: row[1] for row in db_cursor.fetchall()}

        # High yield should have higher score
        assert rows["high-yield.com"] > rows["low-yield.com"]

    def test_error_rate_penalizes_score(self, db_cursor):
        """Higher error rate should decrease priority."""
        # Setup: Insert domains with different error rates
        low_error_id = uuid.uuid4()
        high_error_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored, total_error_count, seed_rank)
            VALUES (%s, %s, 'active', 100, 50, 5, 0), (%s, %s, 'active', 100, 50, 50, 0)
            """,
            (low_error_id, "low-error.com", high_error_id, "high-error.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Get scores
        db_cursor.execute(
            "SELECT domain, priority_score FROM domains WHERE id IN (%s, %s)",
            (low_error_id, high_error_id),
        )
        rows = {row[0]: row[1] for row in db_cursor.fetchall()}

        # Low error should have higher score
        assert rows["low-error.com"] > rows["high-error.com"]

    def test_staleness_contributes_to_score(self, db_cursor):
        """Domains not crawled recently should get higher priority."""
        # Setup: Insert domains with different last_crawled_at
        stale_id = uuid.uuid4()
        recent_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, images_stored, last_crawled_at, seed_rank)
            VALUES (%s, %s, 'active', 10, 5, CURRENT_TIMESTAMP - INTERVAL '30 days', 0),
                   (%s, %s, 'active', 10, 5, CURRENT_TIMESTAMP - INTERVAL '1 day', 0)
            """,
            (stale_id, "stale.com", recent_id, "recent.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Get scores
        db_cursor.execute(
            "SELECT domain, priority_score FROM domains WHERE id IN (%s, %s)",
            (stale_id, recent_id),
        )
        rows = {row[0]: row[1] for row in db_cursor.fetchall()}

        # Stale domain should have higher score
        assert rows["stale.com"] > rows["recent.com"]

    def test_pages_remaining_contributes_to_score(self, db_cursor):
        """Domains with more pages remaining should get higher priority."""
        # Setup: Insert domains with different pages remaining
        more_remaining_id = uuid.uuid4()
        less_remaining_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, pages_crawled, pages_discovered, images_stored, seed_rank)
            VALUES (%s, %s, 'active', 100, 500, 0, 0), (%s, %s, 'active', 100, 150, 0, 0)
            """,
            (more_remaining_id, "more-remaining.com", less_remaining_id, "less-remaining.com"),
        )
        db_cursor.commit()

        # Recalculate
        recalculate_priorities()

        # Get scores
        db_cursor.execute(
            "SELECT domain, priority_score FROM domains WHERE id IN (%s, %s)",
            (more_remaining_id, less_remaining_id),
        )
        rows = {row[0]: row[1] for row in db_cursor.fetchall()}

        # More remaining pages should have higher score
        assert rows["more-remaining.com"] > rows["less-remaining.com"]
