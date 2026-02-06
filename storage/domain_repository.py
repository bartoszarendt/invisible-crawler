"""Domain repository for database operations.

This module provides data access functions for the domains table,
enabling domain tracking, statistics updates, and backfill operations.
"""

import logging
from typing import Any

from storage.db import get_cursor

logger = logging.getLogger(__name__)


def upsert_domain(domain: str, source: str, seed_rank: int | None = None) -> bool:
    """Insert domain or ignore if already exists.

    Uses ON CONFLICT DO NOTHING to ensure idempotent inserts.
    This is the primary way domains enter the system during seed processing.

    Args:
        domain: Canonical domain name (e.g., "example.com")
        source: Source of the domain (e.g., "file", "redis", "tranco_20260205")
        seed_rank: Optional rank from seed list (lower = higher priority)

    Returns:
        True if domain was inserted, False if it already existed
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO domains (domain, source, seed_rank, status)
                VALUES (%s, %s, %s, 'pending')
                ON CONFLICT (domain) DO NOTHING
                RETURNING id
                """,
                (domain, source, seed_rank),
            )
            result = cur.fetchone()
            inserted = result is not None
            if inserted:
                logger.debug(f"Upserted domain: {domain} (source: {source})")
            return inserted
    except Exception as e:
        logger.error(f"Failed to upsert domain {domain!r}: {e}")
        # Don't re-raise; this is best-effort tracking
        return False


def update_domain_stats(
    domain: str,
    pages_crawled_delta: int = 0,
    images_found_delta: int = 0,
    images_stored_delta: int = 0,
    status: str | None = None,
    last_crawl_run_id: str | None = None,
) -> bool:
    """Increment counters and update status for a domain.

    Uses += for deltas to support cumulative tracking across multiple runs.
    All updates are atomic and use the updated_at trigger.

    Args:
        domain: Canonical domain name
        pages_crawled_delta: Number of pages crawled in this run (added to total)
        images_found_delta: Number of images found in this run (added to total)
        images_stored_delta: Number of images stored in this run (added to total)
        status: New status (pending, active, exhausted, blocked, unreachable)
        last_crawl_run_id: UUID of the crawl run that produced these stats

    Returns:
        True if update succeeded, False otherwise
    """
    try:
        with get_cursor() as cur:
            # Build dynamic query based on what needs updating
            updates = [
                "pages_crawled = pages_crawled + %s",
                "images_found = images_found + %s",
                "images_stored = images_stored + %s",
                "last_crawled_at = CURRENT_TIMESTAMP",
            ]
            params: list[Any] = [pages_crawled_delta, images_found_delta, images_stored_delta]

            if status is not None:
                updates.append("status = %s")
                params.append(status)

            if last_crawl_run_id is not None:
                updates.append("last_crawl_run_id = %s")
                params.append(last_crawl_run_id)

            # Calculate yield rate
            updates.append(
                "image_yield_rate = CASE WHEN pages_crawled + %s > 0 "
                "THEN (images_stored + %s)::DOUBLE PRECISION / (pages_crawled + %s) "
                "ELSE NULL END"
            )
            params.extend([pages_crawled_delta, images_stored_delta, pages_crawled_delta])

            query = f"""
                UPDATE domains
                SET {", ".join(updates)}
                WHERE domain = %s
                RETURNING id
            """
            params.append(domain)

            cur.execute(query, params)
            result = cur.fetchone()
            success = result is not None
            if success:
                logger.debug(
                    f"Updated domain stats: {domain} "
                    f"(pages: +{pages_crawled_delta}, images: +{images_stored_delta})"
                )
            return success
    except Exception as e:
        logger.error(f"Failed to update domain stats for {domain!r}: {e}")
        # Don't re-raise; this is best-effort tracking
        return False


def get_domain(domain: str) -> dict[str, Any] | None:
    """Fetch domain row as dictionary.

    Args:
        domain: Canonical domain name

    Returns:
        Domain row as dict, or None if not found
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT id, domain, status, pages_crawled, images_found,
                       images_stored, image_yield_rate, last_crawled_at,
                       first_seen_at, source, seed_rank
                FROM domains
                WHERE domain = %s
                """,
                (domain,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "domain": row[1],
                    "status": row[2],
                    "pages_crawled": row[3],
                    "images_found": row[4],
                    "images_stored": row[5],
                    "image_yield_rate": row[6],
                    "last_crawled_at": row[7],
                    "first_seen_at": row[8],
                    "source": row[9],
                    "seed_rank": row[10],
                }
            return None
    except Exception as e:
        logger.error(f"Failed to fetch domain {domain!r}: {e}")
        return None


