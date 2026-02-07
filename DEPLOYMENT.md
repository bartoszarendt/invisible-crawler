# InvisibleCrawler Deployment Architecture

## 1. Purpose

This document defines a production-ready deployment architecture for running InvisibleCrawler continuously on a VPS while keeping active development and safe updates practical.

It includes:
- Runtime architecture (Docker Compose based)
- Environment and configuration model (`APP_ENV`, `CRAWL_PROFILE`, explicit overrides)
- Operational procedures (bootstrap, run, monitor, update, rollback)
- An implementation roadmap mapped to concrete repo files

## 2. Deployment Goals

1. Keep crawler online for long-running crawls.
2. Support safe, repeatable updates as code evolves.
3. Separate environment identity from crawl aggressiveness.
4. Avoid hidden behavior differences between local and VPS.
5. Keep queue/database state durable across restarts and deploys.

## 3. Architecture Overview

### 3.1 Runtime components

- `crawler` (Scrapy process)
- `postgres` (metadata store)
- `redis` (scheduler frontier + dedupe state)
- `migrate` (one-off Alembic migrations)
- `seed_ingest` (one-off seed loading)

### 3.2 Compose files

- `docker-compose.yml`: base stack and shared defaults
- `docker-compose.dev.yml`: local development overrides
- `docker-compose.prod.yml`: VPS production overrides (restart policy, resource limits)

### 3.3 Persistence

- `postgres_data` volume
- `redis_data` volume
- `crawler_state` volume (`/app/.scrapy`)
- `crawler_logs` volume (`/app/logs`)

State survives container recreation, image updates, and host reboots.

## 4. Configuration Model

### 4.1 Environment model

Use two explicit selectors:

- `APP_ENV`: deployment context (`dev`, `staging`, `prod`)
- `CRAWL_PROFILE`: crawl behavior profile (`conservative`, `broad`)

This keeps application environment separate from crawl policy.

### 4.2 Override hierarchy

1. Process environment variables (from Compose/env-file)
2. `.env` file loaded by `env_config.py`
3. Typed defaults in `env_config.py`

`load_dotenv(..., override=False)` preserves runtime environment precedence.

Credential note:
- Use URL-encoded credentials in `DATABASE_URL` (for example, `$` -> `%24`) to avoid parser/interpolation issues.

### 4.3 Central config boundary

`env_config.py` is the single source for typed runtime settings:

- Application identity (`APP_ENV`, `CRAWL_PROFILE`)
- Crawl knobs (`SCRAPY_*`, `CRAWLER_MAX_PAGES`)
- Queue namespace (`QUEUE_NAMESPACE`)
- Existing DB/Redis/image/logging settings

### 4.4 Redis key versioning strategy

Use `QUEUE_NAMESPACE` to isolate queue formats by deploy generation.

Examples:
- `dev-v1`
- `prod-v1`
- `prod-v2` (after scheduler/request serialization changes)

This avoids mixed old/new request blobs in Redis during major upgrades.

## 5. Crawl Profiles and Tuning

### 5.1 Conservative profile (default)

Good for local validation and low-risk runs.

Typical values:
- `SCRAPY_CONCURRENT_REQUESTS=16`
- `SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN=1`
- `SCRAPY_DOWNLOAD_DELAY=1`
- `SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY=1.0`

### 5.2 Broad profile (VPS)

Good for long-running discovery across many domains.

Typical values:
- `SCRAPY_CONCURRENT_REQUESTS=64`
- `SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN=4`
- `SCRAPY_DOWNLOAD_DELAY=0.25`
- `SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY=4.0`

Tune by observing:
- HTTP 429/503 rate
- Redis queue depth trend
- DB write latency
- CPU saturation and memory pressure

### 5.3 Max pages behavior

`CRAWLER_MAX_PAGES` is now centralized in env config.

- `> 0`: cap crawl pages
- `<= 0`: no page cap (continuous mode candidate)

### 5.4 VPS sizing recommendations

#### a) 1 vCPU / 4 GB RAM / 50 GB storage

Use the conservative profile or a moderate override. Full broad profile will saturate the CPU.

