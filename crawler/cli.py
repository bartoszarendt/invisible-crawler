"""CLI commands for InvisibleCrawler.

Provides management commands for:
- Seed ingestion from various sources
- Crawl run management
- Queue inspection
"""

import argparse
import csv
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.redis_keys import (
    requests_key,
    seen_domains_key,
    start_urls_key,
)
from crawler.scheduler import check_redis_available
from env_config import get_redis_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _redis_from_url(redis_url: str) -> Any:
    import redis

    redis_module = cast(Any, redis)
    return redis_module.from_url(redis_url)


def ingest_seeds_command(args: argparse.Namespace) -> int:
    """Ingest seeds from a source into the URL frontier.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    source = args.source
    limit = args.limit
    offset = args.offset
    redis_url = args.redis_url or get_redis_url()

    logger.info(f"Ingesting seeds from {source}")
    logger.info(f"Limit: {limit}, Offset: {offset}")

    # Check Redis availability
    if not check_redis_available(redis_url):
        logger.error("Redis is not available. Cannot ingest seeds.")
        return 1

    # Determine source file path
    source_file: Path | None
    if source == "tranco":
        source_file = Path("config/tranco_top1m.csv")
    elif source == "majestic":
        source_file = Path("config/majestic_million.csv")
    elif source == "custom":
        source_file = Path(args.file) if args.file else None
    else:
        logger.error(f"Unknown source: {source}")
        return 1

    if not source_file or not source_file.exists():
        logger.error(f"Source file not found: {source_file}")
        return 1

    # Parse and ingest seeds
    try:
        stats = ingest_from_csv(
            source_file=source_file,
            redis_url=redis_url,
            limit=limit,
            offset=offset,
            source_name=source,
        )

        logger.info("=" * 50)
        logger.info("Ingestion Complete:")
        logger.info(f"  Total rows read: {stats['rows_read']}")
        logger.info(f"  Seeds ingested: {stats['seeds_ingested']}")
        logger.info(f"  Duplicates skipped: {stats['duplicates_skipped']}")
        logger.info(f"  Errors: {stats['errors']}")
        logger.info("=" * 50)

        return 0

    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        return 1


def ingest_from_csv(
    source_file: Path,
    redis_url: str,
    limit: int,
    offset: int,
    source_name: str,
) -> dict[str, int]:
    """Ingest domains from a CSV file into Redis.

    Args:
        source_file: Path to CSV file.
        redis_url: Redis connection URL.
        limit: Maximum number of domains to ingest.
        offset: Number of rows to skip at start.
        source_name: Name of the source (for logging).

    Returns:
        Dictionary with ingestion statistics.
    """
    stats = {
        "rows_read": 0,
        "seeds_ingested": 0,
        "duplicates_skipped": 0,
        "errors": 0,
    }

    client = _redis_from_url(redis_url)
    queue_key = start_urls_key("discovery")
    seen_key = seen_domains_key("discovery")

    with open(source_file, encoding="utf-8") as f:
        reader = csv.reader(f)

        for row_num, row in enumerate(reader, start=1):
            stats["rows_read"] += 1

            # Skip rows before offset
            if row_num <= offset:
                continue

            # Stop after limit
            if limit > 0 and stats["seeds_ingested"] >= limit:
                break

            try:
                # Parse row (expecting: rank, domain or just domain)
                if len(row) >= 2:
                    rank = int(row[0])
                    domain = row[1].strip()
                elif len(row) == 1:
                    domain = row[0].strip()
                    rank = row_num
                else:
                    continue

                if not domain:
                    continue

                # Ensure domain has scheme
                if not domain.startswith(("http://", "https://")):
                    url = f"https://{domain}"
                else:
                    url = domain

                # Check for duplicates using Redis set
                domain_key = domain.replace("https://", "").replace("http://", "").rstrip("/")
                is_new = client.sadd(seen_key, domain_key)

                if not is_new:
                    stats["duplicates_skipped"] += 1
                    continue

                # Calculate priority (lower rank = higher priority)
                # Use negative rank so higher ranks (lower numbers) get higher priority
                priority = -rank

                # Add to queue with priority
                # Format: priority::url for sorted set
                client.zadd(queue_key, {url: priority})

                stats["seeds_ingested"] += 1

                if stats["seeds_ingested"] % 1000 == 0:
                    logger.info(f"Ingested {stats['seeds_ingested']} seeds...")

            except Exception as e:
                logger.warning(f"Error processing row {row_num}: {e}")
                stats["errors"] += 1

    return stats


def list_runs_command(args: argparse.Namespace) -> int:
    """List recent crawl runs.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    limit = args.limit

    try:
        from storage.db import get_cursor

        with get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, started_at, completed_at, mode,
                       pages_crawled, images_found, images_downloaded
                FROM crawl_runs
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            )

            rows = cursor.fetchall()

            if not rows:
                print("No crawl runs found.")
                return 0

            print(f"\n{'ID':<36} {'Started':<20} {'Mode':<12} {'Pages':<8} {'Images':<8}")
            print("-" * 90)

            for row in rows:
                run_id, started_at, completed_at, mode, pages, images_found, images_dl = row
                print(
                    f"{str(run_id):<36} {str(started_at):<20} {mode:<12} "
                    f"{pages or 0:<8} {images_dl or 0:<8}"
                )

            print()
            return 0

    except Exception as e:
        logger.error(f"Failed to list runs: {e}")
        return 1


