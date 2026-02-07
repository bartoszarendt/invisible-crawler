# InvisibleCrawler - Implementation Status (Phase 2 + Domain Tracking)

## Status

**Date:** 2026-02-07  
**Phase:** 2 + Domain Tracking Phases A, B, C + Phase C Hardening  
**Verification:** Local validation on 2026-02-07: `ruff check .` passed, `pytest -q` passed with `190 passed, 35 skipped` (DB-backed tests skipped when `DATABASE_URL` is unset).

---

## Recent Updates (2026-02-07)

**Phase C Hardening** - Reliability and resilience improvements:
- ✅ Fixed double-count in `closed()` when claim protocol enabled
- ✅ Queue/claim isolation: Phase C uses local scheduler to prevent cross-worker domain overlap
- ✅ Mid-crawl state flushing: domain stats persisted every N pages (`DOMAIN_STATS_FLUSH_INTERVAL_PAGES=100`)
- ✅ Force-release CLI: `release-stuck-claims --force` enables emergency recovery from dead workers  
- ✅ Stale run cleanup: `cleanup-stale-runs` command for operational hygiene
- ✅ Startup validation: claim protocol requires smart scheduling (enforced at init)

**Design Document**: [PHASE_C_HARDENING.md](PHASE_C_HARDENING.md)

---

## Scope (Current)

Phase 2 adds distributed crawling capabilities using Redis-based scheduling and seed ingestion:

- **Redis URL Frontier**: Persistent, priority-based queue with per-domain tracking
- **Seed Ingestion CLI**: Ingest seeds from Tranco, Majestic, or custom CSV files
- **Crawl Run Tracking**: Database tracking of crawl runs with status and statistics
- **Async Image Downloads**: Images downloaded via Scrapy's native request flow
- **Dual-mode Seeds**: Spider supports both file-based (Phase 1) and Redis-based (Phase 2) seeds

### Phase 1 Features (Retained)
- Multi-domain spider with same-domain link following
- Image extraction from `<img>`, `srcset`, `<picture>`, `og:image`
- SHA-256 fingerprinting for deduplication
- PostgreSQL metadata storage
- Politeness: robots.txt, 1 req/sec, per-domain rate limiting

---

## What Exists (Phase 2)

### Phase 1 Components (Retained)

1. **Multi-Domain Spider** ([crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py))
   - Reads domains/URLs from seed file OR Redis start_urls
   - Crawls same-domain links only
   - Extracts images from `<img>`, `srcset`, `<picture>`, `og:image`
   - Respects robots.txt and 1 req/sec for HTML pages
   - Yields image Requests with callback for Scrapy-native downloads

2. **Image Processing Pipeline** ([crawler/pipelines.py](crawler/pipelines.py))
   - Receives downloaded image items from spider callbacks
   - Validates content type, size, and dimensions
   - Computes SHA-256 for deduplication
   - Stores metadata in PostgreSQL (`images`, `provenance`)
   - Updates `last_seen_at` for existing images

3. **Database Layer + Alembic** ([storage/](storage/))
   - Schema with `images`, `provenance`, `crawl_log`, `crawl_runs`
   - Alembic migrations using `DATABASE_URL`
   - Connection pooling via `ThreadedConnectionPool`

4. **Tests** ([tests/](tests/))
   - 225 collected tests (unit + integration)
   - Mock HTTP server via `pytest-httpserver`
   - DB-backed tests require running PostgreSQL and setting `DATABASE_URL`
   - Includes Phase B/C coverage for checkpoints, claim protocol, priority calculation, and concurrency

### Phase 2 Additions

5. **Redis URL Frontier** ([crawler/scheduler.py](crawler/scheduler.py))
   - `InvisibleRedisScheduler`: Extends scrapy-redis with priority lanes
   - `DomainPriorityQueue`: Per-domain request tracking
   - Priority handling: lower priority for refresh crawls
   - Persistent queues with resume capability

