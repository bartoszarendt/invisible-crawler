# Project Overview

InvisibleCrawler is a self-hosted, large-scale web crawler for discovering and indexing image
assets across the public web. It supports the OneMark/InvisibleID ecosystem by fetching images,
generating stable fingerprints (binary and perceptual hashes), and storing provenance metadata.
Current status: Phase 2 + Domain Tracking Phases A-C implemented and hardened; Phase D
(refresh mode) pending.

## Prime Directive

You are not a task executor. You are a proactive engineering partner.

If a request risks architectural drift, hidden/ephemeral state, loss of resumability, security
or compliance violations, or ambiguous intent, you must surface the concern and propose a safer
plan before coding.

## Agent Workflow (Mandatory: Read Before Act)

1. Read this `AGENTS.md` first.
2. Read `IMPLEMENTATION.md` and `DEPLOYMENT.md` for current runtime behavior and ops workflow.
3. Locate canonical commands in repo files before inventing alternatives.
4. Record assumptions explicitly in an Assumptions Ledger entry when context is missing.
5. If change affects persistence, scheduling, APIs/CLI contracts, or security/compliance:
   produce a short Design Note before implementation.
6. Implement only after risks, migration, and tests are explicit.
7. Validate with lint/type-check/tests relevant to the change.
8. Report findings, risks, and any follow-up TODOs.

## Repository Structure

- `SYSTEM_DESIGN.md` - architectural blueprint covering goals, design principles, data flow, and
  evolution path
- `IMPLEMENTATION.md` - implementation status, runbook, and rollout guidance for Phase 2 +
  domain tracking (Phases A-C)
- `DOMAIN_TRACKING_DESIGN.md` - design and migration reference for domain-centric crawl tracking
- `DEPLOYMENT.md` - Docker Compose deployment architecture and VPS runbook
- `crawler/` - Scrapy-based crawling engine with spiders and pipelines
- `processor/` - image fetching, normalization, and fingerprinting logic
- `storage/` - database schemas and Alembic migrations for PostgreSQL
- `config/` - seed domain lists, allowlists, and blocklists
- `tests/` - unit and integration test suites (250 collected tests; DB-backed tests require
  `DATABASE_URL`)

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

- Formatting: Black (line length 100), isort for imports
- Linting: Ruff with strict settings; mypy in `--strict` mode
- Naming: snake_case for functions/variables, PascalCase for classes
- Commit messages: Conventional Commits format (`feat:`, `fix:`, `docs:`, etc.)
- Docstrings: Google-style for all public functions and classes
- Error handling: explicit exception types; no bare `except:` clauses
- Logging: structured logging with context (JSON when in production)

## Architecture Notes

The crawler pipeline is:

1. Seed Sources (Tranco, Majestic Million, curated lists)
2. URL Frontier (Redis-backed scheduler: dedup, prioritization, rate limits)
3. Scrapy Spiders (HTML parsing: `<img>`, `<picture>`, `og:image`)
4. Image Fetcher (content-type filtering, size thresholds, binary fetch)
5. Normalization (strip EXIF, canonical format, colorspace standardization)
6. Fingerprinting (SHA-256 + pHash/dHash)
7. Storage Layer (PostgreSQL metadata + S3-compatible object store)

Key constraints:

- Domain is the scheduling and progress unit.
- Resumability is required for long-running campaigns.
- InvisibleID detection integration is intentionally separated from Phase 1/2 crawling concerns.

For detailed design and invariants, use `SYSTEM_DESIGN.md` and `DOMAIN_TRACKING_DESIGN.md`.

## Testing Strategy

- Unit tests: pytest for fetcher, fingerprinter, spider parsing, scheduling logic
- Integration tests: `pytest-httpserver` for mock HTTP endpoints
- Test count: 250 collected tests
- DB-backed tests require `DATABASE_URL` and migrated schema
- Coverage target: >=80% for core logic
- CI expectation: lint + type-check + tests before merge/deploy

## Security & Compliance

- Secrets via environment variables only; never commit credentials
- Dependency scanning via Dependabot or equivalent
- Conservative rate limits and exponential backoff on 429/503
- `robots.txt` obeyed by default unless explicitly reviewed
- Crawler identity must include contact in `CRAWLER_USER_AGENT`
- Provenance metadata retained; binary retention policy TBD
- License policy is TBD

## Operational Context

- Runtime characteristics: long-running crawler campaigns (days/weeks), with frequent stop/resume.
- Scale expectations: millions of domains over time; queues and state must survive restarts.
- Resource constraints: crawl budget, CPU/memory, Redis/Postgres capacity, and operator attention.
- Failure modes: worker kill/restart, stale claims, queue stalls, and partial progress loss if state
  is not persisted frequently enough.
- Recovery paths: claim expiry/release flows, stale-run cleanup, checkpoint resume, queue namespace
  isolation for major scheduler changes.
- Unit of work: domain. Page/image events are recorded, but scheduling and progress decisions must
  be domain-centric with persistent, queryable state.

## Assumptions Ledger

When assumptions are needed, record them explicitly in this format:

- Assumption:
- Why it matters:
- How to verify in repo:
- Risk if wrong (low/medium/high):
- Action:

