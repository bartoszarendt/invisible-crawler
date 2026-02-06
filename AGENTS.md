# Project Overview

InvisibleCrawler is a self-hosted, large-scale web crawler designed to discover and index image assets across the public web. Built to support the OneMark/InvisibleID ecosystem, it fetches images, generates stable fingerprints (binary and perceptual hashes), and stores provenance metadata. The system is architected for independent evolution and future integration with InvisibleID watermark detection while maintaining clear separation of concerns.

## Repository Structure

* **SYSTEM_DESIGN.md** – architectural blueprint covering goals, design principles, data flow, and evolution path
* **IMPLEMENTATION.md** – Phase 1 implementation details, runbook, and Phase 2 roadmap
* **DOMAIN_TRACKING_DESIGN.md** – design proposal for domain-centric crawl tracking (pending review)
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

## Operational Context

This crawler is designed to operate at scale over weeks and months, crawling millions of domains with frequent stops, resumes, and reruns. Agents must understand these realities:

* **Long-running campaigns**: A full crawl of the seed list takes days/weeks. The process will be stopped and resumed many times. Any in-memory-only state is effectively lost.
* **Budget allocation matters**: Crawl time is a finite resource. Spending budget on unproductive, blocked, or already-exhausted domains is waste. The system must get smarter over time about where to allocate crawl effort.
* **Domain is the unit of work**: Individual pages and images matter for storage, but the *domain* is the unit of scheduling, budgeting, and progress tracking. Every domain should have persistent, queryable state.
* **Resumability is non-negotiable**: Stopping a crawl and restarting it must not mean starting from scratch. Progress must be checkpointed and recoverable.

## Proactive Architecture Review

Agents working on this project should periodically (at phase boundaries, when planning new work, or when asked to evaluate the system) perform the following checks. **Do not wait to be asked** — flag these proactively when relevant.

### 1. Design-Implementation Alignment

Compare `SYSTEM_DESIGN.md` goals against actual implementation. Flag any designed capability that has no code counterpart or is only partially implemented. Specifically verify:
* Are all data flow stages from the architecture diagram implemented end-to-end?
* Do the scheduling capabilities described (per-domain queues, priority lanes, dedup) actually exist in code?
* Are both crawl modes (discovery and refresh) functional, or is one just a placeholder?

### 2. State Persistence Audit

Any in-memory state that affects crawl behavior **must** have a persistent counterpart in the database. Ephemeral-only state is a design gap. Check for:
* Blocked domain lists that only live in memory
* Error counters that reset on restart
* Progress counters with no database backing
* Scheduling decisions based on data that doesn't survive a restart

### 3. Resumability Verification

The crawler must be able to stop and resume without losing meaningful progress. Verify:
* Can a crawl be interrupted and restarted, continuing from where it left off?
* Is per-domain progress tracked persistently, or does it reset each run?
* Are partially-crawled domains distinguishable from never-crawled domains?

### 4. Crawl Effectiveness

Before adding new features, evaluate whether the current crawl strategy is efficient:
* Are we wasting budget on unproductive domains (0 images, persistent errors)?
* Are we re-crawling already-exhausted domains with no new content?
* Do we have enough metadata to make intelligent scheduling decisions?
* Is there a feedback loop from crawl results to future prioritization?

### 5. Architectural Proposals

When structural gaps are identified, agents should:
* Write a design proposal document for human review **before** implementing structural changes
* Reference specific `SYSTEM_DESIGN.md` sections that the proposal addresses
* Include migration strategy with risk assessment per phase
* Identify impact on existing tests and data

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
* Architectural changes to scheduling, state management, or domain lifecycle

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
* **DOMAIN_TRACKING_DESIGN.md** – proposal for persistent domain state, smart scheduling, and resume support

---

**Last Updated**: 2026-02-06  
**Status**: Phase 2 (Redis scheduling); domain tracking proposal under review  
**Maintainer Contact**: TBD