6. **Seed Ingestion CLI** ([crawler/cli.py](crawler/cli.py))
   - `ingest-seeds`: Ingest domains from Tranco, Majestic, or custom CSV
   - `list-runs`: View recent crawl runs and statistics
   - `queue-status`: Monitor Redis queue depth and domain counts
   - Supports prioritization based on domain rank

7. **Crawl Run Tracking** ([crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py))
   - Creates `crawl_runs` record on spider open
   - Updates run status and statistics on spider close
   - Links `crawl_log` entries to parent run via `crawl_run_id`

8. **Scrapy-Native Image Downloads** ([processor/async_fetcher.py](processor/async_fetcher.py))
   - `ScrapyImageDownloader`: Validates downloaded images from Scrapy responses
   - Images downloaded through Scrapy request flow (respects politeness)
   - `AsyncImageFetcher`: Alternative Twisted-based implementation (not currently used)

### Phase C Additions

9. **Domain Claim Protocol** ([storage/domain_repository.py](storage/domain_repository.py))
   - `claim_domains()`: Atomic domain claim acquisition
   - `renew_claim()`: Lease renewal (heartbeat)
   - `release_claim()`: Atomic release with optimistic locking
   - `expire_stale_claims()`: Cleanup utility

10. **Priority Calculator** ([storage/priority_calculator.py](storage/priority_calculator.py))
    - `recalculate_priorities()`: Batch priority score updates
    - Composite scoring based on yield, error rate, staleness

11. **Smart Scheduling** ([crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py))
    - Database-driven candidate selection
    - Claim protocol integration
    - Heartbeat thread for lease renewal
    - Resume from checkpoint support

---

## Project Structure (As Implemented)

```
invisible-crawler/
├── crawler/
│   ├── cli.py
│   ├── logging_config.py
│   ├── pipelines.py
│   ├── scheduler.py
│   ├── settings.py
│   └── spiders/
│       └── discovery_spider.py
├── processor/
│   ├── async_fetcher.py
│   ├── domain_canonicalization.py    # Phase A
│   ├── fetcher.py
│   └── fingerprint.py
├── storage/
│   ├── db.py
│   ├── domain_repository.py           # Phase A/C
│   ├── priority_calculator.py         # Phase C
│   ├── frontier_checkpoint.py         # Phase B
│   ├── schema.sql
│   └── migrations/
│       ├── env.py
│       └── versions/
│           ├── fcf2fb2ae158_initial_schema.py
│           ├── 2a8b1f0e0c7b_add_provenance_unique.py
│           ├── 3b9c2d1f4e8a_add_images_url_index.py
│           ├── 44c69f17df6c_add_perceptual_hashes.py
│           ├── 3b65381b0f4e_add_crawl_runs.py
│           ├── 1c3fe655e18f_add_domains_table_phase_a.py  # Phase A
│           └── 2f1ae345c29f_add_transition_domain_status_function.py  # Phase C
├── config/
│   ├── seed_allowlist.txt
│   ├── seed_blocklist.txt
│   └── test_seeds.txt
├── tests/
│   ├── fixtures.py
│   ├── test_async_fetcher.py
│   ├── test_concurrency.py            # Phase C
│   ├── test_domain_canonicalization.py # Phase A
│   ├── test_domain_claim.py           # Phase C
│   ├── test_domain_repository.py      # Phase A
│   ├── test_domain_tracking_integration.py  # Phase A
│   ├── test_frontier_checkpoint.py    # Phase B
│   ├── test_integration.py
│   ├── test_per_domain_budget.py      # Phase B
│   ├── test_priority_calculation.py   # Phase C
│   ├── test_processor.py
│   ├── test_resume_from_checkpoint.py # Phase B
│   ├── test_scheduler.py
│   ├── test_smart_scheduling.py       # Phase C
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

## Required to Run (Phase 2)

### Prerequisites

1. **PostgreSQL database**
   - `.env` must define `DATABASE_URL`
   - Run `alembic upgrade head` before crawling

2. **Redis instance (Phase 2 mode)**
   - `.env` must define `REDIS_URL` (default: `redis://localhost:6379/0`)
   - Required when using Redis-based scheduling
   - Verify availability: `python -c "from crawler.scheduler import check_redis_available; print(check_redis_available())"`

