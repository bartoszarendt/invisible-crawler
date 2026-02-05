# InvisibleCrawler - Phase 1 Implementation (Local, On-Demand)

## Status

**Date:** 2026-02-05  
**Phase:** 1 (Local device, manual trigger)  
**Verification:** ✅ Code reviewed, 32 tests passing, runtime validated against wordpress.org

---

## Phase 1 Scope (Current)

The current implementation is scoped to **local, on-demand crawling** using a seed file you provide. It does **not** include Phase 2 items like Redis scheduling, Tranco-based seeding, or Scrapy broad crawls.

### Phase 2 (Explicitly Out of Scope for Now)
- Redis-based URL frontier and queueing
- Tranco/Majestic seed ingestion
- Scrapy broad crawl strategy
- Distributed crawl orchestration

---

## What Exists (Verified by Code Scan)

1. **Multi-Domain Spider** (`crawler/spiders/discovery_spider.py`)
   - Reads domains/URLs from a seed file
   - Crawls same-domain links only
   - Extracts images from `<img>`, `srcset`, `<picture>`, `og:image`
   - Respects robots.txt and 1 req/sec for HTML pages

2. **Image Processing Pipeline** (`crawler/pipelines.py`)
   - Downloads images via `ImageFetcher`
   - Computes SHA-256 for deduplication
   - Stores metadata in PostgreSQL (`images`, `provenance`)
   - Updates `last_seen_at` for existing images

3. **Image Fetcher** (`processor/fetcher.py`)
   - Validates content type
   - Enforces size limits (min 1KB, max 50MB)
   - Enforces minimum dimensions (256x256)
   - Computes SHA-256 on content

4. **Database Layer + Alembic** (`storage/`)
   - Schema with `images`, `provenance`, `crawl_log`
   - Alembic migrations using `DATABASE_URL`
   - Connection pooling via `ThreadedConnectionPool`

5. **Tests** (`tests/`)
   - 32 total tests (unit + integration)
   - Mock HTTP server via `pytest-httpserver`
   - Integration tests require a running PostgreSQL instance

6. **Logging Utilities** (`crawler/logging_config.py`)
   - Structured JSON formatter and crawl statistics helpers
   - Not yet wired into spider/pipeline execution

---

## Project Structure (As Implemented)

```
invisible-crawler/
├── crawler/
│   ├── logging_config.py
│   ├── pipelines.py
│   ├── settings.py
│   └── spiders/
│       └── discovery_spider.py
├── processor/
│   ├── fetcher.py
│   └── fingerprint.py
├── storage/
│   ├── db.py
│   ├── schema.sql
│   └── migrations/
│       ├── env.py
│       └── versions/
│           └── fcf2fb2ae158_initial_schema.py
├── config/
│   └── test_seeds.txt
├── tests/
│   ├── fixtures.py
│   ├── test_integration.py
│   ├── test_processor.py
│   └── test_spider.py
├── alembic.ini
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
└── scrapy.cfg
```

---

## Database Schema (Summary)

**images**
- id (UUID, PK)
- url (TEXT, UNIQUE)
- sha256_hash (VARCHAR(64))
- width, height (INTEGER)
- format (VARCHAR)
- content_type (VARCHAR)
- file_size_bytes (INTEGER)
- discovered_at, last_seen_at (TIMESTAMPTZ)
- download_success (BOOLEAN)
- reserved InvisibleID fields (nullable)

**provenance**
- id (UUID, PK)
- image_id (UUID, FK → images.id)
- source_page_url (TEXT)
- source_domain (VARCHAR)
- discovered_at (TIMESTAMPTZ)
- discovery_type (VARCHAR)

**crawl_log**
- id (UUID, PK)
- page_url (TEXT)
- domain (VARCHAR)
- crawled_at (TIMESTAMPTZ)
- status (INTEGER)
- images_found, images_downloaded (INTEGER)
- error_message (TEXT)
- crawl_type (VARCHAR)

---

## Dependencies and Tooling

**Core**
- Scrapy, psycopg2-binary, Pillow, requests, python-dotenv, alembic

