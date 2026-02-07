"""Domain repository for database operations.

This module provides data access functions for the domains table,
enabling domain tracking, statistics updates, and backfill operations.
"""

import logging
from typing import Any

from processor.domain_canonicalization import canonicalize_domain
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
    pages_discovered_delta: int = 0,
    images_found_delta: int = 0,
    images_stored_delta: int = 0,
    total_error_count_delta: int = 0,
    consecutive_error_count: int | None = None,
    status: str | None = None,
    last_crawl_run_id: str | None = None,
) -> bool:
    """Increment counters and update status for a domain.

    Uses += for deltas to support cumulative tracking across multiple runs.
    All updates are atomic and use the updated_at trigger.

    Args:
        domain: Canonical domain name
        pages_crawled_delta: Number of pages crawled in this run (added to total)
        pages_discovered_delta: Number of pages discovered in this run (added to total)
        images_found_delta: Number of images found in this run (added to total)
        images_stored_delta: Number of images stored in this run (added to total)
        total_error_count_delta: Number of errors in this run (added to total)
        consecutive_error_count: Absolute value for consecutive errors (reset to 0 on success)
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
                "pages_discovered = pages_discovered + %s",
                "images_found = images_found + %s",
                "images_stored = images_stored + %s",
                "total_error_count = total_error_count + %s",
                "last_crawled_at = CURRENT_TIMESTAMP",
            ]
            params: list[Any] = [
                pages_crawled_delta,
                pages_discovered_delta,
                images_found_delta,
                images_stored_delta,
                total_error_count_delta,
            ]

            if consecutive_error_count is not None:
                updates.append("consecutive_error_count = %s")
                params.append(consecutive_error_count)

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
                       first_seen_at, source, seed_rank,
                       frontier_checkpoint_id, priority_score,
                       claimed_by, claim_expires_at,
                       pages_discovered, total_error_count,
                       consecutive_error_count, block_reason,
                       frontier_size, version
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
                    "frontier_checkpoint_id": row[11],
                    "priority_score": row[12],
                    "claimed_by": row[13],
                    "claim_expires_at": row[14],
                    "pages_discovered": row[15],
                    "total_error_count": row[16],
                    "consecutive_error_count": row[17],
                    "block_reason": row[18],
                    "frontier_size": row[19],
                    "version": row[20],
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
            # Step 1: Read raw domain data from crawl_log
            logger.info("Backfilling domains from crawl_log...")
            cur.execute("""
                SELECT
                    domain,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status < 400) AS pages_ok,
                    COALESCE(SUM(images_found), 0) AS images_found,
                    COUNT(*) FILTER (WHERE status >= 400) AS error_count,
                    MIN(crawled_at) AS first_seen,
                    MAX(crawled_at) AS last_seen
                FROM crawl_log
                WHERE domain IS NOT NULL
                GROUP BY domain
            """)
            raw_rows = cur.fetchall()

            # Aggregate by canonical domain using Python-side canonicalization
            # (handles IDN, trailing dots, default ports, etc.)
            canonical_agg: dict[str, dict] = {}
            for row in raw_rows:
                raw_domain, total, pages_ok, imgs, errs, first, last = row
                try:
                    canon = canonicalize_domain(raw_domain)
                except Exception:
                    canon = raw_domain.lower().removeprefix("www.")
                entry = canonical_agg.setdefault(canon, {
                    "pages_discovered": 0,
                    "pages_crawled": 0,
                    "images_found": 0,
                    "error_count": 0,
                    "first_seen": first,
                    "last_seen": last,
                })
                entry["pages_discovered"] += total
                entry["pages_crawled"] += pages_ok
                entry["images_found"] += imgs
                entry["error_count"] += errs
                if first and (entry["first_seen"] is None or first < entry["first_seen"]):
                    entry["first_seen"] = first
                if last and (entry["last_seen"] is None or last > entry["last_seen"]):
                    entry["last_seen"] = last

            # Upsert aggregated rows
            for canon, agg in canonical_agg.items():
                status = (
                    "blocked"
                    if agg["error_count"] > agg["pages_discovered"] * 0.5
                    else "exhausted"
                )
                cur.execute(
                    """
                    INSERT INTO domains (
                        domain, status, pages_discovered, pages_crawled,
                        images_found, total_error_count, source,
                        first_seen_at, last_crawled_at
                    ) VALUES (%s, %s::domain_status, %s, %s, %s, %s,
                              'backfill_crawl_log', %s, %s)
                    ON CONFLICT (domain) DO UPDATE SET
                        pages_crawled = EXCLUDED.pages_crawled,
                        images_found = EXCLUDED.images_found,
                        last_crawled_at = EXCLUDED.last_crawled_at
                    """,
                    (
                        canon, status,
                        agg["pages_discovered"], agg["pages_crawled"],
                        agg["images_found"], agg["error_count"],
                        agg["first_seen"], agg["last_seen"],
                    ),
                )
            stats["domains_created"] = len(canonical_agg)
            logger.info(f"Backfilled {stats['domains_created']} domains from crawl_log")

            # Step 2: Update images_stored from provenance
            logger.info("Updating images_stored from provenance...")
            cur.execute("""
                SELECT source_domain, COUNT(DISTINCT image_id)
                FROM provenance
                WHERE source_domain IS NOT NULL
                GROUP BY source_domain
            """)
            prov_rows = cur.fetchall()

            # Canonicalize and aggregate
            prov_agg: dict[str, int] = {}
            for raw_domain, img_count in prov_rows:
                try:
                    canon = canonicalize_domain(raw_domain)
                except Exception:
                    canon = raw_domain.lower().removeprefix("www.")
                prov_agg[canon] = prov_agg.get(canon, 0) + img_count

            updated = 0
            for canon, img_count in prov_agg.items():
                cur.execute(
                    """
                    UPDATE domains
                    SET images_stored = %s,
                        image_yield_rate = CASE
                            WHEN pages_crawled > 0
                            THEN %s::DOUBLE PRECISION / pages_crawled
                            ELSE NULL
                        END
                    WHERE domain = %s
                    """,
                    (img_count, img_count, canon),
                )
                updated += cur.rowcount
            stats["images_stored_updated"] = updated
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
            cur.execute("""
                SELECT
                    status,
                    COUNT(*) as count,
                    AVG(pages_crawled)::INTEGER as avg_pages,
                    SUM(images_stored) as total_images
                FROM domains
                GROUP BY status
                """)
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


# Phase C: Domain Claim Protocol for multi-worker concurrency


def claim_domains(worker_id: str, batch_size: int = 10) -> list[dict]:
    """Atomically claim unclaimed or expired domains using FOR UPDATE SKIP LOCKED.

    This is the core of the Phase C concurrency protocol. Uses PostgreSQL's
    row-level locking to ensure only one worker claims each domain.

    Args:
        worker_id: Unique identifier for this worker (e.g., "hostname-pid")
        batch_size: Number of domains to claim (default: 10)

    Returns:
        List of claimed domain rows as dicts, empty list if no domains available
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                WITH candidates AS (
                    SELECT id, version, domain, frontier_checkpoint_id,
                           status, priority_score, pages_crawled, images_stored
                    FROM domains
                    WHERE status IN ('pending', 'active')
                      AND (next_crawl_after IS NULL OR next_crawl_after < CURRENT_TIMESTAMP)
                      AND (claimed_by IS NULL OR claim_expires_at < CURRENT_TIMESTAMP)
                    ORDER BY
                        CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                        priority_score DESC,
                        last_crawled_at ASC NULLS FIRST
                    LIMIT %(batch_size)s
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE domains
                SET claimed_by = %(worker_id)s,
                    claim_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 minutes',
                    version = domains.version + 1
                FROM candidates
                WHERE domains.id = candidates.id
                  AND domains.version = candidates.version
                RETURNING domains.id, domains.domain, domains.version, domains.frontier_checkpoint_id,
                          domains.status, domains.priority_score, domains.pages_crawled, domains.images_stored,
                          domains.claimed_by, domains.claim_expires_at;
                """,
                {"worker_id": worker_id, "batch_size": batch_size},
            )
            rows = cur.fetchall()

            claimed = []
            for row in rows:
                claimed.append(
                    {
                        "id": row[0],
                        "domain": row[1],
                        "version": row[2],
                        "frontier_checkpoint_id": row[3],
                        "status": row[4],
                        "priority_score": row[5],
                        "pages_crawled": row[6],
                        "images_stored": row[7],
                        "claimed_by": row[8],
                        "claim_expires_at": row[9],
                    }
                )

            # Re-sort in application code because PostgreSQL does not guarantee
            # that RETURNING rows follow the CTE's ORDER BY. The CTE ordering
            # only affects which rows FOR UPDATE SKIP LOCKED selects.
            claimed.sort(
                key=lambda item: (
                    0 if item["status"] == "active" else 1,
                    -(item["priority_score"] or 0),
                    item["domain"],
                )
            )

            if claimed:
                logger.info(f"Worker {worker_id} claimed {len(claimed)} domains")
            return claimed
    except Exception as e:
        logger.error(f"Failed to claim domains for worker {worker_id}: {e}")
        return []


def renew_claim(domain_id: str, worker_id: str) -> bool:
    """Renew domain claim (heartbeat) to extend lease.

    Workers should call this periodically (every 10 minutes) to prevent
    lease expiry during long crawls.

    Args:
        domain_id: UUID of the domain to renew
        worker_id: ID of the worker that owns the claim

    Returns:
        True if renewal succeeded, False if claim expired or not owned
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                UPDATE domains
                SET claim_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 minutes',
                    version = version + 1
                WHERE id = %(domain_id)s
                  AND claimed_by = %(worker_id)s
                  AND claim_expires_at > CURRENT_TIMESTAMP
                RETURNING id;
                """,
                {"domain_id": domain_id, "worker_id": worker_id},
            )
            success = cur.fetchone() is not None
            if success:
                logger.debug(f"Renewed claim for domain {domain_id}")
            return success
    except Exception as e:
        logger.error(f"Failed to renew claim for domain {domain_id}: {e}")
        return False


def release_claim(
    domain_id: str,
    worker_id: str,
    expected_version: int,
    **updates: Any,
) -> bool:
    """Release domain claim with atomic stats update.

    Uses optimistic locking (version check) to ensure the claim is still
    valid and owned by this worker. If status transition is requested,
    uses the state machine transition function for validation.

    Args:
        domain_id: UUID of the domain
        worker_id: ID of the worker releasing the claim
        expected_version: Expected version for optimistic lock check
        **updates: Additional columns to update (e.g., pages_crawled_delta=5)

    Returns:
        True if release succeeded, False if version mismatch or not owner
    """
    try:
        with get_cursor() as cur:
            # Check if status transition is requested
            new_status = updates.pop("status", None)
            status_transitioned = False

            # If status transition requested, validate and execute it first
            if new_status:
                # Get current status
                cur.execute(
                    """
                    SELECT status FROM domains
                    WHERE id = %(domain_id)s
                      AND claimed_by = %(worker_id)s
                      AND version = %(expected_version)s
                    FOR UPDATE;
                    """,
                    {
                        "domain_id": domain_id,
                        "worker_id": worker_id,
                        "expected_version": expected_version,
                    },
                )
                row = cur.fetchone()
                if not row:
                    logger.warning(
                        f"Failed to release claim for domain {domain_id}: "
                        f"domain not found or version mismatch"
                    )
                    return False

                current_status = row[0]

                # Use transition function to validate and perform status change
                cur.execute(
                    """
                    SELECT transition_domain_status(
                        %(domain_id)s::UUID,
                        %(current_status)s::domain_status,
                        %(new_status)s::domain_status,
                        %(worker_id)s::VARCHAR,
                        %(expected_version)s
                    );
                    """,
                    {
                        "domain_id": domain_id,
                        "current_status": current_status,
                        "new_status": new_status,
                        "worker_id": worker_id,
                        "expected_version": expected_version,
                    },
                )
                transition_result = cur.fetchone()
                if not transition_result or not transition_result[0]:
                    logger.warning(
                        f"Failed to transition domain {domain_id} status: "
                        f"{current_status} -> {new_status}"
                    )
                    return False

                # Status transition succeeded, now release claim and update stats
                # Version was incremented by transition function
                expected_version += 1
                status_transitioned = True

            # Build dynamic SET clauses for remaining updates
            set_clauses = [
                "claimed_by = NULL",
                "claim_expires_at = NULL",
            ]
            if not status_transitioned:
                set_clauses.append("version = version + 1")
            params: dict[str, Any] = {
                "domain_id": domain_id,
                "worker_id": worker_id,
                "expected_version": expected_version,
            }

            for key, value in updates.items():
                if key.endswith("_delta"):
                    # Incremental update: col = col + value
                    base_key = key.replace("_delta", "")
                    set_clauses.append(f"{base_key} = {base_key} + %({key})s")
                else:
                    # Direct assignment
                    set_clauses.append(f"{key} = %({key})s")
                params[key] = value

            cur.execute(
                f"""
                UPDATE domains
                SET {", ".join(set_clauses)}
                WHERE id = %(domain_id)s
                  AND claimed_by = %(worker_id)s
                  AND version = %(expected_version)s
                RETURNING id;
                """,
                params,
            )
            success = cur.fetchone() is not None
            if success:
                logger.debug(f"Released claim for domain {domain_id}")
            else:
                logger.warning(
                    f"Failed to release claim for domain {domain_id}: "
                    f"version mismatch or claim expired"
                )
            return success
    except Exception as e:
        logger.error(f"Failed to release claim for domain {domain_id}: {e}")
        return False


def transition_domain_status(
    domain_id: str,
    from_status: str,
    to_status: str,
    worker_id: str,
    expected_version: int,
) -> bool:
    """Transition domain status using the state machine function.

    Uses the PL/pgSQL transition_domain_status function to enforce
    valid state transitions with optimistic locking.

    Args:
        domain_id: UUID of the domain
        from_status: Current status (must match)
        to_status: Target status (must be valid transition)
        worker_id: Worker attempting the transition
        expected_version: Expected version for optimistic lock

    Returns:
        True if transition succeeded, False otherwise
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT transition_domain_status(
                    %(domain_id)s::UUID,
                    %(from_status)s::domain_status,
                    %(to_status)s::domain_status,
                    %(worker_id)s::VARCHAR,
                    %(expected_version)s
                );
                """,
                {
                    "domain_id": domain_id,
                    "from_status": from_status,
                    "to_status": to_status,
                    "worker_id": worker_id,
                    "expected_version": expected_version,
                },
            )
            result = cur.fetchone()
            success = result[0] if result else False
            if success:
                logger.debug(f"Transitioned domain {domain_id}: {from_status} -> {to_status}")
            else:
                logger.warning(
                    f"Failed to transition domain {domain_id}: {from_status} -> {to_status}"
                )
            return success
    except Exception as e:
        logger.error(f"Failed to transition domain {domain_id}: {e}")
        return False


def expire_stale_claims() -> int:
    """Expire claims that have exceeded their lease duration.

    This is a cleanup utility that should be run periodically (e.g., hourly)
    to release claims from crashed or hung workers.

    Returns:
        Number of claims that were expired
    """
    try:
        with get_cursor() as cur:
            cur.execute("""
                UPDATE domains
                SET claimed_by = NULL,
                    claim_expires_at = NULL,
                    status = CASE
                        WHEN status = 'active' THEN 'active'::domain_status
                        ELSE status
                    END
                WHERE claimed_by IS NOT NULL
                  AND claim_expires_at < CURRENT_TIMESTAMP
                RETURNING id;
                """)
            count = cur.rowcount
            if count > 0:
                logger.warning(f"Expired {count} stale domain claims")
            return count
    except Exception as e:
        logger.error(f"Failed to expire stale claims: {e}")
        return 0


def get_domains_by_status(status: str, limit: int = 50) -> list[dict[str, Any]]:
    """Get domains filtered by status.

    Args:
        status: Domain status to filter by (pending, active, exhausted, blocked, unreachable)
        limit: Maximum number of domains to return

    Returns:
        List of domain dicts
    """
    try:
        with get_cursor() as cur:
            cur.execute(
                """
                SELECT id, domain, status, pages_crawled, images_stored,
                       image_yield_rate, claimed_by, claim_expires_at,
                       priority_score, last_crawled_at, frontier_checkpoint_id
                FROM domains
                WHERE status = %s
                ORDER BY priority_score DESC, last_crawled_at ASC NULLS FIRST
                LIMIT %s
                """,
                (status, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "domain": row[1],
                    "status": row[2],
                    "pages_crawled": row[3],
                    "images_stored": row[4],
                    "image_yield_rate": row[5],
                    "claimed_by": row[6],
                    "claim_expires_at": row[7],
                    "priority_score": row[8],
                    "last_crawled_at": row[9],
                    "frontier_checkpoint_id": row[10],
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Failed to get domains by status {status}: {e}")
        return []


def get_active_claims() -> list[dict[str, Any]]:
    """Get all currently active domain claims.

    Returns:
        List of active claim info dicts with worker_id and expiry
    """
    try:
        with get_cursor() as cur:
            cur.execute("""
                SELECT claimed_by, COUNT(*) as count,
                       MIN(claim_expires_at) as earliest_expiry,
                       MAX(claim_expires_at) as latest_expiry
                FROM domains
                WHERE claimed_by IS NOT NULL
                  AND claim_expires_at > CURRENT_TIMESTAMP
                GROUP BY claimed_by
                ORDER BY count DESC
                """)
            rows = cur.fetchall()
            return [
                {
                    "worker_id": row[0],
                    "count": row[1],
                    "earliest_expiry": row[2],
                    "latest_expiry": row[3],
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Failed to get active claims: {e}")
        return []