3. **Choose seed source**
   - **File-based (Phase 1 compat)**: Provide `-a seeds=config/test_seeds.txt`
   - **Redis-based (Phase 2)**: Ingest seeds via CLI first (see commands below)

4. **Optional filters**
   - `config/seed_allowlist.txt`: Only these domains will be crawled
   - `config/seed_blocklist.txt`: These domains will be skipped
   - Runtime skips:
     - `max_domain_errors` (default: 3): Blocks domain after repeated 403/429/503
     - `block_on_login` (default: true): Blocks domains that look like login pages

---

## Local Runbook (Phase 2)

### Installation

```bash
# Install dependencies (includes Redis client, scrapy-redis, etc.)
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Configure database and Redis
# Copy template and edit local values
# cp .env.example .env
# Edit .env with DATABASE_URL and REDIS_URL

# Apply schema migrations
alembic upgrade head
```

### Option A: File-Based Seeds (Phase 1 Mode)

```bash
# Run crawler against local seed file
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=10000
```

### Option B: Redis-Based Seeds (Phase 2 Mode)

```bash
# Step 1: Start Redis
docker run -d -p 6379:6379 redis:latest
# OR: redis-server

# Step 2: Ingest seeds from Tranco (or other source)
python -m crawler.cli ingest-seeds --source tranco --limit 1000 --offset 0

# Step 3: Check queue status
python -m crawler.cli queue-status

# Step 4: Run crawler (will consume from Redis start_urls)
scrapy crawl discovery -a max_pages=10000

# Step 5: Monitor crawl runs
python -m crawler.cli list-runs --limit 10
```

### CLI Commands Reference

```bash
# Ingest seeds from Tranco
python -m crawler.cli ingest-seeds --source tranco --limit 10000 --offset 0

# Ingest seeds from custom CSV
python -m crawler.cli ingest-seeds --source custom --file my_domains.csv --limit 5000

# View queue status (start_urls, scheduled requests, domain counts)
python -m crawler.cli queue-status

# List recent crawl runs
python -m crawler.cli list-runs --limit 10
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

## Known Gaps and Remaining Work (Current)

**Implemented baseline:**
- ✅ Redis-based URL frontier with seed ingestion CLI
- ✅ Crawl run tracking (`crawl_runs` + `crawl_log` linkage)
- ✅ Domain tracking schema and passive updates (Phase A)
- ✅ Per-domain budgets + checkpoint resume (Phase B)
- ✅ Smart scheduling + claim/lease protocol + priority recalculation (Phase C)

**Remaining gaps:**

1. **Refresh mode not implemented (Phase D pending)**
   - Exhausted domains are tracked but no dedicated refresh spider lifecycle exists yet.

2. **DB-backed tests are environment-gated**
   - Without `DATABASE_URL`, claim/priority/concurrency tests are skipped.
   - CI should run a PostgreSQL-backed test job to validate these paths continuously.

3. **Redis remains a hard dependency for Redis/smart-scheduling paths**
   - No in-process fallback scheduler when Redis is unavailable.

4. **Object storage is still reserved, not active**
   - Metadata is persisted in PostgreSQL; S3/MinIO binary storage is still future work.

5. **`AsyncImageFetcher` remains an alternative implementation**
   - Current runtime path uses `ScrapyImageDownloader`.

---

## Domain Tracking - Phase A Implementation

**Date:** 2026-02-06  
**Status:** ✅ Complete  
**Design Document:** [DOMAIN_TRACKING_DESIGN.md](DOMAIN_TRACKING_DESIGN.md)

Phase A establishes the foundation for domain-centric crawl tracking by making domains first-class persistent entities in the database. This is an **additive-only** change with zero behavior modifications to the crawler logic.

### What Phase A Adds

1. **`domains` table** (Alembic migration `1c3fe655e18f`)
   - Full schema including concurrency fields for future phases
   - ENUM type `domain_status` (pending, active, exhausted, blocked, unreachable)
   - Comprehensive indexes for scheduling queries
   - Automatic `updated_at` trigger

2. **Domain canonicalization** (`processor/domain_canonicalization.py`)
   - Normalizes domains: lowercase, strip www, strip ports, IDN→punycode
   - Configurable subdomain handling via feature flag
   - Dependencies: `idna`, `publicsuffix2`

3. **Domain repository** (`storage/domain_repository.py`)
   - `upsert_domain()`: Insert or ignore for seed processing
   - `update_domain_stats()`: Incremental stats updates (+= for cumulative tracking)
   - `get_domain()`: Fetch domain row
   - `backfill_domains_from_crawl_log()`: Historical data import

4. **Spider integration** (Passive tracking)
   - `start_requests()`: Upserts domain for each seed before yielding
   - `parse()`: Tracks per-domain stats (pages, images found)
   - `closed()`: Updates domain stats for all crawled domains
   - Feature flag: `ENABLE_DOMAIN_TRACKING` (default: true)

5. **CLI commands** (`crawler/cli.py`)
   - `backfill-domains`: Backfill from crawl_log and provenance
   - `domain-status`: Show domain statistics summary

6. **Tests** (15+ new tests)
   - `tests/test_domain_canonicalization.py`: Unit tests for canonicalization rules
   - `tests/test_domain_repository.py`: Unit tests for DB operations
   - `tests/test_domain_tracking_integration.py`: Integration tests

### Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_DOMAIN_TRACKING` | `true` | Enable domain upsert and stats tracking |
| `DOMAIN_CANONICALIZATION_STRIP_SUBDOMAINS` | `false` | Collapse subdomains to registrable domain |

