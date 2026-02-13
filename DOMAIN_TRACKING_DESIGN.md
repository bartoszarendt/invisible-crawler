# Domain-Centric Crawl Tracking — Design and Migration Reference

**Date:** 2026-02-07  
**Status:** Reference design (Phases A-C implemented; Phase D pending)  
**Author:** Automated design analysis  
**Scope:** Schema additions, spider changes, scheduling logic, resume support, concurrency safety  
**Review Findings:** Critical gaps identified in concurrency, frontier scale, canonicalization, and migration hardening

> Implementation note (2026-02-13): Sections labeled as gaps/proposals are the pre-Phase-A baseline used to drive this design. Current runtime behavior and defaults are documented in `IMPLEMENTATION.md`.

---

## 1. Problem Statement

The crawler currently treats domains as disposable, ephemeral values — they exist only in seed files and in-memory data structures during a single spider run. When the process stops, all domain-level knowledge is lost.

### Specific Gaps

| # | Gap | Impact |
|---|-----|--------|
| 1 | **No domain entity in the database** | Cannot query "which domains have we crawled?" or "which are unreachable?" without scanning `crawl_log`. |
| 2 | **No crawl progress tracking** | If `max_pages=100` and a domain has 50,000 pages, we crawl 100, stop, and re-crawl the same pages next time. No awareness that 49,900 pages remain. |
| 3 | **No domain quality signals** | A domain returning 403 on every request burns the same crawl budget as one yielding thousands of images. |
| 4 | **No stop/resume support** | Restarting the crawler means starting from scratch. In-memory state (`_blocked_domains_runtime`, `_domain_error_counts`) is lost on every restart. |
| 5 | **No per-domain crawl budget** | `max_pages` is global. With 50 seed domains and `max_pages=100`, some domains may consume the entire budget while others get zero pages. |
| 6 | **Seeds have no feedback loop** | Domains are processed in file order (Phase 1) or static rank order (Phase 2). Crawl outcomes don't influence future scheduling. |
| 7 | **Refresh mode has no foundation** | Without domain-level metadata, a refresh crawl cannot know which pages to revisit, which domains are worth refreshing, or how stale a domain's data is. |

### Current In-Memory State (Lost on Restart)

```python
# discovery_spider.py — all lost when process exits
self._blocked_domains_runtime: set[str] = set()     # runtime-blocked domains
self._domain_error_counts: dict[str, int] = {}      # per-domain error counters
self.pages_crawled: int = 0                          # global counter only
```

---

## 2. Proposed Solution: Domain as First-Class Entity

### Core Principle

Make **domain** a persistent, stateful entity in the database. Every crawl run reads domain state, acts on it, and writes updated state back. This enables intelligent scheduling, resume, and refresh.

### Design Constraints

- **Coexistence:** The `domains` table coexists with existing file-based and Redis-based seed modes. It is populated as a side effect of crawling, not a replacement for seed ingestion.
- **Incremental adoption:** Existing seed files and Redis queues continue to work. The `domains` table adds intelligence on top without breaking current flows.
- **Concurrency-safe:** Multiple crawler workers must be able to safely claim and update domain state without conflicts or duplicate work.
- **Frontier persistence:** Resume capability must scale to millions of domains with deep link structures (>1000 URLs per domain).

---

## 3. Schema Design

### 3.1 `domains` Table

```sql
CREATE TYPE domain_status AS ENUM ('pending', 'active', 'exhausted', 'blocked', 'unreachable');

CREATE TABLE IF NOT EXISTS domains (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain VARCHAR(255) NOT NULL UNIQUE,  -- See §3.2 for canonicalization rules

    -- Discovery state
    status domain_status NOT NULL DEFAULT 'pending',
    block_reason TEXT,
    block_reason_code VARCHAR(50),  -- Structured: 'login_required', 'rate_limited', 'dns_failure', etc.
    first_blocked_at TIMESTAMPTZ,
    
    -- Concurrency control (CRITICAL for multi-worker deployments)
    claimed_by VARCHAR(255),  -- Worker ID that claimed this domain
    claim_expires_at TIMESTAMPTZ,  -- Lease expiry; other workers skip if claim is active
    version INTEGER NOT NULL DEFAULT 0,  -- Optimistic locking: increment on every update

    -- Crawl progress (cumulative across all runs)
    pages_discovered BIGINT NOT NULL DEFAULT 0,  -- BIGINT for large domains
    pages_crawled BIGINT NOT NULL DEFAULT 0,
    images_found BIGINT NOT NULL DEFAULT 0,
    images_stored BIGINT NOT NULL DEFAULT 0,

    -- Crawl budget
    max_pages_per_run INTEGER DEFAULT 1000,  -- Explicit default instead of NULL
    crawl_depth_reached INTEGER NOT NULL DEFAULT 0,

    -- Quality signals (updated after each crawl run)
    image_yield_rate DOUBLE PRECISION,  -- Changed from FLOAT for precision
    avg_images_per_page DOUBLE PRECISION,
    error_rate DOUBLE PRECISION,
    total_error_count INTEGER NOT NULL DEFAULT 0,
    consecutive_error_count INTEGER NOT NULL DEFAULT 0,  -- Separate counter for state transitions

    -- Scheduling (priority computed at query time, not stored)
    priority_score INTEGER NOT NULL DEFAULT 0,  -- Cached score; refreshed periodically
    priority_computed_at TIMESTAMPTZ,  -- When score was last computed
    seed_rank INTEGER,
    source VARCHAR(100),  -- Extended to include dataset version (e.g., 'tranco_20260205')

    -- Timestamps
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_crawled_at TIMESTAMPTZ,
    next_crawl_after TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- Track last modification

    -- Resume support (see §3.5 for frontier persistence strategy)
    last_crawl_run_id UUID REFERENCES crawl_runs(id) ON DELETE SET NULL,
    frontier_checkpoint_id VARCHAR(100)  -- Reference to external frontier store (not inline JSONB)
);

-- Indexes for scheduling queries
CREATE INDEX IF NOT EXISTS idx_domains_status ON domains(status);
CREATE INDEX IF NOT EXISTS idx_domains_priority_score ON domains(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_domains_next_crawl ON domains(next_crawl_after) WHERE next_crawl_after IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_domains_yield ON domains(image_yield_rate DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_domains_last_crawled ON domains(last_crawled_at);
CREATE INDEX IF NOT EXISTS idx_domains_source ON domains(source);

-- Composite indexes for specific queries
CREATE INDEX IF NOT EXISTS idx_domains_discovery_candidates 
    ON domains(status, next_crawl_after, priority_score DESC) 
    WHERE status IN ('pending', 'active');

CREATE INDEX IF NOT EXISTS idx_domains_refresh_candidates 
    ON domains(status, next_crawl_after, image_yield_rate DESC) 
    WHERE status = 'exhausted';

-- Concurrency indexes
CREATE INDEX IF NOT EXISTS idx_domains_claims ON domains(claimed_by, claim_expires_at) 
    WHERE claimed_by IS NOT NULL;

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_domains_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER domains_updated_at_trigger
    BEFORE UPDATE ON domains
    FOR EACH ROW
    EXECUTE FUNCTION update_domains_updated_at();
```