def queue_status_command(args: argparse.Namespace) -> int:
    """Show queue status.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    redis_url = args.redis_url or get_redis_url()

    if not check_redis_available(redis_url):
        logger.error("Redis is not available.")
        return 1

    try:
        client = _redis_from_url(redis_url)

        # Get queue info
        start_urls_redis_key = start_urls_key("discovery")
        requests_redis_key = requests_key("discovery")
        seen_key = seen_domains_key("discovery")

        start_urls_size = client.zcard(start_urls_redis_key)
        requests_size = client.zcard(requests_redis_key)
        seen_count = client.scard(seen_key)

        print("\nQueue Status:")
        print(f"  Start URLs (seeds): {start_urls_size}")
        print(f"  Scheduled requests: {requests_size}")
        print(f"  Unique domains seen: {seen_count}")

        # Get domain counts if available
        domain_counts_key = f"{requests_redis_key}:domain_counts"
        if client.exists(domain_counts_key):
            domain_counts = client.hgetall(domain_counts_key)
            print(f"  Per-domain pending: {len(domain_counts)} domains")

        print()
        return 0

    except Exception as e:
        logger.error(f"Failed to get queue status: {e}")
        return 1


def backfill_domains_command(args: argparse.Namespace) -> int:
    """Backfill domains table from historical crawl_log data.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    dry_run = args.dry_run

    try:
        from storage.domain_repository import backfill_domains_from_crawl_log

        if dry_run:
            logger.info("DRY RUN: Would backfill domains from crawl_log")
            return 0

        logger.info("Starting backfill of domains from crawl_log...")
        stats = backfill_domains_from_crawl_log()

        logger.info("=" * 50)
        logger.info("Backfill Complete:")
        logger.info(f"  Domains created/updated: {stats['domains_created']}")
        logger.info(f"  Domains with image counts: {stats['images_stored_updated']}")
        logger.info("=" * 50)

        return 0

    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        return 1


