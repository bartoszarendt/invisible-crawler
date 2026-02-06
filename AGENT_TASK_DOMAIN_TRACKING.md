# Agent Task: Implement Domain-Centric Crawl Tracking (Phase A)

**Status:** Ready for autonomous implementation  
**Priority:** High  
**Estimated Complexity:** Medium-High  
**Prerequisites:** Database running, tests passing (46/52)

---

## Context

You are implementing Phase A of the domain tracking design documented in [DOMAIN_TRACKING_DESIGN.md](DOMAIN_TRACKING_DESIGN.md). This is a critical architectural upgrade that transforms the crawler from stateless to stateful by making domains first-class persistent entities.

**Current State:**
- Crawler treats domains as ephemeral (in-memory only)
- No resume capability after stop/crash
- No per-domain progress tracking
- Global `max_pages` budget causes unfair distribution
- Phase 2 (Redis scheduling) is implemented but lacks domain state persistence

**What Phase A Accomplishes:**
- Establishes `domains` table with full schema (including concurrency fields for future phases)
- Adds passive domain tracking (upsert on start, update on close)
- Implements domain canonicalization
- Backfills historical data from `crawl_log`
- **Zero behavior changes** to crawl logic (additive only)

---

## Implementation Scope (Phase A Only)

### ✅ IN SCOPE

1. **Database Migration (Alembic)**
   - Create `domains` table with full schema (§3.1 of design doc)
   - Create ENUM type `domain_status`
   - Create all indexes (including concurrency/Phase C indexes)
   - Create `update_domains_updated_at()` trigger function
   - Migration must be reversible (include `downgrade()`)

2. **Domain Canonicalization Module**
   - New file: `processor/domain_canonicalization.py`
   - Implement `canonicalize_domain()` function (§3.2 of design doc)
   - Dependencies: `idna`, `publicsuffix2` (or `tldextract`)
   - Unit tests with edge cases (IDN, ports, www, subdomains, case)
   - **Decision:** Keep full subdomains (do NOT strip to registrable domain by default)

3. **Database Access Layer**
   - New file: `storage/domain_repository.py`
   - Functions:
     - `upsert_domain(domain, source, seed_rank)` - insert or ignore
     - `update_domain_stats(domain, **kwargs)` - increment counters, update status
     - `get_domain(domain)` - fetch domain row
     - `backfill_domains_from_crawl_log()` - historical data import
   - Use existing connection pool from `storage/db.py`

4. **Spider Integration (Passive Tracking)**
   - Modify `crawler/spiders/discovery_spider.py`:
     - Import canonicalization function
     - In `start_requests()`: call `upsert_domain()` for each seed (before yielding)
     - In `closed()`: call `update_domain_stats()` for each crawled domain
     - **NO CHANGES** to crawl logic, request generation, or budget enforcement
   - Feature flag: `ENABLE_DOMAIN_TRACKING` (default: `true`)
   - If flag is `false`, skip all domain operations (graceful no-op)

5. **Environment Configuration**
   - Add to `env_config.py`:
     - `get_enable_domain_tracking()` -> bool (default: true)
     - `get_domain_canonicalization_strip_subdomains()` -> bool (default: false)

6. **Tests**
   - `tests/test_domain_canonicalization.py` (unit tests for canonicalization)
   - `tests/test_domain_repository.py` (DB operations)
   - `tests/test_domain_tracking_integration.py` (spider integration)
   - Existing tests must continue passing (no regressions)

7. **Backfill Script**
   - CLI command: `python -m crawler.cli backfill-domains`
   - Executes SQL from §12.1 and §12.2 of design doc
   - Idempotent (safe to rerun)
   - Logs progress and summary stats

8. **Documentation Updates**
   - Update `IMPLEMENTATION.md` §"Phase 2 Implementation Status" to note Phase A completion
   - Add "Domain Tracking (Phase A)" section with usage instructions

### ❌ OUT OF SCOPE (Future Phases)

- **Phase B:** Per-domain budgets, frontier checkpoints
- **Phase C:** Smart scheduling, claim protocol, concurrency
- **Phase D:** Refresh mode
- Do NOT modify scheduling logic
- Do NOT modify request prioritization
- Do NOT implement frontier persistence yet
- Do NOT use concurrency fields (`claimed_by`, `claim_expires_at`, `version`) yet

---

## Acceptance Criteria

### Must Pass
1. ✅ Alembic migration runs cleanly: `alembic upgrade head` (no errors)
2. ✅ Migration is reversible: `alembic downgrade -1` (no errors)
3. ✅ All existing tests pass: `pytest tests/` (46+ tests passing)
4. ✅ New domain tracking tests pass (≥15 new tests)
5. ✅ Crawl runs successfully with domain tracking enabled
6. ✅ `domains` table populates during crawl (≥1 row per seed)
7. ✅ Domain stats update in `closed()` (pages_crawled, images_found, etc.)
8. ✅ Backfill script runs successfully on existing `crawl_log` data
9. ✅ Feature flag `ENABLE_DOMAIN_TRACKING=false` disables tracking (no errors)
10. ✅ No crawl behavior changes (same pages visited, same images downloaded)

### Code Quality
- ✅ Type hints on all new functions (`mypy --strict` passes)
- ✅ Google-style docstrings
- ✅ Black formatting (line length 100)
- ✅ Ruff linting passes
- ✅ Exception handling: no bare `except:` clauses
- ✅ Logging: use `logger.info()` for domain operations, `logger.debug()` for details

---

## Implementation Steps (Suggested Order)