### 3.2 Domain Canonicalization Policy

**Critical:** Domain identity must be canonical and consistent. Current implementation uses raw `urlparse(url).netloc`, which allows duplicates and inconsistencies.

#### Canonicalization Rules

| Component | Rule | Example |
|-----------|------|--------|
| **Scheme** | Strip (not part of domain identity) | `https://example.com` → `example.com` |
| **www prefix** | Strip (treat www/non-www as same domain) | `www.example.com` → `example.com` |
| **Port** | Strip if default (80/443) | `example.com:443` → `example.com` |
| **Case** | Lowercase | `Example.COM` → `example.com` |
| **Trailing dot** | Strip | `example.com.` → `example.com` |
| **IDN/Punycode** | Store as punycode | `münchen.de` → `xn--mnchen-3ya.de` |
| **Subdomain handling** | Use registrable domain (public suffix + 1) | `blog.example.com` → `example.com` (configurable) |

#### Implementation

```python
from urllib.parse import urlparse
import idna
from publicsuffix2 import get_sld  # Or tldextract

def canonicalize_domain(url: str, strip_subdomains: bool = False) -> str:
    """Canonicalize domain from URL.
    
    Args:
        url: Full URL or domain
        strip_subdomains: If True, reduce to registrable domain (example.com)
                         If False, keep full subdomain (blog.example.com)
    
    Returns:
        Canonical domain string
    """
    parsed = urlparse(url if '://' in url else f'https://{url}')
    domain = parsed.netloc.lower()
    
    # Strip port
    if ':' in domain:
        domain = domain.split(':')[0]
    
    # Strip trailing dot
    domain = domain.rstrip('.')
    
    # Strip www
    if domain.startswith('www.'):
        domain = domain[4:]
    
    # Handle IDN
    try:
        domain = idna.encode(domain).decode('ascii')
    except (idna.IDNAError, UnicodeError):
        pass  # Keep as-is if encoding fails
    
    # Optionally reduce to registrable domain
    if strip_subdomains:
        try:
            domain = get_sld(domain) or domain
        except Exception:
            pass  # Keep full domain if reduction fails
    
    return domain
```

**Decision Point:** Should `blog.example.com` and `shop.example.com` be separate domains or collapsed to `example.com`?
- **Separate (default):** Better granularity, respects different subdomain content/hosting
- **Collapsed:** Simpler, avoids duplicate work if subdomains share robots.txt/rate limits

**Recommendation:** Start with separate subdomains (full netloc); add `parent_domain` column for future aggregation.

### 3.3 Column Semantics

#### Status Values

| Status | Meaning | How Set | Next Action |
|--------|---------|---------|-------------|
| `pending` | Known domain, never crawled | On seed ingestion or first URL encounter | Schedule for discovery crawl |
| `active` | Partially crawled, more pages exist | When crawl stops before exhausting all discovered links | Resume crawling (next run) |
| `exhausted` | All discovered pages have been crawled | When `pages_crawled >= pages_discovered` | Move to refresh schedule |
| `blocked` | Persistent errors or access denied | After `max_domain_errors` consecutive errors, login wall detection | Skip; retry after cooldown (`next_crawl_after`) |
| `unreachable` | DNS failure, connection refused, timeout | After repeated connection-level failures | Skip; retry after longer cooldown |

#### Quality Signals

| Column | Formula | Purpose |
|--------|---------|---------|
| `image_yield_rate` | `images_stored / pages_crawled` | Prioritize image-dense domains |
| `avg_images_per_page` | Running average of images found per page | Estimate remaining image yield |
| `error_rate` | `total_error_count / pages_crawled` | Deprioritize unreliable domains |
| `consecutive_error_count` | Reset to 0 on success; increment on failure | Trigger blocked/unreachable transitions |

#### Concurrency Fields (Critical)

| Column | Purpose |
|--------|---------|
| `claimed_by` | Worker/process ID that holds the domain lease |
| `claim_expires_at` | Lease expiry time; domain becomes available after this |
| `version` | Optimistic lock version; incremented on every update |

**Claim protocol:** See §7 for full concurrency protocol.

### 3.4 Relationship to Existing Tables

```
domains (NEW)
    ├── 1:N → crawl_log (via domain column match)
    ├── 1:N → provenance (via source_domain column match)
    ├── 1:1 → crawl_runs (via last_crawl_run_id FK)
    └── (conceptual) → images (via provenance join)
```

The `domains` table does not require foreign keys to/from `crawl_log` or `provenance` — it uses the existing `domain` VARCHAR columns for loose coupling. This avoids schema changes to existing tables and allows the `domains` table to be adopted incrementally.

**Phase A:** Loose coupling only (no schema changes to existing tables).

**Phase C+:** Add `domain_id UUID REFERENCES domains(id)` to `crawl_log` and `provenance` for:
- Integrity enforcement
- Efficient joins (no string matching on VARCHAR columns)
- Index-only scans

### 3.5 Frontier Persistence Strategy

**Problem:** Storing frontier_snapshot as inline JSONB does not scale:
- 1,000 URLs × ~100 bytes = ~100-150 KB per domain
- At 1M domains, worst-case is 120-150 GB
- JSONB updates rewrite entire value under MVCC (bloat + I/O amplification)
- Not mergeable under concurrent workers

**Recommended Approach: External Frontier Store**

Replace `frontier_snapshot JSONB` with `frontier_checkpoint_id VARCHAR(100)` referencing:

#### Option A: Redis Durable Frontier (Recommended)

```python
# Store uncrawled URLs in Redis sorted set per domain
key = f"frontier:{domain}:{run_id}"
redis.zadd(key, {url: depth for url, depth in frontier})

# Checkpoint reference in DB
checkpoint_id = f"{domain}:{run_id}"
```

