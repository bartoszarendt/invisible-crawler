"""Priority calculator for domain scheduling.

This module provides functions to recalculate priority scores for domains
based on yield, error rates, staleness, and other quality signals.

Used by Phase C smart scheduling to prioritize high-yield domains.
"""

import logging
from typing import Any

from storage.db import get_cursor

logger = logging.getLogger(__name__)


def recalculate_priorities() -> dict[str, int]:
    """Recalculate priority scores for all domains.

    Updates the following fields for all domains not in blocked/unreachable status:
    - image_yield_rate: images_stored / pages_crawled
    - avg_images_per_page: images_found / pages_crawled
    - error_rate: total_error_count / pages_crawled
    - priority_score: Composite score based on multiple factors

    The priority formula (from DOMAIN_TRACKING_DESIGN.md §6.2):
    - Base: COALESCE(-seed_rank, 0)
    - + image_yield_rate * 1000 (reward high-yield domains)
    - + LEAST(pages_remaining, 500) * 2 (reward incomplete domains)
    - - error_rate * 500 (penalize error-prone domains)
    - + staleness_days * 5 (reward stale domains)

    Returns:
        Dict with statistics: {'updated': N, 'pending': M, 'active': O, ...}
    """
    stats = {"updated": 0, "pending": 0, "active": 0, "exhausted": 0, "error": 0}

    try:
        with get_cursor() as cur:
            # Update quality signals and priority scores
            # This is the SQL from DOMAIN_TRACKING_DESIGN.md §6.2
            cur.execute("""
                UPDATE domains SET
                    image_yield_rate = CASE
                        WHEN pages_crawled > 0 THEN images_stored::DOUBLE PRECISION / pages_crawled
                        ELSE NULL
                    END,
                    avg_images_per_page = CASE
                        WHEN pages_crawled > 0 THEN images_found::DOUBLE PRECISION / pages_crawled
                        ELSE NULL
                    END,
                    error_rate = CASE
                        WHEN pages_crawled > 0 THEN total_error_count::DOUBLE PRECISION / pages_crawled
                        ELSE NULL
                    END,
                    priority_score = (
                        COALESCE(-seed_rank, 0)
                        + COALESCE((images_stored::DOUBLE PRECISION / NULLIF(pages_crawled, 0)) * 1000, 0)::INTEGER
                        + LEAST(GREATEST(pages_discovered - pages_crawled, 0), 500) * 2
                        - COALESCE((total_error_count::DOUBLE PRECISION / NULLIF(pages_crawled, 0)) * 500, 0)::INTEGER
                        + (EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(last_crawled_at, '2000-01-01'::TIMESTAMPTZ))) / 86400 * 5)::INTEGER
                    ),
                    priority_computed_at = CURRENT_TIMESTAMP
                WHERE status NOT IN ('blocked', 'unreachable')
                RETURNING status;
                """)

            rows = cur.fetchall()
            stats["updated"] = len(rows)

            # Count by status
            for row in rows:
                status = row[0]
                if status in stats:
                    stats[status] += 1

        logger.info(
            f"Recalculated priorities for {stats['updated']} domains "
            f"(pending: {stats['pending']}, active: {stats['active']}, "
            f"exhausted: {stats['exhausted']})"
        )
        return stats

    except Exception as e:
        logger.error(f"Failed to recalculate priorities: {e}")
        stats["error"] = 1
        return stats


def get_priority_stats() -> dict[str, Any]:
    """Get statistics about current priority distribution.

    Returns:
        Dict with priority score statistics and distribution
    """
    try:
        with get_cursor() as cur:
            # Get overall stats
            cur.execute("""
                SELECT
                    COUNT(*) as total_domains,
                    AVG(priority_score)::INTEGER as avg_score,
                    MIN(priority_score) as min_score,
                    MAX(priority_score) as max_score,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY priority_score) as median_score
                FROM domains
                WHERE status NOT IN ('blocked', 'unreachable')
                """)
            row = cur.fetchone()
            overall = {
                "total": row[0],
                "average": row[1],
                "min": row[2],
                "max": row[3],
                "median": row[4],
            }

            # Get top domains by priority
            cur.execute("""
                SELECT domain, priority_score, status, image_yield_rate, pages_crawled
                FROM domains
                WHERE status NOT IN ('blocked', 'unreachable')
                ORDER BY priority_score DESC
                LIMIT 20
                """)
            top_domains = [
                {
                    "domain": row[0],
                    "priority_score": row[1],
                    "status": row[2],
                    "image_yield_rate": row[3],
                    "pages_crawled": row[4],
                }
                for row in cur.fetchall()
            ]

            # Get score distribution by quartile
            cur.execute("""
                SELECT
                    CASE
                        WHEN priority_score >= 1000 THEN 'very_high'
                        WHEN priority_score >= 500 THEN 'high'
                        WHEN priority_score >= 0 THEN 'medium'
                        ELSE 'low'
                    END as tier,
                    COUNT(*) as count
                FROM domains
                WHERE status NOT IN ('blocked', 'unreachable')
                GROUP BY 1
                ORDER BY MIN(priority_score) DESC
                """)
            distribution = {row[0]: row[1] for row in cur.fetchall()}

            return {
                "overall": overall,
                "top_domains": top_domains,
                "distribution": distribution,
            }

    except Exception as e:
        logger.error(f"Failed to get priority stats: {e}")
        return {"error": str(e)}