def domain_status_command(args: argparse.Namespace) -> int:
    """Show domain status summary or list domains by status.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    status_filter = args.status
    limit = args.limit

    try:
        # If no status filter, show summary
        if not status_filter:
            from storage.domain_repository import get_domain_stats_summary

            stats = get_domain_stats_summary()

            if "error" in stats:
                logger.error(f"Failed to get domain stats: {stats['error']}")
                return 1

            print(f"\n{'STATUS':<15} {'COUNT':<10} {'AVG PAGES':<12} {'TOTAL IMAGES':<15}")
            print("-" * 55)

            for status, data in sorted(stats["by_status"].items()):
                print(
                    f"{status:<15} {data['count']:<10} {data['avg_pages'] or 0:<12} {data['total_images'] or 0:<15}"
                )

            print("-" * 55)
            print(
                f"{'TOTAL':<15} {stats['total_domains']:<10} {'':<12} {stats['total_images']:<15}"
            )
            print()

            return 0

        # Show domains with specific status
        from storage.domain_repository import get_domains_by_status

        domains = get_domains_by_status(status_filter, limit=limit)

        if not domains:
            print(f"No domains found with status '{status_filter}'")
            return 0

        print(f"\nDomains with status '{status_filter}' (limit: {limit}):")
        print(f"{'DOMAIN':<40} {'PRIORITY':<10} {'PAGES':<10} {'IMAGES':<10} {'CLAIMED BY':<20}")
        print("-" * 100)

        for domain in domains:
            claimed = domain.get("claimed_by", "") or ""
            expires_at = domain.get("claim_expires_at")
            if expires_at:
                try:
                    claimed += f" (expires {expires_at.strftime('%H:%M')})"
                except (AttributeError, ValueError):
                    claimed += f" (expires {expires_at})"
            print(
                f"{domain['domain']:<40} {domain['priority_score']:<10} "
                f"{domain['pages_crawled'] or 0:<10} {domain['images_stored'] or 0:<10} "
                f"{claimed[:38]:<38}"
            )

        print()
        return 0

    except Exception as e:
        logger.error(f"Failed to get domain status: {e}")
        return 1


def domain_info_command(args: argparse.Namespace) -> int:
    """Show detailed information about a specific domain.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    domain = args.domain

    try:
        from storage.domain_repository import get_domain

        info = get_domain(domain)

        if not info:
            print(f"Domain '{domain}' not found")
            return 1

        print(f"\nDomain: {info['domain']}")
        print(f"ID: {info['id']}")
        print(f"Status: {info['status']}")
        print(f"Source: {info.get('source', 'N/A')}")
        print(f"Seed Rank: {info.get('seed_rank', 'N/A')}")
        print(f"Version: {info.get('version', 0)}")
        print()
        print(f"Pages Crawled: {info['pages_crawled']}")
        print(f"Pages Discovered: {info.get('pages_discovered', 0)}")
        print(f"Images Found: {info['images_found']}")
        print(f"Images Stored: {info['images_stored']}")
        print(
            f"Image Yield Rate: {info['image_yield_rate']:.4f}"
            if info["image_yield_rate"]
            else "Image Yield Rate: N/A"
        )
        print()
        print(f"Total Errors: {info.get('total_error_count', 0)}")
        print(f"Consecutive Errors: {info.get('consecutive_error_count', 0)}")
        if info.get("block_reason"):
            print(f"Block Reason: {info['block_reason']}")
        print()
        print(f"First Seen: {info['first_seen_at']}")
        print(f"Last Crawled: {info['last_crawled_at'] or 'Never'}")
        print(f"Priority Score: {info.get('priority_score', 'N/A')}")
        print()
        if info.get("frontier_checkpoint_id"):
            print(f"Frontier Checkpoint: {info['frontier_checkpoint_id']}")
            print(f"Frontier Size: {info.get('frontier_size', 0)} URLs")
        if info.get("claimed_by"):
            print(f"Claimed By: {info['claimed_by']}")
            expires_at = info.get("claim_expires_at")
            if expires_at:
                try:
                    print(f"Claim Expires: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}")
                except (AttributeError, ValueError):
                    print(f"Claim Expires: {expires_at}")
        print()

        return 0

    except Exception as e:
        logger.error(f"Failed to get domain info: {e}")
        return 1