**Pros:**
- Handles millions of URLs per domain
- Fast push/pop operations
- Redis persistence (AOF/RDB) provides durability
- No PostgreSQL bloat
- Concurrent workers can atomically pop from shared frontier

**Cons:**
- Adds Redis as critical dependency for resume
- Requires Redis persistence configuration

#### Option B: Separate `domain_frontier` Table

```sql
CREATE TABLE domain_frontier (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    domain_id UUID NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(domain_id, run_id, url)
);

CREATE INDEX idx_frontier_domain_run ON domain_frontier(domain_id, run_id, priority DESC);
```

**Pros:**
- No external dependencies
- Transactional consistency with domain state
- Queryable (can introspect frontier)

**Cons:**
- PostgreSQL table bloat at scale
- Slower than Redis for queue operations
- Vacuum overhead

#### Option C: Hybrid (Top-N Checkpoint + Redis Deep Frontier)

- Store top 100 URLs in `domains` table as lightweight JSONB checkpoint (fast resume path)
- Store full deep frontier in Redis (handles domains with >100 uncrawled URLs)
- Spider checks in-row checkpoint first, falls back to Redis if needed

**Recommendation:** Option A (Redis durable frontier) for Phase C. Option B (separate table) if Redis dependency is unacceptable. Option C as fallback hybrid.

**DB Schema Change:**

```sql
-- Replace:
-- frontier_snapshot JSONB

-- With:
frontier_checkpoint_id VARCHAR(100),  -- Format: "{domain}:{run_id}" or "redis:{key}"
frontier_size INTEGER DEFAULT 0,  -- Number of URLs in frontier (observability)
frontier_updated_at TIMESTAMPTZ  -- Last frontier update
```

---

## 4. Domain Lifecycle

### 4.1 State Machine

```
                    ┌─────────────────────────────┐
                    │                             │
                    ▼                             │
  seed file ──→ [pending] ──crawl──→ [active] ───┤
  Redis              │                   │        │
  CLI ingest         │                   │        │ budget exhausted
                     │                   │        │ or max_pages hit
                     │                   ▼        │
                     │             [exhausted] ◄──┘
                     │                   │
                     │          refresh schedule
                     │                   │
                     │                   ▼
                     │             [active] (refresh)
                     │                   │
                     ▼                   ▼
               [blocked] ◄── errors ── [active]
                     │
            cooldown expires
                     │
                     ▼
               [pending] (retry)
                     
               [unreachable] ◄── DNS/connection failure
                     │
            long cooldown expires
                     │
                     ▼
               [pending] (retry)
```

### 4.2 Lifecycle Events

| Event | Trigger | State Change | Data Updated |
|-------|---------|-------------|-------------|
| **Seed ingested** | CLI, file read, or first URL encounter | `→ pending` | `source`, `seed_rank`, `priority_score` |
| **Crawl starts** | Spider begins processing domain | `pending → active` | `last_crawled_at`, `last_crawl_run_id` |
| **Page crawled** | Spider fetches a page | (stays `active`) | `pages_crawled++`, link discovery updates `pages_discovered` |
| **Image stored** | Pipeline stores image | (stays `active`) | `images_stored++` |
| **Budget hit** | Per-domain page limit reached | (stays `active`) | `frontier_checkpoint_id` saved |
| **All pages done** | `pages_crawled >= pages_discovered` | `active → exhausted` | `next_crawl_after` set |
| **Errors exceed threshold** | N consecutive 403/429/503 | `active → blocked` | `block_reason`, `next_crawl_after` set |
| **Connection fails** | DNS/TCP errors | `active → unreachable` | `block_reason`, `next_crawl_after` set |
| **Cooldown expires** | `next_crawl_after < NOW()` | `blocked → pending` | `consecutive_error_count` reset |
| **Refresh due** | `next_crawl_after < NOW()` | `exhausted → pending` | priority recalculated |

---

## 5. Spider Integration

### 5.1 `start_requests` Changes

Current behavior: read all seeds from file, yield one `Request` per seed, process in Scrapy's default order.

Proposed behavior:

```python
def start_requests(self):
    # 1. Upsert domains from seed source (file / Redis / CLI)
    #    This ensures every seed has a domains row
    seed_domains = self._load_seeds()  # existing logic
    self._upsert_domains(seed_domains)

    # 2. Query domains table for crawl candidates
    candidates = self._get_crawl_candidates()
    
    # 3. For each candidate, check for frontier checkpoint (resume)
    for domain_row in candidates:
        if domain_row.frontier_checkpoint_id:
            # Resume: load frontier from Redis/storage and yield URLs
            frontier_urls = self._load_frontier_checkpoint(domain_row.frontier_checkpoint_id)
            for entry in frontier_urls:
                yield Request(
                    url=entry['url'],
                    meta={'depth': entry['depth'], 'domain': domain_row.domain},
                    callback=self.parse,
                )
        else:
            # Fresh start: yield root URL
            yield Request(
                url=f"https://{domain_row.domain}",
                meta={'depth': 0, 'domain': domain_row.domain},
                callback=self.parse,
            )
        
        # Mark domain as active
        self._update_domain_status(domain_row.domain, 'active')
```

### 5.2 Per-Domain Page Counting

Replace the global `self.pages_crawled` counter with a per-domain dict:

```python
def __init__(self, ...):
    ...
    self._domain_pages: dict[str, int] = {}   # domain → pages crawled this run
    self._domain_budget: dict[str, int] = {}  # domain → max pages this run

def parse(self, response):
    domain = response.meta['domain']
    self._domain_pages[domain] = self._domain_pages.get(domain, 0) + 1
    
    # Check per-domain budget
    budget = self._domain_budget.get(domain, self.max_pages)
    if self._domain_pages[domain] >= budget:
        self.logger.info(f"Domain budget reached for {domain}: {budget} pages")
        checkpoint_id = self._save_frontier_checkpoint(domain, pending_urls)
        self._update_domain_frontier_checkpoint(domain, checkpoint_id)
        self._update_domain_status(domain, 'active')
        return  # Stop yielding links for this domain
    
    # ... existing parse logic (extract images, follow links) ...
```

### 5.3 `closed` Handler Changes