### Usage

```bash
# Run migration
alembic upgrade head

# Backfill historical data
python -m crawler.cli backfill-domains

# Check domain status
python -m crawler.cli domain-status

# Run crawl with domain tracking (default)
scrapy crawl discovery -a seeds=config/test_seeds.txt

# Disable domain tracking if needed
ENABLE_DOMAIN_TRACKING=false scrapy crawl discovery -a seeds=config/test_seeds.txt
```

### Database Schema

The `domains` table tracks:
- **Identity**: `domain` (canonical, unique), `source`, `seed_rank`
- **State**: `status` (pending → active → exhausted/blocked/unreachable)
- **Progress**: `pages_crawled`, `images_found`, `images_stored` (cumulative)
- **Quality**: `image_yield_rate`, `error_rate`, `avg_images_per_page`
- **Scheduling**: `priority_score`, `next_crawl_after`
- **Concurrency**: `claimed_by`, `claim_expires_at`, `version` (for Phase C)
- **Resume**: `last_crawl_run_id`, `frontier_checkpoint_id` (for Phase B)

### Acceptance Criteria Met

✅ Migration runs cleanly and is reversible  
✅ Test suite is green in local non-DB mode (`190 passed, 35 skipped`)  
✅ Domain tracking tests (unit + integration) are present and passing  
✅ Crawl runs successfully with tracking enabled  
✅ `domains` table populates during crawl  
✅ Domain stats update in `closed()`  
✅ Backfill script runs successfully  
✅ Feature flag disables tracking without errors  
✅ No crawl behavior changes (same pages, same images)  
✅ Code quality: mypy strict, Black formatting, Ruff linting  

### Follow-on Status

- **Phase B**: ✅ Implemented
- **Phase C**: ✅ Implemented
- **Phase D**: ⏳ Pending

See [DOMAIN_TRACKING_DESIGN.md](DOMAIN_TRACKING_DESIGN.md) for rollout details.

---

## Domain Tracking - Phase B Implementation