### Step 1: Database Schema (30 minutes)
1. Create migration file: `alembic revision -m "add_domains_table_phase_a"`
2. Copy schema from DOMAIN_TRACKING_DESIGN.md §3.1
3. Test upgrade: `alembic upgrade head`
4. Test downgrade: `alembic downgrade -1`
5. Verify indexes created: `\d domains` in psql

### Step 2: Domain Canonicalization (45 minutes)
1. Add dependencies to `requirements.txt`: `idna`, `publicsuffix2`
2. Create `processor/domain_canonicalization.py`
3. Implement `canonicalize_domain()` with all rules from §3.2
4. Create `tests/test_domain_canonicalization.py`
5. Test cases:
   - `https://Example.COM/path` → `example.com`
   - `www.example.com` → `example.com`
   - `example.com:443` → `example.com`
   - `münchen.de` → `xn--mnchen-3ya.de`
   - `blog.example.com` → `blog.example.com` (keep subdomain)

### Step 3: Database Access Layer (60 minutes)
1. Create `storage/domain_repository.py`
2. Implement functions (with proper error handling):
   ```python
   def upsert_domain(domain: str, source: str, seed_rank: int | None = None) -> None:
       """Insert domain or ignore if exists. Uses ON CONFLICT DO NOTHING."""
   
   def update_domain_stats(
       domain: str,
       pages_crawled_delta: int = 0,
       images_found_delta: int = 0,
       images_stored_delta: int = 0,
       status: str | None = None,
       last_crawl_run_id: str | None = None,
   ) -> None:
       """Increment counters and update status. Uses += for deltas."""
   
   def get_domain(domain: str) -> dict | None:
       """Fetch domain row as dict."""
   
   def backfill_domains_from_crawl_log() -> dict[str, int]:
       """Execute backfill SQL. Returns stats."""
   ```
3. Create `tests/test_domain_repository.py`

### Step 4: Spider Integration (45 minutes)
1. Modify `discovery_spider.py`:
   - Import `canonicalize_domain` and `domain_repository`
   - Add to `__init__()`:
     ```python
     self.enable_domain_tracking = get_enable_domain_tracking()
     self._domain_stats: dict[str, dict] = {}  # Track per-domain stats
     ```
   - In `start_requests()` (before yielding):
     ```python
     if self.enable_domain_tracking:
         canonical_domain = canonicalize_domain(seed_url)
         domain_repository.upsert_domain(
             domain=canonical_domain,
             source=self.seeds_file or "redis",
             seed_rank=None
         )
     ```
   - Track stats during crawl (in `parse()`):
     ```python
     if self.enable_domain_tracking:
         self._domain_stats.setdefault(canonical_domain, {
             'pages': 0, 'images_found': 0, 'images_stored': 0
         })
         self._domain_stats[canonical_domain]['pages'] += 1
         self._domain_stats[canonical_domain]['images_found'] += len(image_urls)
     ```
   - In `closed()`:
     ```python
     if self.enable_domain_tracking:
         for domain, stats in self._domain_stats.items():
             domain_repository.update_domain_stats(
                 domain=domain,
                 pages_crawled_delta=stats['pages'],
                 images_found_delta=stats['images_found'],
                 images_stored_delta=stats.get('images_stored', 0),
                 status='active',  # Simple for Phase A
                 last_crawl_run_id=self.crawl_run_id
             )
     ```
2. Test with small seed file (5 domains, max_pages=50)

### Step 5: Backfill Script (30 minutes)
1. Add CLI command to `crawler/cli.py`:
   ```python
   @click.command()
   def backfill_domains():
       """Backfill domains table from historical crawl_log data."""
       click.echo("Starting backfill...")
       stats = domain_repository.backfill_domains_from_crawl_log()
       click.echo(f"Backfilled {stats['domains_created']} domains")
       click.echo(f"Updated {stats['images_stored_updated']} with image counts")
   ```
2. Test on existing database

### Step 6: Integration Testing (30 minutes)
1. Create `tests/test_domain_tracking_integration.py`
2. Test scenarios:
   - Crawl with tracking enabled → domains table populated
   - Crawl with tracking disabled → domains table unchanged
   - Crawl same seed twice → stats cumulative (not reset)
   - Backfill on empty database → no errors
3. Run full test suite: `pytest tests/ -v`

### Step 7: Documentation (15 minutes)
1. Update `IMPLEMENTATION.md`:
   - Add section after Phase 2
   - Document feature flag
   - Add backfill command usage
2. Update `AGENTS.md` if needed

---

## File Checklist

### New Files (Create)
- [ ] `storage/migrations/versions/XXXX_add_domains_table_phase_a.py`
- [ ] `processor/domain_canonicalization.py`
- [ ] `storage/domain_repository.py`
- [ ] `tests/test_domain_canonicalization.py`
- [ ] `tests/test_domain_repository.py`
- [ ] `tests/test_domain_tracking_integration.py`

### Modified Files
- [ ] `crawler/spiders/discovery_spider.py` (add passive tracking)
- [ ] `crawler/cli.py` (add backfill command)
- [ ] `env_config.py` (add feature flag)
- [ ] `requirements.txt` (add idna, publicsuffix2)
- [ ] `IMPLEMENTATION.md` (document Phase A)

### No Changes
- `crawler/scheduler.py` (scheduling logic unchanged)
- `crawler/pipelines.py` (image processing unchanged)
- `storage/schema.sql` (manual reference only; Alembic is source of truth)

---

## Testing Strategy

### Unit Tests (Isolated)
```bash
pytest tests/test_domain_canonicalization.py -v
pytest tests/test_domain_repository.py -v
```

### Integration Tests (Requires DB)
```bash
# Ensure DB is running and migrated
alembic upgrade head
pytest tests/test_domain_tracking_integration.py -v
```