```python
def closed(self, reason):
    # Update each domain's state
    for domain, pages in self._domain_pages.items():
        pages_discovered = self._domain_links_found.get(domain, 0)
        images_found = self._domain_images_found.get(domain, 0)
        images_stored = self._domain_images_stored.get(domain, 0)
        errors = self._domain_error_counts.get(domain, 0)
        
        # Determine final status
        if domain in self._blocked_domains_runtime:
            status = 'blocked'
        elif pages >= pages_discovered and pages_discovered > 0:
            status = 'exhausted'
        else:
            status = 'active'
        
        # Save frontier checkpoint if domain still active
        checkpoint_id = None
        if status == 'active' and domain in self._pending_urls:
            checkpoint_id = self._save_frontier_checkpoint(domain, self._pending_urls[domain])
        
        self._update_domain_after_run(
            domain=domain,
            pages_crawled_delta=pages,
            pages_discovered_delta=pages_discovered,
            images_found_delta=images_found,
            images_stored_delta=images_stored,
            total_error_count_delta=errors,
            consecutive_error_count=errors if domain in self._blocked_domains_runtime else 0,
            status=status,
            crawl_run_id=self.crawl_run_id,
            frontier_checkpoint_id=checkpoint_id,
        )
    
    # ... existing crawl_runs update ...
```

---

## 6. Scheduling Intelligence

### 6.1 Candidate Selection Query

Replace "read all seeds from file" with a database query:

```sql
-- Discovery candidates: domains that need crawling
SELECT * FROM domains
WHERE status IN ('pending', 'active')
  AND (next_crawl_after IS NULL OR next_crawl_after < NOW())
ORDER BY
    -- Active (partially crawled) domains first — they're the best resume targets
    CASE WHEN status = 'active' THEN 0 ELSE 1 END,
    -- Then by dynamic priority score
    priority_score DESC,
    -- Break ties with least-recently-crawled
    last_crawled_at ASC NULLS FIRST
LIMIT :batch_size;
```

```sql
-- Refresh candidates: exhausted domains due for revisit
SELECT * FROM domains
WHERE status = 'exhausted'
  AND next_crawl_after < NOW()
ORDER BY
    image_yield_rate DESC NULLS LAST,
    last_crawled_at ASC
LIMIT :batch_size;
```

### 6.2 Dynamic Priority Calculation

Run after each crawl completes (or as a periodic maintenance task):

```sql
UPDATE domains SET
    image_yield_rate = CASE 
        WHEN pages_crawled > 0 THEN images_stored::DOUBLE PRECISION / pages_crawled 
        ELSE NULL 
    END,
    avg_images_per_page = CASE 
        WHEN pages_crawled > 0 THEN images_found::DOUBLE PRECISION / pages_crawled 
        ELSE NULL 
    END,
    error_rate = CASE 
        WHEN pages_crawled > 0 THEN total_error_count::DOUBLE PRECISION / pages_crawled 
        ELSE NULL 
    END,
    priority_score = COALESCE(seed_rank, 0)
        + COALESCE(image_yield_rate * 1000, 0)::INTEGER
        + LEAST(pages_discovered - pages_crawled, 500) * 2
        - COALESCE(error_rate * 500, 0)::INTEGER
        + (EXTRACT(EPOCH FROM (NOW() - COALESCE(last_crawled_at, '2000-01-01'))) / 86400 * 5)::INTEGER,
    priority_computed_at = CURRENT_TIMESTAMP
WHERE status NOT IN ('blocked', 'unreachable');
```

**Priority factors:**

| Factor | Weight | Rationale |
|--------|--------|-----------|
| `seed_rank` | 1x base | Tranco/Majestic rank provides initial ordering |
| `image_yield_rate` | 1000x | A domain yielding 2 images/page scores +2000 over a barren domain |
| `pages_remaining` | 2x (capped at 500) | Incomplete domains are more valuable than fully exhausted ones |
| `error_rate` | -500x | A domain with 50% error rate loses 250 points |
| `staleness (days)` | 5x per day | Domains not crawled in 30 days get +150 |

### 6.3 Cooldown Periods

When a domain is blocked or unreachable, set `next_crawl_after` to a future time:

| Condition | Cooldown | Rationale |
|-----------|----------|-----------|
| `blocked` (login wall) | 30 days | Unlikely to change quickly |
| `blocked` (rate limited, 429) | 7 days | Respect the site's limits |
| `blocked` (403 forbidden) | 14 days | May be temporary IP block |
| `unreachable` (DNS/connection) | 7 days | Could be temporary outage |
| `exhausted` (refresh schedule) | Configurable (default: 14 days) | Balance freshness vs. load |

---

## 7. Concurrency and Distributed Crawling

### 7.1 Domain Claim Protocol (CRITICAL)

**Problem:** Without lease/claim mechanism, multiple workers will crawl same domain, causing:
- Duplicate work and wasted resources
- Rate limit violations
- Conflicting state updates (last-writer-wins)

**Solution: Atomic Domain Claiming with Lease Expiry**

#### Claim Acquisition Query

```sql
-- Worker attempts to claim unclaimed or expired domains
WITH candidates AS (
    SELECT id, version
    FROM domains
    WHERE status IN ('pending', 'active')
      AND (next_crawl_after IS NULL OR next_crawl_after < CURRENT_TIMESTAMP)
      AND (claimed_by IS NULL OR claim_expires_at < CURRENT_TIMESTAMP)
    ORDER BY priority_score DESC, last_crawled_at ASC NULLS FIRST
    LIMIT 10  -- Batch size
    FOR UPDATE SKIP LOCKED  -- Skip domains locked by other transactions
)
UPDATE domains
SET claimed_by = %(worker_id)s,
    claim_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 minutes',
    version = version + 1
FROM candidates
WHERE domains.id = candidates.id
  AND domains.version = candidates.version  -- Optimistic lock check
RETURNING domains.*;
```

**Key mechanisms:**
- `FOR UPDATE SKIP LOCKED`: Skip rows locked by concurrent transactions (non-blocking)
- `version` check: Detects if domain was updated between SELECT and UPDATE
- `claim_expires_at`: Lease timeout (default 30 minutes); auto-releases stuck claims

#### Claim Renewal (Heartbeat)

Worker should periodically renew lease if crawl is still active:

```sql
UPDATE domains
SET claim_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 minutes',
    version = version + 1
WHERE id = %(domain_id)s
  AND claimed_by = %(worker_id)s
  AND claim_expires_at > CURRENT_TIMESTAMP;  -- Only renew if not expired
```

**Renewal cadence:** Every 10 minutes (1/3 of lease duration).

#### Claim Release

Worker releases domain at end of crawl:

```python
# In Python/application code
with db.cursor() as cur:
    cur.execute("""
        UPDATE domains
        SET claimed_by = NULL,
            claim_expires_at = NULL,
            status = %(new_status)s,
            pages_crawled = pages_crawled + %(pages_crawled_delta)s,
            images_stored = images_stored + %(images_stored_delta)s,
            version = version + 1
        WHERE id = %(domain_id)s
          AND claimed_by = %(worker_id)s
          AND version = %(expected_version)s
        RETURNING id
    """, params)
    
    if cur.rowcount == 0:
        raise Exception('Domain claim lost or version mismatch')
```

### 7.2 State Transition Function (Enforced Transitions)

To prevent invalid state transitions under concurrency, enforce transitions via function:

```sql
CREATE OR REPLACE FUNCTION transition_domain_status(
    p_domain_id UUID,
    p_from_status domain_status,
    p_to_status domain_status,
    p_worker_id VARCHAR(255),
    p_expected_version INTEGER
)
RETURNS BOOLEAN AS $$
DECLARE
    v_row_count INTEGER;
BEGIN
    -- Valid transitions matrix
    IF NOT (
        (p_from_status = 'pending' AND p_to_status IN ('active', 'unreachable')) OR
        (p_from_status = 'active' AND p_to_status IN ('active', 'exhausted', 'blocked', 'unreachable')) OR
        (p_from_status = 'exhausted' AND p_to_status IN ('pending', 'active')) OR
        (p_from_status = 'blocked' AND p_to_status IN ('pending', 'active')) OR
        (p_from_status = 'unreachable' AND p_to_status IN ('pending', 'active'))
    ) THEN
        RAISE EXCEPTION 'Invalid transition: % -> %', p_from_status, p_to_status;
    END IF;

    -- Attempt transition with optimistic lock
    UPDATE domains
    SET status = p_to_status,
        version = version + 1,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = p_domain_id
      AND status = p_from_status
      AND claimed_by = p_worker_id
      AND version = p_expected_version;
    
    GET DIAGNOSTICS v_row_count = ROW_COUNT;
    RETURN v_row_count > 0;
END;
$$ LANGUAGE plpgsql;
```

### 7.3 Failure Scenarios

| Scenario | Protection Mechanism | Recovery |
|----------|---------------------|----------|
| Worker crashes mid-crawl | Lease expires after 30 min; domain becomes available | Next worker claims expired domain and resumes |
| Two workers claim same domain | `FOR UPDATE SKIP LOCKED` serializes claims | Only one succeeds; other skips to next candidate |
| Worker loses DB connection | Optimistic version check fails on reconnect | Worker aborts crawl; domain released by lease expiry |
| Stale priority_score | Cached score refreshed periodically; not used for claim locking | Acceptable drift; scoring is advisory only (see §6.2) |
| Version conflict on finalize | `WHERE version = expected_version` fails | Worker retries with fresh read or aborts gracefully |

### 7.4 Observability

Add metrics/queries for monitoring:

```sql
-- Active claims by worker
SELECT claimed_by, COUNT(*), 
       MIN(claim_expires_at) AS earliest_expiry
FROM domains
WHERE claimed_by IS NOT NULL
  AND claim_expires_at > CURRENT_TIMESTAMP
GROUP BY claimed_by;

-- Expired/stuck claims
SELECT COUNT(*)
FROM domains
WHERE claimed_by IS NOT NULL
  AND claim_expires_at < CURRENT_TIMESTAMP;

-- Domains waiting for claims
SELECT status, COUNT(*)
FROM domains
WHERE status IN ('pending', 'active')
  AND (claimed_by IS NULL OR claim_expires_at < CURRENT_TIMESTAMP)
GROUP BY status;
```

## 8. Stop/Resume Flow

### 8.1 Graceful Stop (Ctrl+C / SIGINT)

Scrapy already handles `SIGINT` by calling `spider.closed(reason='shutdown')`. The proposed `closed()` handler (§5.3) persists domain state, so resume is automatic.

```
Run 1: Seeds A, B, C, D
  - Domain A: crawled 100/500 pages → status=active, frontier saved
  - Domain B: crawled 50/50 pages → status=exhausted
  - Domain C: all 403 → status=blocked, next_crawl_after=+14d
  - Domain D: not started (Ctrl+C) → status=pending

Run 2 (resume):
  - Domain A: resumes from frontier checkpoint (page 101+)
  - Domain B: skipped (exhausted, not due for refresh)
  - Domain C: skipped (blocked, cooldown active)
  - Domain D: starts fresh from root URL
```

### 8.2 Crash Recovery

If the process crashes without calling `closed()`, domain state from the *previous* successful run is still in the database. The current run's progress is lost, but the worst case is re-crawling pages from the current run — not from scratch.

**Improvement:** The spider could periodically flush domain state mid-crawl (e.g., every 100 pages per domain). This makes crash recovery more granular at the cost of more DB writes.

### 8.3 Rerun Scenarios

| Scenario | What Happens |
|----------|-------------|
| **Same seeds, same `max_pages`** | Only `pending` and `active` domains crawled. Exhausted domains skipped. Blocked domains skipped until cooldown expires. |
| **Same seeds, higher `max_pages`** | `active` domains resume and get more budget. `exhausted` domains remain skipped (already done). |
| **New seeds added** | New domains enter as `pending`, get crawled. Existing domains retain their state. |
| **Force re-crawl of exhausted domain** | Admin sets `status='pending'` via CLI or direct SQL. |

---

## 9. Interaction with Existing Seed Modes

Since the design coexists with file-based and Redis-based seeds, the integration point is at `start_requests`:

```
File Seeds (config/test_seeds.txt)
        │
        ▼
   ┌─────────────────┐
   │  Upsert into    │ ← Ensures every seed has a domains row
   │  domains table  │    (INSERT ... ON CONFLICT DO NOTHING)
   └────────┬────────┘
            │
Redis Seeds (start_urls sorted set)
        │
        ▼
   ┌─────────────────┐
   │  Upsert into    │
   │  domains table  │
   └────────┬────────┘
            │
            ▼
   ┌─────────────────┐
   │  Query domains  │ ← Source of truth for scheduling
   │  WHERE status   │
   │  IN (pending,   │
   │      active)    │
   │  ORDER BY       │
   │  priority_score │
   │  DESC           │
   └────────┬────────┘
            │
            ▼
     Spider crawls
```

**Key point:** Seeds flow *into* the `domains` table, but the `domains` table (not the seed file) determines what actually gets crawled and in what order. This means:
- Adding a domain to the seed file that was already `blocked` does NOT re-crawl it (until cooldown)
- A domain marked `exhausted` is not re-crawled even if present in seeds
- To force re-crawl: `UPDATE domains SET status='pending' WHERE domain='example.com'`

