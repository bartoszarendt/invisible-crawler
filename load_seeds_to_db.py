"""Load seed domains into the domains table for Phase C smart scheduling."""
import sys
from pathlib import Path

from storage.db import get_cursor
from processor.domain_canonicalization import canonicalize_domain


def load_seeds_to_db(seed_file: str, source_name: str = "tranco_last1000"):
    """Load domains from seed file into domains table.
    
    Args:
        seed_file: Path to seed file (one domain per line)
        source_name: Source identifier for tracking
    """
    seed_path = Path(seed_file)
    if not seed_path.exists():
        print(f"Error: Seed file not found: {seed_file}", file=sys.stderr)
        sys.exit(1)
    
    domains = []
    with open(seed_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            # Ensure domain has scheme for parsing
            domain = line
            if not domain.startswith(("http://", "https://")):
                domain = f"https://{domain}"
            
            # Canonicalize domain (strip www, etc)
            try:
                canonical = canonicalize_domain(domain, strip_subdomains=False)
                domains.append(canonical)
            except Exception as e:
                print(f"Warning: Failed to parse domain {line}: {e}")
                continue
    
    if not domains:
        print("Error: No valid domains found in seed file", file=sys.stderr)
        sys.exit(1)
    
    print(f"Loading {len(domains)} domains into database...")
    
    try:
        inserted = 0
        skipped = 0
        errors = 0
        
        for idx, domain in enumerate(domains, 1):
            try:
                # Use separate transaction for each insert
                with get_cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO domains (
                            domain, source, seed_rank, status
                        ) VALUES (
                            %s, %s, %s, 'pending'
                        )
                        ON CONFLICT (domain) DO NOTHING
                        """,
                        (domain, source_name, idx)
                    )
                    
                    if cursor.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
                    
                    if idx % 100 == 0:
                        print(f"  Progress: {idx}/{len(domains)} domains processed...")
                        
            except Exception as e:
                errors += 1
                if errors <= 5:  # Only print first 5 errors
                    print(f"Warning: Failed to insert {domain}: {e}")
                continue
            
        print(f"\n✓ Successfully loaded {inserted} domains")
        if skipped > 0:
            print(f"  (Skipped {skipped} existing domains)")
        if errors > 0:
            print(f"  (Failed to insert {errors} domains)")
            
    except Exception as e:
        print(f"Error loading domains: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python load_seeds_to_db.py <seed_file> [source_name]")
        print("Example: python load_seeds_to_db.py config/last1000_seeds.txt tranco_last1000")
        sys.exit(1)
    
    seed_file = sys.argv[1]
    source_name = sys.argv[2] if len(sys.argv) > 2 else "tranco_last1000"
    
    load_seeds_to_db(seed_file, source_name)