Recommended `.env.prod` settings:
```
CRAWL_PROFILE=conservative
SCRAPY_CONCURRENT_REQUESTS=16
SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN=1
SCRAPY_DOWNLOAD_DELAY=1
SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY=1.0
CRAWLER_MAX_PAGES=0
```

Optional moderate override (up to ~2x throughput without saturating):
```
SCRAPY_CONCURRENT_REQUESTS=32
SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN=2
SCRAPY_DOWNLOAD_DELAY=0.5
SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY=2.0
```

Container resource limits (`docker-compose.prod.yml`):

| Service  | CPU limit | CPU reservation | Memory limit | Memory reservation |
|----------|-----------|-----------------|--------------|-------------------|
| postgres | 0.50      | 0.10            | 768M         | 256M              |
| redis    | 0.25      | 0.05            | 256M         | 128M              |
| crawler  | 1.00      | 0.25            | 2G           | 512M              |
| **Total**| **1.75**  | **0.40**        | **3G**       | **896M**          |

Leaves ~1 GB for OS and Docker daemon.

#### b) 2-4 vCPU / 8 GB RAM / 50+ GB storage

Full broad profile is appropriate. This is the recommended tier for sustained production crawling.

Recommended `.env.prod` settings:
```
CRAWL_PROFILE=broad
SCRAPY_CONCURRENT_REQUESTS=64
SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN=4
SCRAPY_DOWNLOAD_DELAY=0.25
SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY=4.0
CRAWLER_MAX_PAGES=0
```

Container resource limits (`docker-compose.prod.yml`):

| Service  | CPU limit | CPU reservation | Memory limit | Memory reservation |
|----------|-----------|-----------------|--------------|-------------------|
| postgres | 1.00      | 0.25            | 1G           | 512M              |
| redis    | 0.50      | 0.10            | 512M         | 256M              |
| crawler  | 2.00      | 1.00            | 4G           | 1G                |
| **Total**| **3.50**  | **1.35**        | **5.5G**     | **1.75G**         |

Leaves ~2.5 GB for OS and Docker daemon. CPU limits allow burst above reservations since services rarely peak simultaneously.

#### Scaling signals (when to upgrade)

- Sustained CPU > 80% during crawling → add vCPUs
- Redis memory > 400 MB → more RAM (frontier growing large)
- HTTP 429/503 rate climbing → not a hardware issue; tune concurrency down
- Disk > 80% → add storage or enable log rotation review

## 6. Practical Operations (Docker Compose)

### 6.1 First-time bootstrap (dev)

1. Create env file:
   - `cp .env.dev.example .env.dev`
2. Build image:
   - `docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml build`
3. Start dependencies:
   - `docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis`
4. Run migrations:
   - `docker compose --env-file .env.dev -f docker-compose.yml run --rm --profile ops migrate`
5. Optionally ingest seeds:
   - `docker compose --env-file .env.dev -f docker-compose.yml run --rm --profile ops seed_ingest`
6. Start crawler:
   - `docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d crawler`

### 6.2 VPS production start

1. Create `.env.prod` from `.env.prod.example` and set secrets.
2. Build/pull production image.
3. Run migrations before crawler restart.
4. Start stack:
   - `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d`

### 6.3 Common runtime commands

- Queue status:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli queue-status`
- List crawl runs:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli list-runs --limit 20`
- Seed ingestion (custom limit):
  - `SEED_LIMIT=10000 docker compose --env-file .env.prod -f docker-compose.yml run --rm --profile ops seed_ingest`

## 7. Update and Rollback Strategy

### 7.1 Update workflow (safe default)

1. Build and tag image (`invisible-crawler:<version>`).
2. Apply DB migrations with `migrate` job.
3. Deploy crawler container with new image.
4. Verify queue status and crawl run telemetry.
5. Monitor errors, HTTP reject rates, and process health.

If scheduler/request serialization changes:
- bump `QUEUE_NAMESPACE`
- ingest seeds into new namespace
- switch crawler to new namespace
- retire old namespace after validation

### 7.2 Rollback workflow

1. Revert crawler image tag.
2. Restore previous `QUEUE_NAMESPACE` if needed.
3. Restart crawler service.
4. Keep DB rollback limited to tested reversible migrations only.

