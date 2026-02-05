"""Analyze crawl results from the database."""
import os
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')

def analyze_crawl():
    """Generate comprehensive crawl analysis."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    print("=" * 80)
    print("CRAWL RESULTS ANALYSIS")
    print("=" * 80)
    print()
    
    # Overall statistics
    print("📊 OVERALL STATISTICS")
    print("-" * 80)
    
    cur.execute("SELECT COUNT(*) as total_images FROM images")
    total_images = cur.fetchone()['total_images']
    print(f"Total unique images discovered: {total_images}")
    
    cur.execute("SELECT COUNT(DISTINCT url) as unique_urls FROM images")
    unique_urls = cur.fetchone()['unique_urls']
    print(f"Unique image URLs: {unique_urls}")
    
    cur.execute("SELECT COUNT(*) as total_provenance FROM provenance")
    total_provenance = cur.fetchone()['total_provenance']
    print(f"Total provenance records: {total_provenance}")
    
    cur.execute("SELECT COUNT(DISTINCT source_domain) as unique_domains FROM provenance")
    unique_domains = cur.fetchone()['unique_domains']
    print(f"Unique source domains: {unique_domains}")
    
    cur.execute("SELECT COUNT(*) as total_pages FROM crawl_log")
    total_pages = cur.fetchone()['total_pages']
    print(f"Total pages crawled: {total_pages}")
    
    cur.execute("SELECT SUM(images_found) as total_found, SUM(images_downloaded) as total_downloaded FROM crawl_log")
    totals = cur.fetchone()
    print(f"Total images found: {totals['total_found']}")
    print(f"Total images downloaded: {totals['total_downloaded']}")
    print()
    
    # Top domains by image count
    print("🌐 TOP 10 DOMAINS BY IMAGE COUNT")
    print("-" * 80)
    cur.execute("""
        SELECT source_domain, COUNT(*) as image_count
        FROM provenance
        GROUP BY source_domain
        ORDER BY image_count DESC
        LIMIT 10
    """)
    for row in cur.fetchall():
        print(f"  {row['source_domain']:40s} {row['image_count']:>5} images")
    print()
    
    # Image format distribution
    print("📷 IMAGE FORMAT DISTRIBUTION")
    print("-" * 80)
    cur.execute("""
        SELECT 
            COALESCE(format, 'unknown') as format,
            COUNT(*) as count,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as percentage
        FROM images
        GROUP BY format
        ORDER BY count DESC
    """)
    for row in cur.fetchall():
        print(f"  {row['format']:15s} {row['count']:>5} ({row['percentage']:>6}%)")
    print()
    
    # Image size distribution
    print("📐 IMAGE DIMENSION STATISTICS")
    print("-" * 80)
    cur.execute("""
        SELECT 
            AVG(width) as avg_width,
            AVG(height) as avg_height,
            MAX(width) as max_width,
            MAX(height) as max_height,
            MIN(width) as min_width,
            MIN(height) as min_height
        FROM images
        WHERE width IS NOT NULL AND height IS NOT NULL
    """)
    stats = cur.fetchone()
    if stats:
        print(f"  Average: {int(stats['avg_width'])}x{int(stats['avg_height'])}")
        print(f"  Max: {stats['max_width']}x{stats['max_height']}")
        print(f"  Min: {stats['min_width']}x{stats['min_height']}")
    print()
    
    # File size distribution
    print("💾 FILE SIZE STATISTICS")
    print("-" * 80)
    cur.execute("""
        SELECT 
            AVG(file_size_bytes) as avg_size,
            MAX(file_size_bytes) as max_size,
            MIN(file_size_bytes) as min_size
        FROM images
        WHERE file_size_bytes IS NOT NULL
    """)
    stats = cur.fetchone()
    if stats:
        print(f"  Average: {int(stats['avg_size']):,} bytes ({stats['avg_size']/1024:.1f} KB)")
        print(f"  Max: {int(stats['max_size']):,} bytes ({stats['max_size']/1024:.1f} KB)")
        print(f"  Min: {int(stats['min_size']):,} bytes ({stats['min_size']/1024:.1f} KB)")
    print()
    
    # Download success rate
    print("✅ DOWNLOAD SUCCESS RATE")
    print("-" * 80)
    cur.execute("""
        SELECT 
            download_success,
            COUNT(*) as count,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 2) as percentage
        FROM images
        GROUP BY download_success
    """)
    for row in cur.fetchall():
        status = "Success" if row['download_success'] else "Failed"
        print(f"  {status:15s} {row['count']:>5} ({row['percentage']:>6}%)")
    print()
    
    # Pages with most images
    print("📄 TOP 10 PAGES BY IMAGE COUNT")
    print("-" * 80)
    cur.execute("""
        SELECT source_page_url, COUNT(*) as image_count
        FROM provenance
        GROUP BY source_page_url
        ORDER BY image_count DESC
        LIMIT 10
    """)
    for row in cur.fetchall():
        url = row['source_page_url'][:70] + "..." if len(row['source_page_url']) > 70 else row['source_page_url']
        print(f"  {url:73s} {row['image_count']:>5} images")
    print()
    
    # Crawl errors
    print("⚠️  CRAWL ERRORS SUMMARY")
    print("-" * 80)
    cur.execute("""
        SELECT 
            CASE 
                WHEN status >= 200 AND status < 300 THEN '2xx Success'
                WHEN status >= 300 AND status < 400 THEN '3xx Redirect'
                WHEN status >= 400 AND status < 500 THEN '4xx Client Error'
                WHEN status >= 500 AND status < 600 THEN '5xx Server Error'
                ELSE 'Other'
            END as status_category,
            COUNT(*) as count
        FROM crawl_log
        WHERE status IS NOT NULL
        GROUP BY status_category
        ORDER BY status_category
    """)
    for row in cur.fetchall():
        print(f"  {row['status_category']:20s} {row['count']:>5}")
    print()
    
    # Sample of discovered images
    print("🖼️  SAMPLE IMAGES (First 10)")
    print("-" * 80)
    cur.execute("""
        SELECT 
            i.url,
            i.format,
            i.width,
            i.height,
            i.file_size_bytes,
            p.source_domain
        FROM images i
        LEFT JOIN provenance p ON i.id = p.image_id
        ORDER BY i.discovered_at DESC
        LIMIT 10
    """)
    for idx, row in enumerate(cur.fetchall(), 1):
        print(f"\n  {idx}. {row['url'][:75]}")
        print(f"     Format: {row['format']}, Size: {row['width']}x{row['height']}, {row['file_size_bytes']:,} bytes")
        print(f"     Source: {row['source_domain']}")
    print()
    
    print("=" * 80)
    
    cur.close()
    conn.close()

if __name__ == '__main__':
    analyze_crawl()
