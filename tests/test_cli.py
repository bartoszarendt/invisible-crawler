"""Tests for CLI commands (Workstream D/E)."""

import uuid
from unittest.mock import MagicMock, patch


class TestReleaseStuckClaimsCLI:
    """Test release-stuck-claims CLI command with force options."""

    def test_default_releases_expired_only(self, db_cursor):
        """Default behavior should only release expired claims."""
        from crawler.cli import release_stuck_claims_command

        # Setup: Insert expired and active claims
        expired_id = uuid.uuid4()
        active_id = uuid.uuid4()

        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
            VALUES
                (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP - INTERVAL '5 minutes'),
                (%s, %s, 'active', 'worker-2', CURRENT_TIMESTAMP + INTERVAL '20 minutes')
            """,
            (expired_id, "expired.com", active_id, "active.com"),
        )
        db_cursor.commit()

        # Run command (default: expired only)
        args = MagicMock(dry_run=False, force=False, worker_id=None, all_active=False)
        exit_code = release_stuck_claims_command(args)

        assert exit_code == 0

        # Verify: expired released, active remains
        db_cursor.execute("SELECT claimed_by FROM domains WHERE id = %s", (expired_id,))
        assert db_cursor.fetchone()[0] is None

        db_cursor.execute("SELECT claimed_by FROM domains WHERE id = %s", (active_id,))
        assert db_cursor.fetchone()[0] == "worker-2"

    def test_force_worker_id_releases_specific_worker(self, db_cursor):
        """--force --worker-id should release specific worker's claims."""
        from crawler.cli import release_stuck_claims_command

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

        # Mock user confirmation
        with patch("crawler.cli._confirm", return_value=True):
            args = MagicMock(
                dry_run=False, force=True, worker_id="worker-1", all_active=False
            )
            exit_code = release_stuck_claims_command(args)

        assert exit_code == 0

        # Verify: worker-1 claims released, worker-2 remains
        db_cursor.execute("SELECT COUNT(*) FROM domains WHERE claimed_by = 'worker-1'")
        assert db_cursor.fetchone()[0] == 0

        db_cursor.execute("SELECT COUNT(*) FROM domains WHERE claimed_by = 'worker-2'")
        assert db_cursor.fetchone()[0] == 1

    def test_force_all_active_releases_everything(self, db_cursor):
        """--force --all-active should release all claims (with confirmation)."""
        from crawler.cli import release_stuck_claims_command

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

        # Mock user confirmation
        with patch("crawler.cli._confirm", return_value=True):
            args = MagicMock(dry_run=False, force=True, worker_id=None, all_active=True)
            exit_code = release_stuck_claims_command(args)

        assert exit_code == 0

        # Verify: all claims released
        db_cursor.execute("SELECT COUNT(*) FROM domains WHERE claimed_by IS NOT NULL")
        assert db_cursor.fetchone()[0] == 0

    def test_dry_run_preview_only(self, db_cursor):
        """--dry-run should preview without making changes."""
        from crawler.cli import release_stuck_claims_command

        # Setup: Insert active claim
        domain_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO domains (id, domain, status, claimed_by, claim_expires_at)
            VALUES (%s, %s, 'active', 'worker-1', CURRENT_TIMESTAMP + INTERVAL '20 minutes')
            """,
            (domain_id, "example.com"),
        )
        db_cursor.commit()

        # Run dry-run
        args = MagicMock(dry_run=True, force=True, worker_id="worker-1", all_active=False)
        exit_code = release_stuck_claims_command(args)

        assert exit_code == 0

        # Verify: claim still active (no changes)
        db_cursor.execute("SELECT claimed_by FROM domains WHERE id = %s", (domain_id,))
        assert db_cursor.fetchone()[0] == "worker-1"

    def test_all_active_without_force_fails(self, db_cursor):
        """--all-active without --force should return error."""
        from crawler.cli import release_stuck_claims_command

        args = MagicMock(dry_run=False, force=False, worker_id=None, all_active=True)
        exit_code = release_stuck_claims_command(args)

        assert exit_code == 1  # Error


class TestCleanupStaleRunsCLI:
    """Test cleanup-stale-runs CLI command."""

    def test_marks_stale_runs_as_failed(self, db_cursor):
        """Should mark runs with no recent activity as failed."""
        from crawler.cli import cleanup_stale_runs_command

        # Setup: Insert stale and active runs
        stale_id = uuid.uuid4()
        active_id = uuid.uuid4()

        # Stale run (started 2 hours ago, no activity)
        db_cursor.execute(
            """
            INSERT INTO crawl_runs (id, started_at, status, mode)
            VALUES (%s, CURRENT_TIMESTAMP - INTERVAL '2 hours', 'running', 'discovery')
            """,
            (stale_id,),
        )

        # Active run (started 10 minutes ago)
        db_cursor.execute(
            """
            INSERT INTO crawl_runs (id, started_at, status, mode)
            VALUES (%s, CURRENT_TIMESTAMP - INTERVAL '10 minutes', 'running', 'discovery')
            """,
            (active_id,),
        )

        # Add crawl_log entry for active run (recent activity)
        db_cursor.execute(
            """
            INSERT INTO crawl_log (page_url, domain, crawled_at, crawl_run_id, status, images_downloaded)
            VALUES ('https://active.com', 'active.com', CURRENT_TIMESTAMP - INTERVAL '5 minutes',
                    %s, 200, 0)
            """,
            (active_id,),
        )
        db_cursor.commit()

        # Mock user confirmation
        with patch("crawler.cli._confirm", return_value=True):
            args = MagicMock(older_than_minutes=60, dry_run=False)
            exit_code = cleanup_stale_runs_command(args)

        assert exit_code == 0

        # Verify: stale run marked failed, active run still running
        db_cursor.execute("SELECT status FROM crawl_runs WHERE id = %s", (stale_id,))
        assert db_cursor.fetchone()[0] == "failed"

        db_cursor.execute("SELECT status FROM crawl_runs WHERE id = %s", (active_id,))
        assert db_cursor.fetchone()[0] == "running"

    def test_no_stale_runs_returns_success(self, db_cursor):
        """Should return 0 when no stale runs exist."""
        from crawler.cli import cleanup_stale_runs_command

        # Setup: Insert only active runs
        active_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO crawl_runs (id, started_at, status, mode)
            VALUES (%s, CURRENT_TIMESTAMP - INTERVAL '10 minutes', 'running', 'discovery')
            """,
            (active_id,),
        )
        db_cursor.commit()

        args = MagicMock(older_than_minutes=60, dry_run=False)
        exit_code = cleanup_stale_runs_command(args)

        assert exit_code == 0

    def test_dry_run_preview_only(self, db_cursor):
        """--dry-run should preview without marking runs."""
        from crawler.cli import cleanup_stale_runs_command

        # Setup: Insert stale run
        stale_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO crawl_runs (id, started_at, status, mode)
            VALUES (%s, CURRENT_TIMESTAMP - INTERVAL '2 hours', 'running', 'discovery')
            """,
            (stale_id,),
        )
        db_cursor.commit()

        args = MagicMock(older_than_minutes=60, dry_run=True)
        exit_code = cleanup_stale_runs_command(args)

        assert exit_code == 0

        # Verify: run still running (no changes)
        db_cursor.execute("SELECT status FROM crawl_runs WHERE id = %s", (stale_id,))
        assert db_cursor.fetchone()[0] == "running"

    def test_custom_timeout_threshold(self, db_cursor):
        """Should respect custom --older-than-minutes threshold."""
        from crawler.cli import cleanup_stale_runs_command

        # Setup: Insert run with 30 minutes inactivity
        run_id = uuid.uuid4()
        db_cursor.execute(
            """
            INSERT INTO crawl_runs (id, started_at, status, mode)
            VALUES (%s, CURRENT_TIMESTAMP - INTERVAL '30 minutes', 'running', 'discovery')
            """,
            (run_id,),
        )
        db_cursor.commit()

        # Mock user confirmation
        with patch("crawler.cli._confirm", return_value=True):
            # Threshold: 20 minutes (should match)
            args = MagicMock(older_than_minutes=20, dry_run=False)
            exit_code = cleanup_stale_runs_command(args)

        assert exit_code == 0

        # Verify: run marked failed
        db_cursor.execute("SELECT status FROM crawl_runs WHERE id = %s", (run_id,))
        assert db_cursor.fetchone()[0] == "failed"