Escalate to human review immediately when assumption risk is medium/high and affects persistence,
scheduling, API/CLI behavior, or security/compliance.

## Proactive Architecture Review

When any trigger fires, the agent must output this structure before implementation:

- Findings:
- Risk level: low/medium/high
- Recommendation: implement / propose / escalate
- Design Note (required if recommendation is `propose`):
  - Scope
  - Migration/rollback
  - Tests/validation

Review triggers:

1. Design-implementation mismatch against `SYSTEM_DESIGN.md`
2. In-memory state that affects behavior without durable persistence
3. Non-resumable behavior in long-running workflows
4. Increasing coupling, duplication, or contradictory source-of-truth docs
5. Scheduling/retry/backoff/claim protocol changes
6. Cost/performance growth uncertainty (queue growth, DB pressure, worker churn)

Reviews are mandatory:

- At phase boundaries
- Before major structural changes
- When asked to evaluate architecture
- When recurring bugs indicate systemic design issues

## Agent Guardrails

Defensive rules:

- Never modify `SYSTEM_DESIGN.md` without explicit human approval.
- Database migration files are immutable post-deployment.
- Crawl politeness setting changes require human review.
- New external dependencies require security and license review.
- Fingerprinting algorithm changes require reproducibility review.
- Max 3 retry attempts for failed file edits before escalating.
- Crawl experiments limited to max 100 URLs without explicit approval.
- No automated deployment without passing full test suite.

Proactive rules:

- Flag design/implementation misalignment when discovered.
- Flag in-memory-only state that should be persisted.
- Flag missing recovery paths for restart/claim/queue failure scenarios.
- Propose design doc updates before structural scheduler/state changes.
- Verify behavior-impacting changes with tests and operational checks.

Stop-the-line escalation rules (mandatory):

- Introducing or modifying persistent state semantics
- Changing scheduling/retry/backoff/claim semantics
- Altering public APIs or CLI contracts
- Adding external integrations
- Changes with significant cost-profile impact
- Ambiguous requirements that can cause irreversible data or state effects

Definition of Done:

1. Relevant tests added/updated and passing.
2. Lint/type-check passing, or explicit documented reason if not run.
3. Migration and rollback impact considered for stateful changes.
4. Operational observability/commands updated when runtime behavior changes.
5. Docs updated (`IMPLEMENTATION.md`, `DEPLOYMENT.md`, or design refs) when behavior changes.

> TODO: define canonical curated seed file path for "never modify" guardrail. Current historical
> reference `config/seed_domains.txt` does not exist in this repo snapshot.

## Extensibility Hooks

- Custom spider middleware for domain-specific parsing
- Pluggable fingerprinting algorithm registry
- Storage backend abstraction for PostgreSQL/object storage swaps
- Seed providers from external APIs
- Post-processing pipeline hooks for analyzers

Environment variables currently used:

- `APP_ENV`: Deployment environment label (`dev`, `staging`, `prod`)
- `CRAWL_PROFILE`: Crawl tuning profile (`conservative`, `broad`)
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `QUEUE_NAMESPACE`: Redis namespace prefix for queue/version isolation
- `CRAWLER_USER_AGENT`: User-Agent string for image fetching
- `CRAWLER_MAX_PAGES`: Global/default page cap (`<=0` means uncapped)
- `DISCOVERY_REFRESH_AFTER_DAYS`: Re-fetch images older than N days (default: 0 = disabled)
- `ENABLE_DOMAIN_TRACKING`: Enable persistent domain tracking (Phase A)
- `DOMAIN_CANONICALIZATION_STRIP_SUBDOMAINS`: Collapse to registrable domain when enabled
- `ENABLE_PER_DOMAIN_BUDGET`: Enable per-domain crawl budgets + checkpointing (Phase B)
- `MAX_PAGES_PER_RUN`: Per-domain page budget when Phase B is enabled
- `ENABLE_SMART_SCHEDULING`: Enable DB-driven candidate selection (Phase C)
- `ENABLE_CLAIM_PROTOCOL`: Enable claim/lease concurrency protocol (Phase C)
- `DOMAIN_STATS_FLUSH_INTERVAL_PAGES`: Mid-crawl flush interval for domain stats
- `ENABLE_CONTINUOUS_MODE`: Keep Phase C workers alive and refill claims on idle
- `ENABLE_PERSISTENT_DUPEFILTER`: Persist URL dedup fingerprints in Redis
- `ENABLE_IMMUTABLE_ASSETS`: Enable `image_assets`/`image_observations`/
  `invisibleid_detections` schema path

## Further Reading

- `IMPLEMENTATION.md` - current implementation state and runbook (current source of runtime truth)
- `DEPLOYMENT.md` - VPS/Docker Compose operations and rollout/rollback procedures
- `DOMAIN_TRACKING_DESIGN.md` - domain tracking design reference and migration rationale
- `SYSTEM_DESIGN.md` - architectural blueprint and long-term design goals (human-controlled)

> TODO: add explicit invariants/ADR index if introduced (for state machine, claim protocol,
> resumability guarantees).

---

**Last Updated**: 2026-02-13  
**Maintainer Contact**: TBD
