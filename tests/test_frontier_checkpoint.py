"""Tests for frontier checkpoint storage.

Tests the Redis-based checkpoint save/load/delete operations used for
domain crawl resume support (Phase B).
"""

from unittest.mock import MagicMock

import pytest

from storage.frontier_checkpoint import (
    checkpoint_exists,
    delete_checkpoint,
    get_checkpoint_size,
    load_checkpoint,
    save_checkpoint,
)


class TestSaveCheckpoint:
    """Tests for save_checkpoint function."""

    def test_save_checkpoint_creates_sorted_set(self):
        """Checkpoint is saved as Redis sorted set with depth as score."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        urls = [
            {"url": "https://example.com/page1", "depth": 1},
            {"url": "https://example.com/page2", "depth": 2},
            {"url": "https://example.com/page3", "depth": 1},
        ]

        checkpoint_id = save_checkpoint("example.com", "run-123", urls, mock_redis)

        assert checkpoint_id == "example.com:run-123"
        # Verify pipeline was used
        mock_redis.pipeline.assert_called_once()
        # Verify zadd was called for each URL
        assert mock_pipeline.zadd.call_count == 3
        # Verify expire was set
        mock_pipeline.expire.assert_called_once_with("frontier:example.com:run-123", 2592000)
        # Verify pipeline was executed
        mock_pipeline.execute.assert_called_once()

    def test_save_checkpoint_with_custom_ttl(self):
        """Custom TTL is respected."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        urls = [{"url": "https://example.com/page1", "depth": 1}]

        save_checkpoint("example.com", "run-123", urls, mock_redis, ttl=3600)

        mock_pipeline.expire.assert_called_once_with("frontier:example.com:run-123", 3600)

    def test_save_checkpoint_empty_urls(self):
        """Empty URL list returns checkpoint ID without touching Redis."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        checkpoint_id = save_checkpoint("example.com", "run-123", [], mock_redis)

        assert checkpoint_id == "example.com:run-123"
        # Early return: no pipeline operations for empty list
        mock_pipeline.zadd.assert_not_called()
        mock_pipeline.expire.assert_not_called()
        mock_pipeline.execute.assert_not_called()

    def test_save_checkpoint_skips_invalid_entries(self):
        """Entries without 'url' key are skipped."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        urls = [
            {"url": "https://example.com/page1", "depth": 1},
            {"depth": 2},  # Missing url
            {"url": "https://example.com/page3"},  # Missing depth (default 0)
        ]

        save_checkpoint("example.com", "run-123", urls, mock_redis)

        # Only 2 valid URLs should be added
        assert mock_pipeline.zadd.call_count == 2

    def test_save_checkpoint_raises_on_redis_error(self):
        """Redis errors are propagated."""
        mock_redis = MagicMock()
        mock_redis.pipeline.side_effect = Exception("Redis connection failed")

        urls = [{"url": "https://example.com/page1", "depth": 1}]

        with pytest.raises(Exception, match="Redis connection failed"):
            save_checkpoint("example.com", "run-123", urls, mock_redis)


class TestLoadCheckpoint:
    """Tests for load_checkpoint function."""

    def test_load_checkpoint_returns_ordered_urls(self):
        """URLs are returned ordered by depth (BFS order)."""
        mock_redis = MagicMock()
        # Redis returns list of (member, score) tuples
        mock_redis.zrange.return_value = [
            (b"https://example.com/page1", 1.0),
            (b"https://example.com/page3", 1.0),
            (b"https://example.com/page2", 2.0),
        ]

        result = load_checkpoint("example.com:run-123", mock_redis)

        assert len(result) == 3
        assert result[0] == {"url": "https://example.com/page1", "depth": 1}
        assert result[1] == {"url": "https://example.com/page3", "depth": 1}
        assert result[2] == {"url": "https://example.com/page2", "depth": 2}

    def test_load_checkpoint_handles_string_members(self):
        """Handles both bytes and string members from Redis."""
        mock_redis = MagicMock()
        mock_redis.zrange.return_value = [
            ("https://example.com/page1", 1.0),  # String instead of bytes
            (b"https://example.com/page2", 2.0),
        ]

        result = load_checkpoint("example.com:run-123", mock_redis)

        assert result[0]["url"] == "https://example.com/page1"
        assert result[1]["url"] == "https://example.com/page2"

    def test_load_checkpoint_empty_checkpoint(self):
        """Empty checkpoint returns empty list."""
        mock_redis = MagicMock()
        mock_redis.zrange.return_value = []

        result = load_checkpoint("example.com:run-123", mock_redis)

        assert result == []

    def test_load_checkpoint_uses_correct_key(self):
        """Correct Redis key format is used."""
        mock_redis = MagicMock()
        mock_redis.zrange.return_value = []

        load_checkpoint("example.com:run-123", mock_redis)

        mock_redis.zrange.assert_called_once_with(
            "frontier:example.com:run-123", 0, -1, withscores=True
        )

    def test_load_checkpoint_raises_on_redis_error(self):
        """Redis errors are propagated."""
        mock_redis = MagicMock()
        mock_redis.zrange.side_effect = Exception("Redis connection failed")

        with pytest.raises(Exception, match="Redis connection failed"):
            load_checkpoint("example.com:run-123", mock_redis)


