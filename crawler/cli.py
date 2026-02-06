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
from pathlib import Path

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
    import redis

    stats = {
        "rows_read": 0,
        "seeds_ingested": 0,
        "duplicates_skipped": 0,
        "errors": 0,
    }

    client = redis.from_url(redis_url)
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
        import redis

        client = redis.from_url(redis_url)

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
    """Show domain status summary.

    Args:
        args: Command line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    try:
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
        print(f"{'TOTAL':<15} {stats['total_domains']:<10} {'':<12} {stats['total_images']:<15}")
        print()

        return 0

    except Exception as e:
        logger.error(f"Failed to get domain status: {e}")
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
        help="Show domain tracking status summary",
    )
    domain_parser.set_defaults(func=domain_status_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