### End-to-End Validation
```bash
# 1. Backfill existing data
python -m crawler.cli backfill-domains

# 2. Run small crawl with tracking
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=50

# 3. Verify domains table
psql $DATABASE_URL -c "SELECT domain, status, pages_crawled, images_found FROM domains LIMIT 10;"

# 4. Verify stats are cumulative (run again)
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=50
psql $DATABASE_URL -c "SELECT domain, pages_crawled FROM domains LIMIT 5;"
# pages_crawled should be ~100 (cumulative)
```

---

## Rollback Plan

If Phase A causes issues:

```bash
# 1. Disable feature flag
export ENABLE_DOMAIN_TRACKING=false

# 2. Revert migration (if needed)
alembic downgrade -1

# 3. Revert code changes
git revert <commit-hash>
```

---

## Success Metrics

After Phase A completion:
- `domains` table exists with ≥1 row per historically crawled domain
- Domain tracking logs appear: "Upserted domain: example.com (source: file)"
- Crawl behavior unchanged (same page count, same image count)
- Stats are cumulative across multiple runs
- Feature flag works (tracking can be disabled without errors)

---

## Critical Constraints (DO NOT VIOLATE)

1. **No behavior changes:** Crawl must visit same pages in same order as before
2. **Performance:** Domain operations must not slow crawl by >5%
3. **Backwards compatibility:** Old code without tracking must still work
4. **Data integrity:** `ON CONFLICT` handling must prevent duplicate domains
5. **Transaction safety:** Domain updates in `closed()` must not fail spider close
6. **Schema completeness:** Create full schema now (even if unused in Phase A) to avoid migrations in Phase B/C

---

## Questions? Blockers?

- Unsure about canonicalization behavior → Check examples in §3.2
- SQL syntax error → Verify against PostgreSQL 14+ syntax
- Test failures → Check if DB is migrated (`alembic current`)
- Import errors → Verify all dependencies installed (`pip install -r requirements.txt`)

**Reference Documents:**
- Full design: [DOMAIN_TRACKING_DESIGN.md](DOMAIN_TRACKING_DESIGN.md)
- Current implementation: [IMPLEMENTATION.md](IMPLEMENTATION.md)
- Schema reference: §3 of design doc
- Backfill SQL: §12 of design doc

---

**Ready to implement? Start with Step 1 (Database Schema).**

---
---

# Agent Task: Implement Per-Domain Budgets (Phase B)

**Status:** Ready after Phase A completion  
**Priority:** High  
**Estimated Complexity:** Medium  
**Prerequisites:** Phase A deployed, stable for ≥2 weeks, `domains` table populated

---

## Context

You are implementing Phase B of the domain tracking design. Phase A established the `domains` table and passive tracking. Phase B makes the crawler **fair** by enforcing per-domain crawl budgets instead of a single global limit.