---

## 10. CLI Extensions

### 10.1 New Commands

```bash
# List domains and their status
python -m crawler.cli domain-status [--status active|blocked|exhausted] [--limit 50]

# Show detailed info for a specific domain
python -m crawler.cli domain-info example.com

# Force re-crawl a domain
python -m crawler.cli domain-reset example.com [--reason "manual review"]

# Recalculate priorities for all domains
python -m crawler.cli recalculate-priorities

# Show domains ranked by image yield
python -m crawler.cli top-domains [--limit 20]
```

### 10.2 Example Output: `domain-status`

```
DOMAIN                          STATUS      PAGES    IMAGES   YIELD   LAST CRAWLED
───────────────────────────────────────────────────────────────────────────────────
unsplash.com                    active      450/2100 3200     7.1     2026-02-05 14:30
flickr.com                      active      200/8500 1800     9.0     2026-02-05 14:25  
wikipedia.org                   exhausted   50/50    12       0.24    2026-02-04 10:00
example-login.com               blocked     3/3      0        0.0     2026-02-05 14:31
  └─ reason: login_required
dead-domain.invalid             unreachable 0/0      0        -       2026-02-05 14:28
  └─ reason: DNS resolution failed
```

---

## 11. Migration Strategy (Revised with Hardening)

### Overview

Phased migration with explicit rollback and mixed-version support. Each phase includes:
- Feature flag for enable/disable without code redeployment
- Backfill strategy for historical data
- Compatibility testing with prior phase workers
- Rollback runbook

### Phase A: Schema + Passive Tracking (Low Risk)

**Goal:** Establish `domains` table and start accumulating data without behavior changes.

**Steps:**
1. Deploy Alembic migration adding `domains` table with concurrency fields
2. Add canonicalization utility function to codebase
3. Spider upserts domain rows during `start_requests` using canonical names
4. Spider updates domain stats in `closed()` (best-effort; no errors on failure)
5. **Feature flag:** `ENABLE_DOMAIN_TRACKING=true` (default: true)

**Backfill (Historical Data):**
```sql
-- Backfill domains from existing crawl_log
INSERT INTO domains (domain, status, pages_crawled, images_found, source, first_seen_at, last_crawled_at)
SELECT 
    domain,
    'exhausted' AS status,  -- Conservative: assume historical domains are done
    COUNT(*) AS pages_crawled,
    SUM(images_found) AS images_found,
    'backfill_crawl_log' AS source,
    MIN(crawled_at) AS first_seen_at,
    MAX(crawled_at) AS last_crawled_at
FROM crawl_log
WHERE domain IS NOT NULL
GROUP BY domain
ON CONFLICT (domain) DO NOTHING;  -- Don't overwrite if already exists from seed ingestion
```

**Compatibility:** Fully backward compatible. Old workers ignore `domains` table.

**Rollback:** Set `ENABLE_DOMAIN_TRACKING=false`; spider skips upsert/update logic.

**Validation:**
- `SELECT COUNT(*) FROM domains;` should grow steadily
- No crawl behavior changes
- Logs show domain upserts without errors

---

### Phase B: Per-Domain Budgets (Medium Risk)

**Goal:** Enforce fair per-domain crawl budgets instead of global `max_pages`.

**Steps:**
1. Add per-domain counters to spider: `self._domain_pages_crawled: dict[str, int]`
2. Check per-domain budget before following links
3. Save frontier checkpoint when domain budget exhausted
4. **Feature flag:** `ENABLE_PER_DOMAIN_BUDGET=true` (default in current code: true)

**Mixed-Version Behavior:**
- Old workers continue using global `max_pages`
- New workers with flag=false also use global `max_pages`
- New workers with flag=true enforce per-domain budgets
- **No conflicts:** Both modes write to same `domains` table; counters are cumulative

**Rollback:** Set `ENABLE_PER_DOMAIN_BUDGET=false`; revert to global counter.

**Validation:**
- Query domains with pages_crawled >> 0 vs. pages_crawled == 0
- Expect more uniform distribution across domains
- Crawl logs show budget hits per domain, not global

---

### Phase C: Smart Scheduling + Concurrency (HIGHEST RISK)

**Goal:** Replace seed-driven scheduling with DB-driven candidate selection and claim protocol.

**Prerequisites:**
- Phase A and B stable for ≥2 weeks
- Backfill complete
- Priority recalculation tested
- Claim protocol tested in staging with multiple workers

**Steps:**
1. Implement domain claim protocol (§7.1)
2. Implement state transition function (§7.2)
3. Update `start_requests` to query `domains` table with claim acquisition
4. Add claim renewal heartbeat (every 10 min)
5. Add claim release in `closed()`
6. **Feature flags:**
   - `ENABLE_SMART_SCHEDULING=true` (use DB candidates instead of seeds)
   - `ENABLE_CLAIM_PROTOCOL=true` (claim domains before crawl)

**Mixed-Version DANGER ZONE:**
- **DO NOT RUN** Phase C workers alongside Phase A/B workers
- Phase C workers use claim protocol; Phase A/B workers do not
- **Result:** Phase A/B workers will duplicate work from claimed domains

**Deployment:**
1. Stop all Phase A/B workers
2. Deploy Phase C code to all workers
3. Enable `ENABLE_SMART_SCHEDULING=true` on 1 worker (canary)
4. Monitor for 24 hours: check claim expiries, version conflicts, duplicate work
5. Roll out to remaining workers

**Rollback:**
1. Set `ENABLE_SMART_SCHEDULING=false` on all workers
2. Workers revert to seed-driven `start_requests`
3. Release any active claims:
   ```sql
   UPDATE domains
   SET claimed_by = NULL, claim_expires_at = NULL
   WHERE claim_expires_at > CURRENT_TIMESTAMP;
   ```

**Validation:**
- Query active claims: should be ~1 claim per active worker
- No expired claims with `claim_expires_at < NOW()`
- No version conflict errors in logs
- `domains.pages_crawled` increments match `crawl_log` row counts

---

### Phase D: Refresh Mode (Requires Phase C)

**Goal:** Add refresh spider that revisits `exhausted` domains.

**Steps:**
1. Implement refresh candidate query (§6.1)
2. Create `RefreshSpider` that queries `exhausted` domains
3. Refresh spider re-visits known URLs from `crawl_log`
4. **Feature flag:** `ENABLE_REFRESH_MODE=true`

**Compatibility:** Additive. Discovery spider continues unchanged.

**Rollback:** Stop refresh spider workers; set `ENABLE_REFRESH_MODE=false`.

