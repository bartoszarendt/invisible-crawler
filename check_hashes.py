"""Check if perceptual hashes are populated in the database."""
import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

# Count images with hashes
cur.execute("""
    SELECT 
        COUNT(*) as total,
        COUNT(phash_hash) as with_phash,
        COUNT(dhash_hash) as with_dhash
    FROM images
""")
row = cur.fetchone()
print(f"Total images: {row[0]}")
print(f"Images with pHash: {row[1]} ({100*row[1]/row[0] if row[0] > 0 else 0:.1f}%)")
print(f"Images with dHash: {row[2]} ({100*row[2]/row[0] if row[0] > 0 else 0:.1f}%)")

# Show sample hashes
print("\nSample perceptual hashes:")
cur.execute("""
    SELECT url, phash_hash, dhash_hash 
    FROM images 
    WHERE phash_hash IS NOT NULL 
    LIMIT 5
""")
for idx, r in enumerate(cur.fetchall(), 1):
    print(f"\n{idx}. {r[0][:70]}")
    print(f"   pHash: {r[1]}")
    print(f"   dHash: {r[2]}")

cur.close()
conn.close()