**Current Problem (Post-Phase A):**
- Global `max_pages` means first domain can consume entire budget
- With 50 seed domains and `max_pages=1000`, some domains get 1000 pages, others get 0
- No awareness of "partially complete" domains
- Frontier is lost when budget is hit (can't resume where left off)

**What Phase B Accomplishes:**
- Per-domain page counters replace global counter
- Fair budget distribution: each domain gets `max_pages_per_run` pages
- Frontier checkpoint saved when domain budget exhausted
- Domains can resume from checkpoint on next run
- **Still file/Redis seed-driven** (smart scheduling comes in Phase C)

---

## Implementation Scope (Phase B Only)

### ✅ IN SCOPE

1. **Frontier Checkpoint Module**
   - New file: `storage/frontier_checkpoint.py`
   - Functions:
     - `save_checkpoint(domain, run_id, urls)` → checkpoint_id
     - `load_checkpoint(checkpoint_id)` → list of URLs with depth
     - Implementation: **Redis sorted sets** (Option A from §3.5)
   - Redis key format: `frontier:{domain}:{run_id}`
   - Store URL + depth as sorted set (score = depth for BFS)

2. **Spider Per-Domain Budget Enforcement**
   - Modify `discovery_spider.py`:
     - Add `self._domain_pages_crawled: dict[str, int]` to track per-domain
     - In `parse()`: check `domain_pages` against `max_pages_per_run` before following links
     - When budget hit: save frontier checkpoint, update domain status to 'active'
     - Track pending URLs per domain: `self._domain_pending_urls: dict[str, list]`
   - Feature flag: `ENABLE_PER_DOMAIN_BUDGET` (default: `false` initially)

3. **Resume from Checkpoint**
   - Modify `start_requests()`:
     - For each domain, check if `frontier_checkpoint_id` exists
     - If exists: load checkpoint and yield URLs from checkpoint
     - If not: yield root URL as before
   - Delete checkpoint after successful load

4. **Domain Repository Updates**
   - Add to `storage/domain_repository.py`:
     - `update_frontier_checkpoint(domain, checkpoint_id, frontier_size)`
     - `clear_frontier_checkpoint(domain)`

5. **Environment Configuration**
   - Add to `env_config.py`:
     - `get_enable_per_domain_budget()` → bool (default: false)
     - `get_default_max_pages_per_run()` → int (default: 1000)

6. **Tests**
   - `tests/test_frontier_checkpoint.py` (Redis checkpoint save/load)
   - `tests/test_per_domain_budget.py` (budget enforcement logic)
   - `tests/test_resume_from_checkpoint.py` (integration test)

### ❌ OUT OF SCOPE

- Smart scheduling (queries `domains` table for candidates) → Phase C
- Claim protocol, concurrency → Phase C
- Refresh mode → Phase D
- Do NOT change seed source (still file/Redis driven)
- Do NOT implement priority-based selection yet

---

## Acceptance Criteria

### Must Pass
1. ✅ With flag enabled: each domain gets fair share of pages (±10% variance)
2. ✅ With flag disabled: behavior unchanged (global counter)
3. ✅ Frontier checkpoint saved to Redis when domain budget exhausted
4. ✅ Resume from checkpoint works: next run continues from saved URLs
5. ✅ Checkpoint cleared after successful resume
6. ✅ Multi-domain crawl: all domains get crawled (not just first)
7. ✅ All existing tests pass
8. ✅ New tests pass (≥10 new tests)
9. ✅ Redis connection failure handled gracefully (logs warning, continues without resume)

### Code Quality
- Same standards as Phase A (type hints, docstrings, black, ruff, mypy)

---

## Implementation Steps (Suggested Order)

### Step 1: Frontier Checkpoint Module (60 minutes)
1. Create `storage/frontier_checkpoint.py`
2. Implement Redis-based checkpoint storage:
   ```python
   from redis import Redis
   from typing import List, Dict
   
   def save_checkpoint(domain: str, run_id: str, urls: List[Dict[str, any]], redis_client: Redis) -> str:
       """Save frontier URLs to Redis sorted set. Returns checkpoint_id."""
       checkpoint_id = f"{domain}:{run_id}"
       key = f"frontier:{checkpoint_id}"
       
       # Store as sorted set: url as member, depth as score
       pipeline = redis_client.pipeline()
       for entry in urls:
           pipeline.zadd(key, {entry['url']: entry['depth']})
       pipeline.expire(key, 86400 * 30)  # 30 day TTL
       pipeline.execute()
       
       return checkpoint_id
   
   def load_checkpoint(checkpoint_id: str, redis_client: Redis) -> List[Dict[str, any]]:
       """Load frontier URLs from Redis. Returns list of {url, depth}."""
       key = f"frontier:{checkpoint_id}"
       members = redis_client.zrange(key, 0, -1, withscores=True)
       return [{'url': url.decode('utf-8'), 'depth': int(depth)} for url, depth in members]
   
   def delete_checkpoint(checkpoint_id: str, redis_client: Redis) -> None:
       """Delete checkpoint after successful load."""
       key = f"frontier:{checkpoint_id}"
       redis_client.delete(key)
   ```
3. Add tests: `tests/test_frontier_checkpoint.py`

### Step 2: Spider Budget Enforcement (90 minutes)
1. Modify `discovery_spider.py.__init__()`:
   ```python
   self.enable_per_domain_budget = get_enable_per_domain_budget()
   self._domain_pages_crawled: dict[str, int] = {}
   self._domain_pending_urls: dict[str, list] = {}  # URLs discovered but not yet crawled
   self.max_pages_per_run = get_default_max_pages_per_run()
   ```

2. Modify `parse()` to track pending URLs and enforce budget:
   ```python
   def parse(self, response):
       domain = response.meta['domain']
       
       if self.enable_per_domain_budget:
           self._domain_pages_crawled[domain] = self._domain_pages_crawled.get(domain, 0) + 1
           
           # Check budget
           if self._domain_pages_crawled[domain] >= self.max_pages_per_run:
               self.logger.info(f"Domain {domain} reached budget: {self.max_pages_per_run} pages")
               # Don't follow more links for this domain
               # Save any pending links as frontier checkpoint (done in closed())
               return
       
       # ... existing parse logic ...
       
       # Track discovered links for this domain
       for next_url in self._extract_links(response, domain):
           if self.enable_per_domain_budget:
               self._domain_pending_urls.setdefault(domain, []).append({
                   'url': next_url,
                   'depth': response.meta.get('depth', 0) + 1
               })
           
           # Yield if budget permits
           if not self.enable_per_domain_budget or \
              self._domain_pages_crawled[domain] < self.max_pages_per_run:
               yield Request(url=next_url, ...)
   ```

3. Modify `closed()` to save checkpoints:
   ```python
   def closed(self, reason):
       if self.enable_per_domain_budget:
           from storage.frontier_checkpoint import save_checkpoint
           import redis
           
           redis_client = redis.from_url(get_redis_url())
           
           for domain, pending_urls in self._domain_pending_urls.items():
               if pending_urls and self._domain_pages_crawled.get(domain, 0) >= self.max_pages_per_run:
                   # Domain budget exhausted but has pending URLs
                   checkpoint_id = save_checkpoint(domain, str(self.crawl_run_id), pending_urls, redis_client)
                   domain_repository.update_frontier_checkpoint(domain, checkpoint_id, len(pending_urls))
                   self.logger.info(f"Saved frontier checkpoint for {domain}: {len(pending_urls)} URLs")
       
       # ... existing closed() logic ...
   ```

### Step 3: Resume from Checkpoint (45 minutes)
1. Modify `start_requests()`:
   ```python
   def start_requests(self):
       # ... existing seed loading ...
       
       if self.enable_per_domain_budget:
           from storage.frontier_checkpoint import load_checkpoint, delete_checkpoint
           import redis
           
           redis_client = redis.from_url(get_redis_url())
           
           for domain in self._domains:
               domain_row = domain_repository.get_domain(canonicalize_domain(domain))
               
               if domain_row and domain_row.get('frontier_checkpoint_id'):
                   # Resume from checkpoint
                   try:
                       checkpoint_urls = load_checkpoint(domain_row['frontier_checkpoint_id'], redis_client)
                       self.logger.info(f"Resuming {domain} from checkpoint: {len(checkpoint_urls)} URLs")
                       
                       for entry in checkpoint_urls:
                           yield Request(
                               url=entry['url'],
                               callback=self.parse,
                               meta={'domain': domain, 'depth': entry['depth']}
                           )
                       
                       # Clear checkpoint after successful load
                       delete_checkpoint(domain_row['frontier_checkpoint_id'], redis_client)
                       domain_repository.clear_frontier_checkpoint(domain)
                   except Exception as e:
                       self.logger.warning(f"Failed to load checkpoint for {domain}: {e}")
                       # Fall back to root URL
                       yield Request(url=f"https://{domain}", ...)
               else:
                   # Fresh start
                   yield Request(url=f"https://{domain}", ...)
       else:
           # Phase A behavior: just yield root URLs
           for domain in self._domains:
               yield Request(url=f"https://{domain}", ...)
   ```

### Step 4: Testing (60 minutes)
1. Create `tests/test_per_domain_budget.py`:
   - Test: 3 domains, `max_pages_per_run=10` → each gets ~10 pages
   - Test: Flag disabled → global budget behavior
   - Test: Budget hit → checkpoint saved, no more links followed

2. Create `tests/test_resume_from_checkpoint.py`:
   - Test: Save checkpoint → restart spider → resumes from checkpoint
   - Test: Checkpoint loaded → checkpoint deleted
   - Test: Redis unavailable → graceful fallback

### Step 5: Documentation (15 minutes)
Update `IMPLEMENTATION.md`:
- Add Phase B completion status
- Document feature flag usage
- Add checkpoint debugging commands

---

## File Checklist

### New Files
- [ ] `storage/frontier_checkpoint.py`
- [ ] `tests/test_frontier_checkpoint.py`
- [ ] `tests/test_per_domain_budget.py`
- [ ] `tests/test_resume_from_checkpoint.py`

### Modified Files
- [ ] `crawler/spiders/discovery_spider.py` (budget enforcement, resume)
- [ ] `storage/domain_repository.py` (checkpoint field updates)
- [ ] `env_config.py` (feature flags)
- [ ] `IMPLEMENTATION.md` (Phase B documentation)

---

## Testing Strategy

### Unit Tests
```bash
pytest tests/test_frontier_checkpoint.py -v
pytest tests/test_per_domain_budget.py -v
```

### Integration Test (Requires Redis + DB)
```bash
# Set feature flag
export ENABLE_PER_DOMAIN_BUDGET=true

# Run crawl with 5 domains, 20 pages per domain
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=100

# Verify each domain got ~20 pages (100 / 5)
psql $DATABASE_URL -c "SELECT domain, pages_crawled FROM domains ORDER BY pages_crawled;"

# Force stop mid-crawl (Ctrl+C after ~50 pages)
# Check checkpoint saved
redis-cli KEYS "frontier:*"

# Resume crawl
scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=100

# Verify resume worked (checkpoints cleared, total pages ~200)
psql $DATABASE_URL -c "SELECT domain, pages_crawled, frontier_checkpoint_id FROM domains;"
```

---

## Rollback Plan

```bash
# Disable feature flag
export ENABLE_PER_DOMAIN_BUDGET=false

# Clear any stuck checkpoints
redis-cli KEYS "frontier:*" | xargs redis-cli DEL
psql $DATABASE_URL -c "UPDATE domains SET frontier_checkpoint_id = NULL, frontier_size = 0;"
```

---

## Success Metrics

- Crawl distribution is fair: variance ≤10% across domains
- Resume works: checkpoint loaded, crawl continues from saved URLs
- No Redis bloat: checkpoints deleted after load or expire after 30 days
- Performance: checkpoint save/load adds <2% overhead

---

## Critical Constraints

1. **Redis is optional dependency:** Graceful degradation if Redis unavailable
2. **Checkpoint TTL:** Must expire after 30 days to prevent Redis bloat
3. **Atomic checkpoint operations:** Use Redis pipelines for consistency
4. **No Phase C features:** Do NOT implement smart scheduling yet

---

**Ready for Phase B? Complete Phase A first, then start with Step 1 (Frontier Checkpoint Module).**

---
---

# Agent Task: Implement Smart Scheduling + Concurrency (Phase C)

**Status:** Ready after Phase B completion  
**Priority:** Critical  
**Estimated Complexity:** High  
**Prerequisites:** Phases A+B stable for ≥2 weeks, tested with multiple workers in staging

---

## Context

You are implementing Phase C of the domain tracking design. This is the **highest-risk phase** because it fundamentally changes how domains are selected and introduces multi-worker concurrency.

**Current Problem (Post-Phase B):**
- Seeds still drive scheduling (file order or Redis order)
- No feedback loop: bad domains get same priority as good ones
- Multiple workers can crawl same domain (duplicate work, rate limit violations)
- No claim/lease mechanism to prevent conflicts

**What Phase C Accomplishes:**
- **Smart scheduling:** Query `domains` table for best candidates (priority-based)
- **Concurrency-safe:** Domain claim/lease protocol prevents duplicate work
- **Self-optimizing:** Priority score uses yield, error rate, staleness
- **Distributed crawling:** Multiple workers can run safely
- **Resume-aware:** Active domains prioritized over pending (resume first)

**⚠️ DANGER ZONE:** Phase C workers **CANNOT** coexist with Phase A/B workers. You must deploy to ALL workers atomically or risk duplicate work.

---

## Implementation Scope (Phase C Only)

### ✅ IN SCOPE

1. **Domain Claim Repository**
   - Add to `storage/domain_repository.py`:
     - `claim_domains(worker_id, batch_size)` → list of claimed domains (uses `FOR UPDATE SKIP LOCKED`)
     - `renew_claim(domain_id, worker_id)` → extend lease by 30 minutes
     - `release_claim(domain_id, worker_id, expected_version, **updates)` → atomic release with stats update
     - `expire_stale_claims()` → cleanup task for stuck claims

2. **Priority Calculation**
   - New file: `storage/priority_calculator.py`
   - Function: `recalculate_priorities()` → executes SQL from §6.2
   - CLI command: `python -m crawler.cli recalculate-priorities`
   - Run after each crawl or on schedule (hourly)

3. **State Transition Function**
   - Migration: Add `transition_domain_status()` PL/pgSQL function (§7.2)
   - Use in spider for safe status transitions

4. **Spider Smart Scheduling**
   - Modify `start_requests()`:
     - **Phase C mode:** Query `domains` table via `claim_domains()` instead of reading seeds
     - Claim domains before crawling (lease = 30 minutes)
     - Resume from checkpoint if `frontier_checkpoint_id` exists
   - Add claim renewal heartbeat (background thread, every 10 minutes)
   - Modify `closed()`:
     - Release domain claim atomically with stats update
     - Use optimistic version locking

5. **Feature Flags (Critical)**
   - `ENABLE_SMART_SCHEDULING` (default: `false`) → query domains table for candidates
   - `ENABLE_CLAIM_PROTOCOL` (default: `false`) → claim domains before crawl
   - Both must be enabled together for Phase C

6. **CLI Extensions**
   - `python -m crawler.cli domain-status --status active --limit 50`
   - `python -m crawler.cli domain-info example.com`
   - `python -m crawler.cli recalculate-priorities`
   - `python -m crawler.cli release-stuck-claims` (cleanup util)

7. **Observability**
   - Metrics for active claims, expired claims, domain status distribution
   - Logging: claim acquisition, renewal, release, version conflicts

8. **Tests**
   - `tests/test_domain_claim.py` (claim protocol, lease expiry)
   - `tests/test_priority_calculation.py` (scoring formula)
   - `tests/test_smart_scheduling.py` (candidate selection)
   - `tests/test_concurrency.py` (multi-worker simulation)

### ❌ OUT OF SCOPE

- Refresh mode → Phase D
- Do NOT implement refresh spider yet
- Do NOT modify CLI seed ingestion (still works, populates domains table)

---

## Acceptance Criteria

### Must Pass (Functional)
1. ✅ Smart scheduling: domains selected by priority score, not file order
2. ✅ Claim protocol: only one worker claims each domain
3. ✅ Lease expiry: stuck claims auto-release after 30 minutes
4. ✅ Lease renewal: active crawls extend lease every 10 minutes
5. ✅ Optimistic locking: version conflicts detected and handled
6. ✅ Resume-aware: `active` domains prioritized over `pending`
7. ✅ Multi-worker test: 3 workers, 100 domains → no duplicate work
8. ✅ Canary deployment: single worker stable for 24 hours before full rollout
9. ✅ All existing tests pass
10. ✅ New tests pass (≥20 new tests)

### Must Pass (Safety)
1. ✅ Feature flags allow safe rollback without code change
2. ✅ Mixed-version check: Phase C detects and refuses to run with Phase A/B workers
3. ✅ Graceful degradation: if claim fails, log and skip (don't crash spider)
4. ✅ Version conflict handling: retry with exponential backoff (max 3 attempts)

---

## Implementation Steps (Suggested Order)

### Step 1: State Transition Function (30 minutes)
1. Create Alembic migration: `alembic revision -m "add_transition_domain_status_function"`
2. Add function from DOMAIN_TRACKING_DESIGN.md §7.2:
   ```sql
   CREATE OR REPLACE FUNCTION transition_domain_status(
       p_domain_id UUID,
       p_from_status domain_status,
       p_to_status domain_status,
       p_worker_id VARCHAR(255),
       p_expected_version INTEGER
   )
   RETURNS BOOLEAN AS $$
   -- (full function from design doc)
   ```
3. Test: `alembic upgrade head`

### Step 2: Domain Claim Repository (90 minutes)
1. Add to `storage/domain_repository.py`:
   ```python
   def claim_domains(worker_id: str, batch_size: int = 10) -> list[dict]:
       """Atomically claim unclaimed/expired domains using FOR UPDATE SKIP LOCKED."""
       with get_cursor() as cur:
           cur.execute("""
               WITH candidates AS (
                   SELECT id, version, domain, frontier_checkpoint_id
                   FROM domains
                   WHERE status IN ('pending', 'active')
                     AND (next_crawl_after IS NULL OR next_crawl_after < CURRENT_TIMESTAMP)
                     AND (claimed_by IS NULL OR claim_expires_at < CURRENT_TIMESTAMP)
                   ORDER BY
                       CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                       priority_score DESC,
                       last_crawled_at ASC NULLS FIRST
                   LIMIT %(batch_size)s
                   FOR UPDATE SKIP LOCKED
               )
               UPDATE domains
               SET claimed_by = %(worker_id)s,
                   claim_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 minutes',
                   version = version + 1
               FROM candidates
               WHERE domains.id = candidates.id
                 AND domains.version = candidates.version
               RETURNING domains.*;
           """, {'worker_id': worker_id, 'batch_size': batch_size})
           return [dict(row) for row in cur.fetchall()]
   
   def renew_claim(domain_id: str, worker_id: str) -> bool:
       """Renew domain claim (heartbeat)."""
       with get_cursor() as cur:
           cur.execute("""
               UPDATE domains
               SET claim_expires_at = CURRENT_TIMESTAMP + INTERVAL '30 minutes',
                   version = version + 1
               WHERE id = %(domain_id)s
                 AND claimed_by = %(worker_id)s
                 AND claim_expires_at > CURRENT_TIMESTAMP
               RETURNING id;
           """, {'domain_id': domain_id, 'worker_id': worker_id})
           return cur.rowcount > 0
   
   def release_claim(domain_id: str, worker_id: str, expected_version: int, **updates) -> bool:
       """Release domain claim with atomic stats update."""
       with get_cursor() as cur:
           set_clauses = ['claimed_by = NULL', 'claim_expires_at = NULL', 'version = version + 1']
           params = {'domain_id': domain_id, 'worker_id': worker_id, 'expected_version': expected_version}
           
           for key, value in updates.items():
               if key.endswith('_delta'):
                   base_key = key.replace('_delta', '')
                   set_clauses.append(f"{base_key} = {base_key} + %({key})s")
               else:
                   set_clauses.append(f"{key} = %({key})s")
               params[key] = value
           
           cur.execute(f"""
               UPDATE domains
               SET {', '.join(set_clauses)}
               WHERE id = %(domain_id)s
                 AND claimed_by = %(worker_id)s
                 AND version = %(expected_version)s
               RETURNING id;
           """, params)
           return cur.rowcount > 0
   ```

2. Add cleanup function:
   ```python
   def expire_stale_claims() -> int:
       """Expire claims > 30 minutes old. Returns count."""
       with get_cursor() as cur:
           cur.execute("""
               UPDATE domains
               SET claimed_by = NULL,
                   claim_expires_at = NULL
               WHERE claimed_by IS NOT NULL
                 AND claim_expires_at < CURRENT_TIMESTAMP
               RETURNING id;
           """)
           return cur.rowcount
   ```

### Step 3: Priority Calculator (45 minutes)
1. Create `storage/priority_calculator.py`:
   ```python
   def recalculate_priorities() -> dict[str, int]:
       """Recalculate priority scores for all domains. Returns stats."""
       from storage.db import get_cursor
       
       with get_cursor() as cur:
           # Execute SQL from DOMAIN_TRACKING_DESIGN.md §6.2
           cur.execute("""
               UPDATE domains SET
                   image_yield_rate = CASE 
                       WHEN pages_crawled > 0 THEN images_stored::DOUBLE PRECISION / pages_crawled 
                       ELSE NULL 
                   END,
                   -- (full SQL from design doc)
               WHERE status NOT IN ('blocked', 'unreachable');
           """)
           updated = cur.rowcount
       
       return {'updated': updated}
   ```

2. Add CLI command in `crawler/cli.py`:
   ```python
   @click.command()
   def recalculate_priorities():
       """Recalculate priority scores for all domains."""
       from storage.priority_calculator import recalculate_priorities
       stats = recalculate_priorities()
       click.echo(f"Updated {stats['updated']} domains")
   ```

### Step 4: Claim Renewal Heartbeat (60 minutes)
1. Add to `discovery_spider.py`:
   ```python
   import threading
   import time
   
   def __init__(self, ...):
       # ... existing init ...
       self.worker_id = f"{socket.gethostname()}-{os.getpid()}"
       self._claimed_domains: dict[str, dict] = {}  # domain_id -> {domain, version}
       self._heartbeat_thread = None
       self._stop_heartbeat = threading.Event()
   
   def spider_opened(self, spider):
       # Start heartbeat thread
       if get_enable_claim_protocol():
           self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
           self._heartbeat_thread.start()
   
   def _heartbeat_loop(self):
       """Background thread: renew claims every 10 minutes."""
       while not self._stop_heartbeat.is_set():
           time.sleep(600)  # 10 minutes
           
           for domain_id, info in list(self._claimed_domains.items()):
               try:
                   success = domain_repository.renew_claim(domain_id, self.worker_id)
                   if success:
                       self.logger.debug(f"Renewed claim for {info['domain']}")
                   else:
                       self.logger.warning(f"Failed to renew claim for {info['domain']} (expired?)")
                       del self._claimed_domains[domain_id]
               except Exception as e:
                   self.logger.error(f"Heartbeat error for {info['domain']}: {e}")
   
   def closed(self, reason):
       # Stop heartbeat
       self._stop_heartbeat.set()
       if self._heartbeat_thread:
           self._heartbeat_thread.join(timeout=5)
       
       # Release all claims
       for domain_id, info in self._claimed_domains.items():
           # (release logic in main closed() flow)
   ```

### Step 5: Smart Scheduling in Spider (120 minutes)
1. Modify `start_requests()`:
   ```python
   def start_requests(self):
       if get_enable_smart_scheduling() and get_enable_claim_protocol():
           # Phase C: Smart scheduling with claims
           claimed_domains = domain_repository.claim_domains(self.worker_id, batch_size=10)
           
           if not claimed_domains:
               self.logger.info("No domains available to claim")
               return
           
           self.logger.info(f"Claimed {len(claimed_domains)} domains")
           
           for domain_row in claimed_domains:
               self._claimed_domains[domain_row['id']] = {
                   'domain': domain_row['domain'],
                   'version': domain_row['version']
               }
               
               # Resume from checkpoint if exists
               if domain_row.get('frontier_checkpoint_id'):
                   checkpoint_urls = self._load_checkpoint(domain_row['frontier_checkpoint_id'])
                   for entry in checkpoint_urls:
                       yield Request(url=entry['url'], meta={'domain': domain_row['domain'], 'domain_id': domain_row['id']}, ...)
               else:
                   yield Request(url=f"https://{domain_row['domain']}", meta={'domain': domain_row['domain'], 'domain_id': domain_row['id']}, ...)
       else:
           # Phase A/B: Seed-driven (existing logic)
           self._upsert_domains_from_seeds()  # Phase A logic
           for seed in self._load_seeds():
               yield Request(url=seed, ...)
   ```

2. Modify `closed()` to release claims:
   ```python
   def closed(self, reason):
       # Release claims with stats
       for domain_id, info in self._claimed_domains.items():
           domain = info['domain']
           stats = self._domain_stats.get(domain, {})
           
           # Determine final status
           status = 'active'  # or 'exhausted', 'blocked' based on logic
           
           # Save checkpoint if needed
           checkpoint_id = None
           if status == 'active' and domain in self._domain_pending_urls:
               checkpoint_id = self._save_checkpoint(domain, self._domain_pending_urls[domain])
           
           # Atomic release with retries
           for attempt in range(3):
               try:
                   success = domain_repository.release_claim(
                       domain_id=domain_id,
                       worker_id=self.worker_id,
                       expected_version=info['version'],
                       pages_crawled_delta=stats.get('pages', 0),
                       images_found_delta=stats.get('images_found', 0),
                       images_stored_delta=stats.get('images_stored', 0),
                       status=status,
                       frontier_checkpoint_id=checkpoint_id,
                       frontier_size=len(self._domain_pending_urls.get(domain, [])),
                       last_crawl_run_id=self.crawl_run_id
                   )
                   
                   if success:
                       self.logger.info(f"Released claim for {domain}")
                       break
                   else:
                       self.logger.warning(f"Version conflict releasing {domain}, retry {attempt+1}")
                       info['version'] += 1  # Increment and retry
               except Exception as e:
                   self.logger.error(f"Failed to release claim for {domain}: {e}")
       
       # ... existing crawl_runs update ...
   ```

### Step 6: CLI Domain Commands (45 minutes)
Implement commands from DOMAIN_TRACKING_DESIGN.md §10.1

### Step 7: Comprehensive Testing (120 minutes)
1. Unit tests for claim logic
2. Multi-worker integration test (3 workers, 100 domains)
3. Lease expiry test (mock time, verify auto-release)
4. Version conflict simulation
5. Priority scoring validation

---

## File Checklist

### New Files
- [ ] `storage/priority_calculator.py`
- [ ] `tests/test_domain_claim.py`
- [ ] `tests/test_priority_calculation.py`
- [ ] `tests/test_smart_scheduling.py`
- [ ] `tests/test_concurrency.py`

### Modified Files
- [ ] `storage/domain_repository.py` (claim functions)
- [ ] `crawler/spiders/discovery_spider.py` (smart scheduling, heartbeat, claim release)
- [ ] `crawler/cli.py` (new commands)
- [ ] `env_config.py` (Phase C feature flags)
- [ ] New Alembic migration (transition function)
- [ ] `IMPLEMENTATION.md` (Phase C documentation)

---

## Testing Strategy

### Multi-Worker Test (Critical)
```bash
# Terminal 1
export ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true
scrapy crawl discovery -a max_pages=1000 &

# Terminal 2
export ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true
scrapy crawl discovery -a max_pages=1000 &

# Terminal 3
export ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true
scrapy crawl discovery -a max_pages=1000 &

# Wait for completion, then verify NO duplicate work
psql $DATABASE_URL -c "
    SELECT domain, COUNT(DISTINCT id) as claim_count
    FROM (
        SELECT domain, id FROM domains WHERE last_crawled_at > NOW() - INTERVAL '10 minutes'
    ) t
    GROUP BY domain
    HAVING COUNT(*) > 1;
"
# Should return 0 rows
```

---

## Deployment Protocol (Strict)

### Pre-Deployment
1. ✅ Phase A+B stable for ≥2 weeks
2. ✅ All tests passing in staging
3. ✅ Multi-worker test passed (no duplicate work)
4. ✅ Backfill complete (`domains` table fully populated)
5. ✅ Communication: notify all operators of deployment window

### Deployment Steps
1. **Stop ALL Phase A/B workers** (critical!)
2. Deploy Phase C code to all workers
3. Run priority recalculation: `python -m crawler.cli recalculate-priorities`
4. Enable flags on 1 worker (canary):
   ```bash
   export ENABLE_SMART_SCHEDULING=true ENABLE_CLAIM_PROTOCOL=true
   scrapy crawl discovery -a max_pages=1000
   ```
5. Monitor canary for 24 hours:
   - Check claim metrics
   - Verify no version conflicts
   - Verify no stuck claims
6. If stable: enable flags on all workers
7. If issues: rollback (see below)

---

## Rollback Plan

```bash
# 1. Disable feature flags on ALL workers
export ENABLE_SMART_SCHEDULING=false ENABLE_CLAIM_PROTOCOL=false

# 2. Release all active claims
psql $DATABASE_URL -c "UPDATE domains SET claimed_by = NULL, claim_expires_at = NULL WHERE claim_expires_at > CURRENT_TIMESTAMP;"

# 3. Workers revert to Phase B behavior (seed-driven)
# Seeds will populate domains table again via Phase A upsert logic
```

---

## Success Metrics

- Smart scheduling works: domains selected by priority (high-yield first)
- Concurrency-safe: 0 duplicate work with 3+ workers
- Lease management: 0 stuck claims after 1 hour
- Performance: claim overhead <3% vs Phase B

---

## Critical Constraints

1. **NO MIXED VERSIONS:** Phase C workers cannot coexist with Phase A/B workers
2. **Atomic claim operations:** Use `FOR UPDATE SKIP LOCKED` always
3. **Version locking:** Always check version in release
4. **Heartbeat mandatory:** Prevent lease expiry during long crawls
5. **Graceful degradation:** Claim failure must not crash spider

---

**Ready for Phase C? Complete Phases A+B, test in staging with multiple workers, then follow deployment protocol strictly.**