def backfill_domains_from_crawl_log() -> dict[str, int]:
    """Backfill domains table from historical crawl_log data.

    Executes SQL from DOMAIN_TRACKING_DESIGN.md §12.1 and §12.2.
    This is idempotent - safe to rerun multiple times.

    Returns:
        Dict with backfill statistics:
        - domains_created: Number of domains inserted
        - images_stored_updated: Number of domains with image counts updated
    """
    stats = {"domains_created": 0, "images_stored_updated": 0}

    try:
        with get_cursor() as cur:
            # Step 1: Create domains from crawl_log
            logger.info("Backfilling domains from crawl_log...")
            cur.execute(
                """
                INSERT INTO domains (
                    domain, status, pages_discovered, pages_crawled,
                    images_found, total_error_count, source,
                    first_seen_at, last_crawled_at
                )
                SELECT
                    domain,
                    CASE
                        WHEN COUNT(*) FILTER (WHERE status >= 400) > COUNT(*) * 0.5
                        THEN 'blocked'::domain_status
                        ELSE 'exhausted'::domain_status
                    END AS status,
                    COUNT(*) AS pages_discovered,
                    COUNT(*) FILTER (WHERE status < 400) AS pages_crawled,
                    COALESCE(SUM(images_found), 0) AS images_found,
                    COUNT(*) FILTER (WHERE status >= 400) AS total_error_count,
                    'backfill_crawl_log' AS source,
                    MIN(crawled_at) AS first_seen_at,
                    MAX(crawled_at) AS last_crawled_at
                FROM crawl_log
                WHERE domain IS NOT NULL
                GROUP BY domain
                ON CONFLICT (domain) DO UPDATE SET
                    pages_crawled = EXCLUDED.pages_crawled,
                    images_found = EXCLUDED.images_found,
                    last_crawled_at = EXCLUDED.last_crawled_at
                """
            )
            stats["domains_created"] = cur.rowcount
            logger.info(f"Backfilled {stats['domains_created']} domains from crawl_log")

            # Step 2: Update images_stored from provenance
            logger.info("Updating images_stored from provenance...")
            cur.execute(
                """
                WITH domain_images AS (
                    SELECT
                        source_domain AS domain,
                        COUNT(DISTINCT image_id) AS images_stored
                    FROM provenance
                    WHERE source_domain IS NOT NULL
                    GROUP BY source_domain
                )
                UPDATE domains
                SET images_stored = domain_images.images_stored,
                    image_yield_rate = CASE
                        WHEN pages_crawled > 0
                        THEN domain_images.images_stored::DOUBLE PRECISION / pages_crawled
                        ELSE NULL
                    END
                FROM domain_images
                WHERE domains.domain = domain_images.domain
                """
            )
            stats["images_stored_updated"] = cur.rowcount
            logger.info(f"Updated {stats['images_stored_updated']} domains with image counts")

        return stats
    except Exception as e:
        logger.error(f"Failed to backfill domains: {e}")
        raise


def get_domain_stats_summary() -> dict[str, Any]:
    """Get summary statistics about domains table.

    Returns:
        Dict with counts by status and other aggregates
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT
                    status,
                    COUNT(*) as count,
                    AVG(pages_crawled)::INTEGER as avg_pages,
                    SUM(images_stored) as total_images
                FROM domains
                GROUP BY status
                """
            )
            status_counts = {}
            total_domains = 0
            total_images = 0
            for row in cur.fetchall():
                status_counts[row[0]] = {
                    "count": row[1],
                    "avg_pages": row[2],
                    "total_images": row[3],
                }
                total_domains += row[1]
                total_images += row[3] or 0

            return {
                "total_domains": total_domains,
                "total_images": total_images,
                "by_status": status_counts,
            }
    except Exception as e:
        logger.error(f"Failed to get domain stats: {e}")
        return {"total_domains": 0, "total_images": 0, "by_status": {}, "error": str(e)}


def update_frontier_checkpoint(domain: str, checkpoint_id: str, frontier_size: int) -> bool:
    """Update domain's frontier checkpoint reference.

    Called when a domain's crawl budget is exhausted and pending URLs
    are saved to Redis. Stores the checkpoint ID for resume on next run.

    Args:
        domain: Canonical domain name
        checkpoint_id: Redis checkpoint identifier (format: "{domain}:{run_id}")
        frontier_size: Number of URLs in the checkpoint

    Returns:
        True if update succeeded, False otherwise
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE domains
                SET frontier_checkpoint_id = %s,
                    frontier_size = %s,
                    frontier_updated_at = CURRENT_TIMESTAMP,
                    status = 'active'
                WHERE domain = %s
                RETURNING id
                """,
                (checkpoint_id, frontier_size, domain),
            )
            success = cur.fetchone() is not None
            if success:
                logger.debug(
                    f"Updated frontier checkpoint for {domain}: {checkpoint_id} ({frontier_size} URLs)"
                )
            return success
    except Exception as e:
        logger.error(f"Failed to update frontier checkpoint for {domain!r}: {e}")
        return False


def clear_frontier_checkpoint(domain: str) -> bool:
    """Clear domain's frontier checkpoint after successful resume.

    Called after a checkpoint has been successfully loaded and the domain
    has resumed crawling. Clears the checkpoint reference to indicate
    no pending URLs remain.

    Args:
        domain: Canonical domain name

    Returns:
        True if update succeeded (or domain not found), False on error
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE domains
                SET frontier_checkpoint_id = NULL,
                    frontier_size = 0,
                    frontier_updated_at = CURRENT_TIMESTAMP
                WHERE domain = %s
                RETURNING id
                """,
                (domain,),
            )
            success = cur.fetchone() is not None
            if success:
                logger.debug(f"Cleared frontier checkpoint for {domain}")
            return True  # Return True even if domain not found
    except Exception as e:
        logger.error(f"Failed to clear frontier checkpoint for {domain!r}: {e}")
        return False