**Validation:**
- Refresh spider only visits domains with `status='exhausted'`
- `provenance.discovery_type='refresh'` rows appear
- Images' `last_seen_at` updates for unchanged images

### Compressed Deployment: All Phases in a Single Release

The 2-week soak periods between phases are conservative operational guidance for production environments where issues must surface organically under real load. For teams that can invest in thorough pre-deployment verification, **all phases can be implemented and deployed in a single release** without waiting weeks between each one.

#### Why This Works

1. **Phases A and B are purely additive** — they add tracking alongside existing behavior without changing crawl logic. The risk of hidden regressions is minimal.
2. **Feature flags provide incremental activation** — all code ships at once, but phases are enabled sequentially via environment variables. Each flag flip is independently reversible.
3. **Phase C (the highest-risk phase) can be validated in hours, not weeks** — the primary risk is multi-worker concurrency conflicts, which are deterministically testable rather than requiring weeks of observation.

#### Compressed Deployment Protocol

**Step 1: Deploy all code with conservative defaults**
- Ship Phases A through D in a single code deployment
- Default flags in current code: `ENABLE_DOMAIN_TRACKING=true`, `ENABLE_PER_DOMAIN_BUDGET=true`, `ENABLE_SMART_SCHEDULING=false`, `ENABLE_CLAIM_PROTOCOL=false`
- This means Phase A and Phase B are active by default; Phase C remains opt-in

**Step 2: Activate Phase A and run a verification crawl**
```bash
# Run a small crawl (10 domains, 50 pages) with Phase A active
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=50

# Verify domains table populated correctly
psql $DATABASE_URL -c "SELECT domain, status, pages_crawled, images_found FROM domains;"
```
- Confirm: ≥1 row per seed domain, stats non-zero, no errors in logs
- Time: ~10 minutes

**Step 3: Activate Phase B and verify fair budgets**
```bash
export ENABLE_PER_DOMAIN_BUDGET=true
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=100

# Verify fair distribution (each domain should get roughly equal pages)
psql $DATABASE_URL -c "SELECT domain, pages_crawled FROM domains ORDER BY pages_crawled;"

# Verify frontier checkpoints saved for partially-crawled domains
redis-cli KEYS "frontier:*"
```
- Confirm: page counts roughly equal across domains, checkpoints present
- Time: ~15 minutes

**Step 4: Activate Phase C (single worker canary)**
```bash
export ENABLE_SMART_SCHEDULING=true
export ENABLE_CLAIM_PROTOCOL=true

# Run ONE worker and verify claim mechanics
scrapy crawl discovery -a max_pages=200

# Verify claims acquired and released cleanly
psql $DATABASE_URL -c "SELECT domain, claimed_by, claim_expires_at FROM domains WHERE claimed_by IS NOT NULL;"
# Should show 0 rows after crawl completes (all claims released)
```
- Confirm: claims acquired during crawl, released on completion, no stuck claims
- Time: ~15 minutes

**Step 5: Multi-worker concurrency test (Phase C validation)**
```bash
# Run 2-3 workers simultaneously
scrapy crawl discovery -a max_pages=500 &
scrapy crawl discovery -a max_pages=500 &
scrapy crawl discovery -a max_pages=500 &

# After completion, verify zero duplicate work
psql $DATABASE_URL -c "
  SELECT domain, COUNT(*) FROM crawl_log
  WHERE crawled_at > NOW() - INTERVAL '1 hour'
  GROUP BY domain, page_url
  HAVING COUNT(*) > 1;
"
# Should return 0 rows (no duplicates)
```
- Confirm: no duplicate domain claims, no version conflict errors, distinct work per worker
- Time: ~30 minutes

**Total compressed verification: ~1-2 hours** (vs. 6+ weeks with soak periods)

#### Risk Tradeoffs

| Risk | Soak Period Catches It | Compressed Deployment Mitigation |
|------|----------------------|----------------------------------|
| Subtle state machine bugs at scale | Yes (over thousands of transitions) | Comprehensive unit tests for all transitions + integration test with full lifecycle |
| Concurrency edge cases under real load | Yes (naturally) | Explicit multi-worker test (Step 5); stress test with high parallelism |
| Data integrity drift over time | Yes (organic discovery) | Backfill validation queries (§12.4); post-crawl `domains` table consistency checks |
| Performance degradation at scale | Yes (gradual) | Benchmark claim overhead; monitor query latency during test crawls |
| Unexpected interaction between phases | Yes (by isolating phases) | Feature flags still allow phase isolation; can disable any phase independently |

#### When NOT to Use Compressed Deployment

- **First production deployment with real data at scale** (>100K domains): run Phase A passively for at least 48 hours before enabling Phase C
- **Multiple operators/teams**: coordination risk is higher; phased rollout reduces blast radius
- **No automated test suite**: the compressed approach relies heavily on test coverage compensating for reduced observation time

#### Rollback Remains Instant

Even with compressed deployment, rollback is identical to the phased approach:
```bash
# Disable any phase independently
export ENABLE_DOMAIN_TRACKING=false      # Disables Phase A
export ENABLE_PER_DOMAIN_BUDGET=false    # Disables Phase B
export ENABLE_SMART_SCHEDULING=false     # Disables Phase C
export ENABLE_CLAIM_PROTOCOL=false       # Disables Phase C concurrency

# If schema must be reverted
alembic downgrade -1
```

---

## 12. Backfill Strategy

**Goal:** Populate `domains` table from existing `crawl_log` and `provenance` data before activating smart scheduling.

### 12.1 Backfill from crawl_log

```sql
-- Step 1: Create domains from historical crawl_log
INSERT INTO domains (
    domain,
    status,
    pages_discovered,
    pages_crawled,
    images_found,
    total_error_count,
    source,
    first_seen_at,
    last_crawled_at
)
SELECT 
    domain,
    CASE 
        WHEN COUNT(*) FILTER (WHERE status >= 400) > COUNT(*) * 0.5 THEN 'blocked'::domain_status
        ELSE 'exhausted'::domain_status
    END AS status,
    COUNT(*) AS pages_discovered,
    COUNT(*) FILTER (WHERE status < 400) AS pages_crawled,
    COALESCE(SUM(images_found), 0) AS images_found,
    COUNT(*) FILTER (WHERE status >= 400) AS total_error_count,
    'backfill_crawl_log' AS source,
    MIN(crawled_at) AS first_seen_at,
    MAX(crawled_at) AS last_crawled_at
FROM crawl_log
WHERE domain IS NOT NULL
GROUP BY domain
ON CONFLICT (domain) DO UPDATE SET
    pages_crawled = EXCLUDED.pages_crawled,
    images_found = EXCLUDED.images_found,
    last_crawled_at = EXCLUDED.last_crawled_at;
```

