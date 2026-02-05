# InvisibleCrawler - Phase 1 Implementation (Local, On-Demand)

## Status

**Date:** 2026-02-05  
**Phase:** 1 (Local device, manual trigger)  
**Verification:** Code reviewed; runtime validation pending on a local DB

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
   - The table exists but spider/pipeline do not insert crawl entries yet.

2. **Structured logging is not wired**
   - `logging_config.py` provides formatters/stats but is not activated.

3. **Image fetcher uses `requests`**
   - Image downloads bypass Scrapy’s robots/delay/retry controls.
   - If strict politeness for images is required, refactor to Scrapy requests.

---

## Phase 1 Deep Notes (Current Behavior)

1. **Data flow**
   - Spider parses HTML and yields image items.
   - Pipeline downloads each image and writes to DB.
   - Dedup is by SHA-256 hash; duplicates update `last_seen_at`.

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

## Next Steps (Phase 2 Reference)

- Redis-backed URL frontier
- Tranco seed ingestion
- Scrapy broad crawls
- Distributed scheduling and remote deployment