**Dev**
- pytest, pytest-cov, pytest-httpserver, black, ruff, mypy, types-requests

**Style/Config**
- Black line length 100
- Ruff + mypy in strict mode (config in `pyproject.toml`)

---

## Required to Run Domain-Seed Crawls (Now)

These items are required to run crawls against your **`config/test_seeds.txt`** domains and store results locally.

1. **Seed file must be non-empty**
   - `config/test_seeds.txt` should contain one domain or URL per line.
   - Lines may be plain domains; the spider will add `https://` when missing.
   - Optional filters:
     - `config/seed_allowlist.txt` (only these domains will be crawled)
     - `config/seed_blocklist.txt` (these domains will be skipped)
   - Optional runtime skips:
     - `max_domain_errors` (default: 3) blocks a domain after repeated 403/429/503
     - `block_on_login` (default: true) blocks domains that look like login pages

2. **Database must be up and migrated**
   - `.env` must define `DATABASE_URL`.
   - Run `alembic upgrade head` before crawling.

3. **Crawl stop behavior must match your intent**
   - Current default: `max_pages=10` stops the crawl after 10 pages total.
   - If you want **"continue until stopped"**, decide one of:
     - Pass a large `max_pages` value each run, or
     - Change the spider to treat `max_pages=0` (or `None`) as "no limit."

4. **Manual on-demand trigger**
   - The crawler is run via Scrapy CLI and stops when the frontier is exhausted
     (or when `max_pages` is reached).

---

## Local Runbook (On-Demand)

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Configure database
# Edit .env with DATABASE_URL

# Apply schema
alembic upgrade head

# Run crawler against your test seeds
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=10000
```

To stop the crawl, use `Ctrl+C`.

---

## Testing (Local)

Unit and integration tests do **not** use `config/test_seeds.txt`.
Integration tests require a running PostgreSQL instance and the schema applied.

```bash
pytest tests/ --cov=crawler --cov=processor --cov-report=term-missing
```

---

## Validation Commands (Expected)

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
alembic upgrade head

black --check .
ruff check .
mypy crawler/ processor/ storage/
```

---

## Known Gaps (Phase 1)

These are acceptable for Phase 1 but should be tracked:

1. **`crawl_log` is not written by crawler execution**
   - Minimal per-page entries are now written by the spider (best-effort).
   - No aggregation or run-level summaries yet.

2. **Structured logging is not wired**
   - `logging_config.py` provides formatters/stats but is not activated.

3. **Image fetcher uses `requests`**
   - Image downloads bypass Scrapy’s robots/delay/retry controls.
   - If strict politeness for images is required, refactor to Scrapy requests.

---

## Phase 1 Deep Notes (Current Behavior)

1. **Data flow (detailed)**
   - End-to-end path is: `Scrapy Spider -> Item Pipeline -> ImageFetcher -> PostgreSQL`.
   - The spider parses HTML responses and yields image items with:
     - `url`, `source_page`, `source_domain`, `type="image"`.
   - The pipeline receives items and calls `ImageFetcher.fetch(url)`:
     - Validates content-type, size, and dimensions.
     - Computes SHA-256 for deduplication.
   - Database writes:
     - If `sha256_hash` already exists, update `images.last_seen_at`.
     - Else insert new row in `images` and create a `provenance` row.
   - Errors during fetch or DB write mark the item as failed and continue.
   - In **discovery** mode, if the image URL already exists, the pipeline
     skips re-download and only ensures provenance is present.
   - Optional: set `DISCOVERY_REFRESH_AFTER_DAYS` to re-fetch if `last_seen_at`
     is older than the threshold.

```
Spider (HTML) -> Image Item -> Pipeline -> Fetch + Validate -> Hash -> DB (images + provenance)
```

2. **Stopping conditions**
   - Crawl stops when `max_pages` is reached or the frontier is exhausted.
   - Default `max_pages=10` unless overridden via `-a max_pages=...`.

3. **Logging**
   - Basic logging via Scrapy defaults.
   - Structured logging utilities exist but are not wired.

4. **Database usage**
   - `images` + `provenance` are written by the pipeline.
   - `crawl_log` exists but is not written by runtime code.