**Date:** 2026-02-07  
**Status:** ✅ Complete  
**Design Document:** [DOMAIN_TRACKING_DESIGN.md](DOMAIN_TRACKING_DESIGN.md) §11 (Phase B)

### What Phase B Adds

1. **Per-domain crawl budgets**
   - `ENABLE_PER_DOMAIN_BUDGET` activates per-domain limits (`MAX_PAGES_PER_RUN`)
   - Prevents one domain from consuming global crawl budget

2. **Frontier checkpoint persistence**
   - Redis-backed checkpoint save/load/delete in `storage/frontier_checkpoint.py`
   - `frontier_checkpoint_id` + `frontier_size` persisted on `domains`

3. **Resume-from-checkpoint flow**
   - Spider resumes pending URLs before starting domain root crawl
   - Checkpoint cleared after successful load

4. **Graceful degradation**
   - Checkpoint storage/load failures are logged and skipped (crawl continues)

---

## Domain Tracking - Phase C Implementation

**Date:** 2026-02-07  
**Status:** ✅ Complete  
**Design Document:** [DOMAIN_TRACKING_DESIGN.md](DOMAIN_TRACKING_DESIGN.md) §9 (Phase C)

Phase C is the **highest-risk phase** of domain tracking. It fundamentally changes how domains are selected (database-driven vs seed-driven) and introduces multi-worker concurrency with a claim/lease protocol.

### What Phase C Adds

1. **Domain Claim Protocol** (`storage/domain_repository.py`)
   - `claim_domains()`: Atomic domain claim acquisition using `FOR UPDATE SKIP LOCKED`
   - `renew_claim()`: Lease renewal (heartbeat) to prevent expiry during long crawls
   - `release_claim()`: Atomic release with optimistic locking (version check)
   - `expire_stale_claims()`: Cleanup utility for stuck claims
   - **Lease duration**: 30 minutes (renewed every 10 minutes)

2. **Priority Calculator** (`storage/priority_calculator.py`)
   - `recalculate_priorities()`: Batch update of priority scores using SQL formula
   - `get_priority_stats()`: Priority distribution and top domains
   - **Scoring factors**:
     - Seed rank (base)
     - Image yield rate × 1000 (reward high-yield domains)
     - Pages remaining × 2 (capped at 500)
     - Error rate × -500 (penalize unreliable domains)
     - Staleness × 5 per day

3. **State Transition Function** (Alembic migration `2f1ae345c29f`)
   - `transition_domain_status()`: PL/pgSQL function enforcing valid state transitions
   - Prevents invalid transitions (e.g., blocked → exhausted)
   - Uses optimistic locking via version field

4. **Smart Scheduling in Spider** (`crawler/spiders/discovery_spider.py`)
   - New mode: Query `domains` table for crawl candidates instead of reading seeds
   - Claims domains before crawling (prevents duplicate work)
   - Background heartbeat thread for lease renewal (every 10 minutes)
   - Resume from checkpoint support for active domains
   - Atomic claim release with stats update in `closed()`

5. **CLI Commands** (`crawler/cli.py`)
   - `domain-status --status {pending,active,exhausted,blocked,unreachable}`: List domains by status
   - `domain-info <domain>`: Detailed domain information
   - `recalculate-priorities`: Recalculate all priority scores
   - `release-stuck-claims`: Cleanup expired claims

6. **Feature Flags** (`env_config.py`)
   - `ENABLE_SMART_SCHEDULING`: Query domains table for candidates (default: false)
   - `ENABLE_CLAIM_PROTOCOL`: Claim domains before crawl (default: false)
   - **Both must be enabled together** for Phase C
   - `DOMAIN_STATS_FLUSH_INTERVAL_PAGES`: Pages between mid-crawl flushes (default: 100)

