"""Tests for domain repository.

Tests database operations for domain tracking including:
- Domain upsert operations
- Stats updates
- Domain retrieval
- Backfill operations
"""

from unittest.mock import MagicMock, patch

import pytest

from storage.domain_repository import (
    backfill_domains_from_crawl_log,
    get_domain,
    get_domain_stats_summary,
    update_domain_stats,
    upsert_domain,
)


class TestUpsertDomain:
    """Test domain upsert operations."""

    @patch("storage.domain_repository.get_cursor")
    def test_upsert_new_domain(self, mock_get_cursor):
        """Test inserting a new domain."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("domain-uuid",)
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = upsert_domain("example.com", "test_file.txt", seed_rank=100)

        assert result is True
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "INSERT INTO domains" in call_args[0][0]
        assert call_args[0][1] == ("example.com", "test_file.txt", 100)

    @patch("storage.domain_repository.get_cursor")
    def test_upsert_existing_domain(self, mock_get_cursor):
        """Test upsert returns False for existing domain."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None  # ON CONFLICT DO NOTHING returns nothing
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = upsert_domain("existing.com", "file.txt")

        assert result is False

    @patch("storage.domain_repository.get_cursor")
    def test_upsert_handles_exception(self, mock_get_cursor):
        """Test that exceptions are logged and return False."""
        mock_get_cursor.side_effect = Exception("DB error")

        result = upsert_domain("example.com", "file.txt")

        assert result is False


class TestUpdateDomainStats:
    """Test domain stats update operations."""

    @patch("storage.domain_repository.get_cursor")
    def test_update_basic_stats(self, mock_get_cursor):
        """Test updating basic stats."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("domain-uuid",)
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = update_domain_stats(
            domain="example.com",
            pages_crawled_delta=10,
            images_found_delta=50,
            images_stored_delta=25,
        )

        assert result is True
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "UPDATE domains" in call_args[0][0]
        assert "pages_crawled = pages_crawled + %s" in call_args[0][0]

    @patch("storage.domain_repository.get_cursor")
    def test_update_with_status(self, mock_get_cursor):
        """Test updating stats with status change."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("domain-uuid",)
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = update_domain_stats(
            domain="example.com",
            pages_crawled_delta=5,
            status="active",
            last_crawl_run_id="run-123",
        )

        assert result is True
        call_args = mock_cursor.execute.call_args
        assert "status = %s" in call_args[0][0]

    @patch("storage.domain_repository.get_cursor")
    def test_update_domain_not_found(self, mock_get_cursor):
        """Test updating non-existent domain returns False."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = update_domain_stats(domain="nonexistent.com", pages_crawled_delta=1)

        assert result is False

    @patch("storage.domain_repository.get_cursor")
    def test_update_handles_exception(self, mock_get_cursor):
        """Test that exceptions are logged and return False."""
        mock_get_cursor.side_effect = Exception("DB error")

        result = update_domain_stats(domain="example.com", pages_crawled_delta=1)

        assert result is False


class TestGetDomain:
    """Test domain retrieval operations."""

    @patch("storage.domain_repository.get_cursor")
    def test_get_existing_domain(self, mock_get_cursor):
        """Test retrieving an existing domain."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            "domain-uuid",
            "example.com",
            "active",
            100,
            500,
            250,
            2.5,
            None,
            "2026-01-01",
            "file.txt",
            50,
        )
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = get_domain("example.com")

        assert result is not None
        assert result["domain"] == "example.com"
        assert result["status"] == "active"
        assert result["pages_crawled"] == 100
        assert result["images_found"] == 500
        assert result["images_stored"] == 250
        assert result["image_yield_rate"] == 2.5

    @patch("storage.domain_repository.get_cursor")
    def test_get_nonexistent_domain(self, mock_get_cursor):
        """Test retrieving non-existent domain returns None."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = get_domain("nonexistent.com")

        assert result is None

    @patch("storage.domain_repository.get_cursor")
    def test_get_handles_exception(self, mock_get_cursor):
        """Test that exceptions are logged and return None."""
        mock_get_cursor.side_effect = Exception("DB error")

        result = get_domain("example.com")

        assert result is None


class TestBackfillDomainsFromCrawlLog:
    """Test backfill operations."""

    @patch("storage.domain_repository.get_cursor")
    def test_backfill_creates_domains(self, mock_get_cursor):
        """Test backfill creates domains from crawl_log."""
        mock_cursor = MagicMock()
        # First call: INSERT returns rowcount
        # Second call: UPDATE returns rowcount
        mock_cursor.rowcount = 10
        mock_cursor.fetchone.return_value = None
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = backfill_domains_from_crawl_log()

        assert result["domains_created"] == 10
        assert result["images_stored_updated"] == 10
        assert mock_cursor.execute.call_count == 2

    @patch("storage.domain_repository.get_cursor")
    def test_backfill_raises_on_error(self, mock_get_cursor):
        """Test backfill raises exception on critical error."""
        mock_get_cursor.side_effect = Exception("DB error")

        with pytest.raises(Exception, match="DB error"):
            backfill_domains_from_crawl_log()


class TestGetDomainStatsSummary:
    """Test domain stats summary."""

    @patch("storage.domain_repository.get_cursor")
    def test_get_stats_summary(self, mock_get_cursor):
        """Test getting domain statistics summary."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("pending", 10, 5, 100),
            ("active", 5, 50, 500),
            ("exhausted", 20, 100, 2000),
        ]
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        result = get_domain_stats_summary()

        assert result["total_domains"] == 35
        assert result["total_images"] == 2600
        assert "pending" in result["by_status"]
        assert "active" in result["by_status"]
        assert "exhausted" in result["by_status"]

    @patch("storage.domain_repository.get_cursor")
    def test_get_stats_handles_exception(self, mock_get_cursor):
        """Test that exceptions return empty stats with error."""
        mock_get_cursor.side_effect = Exception("DB error")

        result = get_domain_stats_summary()

        assert result["total_domains"] == 0
        assert result["total_images"] == 0
        assert "error" in result