5. **Tests**
   - Integration tests require a running PostgreSQL and migrations applied.
   - Tests do not use `config/test_seeds.txt`.

---

## Phase 2 Implementation Outline (Planned)

1. **Seeds and Frontier**
   - Ingest Tranco/other seed sources into a URL frontier.
   - Introduce Redis-backed scheduler with per-domain queues and dedupe.

2. **Crawl Strategy**
   - Implement Scrapy broad crawl strategy for large-scale coverage.
   - Add crawl modes: discovery vs refresh.

3. **Runtime Topology**
   - Local dev remains single-node.
   - Remote deployment adds Redis + PostgreSQL + object storage.

4. **Data Model Changes**
   - Add frontier tables/queues or Redis schemas.
   - Extend crawl metadata for queue state, retry/backoff, and refresh cadence.

5. **Operational Controls**
   - Config-driven rate limits per domain.
   - Ability to start/stop/continue crawls on demand.

6. **Acceptance Criteria**
   - Seed ingestion produces scheduled URLs.
   - Broad crawl runs for N domains without manual intervention.
   - Queue + retry + backoff are observable and auditable.

---

## Phase 2 Sketch: Async Fetching + Multi-Spider

**Goal:** Keep the entire crawl pipeline non-blocking and allow multiple spiders to run concurrently.

1. **Async image fetching (recommended)**
   - Replace `requests` in the pipeline with Scrapy-native fetching:
     - Option A: Use Scrapy `ImagesPipeline` and customize item fields.
     - Option B: Yield image `Request` objects from the spider and handle in callbacks.
   - Benefits:
     - Uses Twisted reactor (non-blocking).
     - Honors Scrapy politeness settings for images.
     - Better throughput with controlled concurrency.

2. **Run multiple spiders**
   - **Single process**: use `CrawlerProcess` to run multiple spiders in one reactor.
   - **Multi-process**: run separate Scrapy processes for isolation and scaling.
   - Both approaches still integrate with Redis frontier (once added).

3. **Concurrency knobs**
   - Per-domain: `CONCURRENT_REQUESTS_PER_DOMAIN`, `DOWNLOAD_DELAY`
   - Global: `CONCURRENT_REQUESTS`, `AUTOTHROTTLE_TARGET_CONCURRENCY`
   - Image-specific throttling should match domain politeness.

4. **Proposed implementation sequence**
   - Introduce async image fetch path (ImagesPipeline or image requests).
   - Validate equivalence of metadata writes and dedup logic.
   - Add multi-spider launcher (process or multi-process).
   - Connect to Redis frontier for shared queueing.

---

## Next Steps (Phase 2 Reference)

- Redis-backed URL frontier
- Tranco seed ingestion
- Scrapy broad crawls
- Distributed scheduling and remote deployment

---

## Phase 1 Code Review & Fixes Applied (2026-02-05)

### Issues Identified

#### Critical Issues

| Issue | Location | Problem | Resolution |
|-------|----------|---------|------------|
| **Blocking I/O in Pipeline** | `crawler/pipelines.py` | `ImageFetcher` uses synchronous `requests` inside Scrapy's Twisted reactor, blocking the event loop | Documented as known gap; full fix deferred to Phase 2 (async fetching) |
| **Missing perceptual hashes** | `processor/fingerprint.py` | `ImageFingerprinter` has pHash/dHash placeholder methods but pipeline doesn't compute them | Documented as Phase 2 enhancement |

#### Moderate Issues (Fixed)

| Issue | Location | Problem | Fix Applied |
|-------|----------|---------|-------------|
| **User-Agent mismatch** | `processor/fetcher.py` | Hardcoded User-Agent differs from Scrapy settings | Now reads from `CRAWLER_USER_AGENT` env var with fallback |
| **`_is_valid_image_url` always returns True** | `crawler/spiders/discovery_spider.py` | Final `return True` made earlier validation dead code | Removed unconditional return; now actually filters |
| **Connection pool not closed** | `storage/db.py` | `close_all_connections()` exists but isn't called on shutdown | Wired into `close_spider()` in pipeline |
| **No explicit URL index** | Schema | `_get_existing_image_by_url` queries by URL | Added migration for explicit index (PostgreSQL creates implicit index on UNIQUE, but explicit is clearer) |