class TestDeleteCheckpoint:
    """Tests for delete_checkpoint function."""

    def test_delete_checkpoint_returns_true_on_success(self):
        """Returns True when checkpoint exists and is deleted."""
        mock_redis = MagicMock()
        mock_redis.delete.return_value = 1  # 1 key deleted

        result = delete_checkpoint("example.com:run-123", mock_redis)

        assert result is True
        mock_redis.delete.assert_called_once_with("frontier:example.com:run-123")

    def test_delete_checkpoint_returns_false_when_not_exists(self):
        """Returns False when checkpoint doesn't exist."""
        mock_redis = MagicMock()
        mock_redis.delete.return_value = 0  # No keys deleted

        result = delete_checkpoint("example.com:run-123", mock_redis)

        assert result is False

    def test_delete_checkpoint_raises_on_redis_error(self):
        """Redis errors are propagated."""
        mock_redis = MagicMock()
        mock_redis.delete.side_effect = Exception("Redis connection failed")

        with pytest.raises(Exception, match="Redis connection failed"):
            delete_checkpoint("example.com:run-123", mock_redis)


class TestCheckpointExists:
    """Tests for checkpoint_exists function."""

    def test_checkpoint_exists_returns_true(self):
        """Returns True when checkpoint exists."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1

        result = checkpoint_exists("example.com:run-123", mock_redis)

        assert result is True
        mock_redis.exists.assert_called_once_with("frontier:example.com:run-123")

    def test_checkpoint_exists_returns_false(self):
        """Returns False when checkpoint doesn't exist."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0

        result = checkpoint_exists("example.com:run-123", mock_redis)

        assert result is False

    def test_checkpoint_exists_returns_false_on_error(self):
        """Returns False on Redis error (graceful degradation)."""
        mock_redis = MagicMock()
        mock_redis.exists.side_effect = Exception("Redis connection failed")

        result = checkpoint_exists("example.com:run-123", mock_redis)

        assert result is False


class TestGetCheckpointSize:
    """Tests for get_checkpoint_size function."""

    def test_get_checkpoint_size_returns_count(self):
        """Returns number of URLs in checkpoint."""
        mock_redis = MagicMock()
        mock_redis.zcard.return_value = 42

        result = get_checkpoint_size("example.com:run-123", mock_redis)

        assert result == 42
        mock_redis.zcard.assert_called_once_with("frontier:example.com:run-123")

    def test_get_checkpoint_size_returns_zero_when_empty(self):
        """Returns 0 when checkpoint is empty."""
        mock_redis = MagicMock()
        mock_redis.zcard.return_value = 0

        result = get_checkpoint_size("example.com:run-123", mock_redis)

        assert result == 0

    def test_get_checkpoint_size_returns_zero_when_not_exists(self):
        """Returns 0 when checkpoint doesn't exist."""
        mock_redis = MagicMock()
        mock_redis.zcard.return_value = None

        result = get_checkpoint_size("example.com:run-123", mock_redis)

        assert result == 0

    def test_get_checkpoint_size_returns_zero_on_error(self):
        """Returns 0 on Redis error (graceful degradation)."""
        mock_redis = MagicMock()
        mock_redis.zcard.side_effect = Exception("Redis connection failed")

        result = get_checkpoint_size("example.com:run-123", mock_redis)

        assert result == 0


class TestCheckpointIntegration:
    """Integration-style tests for checkpoint workflow."""

    def test_full_checkpoint_lifecycle(self):
        """Save, load, and delete a checkpoint."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        urls = [
            {"url": "https://example.com/page1", "depth": 1},
            {"url": "https://example.com/page2", "depth": 2},
        ]

        # Save
        checkpoint_id = save_checkpoint("example.com", "run-123", urls, mock_redis)
        assert checkpoint_id == "example.com:run-123"

        # Reset mock for load
        mock_redis.reset_mock()
        mock_redis.zrange.return_value = [
            (b"https://example.com/page1", 1.0),
            (b"https://example.com/page2", 2.0),
        ]

        # Load
        loaded = load_checkpoint(checkpoint_id, mock_redis)
        assert len(loaded) == 2

        # Reset mock for delete
        mock_redis.reset_mock()
        mock_redis.delete.return_value = 1

        # Delete
        deleted = delete_checkpoint(checkpoint_id, mock_redis)
        assert deleted is True

    def test_checkpoint_survives_empty_url_list(self):
        """Checkpoint can be saved with empty URL list."""
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline

        checkpoint_id = save_checkpoint("example.com", "run-123", [], mock_redis)

        # Should still create checkpoint (with just expire)
        assert checkpoint_id == "example.com:run-123"
