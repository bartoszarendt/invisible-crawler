# Crawl Analysis Summary - February 5, 2026

## Crawl Configuration
- **Seed Source**: Tranco Top 1M (first 100 domains)
- **Max Pages**: 1,000
- **Mode**: File-based seeds (Phase 2 code path; crawl run tracking active)
- **Politeness**: 1 req/sec, robots.txt respected
- **Minimum Image Size**: 256x256 pixels

## Key Findings

### Overall Statistics
- **290 unique images** successfully discovered and indexed
- **22 pages** crawled across 14 unique domains
- **589 images found** in total (290 passed validation)
- **100% download success rate** for validated images
- **Average crawl depth**: Limited to seed pages (most domains blocked follow links or had errors)

### Domain Coverage Analysis
Only **14 out of 100** seed domains yielded results. Key observations:

**Top Performing Domains:**
1. **apple.com** - 103 images (35.5% of total)
2. **azure.com** - 49 images (16.9%)
3. **cloudflare.com** - 42 images (14.5%)
4. **opera.com** - 35 images (12.1%)
5. **microsoft.com** - 20 images (6.9%)

**Why low domain coverage?**
- Many top domains (google.com, facebook.com, youtube.com, etc.) likely blocked by robots.txt
- Connection timeouts for some domains (e.g., ripn.net)
- Redirect chains leading outside initial domain
- Heavy JavaScript rendering required (not supported in Phase 1)

### Image Quality Metrics

**Format Distribution:**
- **JPEG**: 69.31% (201 images) - Most common for photos
- **Unknown**: 14.14% (41 images) - All are SVG (`image/svg+xml`) assets currently accepted by allowlists
- **PNG**: 11.03% (32 images) - Graphics and logos
- **WEBP**: 5.52% (16 images) - Modern format adoption

**Dimensions:**
- **Average**: 1043×677 pixels
- **Largest**: 4544×2556 pixels (high-resolution asset)
- **Smallest**: 274×264 pixels (just above minimum threshold)

**File Sizes:**
- **Average**: 194 KB per image
- **Largest**: 7.9 MB (high-quality image)
- **Smallest**: 1 KB (optimized asset)

### Validation & Filtering

The crawler's validation rules filtered out many images:

**Common rejection reasons:**
- Images smaller than 256×256 pixels (logos, icons)
- File size under 1 KB threshold (tracking pixels, spacers)
- Invalid content types (misconfigured servers)

**Important correction:** SVG files are currently being indexed (41 records), not rejected. They appear as `format = unknown` because Pillow does not parse SVG as a raster format.

This filtering is mostly working for raster assets, but SVG acceptance currently skews quality metrics and hash coverage.

### HTTP Status Distribution
- **2xx Success**: 16 pages (72.7%)
- **4xx Client Errors**: 2 pages (9.1%)
- **Other/Timeouts**: ~84 domains failed to respond or were blocked

## Insights & Recommendations

### Positive Findings
✅ **Fingerprinting works**: SHA-256 deduplication functioning correctly  
✅ **Validation robust**: Effective filtering of low-quality images  
✅ **Provenance tracking**: Full source attribution maintained  
✅ **Database schema**: Handling real-world data well  

### Areas for Improvement

1. **Low Domain Coverage (14%)**
   - **Issue**: Most Tranco domains unreachable or blocked
   - **Recommendation**: Consider media-specific seed lists (news sites, photo sharing, e-commerce)
   - **Alternative**: Use Majestic Million which favors content-rich domains

2. **Shallow Crawl Depth**
   - **Issue**: Most domains yielded only homepage images
   - **Recommendation**: Investigate robots.txt blocking; consider refresh crawl mode for known-good URLs

3. **Unknown Image Formats (14%)**
   - **Issue**: This is not generic format detection failure; it is SVG ingestion (`image/svg+xml`) in current allowlists
   - **Recommendation**: Restrict allowed formats to JPEG/PNG/WEBP in spider + fetcher/downloader paths

4. **No JavaScript Rendering**
   - **Issue**: Modern SPAs (React/Vue apps) not crawlable
   - **Impact**: Missing images on javascript-heavy sites
   - **Future**: Phase 3 selective rendering for high-value domains

5. **Perceptual Hashes Working Well**
   - **Status**: ✅ Fully implemented and computing (85.9% success rate)
   - **Coverage**: pHash and dHash computed for 249/290 images
   - **Note**: 14.1% hash gaps correlate with indexed SVG assets

## Data Quality Assessment

**Good:**
- 100% download success rate indicates robust fetching
- Clean provenance tracking (no orphaned records)
- Reasonable size distribution (avoiding tiny assets)

**Needs Attention:**
- SVG ingestion policy is inconsistent with future InvisibleID workflow assumptions
- Deeper crawling on permitted domains
- Better seed selection for image-rich sites

## Sample Notable Images

### Largest Image
- **URL**: CloudFlare high-res marketing asset
- **Size**: 4544×2556 pixels, 7.9 MB
- **Format**: JPEG
- **Use case**: Hero banner / high-quality promotional content

### Most Common Source
- **Domain**: apple.com
- **Images**: 103 (all from homepage)
- **Characteristics**: High-quality product photography, consistent sizing

## Next Steps

1. **Expand seed list** with image-focused domains (Flickr, Instagram landing pages, news sites)
2. **Implement refresh crawl** for successful domains to go deeper
3. **Restrict ingestion to JPEG/PNG/WEBP** to eliminate SVG-driven "unknown" records and improve hash coverage
4. **Build similarity search index** using existing perceptual hashes
5. **Consider Phase 2 (Redis)** for distributed crawling at scale

## Technical Notes

- **Database**: PostgreSQL with all Phase 2 migrations applied
- **Schema Version**: 3b65381b0f4e (head)
- **Crawl Duration**: ~5 minutes for 22 pages
- **Rate Limiting**: Strictly enforced (1 req/sec worked well)
- **Storage**: Metadata only (no binary storage yet)

---

**Generated**: February 5, 2026  
**Crawl Run**: `ec1328b0-0c35-4253-8e57-cb281c1724e5` (tracked in `crawl_runs`; `images_downloaded` currently remains `0` due known implementation gap)  
**Analysis Script**: [analyze_results.py](analyze_results.py)