#### Minor Issues (Documented)

| Issue | Location | Notes |
|-------|----------|-------|
| Unused `normalized_hash` | `processor/fingerprint.py` | Reserved for Phase 2 perceptual hashing |
| No retry for image fetch | `processor/fetcher.py` | Unlike Scrapy's retry middleware, fetcher has no retry logic; acceptable for Phase 1 |
| `crawl_log.images_downloaded` not populated | `discovery_spider.py` | Pipeline doesn't update count; tracked for future enhancement |

### Test Coverage Gaps (Tracked)

| Gap | Status |
|-----|--------|
| No pipeline unit tests | Tracked for future |
| No tests for blocklist/allowlist filtering | Tracked for future |
| No tests for refresh mode `_should_refresh()` | Tracked for future |
| Integration tests require live DB | Acceptable for Phase 1 |

---

## Phase 2 Roadmap & Rationale

### High Priority

| Improvement | Rationale |
|-------------|-----------|
| **Async image fetching via Scrapy** | Current `requests` blocks Twisted reactor. Use `scrapy.Request` for images or `ImagesPipeline` to honor politeness for image downloads. Unblocks throughput. |
| **Redis URL frontier** | PostgreSQL isn't designed for high-throughput queue operations. Redis provides O(1) push/pop, natural TTL for retry backoff, and per-domain queue isolation. |

### Medium Priority

| Improvement | Rationale |
|-------------|-----------|
| **Perceptual hash storage** | pHash/dHash enable similarity search for detecting re-encoded/cropped watermarked images. Compute during fetch (cheap) even if similarity index comes later. |
| **Object storage for binaries** | Currently only metadata is stored. Add MinIO/S3 backend for content-addressable binary storage `/{sha256[0:2]}/{sha256[2:4]}/{sha256}`. |
| **Distributed crawl mode** | Support `scrapyd` or `scrapy-redis` for horizontal scaling. Current single-process model won't scale. |
| **Tranco seed ingestion** | `tranco_top1m.csv` exists but no ingestion code. Add CLI command to populate frontier from ranked seed lists. |

### Lower Priority

| Improvement | Rationale |
|-------------|-----------|
| **Refresh crawl spider** | Separate spider for `discovery_type='refresh'` with different depth/politeness settings focusing on known URLs. |
| **Bloom filter for URL dedup** | For large-scale crawling, in-memory Bloom filter reduces DB lookups for already-seen URLs. |
| **Metrics & observability** | Add Prometheus metrics exporter for crawl rate, error rate, queue depth. Current logging is basic. |

### Schema Enhancements (Phase 2)

```sql
-- Add perceptual hashes
ALTER TABLE images ADD COLUMN phash_hash VARCHAR(16);
ALTER TABLE images ADD COLUMN dhash_hash VARCHAR(16);
CREATE INDEX idx_images_phash ON images(phash_hash);

-- Add crawl run tracking
CREATE TABLE crawl_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    mode VARCHAR(20) NOT NULL,  -- 'discovery' | 'refresh'
    pages_crawled INTEGER DEFAULT 0,
    images_found INTEGER DEFAULT 0,
    images_downloaded INTEGER DEFAULT 0,
    seed_source VARCHAR(255)
);

-- Link crawl_log to runs
ALTER TABLE crawl_log ADD COLUMN crawl_run_id UUID REFERENCES crawl_runs(id);
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://localhost/invisible` | PostgreSQL connection string |
| `CRAWLER_USER_AGENT` | `InvisibleCrawler/0.1 (...)` | User-Agent for image fetching |
| `DISCOVERY_REFRESH_AFTER_DAYS` | `0` (disabled) | Re-fetch images older than N days |
| `IMAGE_MIN_WIDTH` | `256` | Minimum image width in pixels |
| `IMAGE_MIN_HEIGHT` | `256` | Minimum image height in pixels |