def recalculate_priorities_command(args: argparse.Namespace) -> int:
    """Recalculate priority scores for all domains.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    dry_run = args.dry_run

    try:
        from storage.priority_calculator import recalculate_priorities

        if dry_run:
            print("DRY RUN: Would recalculate priorities for all domains")
            return 0

        print("Recalculating domain priorities...")
        stats = recalculate_priorities()

        print(f"\nUpdated {stats['updated']} domains:")
        print(f"  Pending: {stats['pending']}")
        print(f"  Active: {stats['active']}")
        print(f"  Exhausted: {stats['exhausted']}")

        return 0

    except Exception as e:
        logger.error(f"Failed to recalculate priorities: {e}")
        return 1


def release_stuck_claims_command(args: argparse.Namespace) -> int:
    """Release stuck/expired domain claims.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    dry_run = args.dry_run
    force = args.force
    worker_id = args.worker_id
    all_active = args.all_active

    try:
        from storage.domain_repository import (
            expire_stale_claims,
            force_release_all_claims,
            force_release_worker_claims,
            get_active_claims,
            preview_claims_by_worker,
        )

        # Validation: --all-active requires --force
        if all_active and not force:
            print("Error: --all-active requires --force flag for safety")
            return 1

        # Validation: --worker-id requires --force
        if worker_id and not force:
            print("Error: --worker-id requires --force flag for safety")
            return 1

        # Show current active claims
        active_claims = get_active_claims()
        if active_claims:
            print("\nActive claims before cleanup:")
            for claim in active_claims:
                print(
                    f"  {claim['worker_id']}: {claim['count']} domains "
                    f"(expires: {claim['earliest_expiry']} - {claim['latest_expiry']})"
                )
        else:
            print("\nNo active claims found")

        # Default behavior: release expired claims only (backward compatible)
        if not force:
            if dry_run:
                print("\nDRY RUN: Would release expired claims")
                return 0

            count = expire_stale_claims()
            print(f"\nReleased {count} expired claims")

            # Show remaining active claims
            active_claims = get_active_claims()
            if active_claims:
                print("\nRemaining active claims:")
                for claim in active_claims:
                    print(f"  {claim['worker_id']}: {claim['count']} domains")

            return 0

        # Force-release logic
        if worker_id:
            # Targeted: release specific worker's claims
            domains = preview_claims_by_worker(worker_id)
            if not domains:
                print(f"\nNo active claims found for worker: {worker_id}")
                return 0

            print(f"\nWould release {len(domains)} claims for worker: {worker_id}")
            print("Sample domains:")
            for d in domains[:5]:
                print(f"  {d['domain']} (status: {d['status']})")
            if len(domains) > 5:
                print(f"  ... and {len(domains) - 5} more")

            if dry_run:
                print("\nDRY RUN: No changes made")
                return 0

            if not _confirm("Proceed with force-release?"):
                print("Aborted")
                return 1

            count = force_release_worker_claims(worker_id)
            print(f"\nForce-released {count} claims for worker: {worker_id}")

        elif all_active:
            # Global: release all active claims
            total_count = sum(c["count"] for c in active_claims)
            print(f"\nWould release {total_count} active claims across all workers")
            print("\nWARNING: This will release ALL active claims, including from running workers!")

            if dry_run:
                print("\nDRY RUN: No changes made")
                return 0

            if not _confirm("Proceed with global force-release?"):
                print("Aborted")
                return 1

            count = force_release_all_claims()
            print(f"\nForce-released {count} claims globally")

        # Show final state
        active_claims = get_active_claims()
        if active_claims:
            print("\nRemaining active claims:")
            for claim in active_claims:
                print(f"  {claim['worker_id']}: {claim['count']} domains")
        else:
            print("\nNo active claims remaining")

        return 0

    except Exception as e:
        logger.error(f"Failed to release stuck claims: {e}")
        return 1


def _confirm(message: str) -> bool:
    """Prompt user for confirmation.

    Args:
        message: Confirmation message.

    Returns:
        True if user confirms, False otherwise.
    """
    response = input(f"{message} [y/N]: ").strip().lower()
    return response in ("y", "yes")