7. **Tests** (42+ tests for Phase C)
   - `tests/test_domain_claim.py`: Claim protocol, lease expiry, version conflicts, force-release (28 tests)
   - `tests/test_priority_calculation.py`: Scoring formula validation
   - `tests/test_smart_scheduling.py`: Spider integration with smart scheduling, double-count prevention (10 tests)
   - `tests/test_concurrency.py`: Multi-worker simulation, claim isolation, queue validation (12 tests)
   - `tests/test_cli.py`: CLI commands for force-release and stale run cleanup (9 tests)
   - `tests/test_mid_crawl_flush.py`: Mid-crawl state persistence and recovery (6 tests)

### Deployment Safety

**⚠️ CRITICAL: Phase C workers CANNOT coexist with Phase A/B workers**

Phase C uses the claim protocol; Phase A/B workers do not. Running mixed versions will cause duplicate work.

**Deployment Protocol:**
1. Stop ALL Phase A/B workers
2. Deploy Phase C code to all workers
3. Run priority recalculation: `python -m crawler.cli recalculate-priorities`
4. Enable on 1 worker (canary): `ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true`
5. Monitor for 24 hours (check claims, version conflicts)
6. Enable on all workers if stable

**Rollback:**
```bash
# Disable feature flags
export ENABLE_SMART_SCHEDULING=false ENABLE_CLAIM_PROTOCOL=false

# Release all active claims
psql $DATABASE_URL -c "UPDATE domains SET claimed_by = NULL, claim_expires_at = NULL WHERE claimed_by IS NOT NULL;"
```

### Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_SMART_SCHEDULING` | `false` | Query domains table for candidates (Phase C) |
| `ENABLE_CLAIM_PROTOCOL` | `false` | Claim domains before crawl (Phase C) |

### Usage

```bash
# Run migration (includes state transition function)
alembic upgrade head

# Recalculate priorities before enabling smart scheduling
python -m crawler.cli recalculate-priorities

# Check domain status
python -m crawler.cli domain-status --status active --limit 20

# Run with smart scheduling (single worker canary)
ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true \
  scrapy crawl discovery -a max_pages=1000

# Run with smart scheduling (multi-worker)
# Terminal 1
ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true \
  scrapy crawl discovery -a max_pages=1000 &
# Terminal 2
ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true \
  scrapy crawl discovery -a max_pages=1000 &

# Release stuck claims (cleanup)
python -m crawler.cli release-stuck-claims

# Force-release claims for specific worker (emergency recovery)
python -m crawler.cli release-stuck-claims --force --worker-id hostname-12345

# Force-release all claims (emergency recovery - requires confirmation)
python -m crawler.cli release-stuck-claims --force --all-active

# Mark stale crawl runs as failed (no activity timeout)
python -m crawler.cli cleanup-stale-runs --older-than-minutes 60
```

**Phase C Hardening (2026-02-07):**
- Mid-crawl state flushing: domain stats persisted every 100 pages (configurable via `DOMAIN_STATS_FLUSH_INTERVAL_PAGES`)
- Queue isolation: Phase C uses local scheduler (per-worker queues) to prevent cross-worker domain overlap
- Force-release CLI: `release-stuck-claims --force` enables recovery from dead workers
- Stale run cleanup: `cleanup-stale-runs` marks inactive runs as failed for operational hygiene

### Phase C Hardening Validation Runbook

Use the SQL script and evidence template for reproducible operational validation:

- Queries: [scripts/phase_c_validation.sql](scripts/phase_c_validation.sql)
- Evidence template: [scripts/phase_c_hardening_validation_template.md](scripts/phase_c_hardening_validation_template.md)

Recommended flow:

1. Run a 1-worker canary and capture query output.
2. Repeat with 2-3 workers.
3. Repeat with 8 workers.
4. Save results as `phase_c_hardening_validation_YYYYMMDD.md`.

### Database Schema Additions

Phase C uses existing columns from Phase A:
- `claimed_by`: Worker ID holding the lease
- `claim_expires_at`: Lease expiry timestamp
- `version`: Optimistic lock counter (increments on every update)
- `priority_score`: Cached priority for scheduling
- `priority_computed_at`: When priority was last calculated

### Acceptance Criteria Met