### 7.3 Phase C Hardening Rollout (Operational)

Use a staged canary with explicit flags and captured evidence.

1. Ensure Phase A/B workers are stopped.
2. Run a 1-worker canary:
   - `ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true scrapy crawl discovery`
3. Run validation queries:
   - `psql $DATABASE_URL -f scripts/phase_c_validation.sql`
4. Scale to 2-3 workers, repeat validation.
5. Scale to 8 workers, repeat validation.
6. Save evidence as `phase_c_hardening_validation_YYYYMMDD.md`.

### 7.4 Phase C Hardening Rollback (Operational)

If overlap or instability is detected:

1. Disable Phase C flags:
   - `export ENABLE_SMART_SCHEDULING=false ENABLE_CLAIM_PROTOCOL=false`
2. Release active claims:
   - `python -m crawler.cli release-stuck-claims --force --all-active`
3. Restart crawlers in Phase A/B mode.

## 8. Security and Reliability Baseline

1. Do not commit `.env.prod`, `.env.dev`, or secrets.
2. Use distinct Redis DB/namespace per environment.
3. Keep `ROBOTSTXT_OBEY=true` unless explicitly reviewed.
4. Use restart policy (`unless-stopped`) in production.
5. Configure host-level firewall and restrict DB/Redis public exposure.
6. Back up Postgres and Redis persistence volumes.
7. Include valid crawler contact in `CRAWLER_USER_AGENT`.

## 9. Monitoring and SLOs (Minimal Set)

Track at least:
- Active crawler container status
- Queue depth (`start_urls`, `requests`)
- Crawl throughput (pages/min, images/min)
- Error ratios (429/503, parse failures, dropped images)
- Resource pressure (CPU, memory, disk)

Practical SLO starter:
- crawler process uptime >= 99%
- Redis queue not stalled for >30 min during active ingestion
- migration job success before each production release

## 10. Implementation Plan

### Phase 1: Foundation (completed in this repo change)

1. Centralized env/config profile model:
   - `env_config.py` now exposes typed getters for `APP_ENV`, `CRAWL_PROFILE`, Scrapy knobs, `QUEUE_NAMESPACE`, `CRAWLER_MAX_PAGES`.
   - `APP_ENV` and `CRAWL_PROFILE` are allowlisted with safe fallback and warning logs on invalid values.
2. Removed hardcoded spider crawl overrides to allow profile-driven settings.
3. Added Redis key abstraction:
   - `crawler/redis_keys.py`
   - integrated into `crawler/settings.py`, `crawler/cli.py`, `crawler/spiders/discovery_spider.py`, `crawler/scheduler.py`.
4. Added Docker scaffolding:
   - `Dockerfile`
   - `.dockerignore`
   - `docker/entrypoint.sh`
   - `docker-compose.yml`
   - `docker-compose.dev.yml`
   - `docker-compose.prod.yml`
   - crawler container healthcheck in base compose
   - production resource limits moved to `deploy.resources`
5. Added environment templates:
   - `.env.example` (expanded)
   - `.env.dev.example`
   - `.env.prod.example`

### Phase 2: Operational hardening (recommended next)

1. Add CI pipeline for:
   - lint, type-check, tests, container build
2. Add health and metrics export endpoint for queue depth + crawl stats.
3. Add scheduled backup automation for Postgres/Redis volumes.
4. Add alerting thresholds for crawl stalls and error spikes.

### Phase 3: Scaling path (when needed)

1. Run multiple crawler workers against shared Redis frontier.
2. Introduce dedicated ingestion worker schedule.
3. Consider managed Postgres/Redis services for operational overhead reduction.

## 11. File Map (Deployment-Relevant)

- `DEPLOYMENT.md`: architecture and ops runbook
- `Dockerfile`: crawler image definition
- `docker/entrypoint.sh`: dependency-ready startup guard
- `docker-compose.yml`: base services + ops jobs
- `docker-compose.dev.yml`: local/dev overrides
- `docker-compose.prod.yml`: VPS production overrides
- `.env.dev.example`: development template
- `.env.prod.example`: production template
- `env_config.py`: typed env boundary
- `crawler/redis_keys.py`: Redis key namespace consistency
