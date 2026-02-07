"""Frontier checkpoint storage for resume support.

This module provides Redis-based storage for frontier URLs when a domain's
crawl budget is exhausted. Checkpoints are saved as Redis sorted sets
with depth as the score for BFS ordering.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default TTL for checkpoints (30 days in seconds)
DEFAULT_CHECKPOINT_TTL = 86400 * 30


def save_checkpoint(
    domain: str,
    run_id: str,
    urls: list[dict[str, Any]],
    redis_client: Any,
    ttl: int = DEFAULT_CHECKPOINT_TTL,
) -> str:
    """Save frontier URLs to Redis sorted set.

    Stores URLs as a sorted set with depth as the score for BFS ordering.
    The checkpoint is automatically expired after the specified TTL to
    prevent Redis bloat.

    Args:
        domain: Canonical domain name (e.g., "example.com")
        run_id: Crawl run identifier
        urls: List of URL entries, each with 'url' and 'depth' keys
        redis_client: Redis client instance
        ttl: Time-to-live in seconds (default: 30 days)

    Returns:
        Checkpoint ID in format "{domain}:{run_id}"

    Example:
        >>> urls = [{'url': 'https://example.com/page1', 'depth': 1}]
        >>> checkpoint_id = save_checkpoint('example.com', 'run-123', urls, redis)
    """
    checkpoint_id = f"{domain}:{run_id}"
    key = f"frontier:{checkpoint_id}"

    if not urls:
        logger.debug(f"No URLs to save for checkpoint {checkpoint_id}")
        return checkpoint_id

    try:
        pipeline = redis_client.pipeline()

        # Store URLs as sorted set: url as member, depth as score
        for entry in urls:
            url = entry.get("url")
            depth = entry.get("depth", 0)
            if url:
                pipeline.zadd(key, {url: depth})

        # Set expiration to prevent Redis bloat
        pipeline.expire(key, ttl)
        pipeline.execute()

        logger.debug(f"Saved checkpoint {checkpoint_id} with {len(urls)} URLs")
        return checkpoint_id

    except Exception as e:
        logger.error(f"Failed to save checkpoint {checkpoint_id}: {e}")
        raise


def load_checkpoint(checkpoint_id: str, redis_client: Any) -> list[dict[str, Any]]:
    """Load frontier URLs from Redis checkpoint.

    Retrieves URLs from a Redis sorted set, ordered by depth (BFS order).

    Args:
        checkpoint_id: Checkpoint ID in format "{domain}:{run_id}"
        redis_client: Redis client instance

    Returns:
        List of URL entries with 'url' and 'depth' keys, ordered by depth

    Example:
        >>> urls = load_checkpoint('example.com:run-123', redis)
        >>> print(urls[0])
        {'url': 'https://example.com/page1', 'depth': 1}
    """
    key = f"frontier:{checkpoint_id}"

    try:
        # Get all members with scores (depth), ordered by score
        members = redis_client.zrange(key, 0, -1, withscores=True)

        result = []
        for url, depth in members:
            # Decode bytes to string if necessary
            url_str = url.decode("utf-8") if isinstance(url, bytes) else url
            result.append({"url": url_str, "depth": int(depth)})

        logger.debug(f"Loaded checkpoint {checkpoint_id} with {len(result)} URLs")
        return result

    except Exception as e:
        logger.error(f"Failed to load checkpoint {checkpoint_id}: {e}")
        raise


def delete_checkpoint(checkpoint_id: str, redis_client: Any) -> bool:
    """Delete checkpoint from Redis.

    Should be called after successful resume to free up Redis memory.

    Args:
        checkpoint_id: Checkpoint ID in format "{domain}:{run_id}"
        redis_client: Redis client instance

    Returns:
        True if checkpoint was deleted, False if it didn't exist

    Example:
        >>> delete_checkpoint('example.com:run-123', redis)
        True
    """
    key = f"frontier:{checkpoint_id}"

    try:
        result = redis_client.delete(key)
        deleted = bool(result) if result is not None else False
        if deleted:
            logger.debug(f"Deleted checkpoint {checkpoint_id}")
        else:
            logger.debug(f"Checkpoint {checkpoint_id} did not exist")
        return deleted

    except Exception as e:
        logger.error(f"Failed to delete checkpoint {checkpoint_id}: {e}")
        raise


def checkpoint_exists(checkpoint_id: str, redis_client: Any) -> bool:
    """Check if a checkpoint exists in Redis.

    Args:
        checkpoint_id: Checkpoint ID in format "{domain}:{run_id}"
        redis_client: Redis client instance

    Returns:
        True if checkpoint exists, False otherwise
    """
    key = f"frontier:{checkpoint_id}"

    try:
        exists_result = redis_client.exists(key)
        return bool(exists_result) if exists_result is not None else False
    except Exception as e:
        logger.error(f"Failed to check checkpoint existence for {checkpoint_id}: {e}")
        return False


def get_checkpoint_size(checkpoint_id: str, redis_client: Any) -> int:
    """Get the number of URLs in a checkpoint.

    Args:
        checkpoint_id: Checkpoint ID in format "{domain}:{run_id}"
        redis_client: Redis client instance

    Returns:
        Number of URLs in the checkpoint, 0 if not found
    """
    key = f"frontier:{checkpoint_id}"

    try:
        return redis_client.zcard(key) or 0
    except Exception as e:
        logger.error(f"Failed to get checkpoint size for {checkpoint_id}: {e}")
        return 0