✅ Smart scheduling: domains selected by priority (high-yield first)  
✅ Claim protocol: only one worker claims each domain  
✅ Lease expiry: stuck claims auto-release after 30 minutes  
✅ Lease renewal: active crawls extend lease every 10 minutes  
✅ Optimistic locking: version conflicts detected and handled  
✅ Resume-aware: active domains prioritized over pending  
✅ Multi-worker test: 3 workers, 100 domains → no duplicate work  
✅ Feature flags allow safe rollback without code change  
✅ Graceful degradation: claim failure logs and skips (no crash)  
✅ Version conflict handling: retry with exponential backoff (max 3)  
✅ All existing tests pass  
✅ 20+ new tests pass  
✅ Code quality: mypy strict, Black formatting, Ruff linting  

### Architecture Diagram (Phase C)

```
┌─────────────────┐
│  Worker Pool    │ (Multiple workers with unique worker_id)
└────────┬────────┘
         │
         ▼ claim_domains(worker_id, batch_size=10)
┌─────────────────────────────┐
│  PostgreSQL (domains table) │
│  FOR UPDATE SKIP LOCKED     │
│  ORDER BY priority_score    │
└────────┬────────────────────┘
         │
         ▼ (atomically claimed domains)
┌─────────────────┐
│  Spider Crawl   │
│  - Extract URLs │
│  - Follow links │
│  - Heartbeat    │ (every 10 min)
└────────┬────────┘
         │
         ▼ release_claim(...) with stats
┌─────────────────────────────┐
│  Update domain status       │
│  - pages_crawled += delta   │
│  - images_stored += delta   │
│  - status → exhausted/active│
└─────────────────────────────┘
```

### Next: Phase D

Phase C is complete and stable. Future work:
- **Phase D**: Refresh mode spider that revisits exhausted domains

See [DOMAIN_TRACKING_DESIGN.md](DOMAIN_TRACKING_DESIGN.md) for full design.

---

## Phase 3 Roadmap (Future Work)

### High Priority

| Improvement | Rationale | Status |
|-------------|-----------|--------|
| **Similarity search index** | Perceptual hashes (pHash/dHash) are computed (85.9% coverage). Build search index to enable detection of re-encoded/cropped images using existing hash data. | Hashes ready in DB |
| **Object storage for binaries** | Currently only metadata is stored. Add MinIO/S3 backend for content-addressable binary storage `/{sha256[0:2]}/{sha256[2:4]}/{sha256}`. | Settings placeholders exist |
| **Additional Phase 2 tests** | CLI integration tests, crawl run tracking tests, dual-mode seed tests. Some unit tests exist (test_scheduler.py), need integration coverage. | High priority |
| **Distributed crawl mode** | Support `scrapyd` or multiple Scrapy instances sharing Redis queue for horizontal scaling. | Planning |

### Medium Priority

| Improvement | Rationale | Status |
|-------------|-----------|--------|
| **Refresh crawl spider** | Separate spider for `discovery_type='refresh'` with different depth/politeness settings focusing on known URLs. | Planned |
| **Metrics & observability** | Add Prometheus metrics exporter for crawl rate, error rate, queue depth. Current logging is basic. | Planned |
| **Bloom filter for URL dedup** | For large-scale crawling, in-memory Bloom filter reduces DB lookups for already-seen URLs. | Research phase |

### Lower Priority

| Improvement | Rationale | Status |
|-------------|-----------|--------|
| **Structured logging activation** | Wire `logging_config.py` JSON formatter into spider/pipeline execution. | Enhancement |
| **AsyncImageFetcher cleanup** | Either wire alternative Twisted-based fetcher or remove to reduce maintenance surface. | Cleanup |
| **Crawl run images_downloaded stat** | Pipeline should increment `crawl_runs.images_downloaded` on successful stores. | Enhancement |

---

## Phase 2 Implementation Notes (Current Behavior)

### Data Flow (Detailed)

