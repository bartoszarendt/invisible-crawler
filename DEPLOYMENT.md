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
- `autoheal` (optional health-based restart watcher)

### 3.2 Compose files

- `docker-compose.yml`: base stack and shared defaults
- `docker-compose.dev.yml`: local development overrides
- `docker-compose.prod.yml`: VPS production overrides (restart policy, resource limits)

Optional reliability profile:
- `--profile autoheal` enables the `autoheal` sidecar that restarts labeled containers when Docker healthcheck status is `unhealthy`.

Default topology follows Umami-style self-hosting:
- application + PostgreSQL + Redis are bundled in Compose with persistent volumes.
- optional external mode remains supported by overriding `DATABASE_URL` and `REDIS_URL`.

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
   - `docker compose --profile ops --env-file .env.dev -f docker-compose.yml run --rm migrate`
5. Optionally ingest seeds:
   - `docker compose --profile ops --env-file .env.dev -f docker-compose.yml run --rm seed_ingest`
6. Start crawler:
   - `docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d crawler`

### 6.2 VPS production start

1. Create `.env.prod` from `.env.prod.example` and set secrets.
2. Build/pull production image.
3. Start dependencies:
   - `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d postgres redis`
4. Run migrations:
   - `docker compose --profile ops --env-file .env.prod -f docker-compose.yml run --rm migrate`
5. Ingest seeds (first bootstrap or namespace switch):
   - `SEED_LIMIT=10000 docker compose --profile ops --env-file .env.prod -f docker-compose.yml run --rm seed_ingest`
6. Start crawler workers:
   - `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d crawler`
7. Verify run status:
   - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli list-runs --limit 20`

### 6.2.1 Optional: healthcheck-based auto-restart

`restart: unless-stopped` already restarts on crashes/OOM exits. To also restart containers that stay running but become `unhealthy`, enable the optional `autoheal` profile.

Start or update stack with autoheal enabled:
- `docker compose --profile autoheal --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d`

Verify autoheal is running:
- `docker compose --profile autoheal --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml ps autoheal`

Disable it later:
- `docker compose --profile autoheal --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml stop autoheal`

### 6.3 Common runtime commands

- Queue status:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli queue-status`
- List crawl runs:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli list-runs --limit 20`
- Seed ingestion (custom limit):
   - `SEED_LIMIT=10000 docker compose --profile ops --env-file .env.prod -f docker-compose.yml run --rm seed_ingest`
- Domain status:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli domain-status --limit 20`
- Recalculate priorities:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli recalculate-priorities`
- Release stuck claims:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli release-stuck-claims`
- Cleanup stale runs:
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli cleanup-stale-runs --older-than-minutes 60`
- Cleanup persistent dupefilter fingerprints (only when `ENABLE_PERSISTENT_DUPEFILTER=true`):
  - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli cleanup-fingerprints`

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

### 7.3 Phase C+ Rollout (Operational)

Use a staged canary with explicit flags and captured evidence. Keep this deployment rule:
- Do not run Phase A/B workers at the same time as Phase C workers.

1. Ensure Phase A/B workers are stopped.
2. Set Phase C flags in `.env.prod`:
   - `ENABLE_SMART_SCHEDULING=true`
   - `ENABLE_CLAIM_PROTOCOL=true`
   - `ENABLE_CONTINUOUS_MODE=true` (recommended for VPS workers)
   - `ENABLE_PERSISTENT_DUPEFILTER=true` (recommended for resumability)
3. Rebuild/restart a single canary worker:
   - `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --scale crawler=1 crawler`
4. Run validation queries:
   - `psql $DATABASE_URL -f scripts/phase_c_validation.sql`
5. Scale to 2-3 workers, repeat validation:
   - `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --scale crawler=3 crawler`
6. Scale to target worker count (for example 8), repeat validation.
7. Save evidence as `phase_c_hardening_validation_YYYYMMDD.md`.

### 7.4 Phase C+ Rollback (Operational)

If overlap or instability is detected:

1. Disable Phase C flags:
   - Set `ENABLE_SMART_SCHEDULING=false`
   - Set `ENABLE_CLAIM_PROTOCOL=false`
   - Set `ENABLE_CONTINUOUS_MODE=false`
   - Set `ENABLE_PERSISTENT_DUPEFILTER=false`
2. Release active claims:
   - `docker compose --env-file .env.prod -f docker-compose.yml run --rm crawler python -m crawler.cli release-stuck-claims --force --all-active`
3. Restart crawler service:
   - `docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d crawler`

## 8. Security and Reliability Baseline

1. Do not commit `.env.prod`, `.env.dev`, or secrets.
2. Use distinct Redis DB/namespace per environment.
3. Keep `ROBOTSTXT_OBEY=true` unless explicitly reviewed.
4. Use restart policy (`unless-stopped`) in production.
5. Configure host-level firewall and restrict DB/Redis public exposure.
6. Back up Postgres and Redis persistence volumes.
7. Include valid crawler contact in `CRAWLER_USER_AGENT`.
8. Optional: use `--profile autoheal` for healthcheck-based restarts of labeled services.

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

## 10. Current Maturity and Next Steps

### 10.1 Implemented and VPS-ready

1. Dockerized runtime with persistent state volumes.
2. Domain tracking with per-domain budgets and smart scheduling/claim protocol flags.
3. Continuous worker mode (`ENABLE_CONTINUOUS_MODE`) for long-lived VPS workers.
4. Optional persistent URL dedup (`ENABLE_PERSISTENT_DUPEFILTER`) with cleanup command.
5. InvisibleID evolution switch (`ENABLE_IMMUTABLE_ASSETS`) with additive schema support.

### 10.2 Recommended next hardening tasks

1. Add CI pipeline for lint, type-check, tests, and image build.
2. Add host-level backup automation for Postgres and Redis volumes.
3. Add alerting thresholds for crawl stalls, claim leaks, and high 429/503 rates.
4. Add operational dashboard for queue depth, run throughput, and domain status transitions.

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