def cleanup_stale_runs_command(args: argparse.Namespace) -> int:
    """Mark stale crawl_runs as failed.

    Identifies runs with no recent activity and marks them as failed
    to clean up operational state.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    older_than_minutes = args.older_than_minutes
    dry_run = args.dry_run

    try:
        from datetime import datetime, timedelta

        from storage.db import get_cursor

        threshold_timestamp = datetime.now() - timedelta(minutes=older_than_minutes)

        with get_cursor() as cur:
            # Find stale runs: no recent activity and still 'running'
            cur.execute(
                """
                WITH run_activity AS (
                    SELECT cr.id, cr.started_at, cr.status,
                           COALESCE(MAX(cl.crawled_at), cr.started_at) AS last_activity
                    FROM crawl_runs cr
                    LEFT JOIN crawl_log cl ON cl.crawl_run_id = cr.id
                    WHERE cr.status = 'running'
                    GROUP BY cr.id, cr.started_at, cr.status
                )
                SELECT id, started_at, last_activity,
                       EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_activity))/60 AS minutes_idle
                FROM run_activity
                WHERE last_activity < %s
                ORDER BY last_activity ASC
                """,
                (threshold_timestamp,),
            )

            stale_runs = cur.fetchall()

            if not stale_runs:
                print(f"\\nNo stale runs found (threshold: {older_than_minutes} minutes)")
                return 0

            print(f"\\nFound {len(stale_runs)} stale runs:")
            for run_id, started, last_activity, minutes_idle in stale_runs:
                print(
                    f"  Run {run_id}: started {started}, "
                    f"last activity {last_activity} ({int(minutes_idle)} min ago)"
                )

            if dry_run:
                print("\\nDRY RUN: No changes made")
                return 0

            if not _confirm("Mark these runs as failed?"):
                print("Aborted")
                return 0

            # Mark as failed
            run_ids = [r[0] for r in stale_runs]
            cur.execute(
                """
                UPDATE crawl_runs
                SET status = 'failed',
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = 'Marked stale: no activity timeout'
                WHERE id = ANY(%s)
                """,
                (run_ids,),
            )

            print(f"\\nMarked {cur.rowcount} runs as failed")
            return 0

    except Exception as e:
        logger.error(f"Failed to cleanup stale runs: {e}")
        return 1


def domain_reset_command(args: argparse.Namespace) -> int:
    """Reset a domain for re-crawling.

    Clears crawl stats and sets status back to 'pending' so the domain
    will be picked up again by the scheduler.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    domain = args.domain
    force = args.force

    try:
        from storage.domain_repository import get_domain

        info = get_domain(domain)
        if not info:
            print(f"Domain '{domain}' not found")
            return 1

        print(f"\nDomain: {info['domain']}")
        print(f"Current Status: {info['status']}")
        print(f"Pages Crawled: {info['pages_crawled']}")
        print(f"Images Stored: {info['images_stored']}")

        if not force:
            confirm = input("\nReset this domain? This will clear all stats. [y/N] ")
            if confirm.lower() not in ("y", "yes"):
                print("Aborted.")
                return 0

        from storage.db import get_cursor

        with get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE domains
                SET status = 'pending',
                    pages_crawled = 0,
                    pages_discovered = 0,
                    images_found = 0,
                    images_stored = 0,
                    total_error_count = 0,
                    consecutive_error_count = 0,
                    image_yield_rate = NULL,
                    last_crawled_at = NULL,
                    last_crawl_run_id = NULL,
                    frontier_checkpoint_id = NULL,
                    frontier_size = 0,
                    claimed_by = NULL,
                    claim_expires_at = NULL,
                    block_reason = NULL,
                    block_reason_code = NULL,
                    version = version + 1
                WHERE domain = %s
                RETURNING id
                """,
                (domain,),
            )
            result = cursor.fetchone()
            if result:
                print(f"\nDomain '{domain}' has been reset to 'pending' status.")
            else:
                print(f"\nFailed to reset domain '{domain}'.")
                return 1

        return 0

    except Exception as e:
        logger.error(f"Failed to reset domain: {e}")
        return 1


def cleanup_fingerprints_command(args: argparse.Namespace) -> int:
    """Clean up old URL fingerprints from persistent dupefilter.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    dry_run = args.dry_run
    redis_url = args.redis_url or get_redis_url()

    try:
        import redis

        client: Any = redis.from_url(redis_url)  # type: ignore[no-untyped-call]
        key = "dupefilter:fingerprints"

        if not client.exists(key):
            print("\nNo fingerprints found")
            return 0

        all_fps = client.smembers(key)

        print(f"\nTotal fingerprints: {len(all_fps)}")

        if dry_run:
            print(f"DRY RUN: Would delete {len(all_fps)} fingerprints")
            return 0

        count = client.srem(key, *all_fps)
        print(f"Deleted {count} fingerprints")
        return 0

    except Exception as e:
        logger.error(f"Failed to cleanup fingerprints: {e}")
        return 1


