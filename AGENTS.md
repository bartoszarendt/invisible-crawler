# Project Overview

InvisibleCrawler is a self-hosted, large-scale web crawler designed to discover and index image assets across the public web. Built to support the OneMark/InvisibleID ecosystem, it fetches images, generates stable fingerprints (binary and perceptual hashes), and stores provenance metadata. The system is architected for independent evolution and future integration with InvisibleID watermark detection while maintaining clear separation of concerns.

## Repository Structure

> TODO: Project is in early design phase; no implementation directories exist yet.

* **SYSTEM_DESIGN.md** – comprehensive architectural blueprint covering goals, design principles, data flow, storage choices, and evolution path.

Once implemented, expected structure:
* **crawler/** – Scrapy-based crawling engine with spiders and pipelines
* **scheduler/** – URL frontier implementation for queue management and rate limiting
* **processor/** – image fetching, normalization, and fingerprinting logic
* **storage/** – database schemas and migration scripts for PostgreSQL
* **config/** – seed domain lists, crawl policies, and environment configs
* **tests/** – unit, integration, and end-to-end test suites
* **docs/** – operational runbooks and architecture decision records

## Build & Development Commands

> TODO: No build system configured yet. Expected tooling:

```bash
# Install dependencies (Python-based, Scrapy core)
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start crawler (discovery mode)
scrapy crawl discovery -a seeds=config/tranco_top10k.txt

# Start crawler (refresh mode)
scrapy crawl refresh -a input=data/known_urls.csv

# Run tests
pytest tests/ --cov=crawler

# Type checking
mypy crawler/ scheduler/ processor/

# Lint
ruff check .

# Format
black .
```

## Code Style & Conventions

> TODO: Style guide to be formalized. Anticipated conventions:

* **Formatting**: Black (line length 100), isort for imports
* **Linting**: Ruff with strict settings; mypy in `--strict` mode
* **Naming**: snake_case for functions/variables, PascalCase for classes
* **Commit messages**: Conventional Commits format (`feat:`, `fix:`, `docs:`, etc.)
* **Docstrings**: Google-style for all public functions and classes
* **Error handling**: explicit exception types; no bare `except:` clauses
* **Logging**: structured logging with context (JSON when in production)

## Architecture Notes

The crawler follows a pipeline architecture with clear stage boundaries:

```
┌─────────────────┐
│  Seed Sources   │  (Tranco, Majestic Million, curated lists)
└────────┬────────┘
         ↓
┌─────────────────┐
│  URL Frontier   │  (Redis-backed scheduler: dedup, prioritization, rate limits)
└────────┬────────┘
         ↓
┌─────────────────┐
│ Scrapy Spiders  │  (HTML parsing: <img>, <picture>, og:image extraction)
└────────┬────────┘
         ↓
┌─────────────────┐
│ Image Fetcher   │  (content-type filtering, size thresholds, binary fetch)
└────────┬────────┘
         ↓
┌─────────────────┐
│  Normalization  │  (strip EXIF, canonical format, colorspace standardization)
└────────┬────────┘
         ↓
┌─────────────────┐
│ Fingerprinting  │  (SHA-256 binary hash + pHash/dHash perceptual hashes)
└────────┬────────┘
         ↓
┌─────────────────┐
│ Storage Layer   │  (PostgreSQL metadata + S3-compatible object store)
└─────────────────┘
```

**Key Components:**

* **URL Frontier**: Per-domain queues with separate priority lanes for discovery vs. refresh crawls. Implements politeness (robots.txt, rate limits, backoff).
* **Scrapy Layer**: No JavaScript rendering in Phase 1; static HTML parsing only.
* **Fingerprinting**: Multi-hash strategy enables exact dedup (SHA-256) and future similarity matching (perceptual hashes).
* **Storage**: PostgreSQL for relational metadata; MinIO/S3 for content-addressable binary assets.
* **Separation Principle**: InvisibleID detection is explicitly out-of-scope for Phase 1; reserved database fields exist but remain unpopulated.

## Testing Strategy

> TODO: Test infrastructure not yet implemented. Planned approach:

* **Unit tests**: pytest for individual components (parsers, fingerprinters, normalizers)
* **Integration tests**: testcontainers for PostgreSQL and Redis fixtures
* **End-to-end tests**: isolated crawl simulations against mock HTTP servers
* **CI pipeline**: GitHub Actions running lint, type-check, and full test suite on every PR
* **Coverage target**: ≥80% for core logic; exceptions allowed for scaffolding code
* **Fixtures**: Synthetic HTML pages and test images versioned in `tests/fixtures/`

## Security & Compliance

* **Secrets**: All credentials via environment variables; never committed to version control
* **Dependency scanning**: Dependabot or equivalent for automated CVE alerts
* **Rate limiting**: Conservative per-domain limits (default: 1 req/sec); exponential backoff on 429/503
* **robots.txt**: Respected by default unless explicitly overridden for specific domains
* **Data retention**: Provenance metadata stored indefinitely; binary retention policy TBD
* **Licensing**: Project license TBD; third-party dependencies reviewed for compatibility
* **Privacy**: Crawler identifies itself via User-Agent; contact information included

> TODO: Formalize GDPR/CCPA compliance strategy for discovered personal images

## Agent Guardrails

**Files Never Modified by Automated Agents:**

* `SYSTEM_DESIGN.md` – architectural source of truth; human-approved changes only
* `config/seed_domains.txt` – seed lists require manual curation and review
* Database migration files after merge – immutable post-deployment

**Required Human Review:**

* Changes to crawl politeness settings (rate limits, robots.txt handling)
* New external dependencies (security and license review)
* Database schema migrations (backwards compatibility verification)
* Modifications to fingerprinting algorithms (reproducibility impact)

**Rate Limits for Agent Operations:**

* Max 3 retry attempts for failed file edits before escalating to human
* Crawl experiments limited to max 100 URLs without explicit approval
* No automated deployment without passing full test suite

## Extensibility Hooks

> TODO: Plugin system design pending. Anticipated extension points:

* **Custom spider middleware**: Inject domain-specific parsing logic
* **Fingerprinting algorithms**: Pluggable hash function registry
* **Storage backends**: Abstract interface for swapping PostgreSQL/object storage
* **Seed providers**: Dynamic seed generation from external APIs
* **Post-processing pipelines**: Hook for adding custom analyzers without forking

**Environment Variables:**

* `CRAWLER_MODE`: `discovery` | `refresh`
* `RATE_LIMIT_DEFAULT`: requests per second per domain
* `IMAGE_MIN_SIZE`: minimum image dimensions to fetch
* `STORAGE_BACKEND`: `postgres` | `clickhouse` (future)
* `OBJECT_STORE_ENDPOINT`: S3-compatible storage URL
* `FEATURE_FLAG_JS_RENDERING`: enable Playwright for selective JS sites

## Further Reading

> TODO: Additional documentation to be created:

* **docs/ARCHITECTURE.md** – detailed component diagrams and interaction protocols
* **docs/OPERATIONS.md** – deployment guide, monitoring, and incident response
* **docs/ADR/** – architecture decision records tracking key design choices
* **docs/SEED_CURATION.md** – methodology for selecting and vetting seed domains
* **docs/FINGERPRINTING.md** – deep dive on hash selection and collision handling
* **docs/INVISIBLEID_INTEGRATION.md** – future integration plan with detection service

---

**Last Updated**: 2026-02-05  
**Status**: Design phase; implementation pending  
**Maintainer Contact**: TBD