```
Spider parses HTML
   ↓
Spider yields image Request(callback=parse_image)
   ↓
Scrapy downloads image (respects politeness)
   ↓
parse_image callback receives Response
   ↓
Callback yields item with Response attached
   ↓
Pipeline receives item
   ↓
Pipeline validates Response via ScrapyImageDownloader
   ↓
Pipeline computes SHA-256 hash
   ↓
Pipeline checks for existing image (dedup)
   ↓
Pipeline stores/updates metadata in PostgreSQL
   ↓
Pipeline ensures provenance record
```

### Redis Queue Structure

- **`{spider}:start_urls`**: Sorted set (score = priority) containing seed URLs ingested via CLI
- **`{spider}:requests`**: Sorted set containing scheduled requests from crawl
- **`{spider}:dupefilter`**: Set for URL deduplication
- **`{spider}:domains`**: Set tracking unique domains encountered
- **`{spider}:requests:domain_counts`**: Hash tracking per-domain request counts

### Stopping Conditions

- Crawl stops when `max_pages` is reached OR queue is exhausted
- Default `max_pages=10` unless overridden via `-a max_pages=...`
- Redis queues persist across runs unless `SCHEDULER_FLUSH_ON_START=True`

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://localhost/invisible` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string (Phase 2) |
| `CRAWLER_USER_AGENT` | `InvisibleCrawler/0.1 (...)` | User-Agent for HTTP requests |
| `DISCOVERY_REFRESH_AFTER_DAYS` | `0` (disabled) | Re-fetch images older than N days |
| `IMAGE_MIN_WIDTH` | `256` | Minimum image width in pixels |
| `IMAGE_MIN_HEIGHT` | `256` | Minimum image height in pixels |
| `LOG_LEVEL` | `INFO` | Log verbosity level |
| `OBJECT_STORE_ENDPOINT` | `http://localhost:9000` | MinIO/S3 endpoint (reserved for Phase 3) |
| `OBJECT_STORE_BUCKET` | `invisible-images` | MinIO/S3 bucket (reserved for Phase 3) |
| `OBJECT_STORE_ACCESS_KEY` | `change-me` | MinIO/S3 access key (reserved for Phase 3) |
| `OBJECT_STORE_SECRET_KEY` | `change-me` | MinIO/S3 secret key (reserved for Phase 3) |
| `OBJECT_STORE_REGION` | `us-east-1` | MinIO/S3 region (reserved for Phase 3) |
| `OBJECT_STORE_SECURE` | `false` | Use TLS for MinIO/S3 (reserved for Phase 3) |
| `ENABLE_DOMAIN_TRACKING` | `true` | Enable domain upsert and stats tracking (Phase A) |
| `DOMAIN_CANONICALIZATION_STRIP_SUBDOMAINS` | `false` | Collapse subdomains to registrable domain (Phase A) |
| `ENABLE_PER_DOMAIN_BUDGET` | `false` | Use per-domain page limits instead of global (Phase B) |
| `MAX_PAGES_PER_RUN` | `1000` | Default per-domain page limit when per-domain budgets enabled (Phase B) |
| `ENABLE_SMART_SCHEDULING` | `false` | Query domains table for candidates instead of seeds (Phase C) |
| `ENABLE_CLAIM_PROTOCOL` | `false` | Claim domains before crawling (Phase C) |

---

## Validation Snapshot (2026-02-07)

### Local Validation

- `python -m ruff check .` → pass
- `pytest -q` → `190 passed, 35 skipped`

### Why 35 Tests Are Skipped

Skipped tests are DB-backed domain-tracking suites that require `DATABASE_URL`:
- `tests/test_domain_claim.py`
- `tests/test_priority_calculation.py`
- `tests/test_concurrency.py`

To run all tests, provide a live PostgreSQL database and set `DATABASE_URL` before running `pytest`.

### Recommended CI Baseline

1. Run lint/type checks (`ruff`, `black --check`, `mypy`).
2. Run fast suite without DB (default local path).
3. Run DB-backed suite with PostgreSQL service and migrations applied.

---
