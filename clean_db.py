"""Clean database tables before starting a new crawl."""
import sys
from storage.db import get_cursor


def clean_database():
    """Truncate all crawl-related tables."""
    try:
        with get_cursor() as cursor:
            print("Cleaning database tables...")
            
            # Truncate tables in correct order (respecting foreign keys)
            tables = [
                "crawl_log",
                "images",
                "provenance",
                "domains",
                "crawl_runs",
            ]
            
            for table in tables:
                cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
                print(f"  ✓ Truncated {table}")
            
            print("\nDatabase cleaned successfully!")
            
    except Exception as e:
        print(f"Error cleaning database: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    clean_database()