### 12.2 Backfill images_stored from provenance

```sql
-- Step 2: Update images_stored count from provenance
WITH domain_images AS (
    SELECT 
        source_domain AS domain,
        COUNT(DISTINCT image_id) AS images_stored
    FROM provenance
    WHERE source_domain IS NOT NULL
    GROUP BY source_domain
)
UPDATE domains
SET images_stored = domain_images.images_stored,
    image_yield_rate = CASE 
        WHEN pages_crawled > 0 THEN domain_images.images_stored::DOUBLE PRECISION / pages_crawled
        ELSE NULL
    END
FROM domain_images
WHERE domains.domain = domain_images.domain;
```

### 12.3 Canonicalize existing domains

```sql
-- Step 3: Apply canonicalization to backfilled domains
-- (Requires custom function or external script)
-- Example: strip www, lowercase, etc.

UPDATE domains
SET domain = lower(regexp_replace(domain, '^www\\.', ''))
WHERE domain ~ '^www\\.';

-- Handle duplicates after canonicalization (merge)
WITH duplicates AS (
    SELECT domain, array_agg(id ORDER BY first_seen_at) AS ids
    FROM domains
    GROUP BY domain
    HAVING COUNT(*) > 1
)
-- Merge logic: keep earliest row, sum counters, update references
-- (Implementation depends on FK structure)
```

### 12.4 Validation Queries

```sql
-- Check backfill coverage
SELECT 
    (SELECT COUNT(DISTINCT domain) FROM crawl_log) AS crawl_log_domains,
    (SELECT COUNT(*) FROM domains WHERE source LIKE 'backfill%') AS backfilled_domains,
    (SELECT COUNT(*) FROM domains) AS total_domains;

-- Verify yield rates
SELECT 
    status,
    COUNT(*) AS domain_count,
    AVG(image_yield_rate) AS avg_yield,
    AVG(pages_crawled) AS avg_pages
FROM domains
WHERE source LIKE 'backfill%'
GROUP BY status;
```

## 13. Open Questions (Revised)

| # | Question | Options | Recommendation |
|---|----------|---------|----------------|
| 1 | **Frontier persistence model?** | Redis durable / Separate table / Hybrid | **Redis durable** (§3.5 Option A) — best scale/performance |
| 2 | **Periodic mid-crawl flush?** | Every N pages per domain | Every 200 pages — better crash recovery |
| 3 | **Subdomain handling?** | Separate / Collapse to parent | **Separate** (keep full netloc); add `parent_domain` for aggregation |
| 4 | **Priority computation?** | Stored / Computed at query | **Computed at query** with cached `priority_score` refreshed hourly |
| 5 | **Should blocked domains auto-retry?** | Automatic (cooldown) / Manual only | **Automatic with cooldown**; max 3 retries before permanent block |
| 6 | **robots.txt blocking?** | Use `blocked` status / Separate status | **Use `blocked`** with `block_reason_code='robots_denied'`, 90-day cooldown |
| 7 | **Claim lease duration?** | 15 / 30 / 60 minutes | **30 minutes** with 10-minute renewal cadence |
| 8 | **Domain canonicalization?** | Strict (with merging) / Lenient (accept variance) | **Strict** with merge logic for existing duplicates |
| 9 | **When to normalize FK to domain_id?** | Phase A / Phase C / Never | **Phase C** when smart scheduling is stable |

---

## 14. Impact on Existing Tests

- **No breaking changes in Phase A** — domain tracking is additive
- New tests needed for:
  - Domain upsert logic (`test_domain_tracking.py`)
  - Status lifecycle transitions
  - Frontier checkpoint persistence (Redis/table operations)
  - Priority score calculation
  - Per-domain budget enforcement
  - Resume from checkpoint (Redis/table)
- Existing `test_spider.py` and `test_integration.py` remain valid — they test page-level behavior which is unchanged

---

## 15. Critical Recommendations Summary

**Must-Have Before Phase C Deployment:**

1. **Domain Claim/Lease Protocol (§7.1)** — Prevents duplicate work under multiple workers
2. **Domain Canonicalization (§3.2)** — Prevents domain identity inconsistencies
3. **Frontier Persistence Model (§3.5)** — JSONB does not scale; use Redis or separate table
4. **Optimistic Locking (version field)** — Detects conflicts on concurrent updates
5. **State Transition Enforcement (§7.2)** — Prevents invalid status transitions
6. **Backfill Job (§12)** — Required for historical domain data before smart scheduling
7. **Feature Flags (§11)** — Enable safe phase-by-phase rollout and rollback
8. **Mixed-Version Controls (§11)** — Phase C must not run alongside Phase A/B workers

**Type/Schema Fixes:**

- `status`: Change VARCHAR(20) → `domain_status` ENUM type
- `pages_*`, `images_*`: Change INTEGER → BIGINT
- `*_rate`: Change FLOAT → DOUBLE PRECISION
- `error_count`: Split into `total_error_count` + `consecutive_error_count`
- `priority`: Rename to `priority_score`; add `priority_computed_at`
- `source`: Extend to VARCHAR(100) for dataset versioning
- Add: `block_reason_code`, `first_blocked_at`, `claimed_by`, `claim_expires_at`, `version`, `updated_at`

**Architecture Changes:**

- Replace `frontier_snapshot JSONB` → `frontier_checkpoint_id` + Redis/table backend
- Add composite indexes for discovery/refresh candidate queries
- Add `FOR UPDATE SKIP LOCKED` to candidate selection
- Compute priority at query time, not stored

## 16. Summary

| What Changes | From | To |
|-------------|------|-----|
| Domain awareness | None (ephemeral in-memory) | Persistent DB entity with state machine |
| Scheduling | File order or static rank | Dynamic priority (yield × staleness × remaining) |
| Progress tracking | Global counter | Per-domain counters with budget enforcement |
| Stop/resume | Start from scratch | Resume from frontier checkpoint (Redis/table) |
| Error handling | In-memory, lost on restart | Persistent status (blocked/unreachable) with cooldowns |
| Refresh mode | No foundation | Query exhausted domains by yield rate and staleness |

The `domains` table is the single highest-leverage addition to the codebase. It transforms the crawler from a stateless batch job into a stateful, resumable, self-optimizing system.