def main() -> int:
    """Main CLI entry point.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        description="InvisibleCrawler Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ingest-seeds command
    ingest_parser = subparsers.add_parser(
        "ingest-seeds",
        help="Ingest seed domains into the URL frontier",
    )
    ingest_parser.add_argument(
        "--source",
        choices=["tranco", "majestic", "custom"],
        default="tranco",
        help="Seed source (default: tranco)",
    )
    ingest_parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Maximum number of seeds to ingest (default: 10000)",
    )
    ingest_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of rows to skip (default: 0)",
    )
    ingest_parser.add_argument(
        "--file",
        type=str,
        help="Path to custom seed file (for --source=custom)",
    )
    ingest_parser.add_argument(
        "--redis-url",
        type=str,
        help="Redis connection URL (default: from REDIS_URL env var)",
    )
    ingest_parser.set_defaults(func=ingest_seeds_command)

    # list-runs command
    runs_parser = subparsers.add_parser(
        "list-runs",
        help="List recent crawl runs",
    )
    runs_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of runs to show (default: 10)",
    )
    runs_parser.set_defaults(func=list_runs_command)

    # queue-status command
    queue_parser = subparsers.add_parser(
        "queue-status",
        help="Show URL queue status",
    )
    queue_parser.add_argument(
        "--redis-url",
        type=str,
        help="Redis connection URL (default: from REDIS_URL env var)",
    )
    queue_parser.set_defaults(func=queue_status_command)

    # backfill-domains command
    backfill_parser = subparsers.add_parser(
        "backfill-domains",
        help="Backfill domains table from historical crawl_log data",
    )
    backfill_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    backfill_parser.set_defaults(func=backfill_domains_command)

    # domain-status command
    domain_parser = subparsers.add_parser(
        "domain-status",
        help="Show domain tracking status summary or list domains by status",
    )
    domain_parser.add_argument(
        "--status",
        choices=["pending", "active", "exhausted", "blocked", "unreachable"],
        help="Filter by status (if omitted, shows summary)",
    )
    domain_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum domains to show when filtering by status (default: 50)",
    )
    domain_parser.set_defaults(func=domain_status_command)

    # domain-info command
    info_parser = subparsers.add_parser(
        "domain-info",
        help="Show detailed information about a specific domain",
    )
    info_parser.add_argument(
        "domain",
        help="Domain name to look up",
    )
    info_parser.set_defaults(func=domain_info_command)

    # recalculate-priorities command
    priority_parser = subparsers.add_parser(
        "recalculate-priorities",
        help="Recalculate priority scores for all domains",
    )
    priority_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    priority_parser.set_defaults(func=recalculate_priorities_command)

    # release-stuck-claims command
    release_parser = subparsers.add_parser(
        "release-stuck-claims",
        help="Release expired domain claims (cleanup utility)",
    )
    release_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    release_parser.add_argument(
        "--force",
        action="store_true",
        help="Force-release active (non-expired) claims",
    )
    release_parser.add_argument(
        "--worker-id",
        help="Release claims for specific worker only (requires --force)",
    )
    release_parser.add_argument(
        "--all-active",
        action="store_true",
        help="Release all active claims across all workers (requires --force)",
    )
    release_parser.set_defaults(func=release_stuck_claims_command)

    # cleanup-stale-runs command
    stale_runs_parser = subparsers.add_parser(
        "cleanup-stale-runs",
        help="Mark stale crawl_runs as failed (no recent activity)",
    )
    stale_runs_parser.add_argument(
        "--older-than-minutes",
        type=int,
        default=60,
        help="Mark runs stale after N minutes of inactivity (default: 60)",
    )
    stale_runs_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without marking as failed",
    )
    stale_runs_parser.set_defaults(func=cleanup_stale_runs_command)

    # domain-reset command
    reset_parser = subparsers.add_parser(
        "domain-reset",
        help="Reset a domain for re-crawling (clears stats and sets status to pending)",
    )
    reset_parser.add_argument(
        "domain",
        help="Domain name to reset",
    )
    reset_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt",
    )
    reset_parser.set_defaults(func=domain_reset_command)

    # cleanup-fingerprints command
    cleanup_fp_parser = subparsers.add_parser(
        "cleanup-fingerprints",
        help="Clear all URL fingerprints from persistent dupefilter (Redis)",
    )
    cleanup_fp_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without deleting",
    )
    cleanup_fp_parser.add_argument(
        "--redis-url",
        type=str,
        help="Redis connection URL (default: from REDIS_URL env var)",
    )
    cleanup_fp_parser.set_defaults(func=cleanup_fingerprints_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handler = cast(Callable[[argparse.Namespace], int], args.func)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
