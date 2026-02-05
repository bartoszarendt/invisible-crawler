# Project Overview

InvisibleCrawler is a self-hosted, large-scale web crawler designed to discover and index image assets across the public web. Built to support the OneMark/InvisibleID ecosystem, it fetches images, generates stable fingerprints (binary and perceptual hashes), and stores provenance metadata. The system is architected for independent evolution and future integration with InvisibleID watermark detection while maintaining clear separation of concerns.

## Repository Structure

* **SYSTEM_DESIGN.md** – architectural blueprint covering goals, design principles, data flow, and evolution path
* **IMPLEMENTATION.md** – Phase 1 implementation details, runbook, and Phase 2 roadmap
* **crawler/** – Scrapy-based crawling engine with spiders and pipelines
* **processor/** – image fetching, normalization, and fingerprinting logic
* **storage/** – database schemas and Alembic migrations for PostgreSQL
* **config/** – seed domain lists, allowlists, and blocklists
* **tests/** – unit and integration test suites (32 tests)

## Build & Development Commands

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run database migrations
alembic upgrade head

# Start crawler (discovery mode)
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=100

# Run tests
pytest tests/ --cov=crawler --cov=processor --cov-report=term-missing

# Type checking
mypy crawler/ processor/ storage/

# Lint
ruff check .

# Format
black .
```

## Code Style & Conventions

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

* **Unit tests**: pytest for individual components (fetcher, fingerprinter, spider parsing)
* **Integration tests**: pytest-httpserver for mock HTTP endpoints; requires running PostgreSQL
* **Test count**: 32 tests covering processor, spider, and pipeline integration
* **Fixtures**: Synthetic HTML pages in `tests/fixtures.py`
* **Coverage target**: ≥80% for core logic

## Security & Compliance

* **Secrets**: All credentials via environment variables; never committed to version control
* **Dependency scanning**: Dependabot or equivalent for automated CVE alerts
* **Rate limiting**: Conservative per-domain limits (default: 1 req/sec); exponential backoff on 429/503
* **robots.txt**: Respected by default unless explicitly overridden for specific domains
* **Data retention**: Provenance metadata stored indefinitely; binary retention policy TBD
* **Licensing**: Project license TBD; third-party dependencies reviewed for compatibility
* **Privacy**: Crawler identifies itself via User-Agent; contact information included

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

* **Custom spider middleware**: Inject domain-specific parsing logic
* **Fingerprinting algorithms**: Pluggable hash function registry
* **Storage backends**: Abstract interface for swapping PostgreSQL/object storage
* **Seed providers**: Dynamic seed generation from external APIs
* **Post-processing pipelines**: Hook for adding custom analyzers without forking

**Environment Variables (Phase 1):**

* `DATABASE_URL`: PostgreSQL connection string
* `CRAWLER_USER_AGENT`: User-Agent string for image fetching
* `DISCOVERY_REFRESH_AFTER_DAYS`: Re-fetch images older than N days (default: 0 = disabled)

## Further Reading

* **IMPLEMENTATION.md** – Phase 1 details, local runbook, and Phase 2 roadmap
* **SYSTEM_DESIGN.md** – architectural blueprint and design principles

---

**Last Updated**: 2026-02-05  
**Status**: Phase 1 complete (local, on-demand crawling)  
**Maintainer Contact**: TBD
