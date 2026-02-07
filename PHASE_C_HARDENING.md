# Phase C Hardening: Queue Isolation + Resilience Fixes

**Status**: Design Proposal  
**Date**: 2026-02-07  
**Target**: Phase C (Smart Scheduling + Claim Protocol) reliability improvements

---

## Executive Summary

Phase C crawl validation revealed critical defects in the claim protocol implementation and crash resilience. Despite per-domain claims being correctly acquired via `FOR UPDATE SKIP LOCKED`, cross-worker domain overlap occurred in 21/58 domains due to shared Redis scheduler queues. Additional issues include double-counted stats on shutdown and complete progress loss on force-kill.

This proposal addresses all confirmed reliability gaps through 5 focused workstreams plus comprehensive tests, with phased rollout and zero risk to Phases A/B operation.

---

## Alignment to SYSTEM_DESIGN.md

### Goals Addressed

From [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md):

> **Separation Principle**: Each phase operates independently; switching modes does not break existing functionality.

> **Scheduling Priority**: Per-domain queues with separate priority lanes for discovery vs. refresh crawls.

> **Resumability**: The crawler must be able to stop and resume without losing meaningful progress.

> **Crawl Effectiveness**: Budget allocation matters. The system must get smarter over time about where to allocate crawl effort.

This proposal directly implements these principles:

- **Phase independence**: Changes apply only to Phase C; Phases A/B continue using shared Redis scheduler
- **Per-domain isolation**: Phase C switches to local scheduling, fulfilling "per-domain queues" requirement
- **Resumability**: Mid-crawl checkpointing ensures progress survives force-kill
- **Effectiveness**: Operator tools (force-release, stale-run cleanup) enable recovery from deadlocks

### Architecture Impact

Current flow (Phase C):
```
Redis Claim → Domain Lock Acquired → Spider Crawls Pages
                                          ↓
                              All Workers Share Redis Queue
                                          ↓
                              Worker B Processes Worker A's Domain Pages  ⚠️
```

Proposed flow (Phase C):
```
Redis Claim → Domain Lock Acquired → Spider Crawls Pages
                                          ↓
                              Local In-Memory Queue (Per Worker)
                                          ↓
                              Domain Confined to Claiming Worker ✓
```

**Key Design Decision**: Phase C uses local Scrapy scheduler; Phases A/B unchanged.

---

## Problem Summary (Validated 2026-02-07)

### Issue 1: Cross-Worker Domain Overlap (Critical)
**Symptom**: 21/58 domains crawled by multiple workers despite claim protocol  
**Root Cause**: Redis scheduler queue key `%(spider)s:requests` is shared per spider name, not per claim-owner  
**File**: [crawler/redis_keys.py](crawler/redis_keys.py#L20), [crawler/scheduler.py](crawler/scheduler.py#L200)  
**Impact**: Violates claim isolation guarantee; wastes budget on duplicate work; stats inconsistency

### Issue 2: Double-Counted Stats (High)
**Symptom**: Domain stats updated twice on graceful shutdown when claim protocol enabled  
**Root Cause**: `closed()` updates via `release_claim()` then again via generic `update_domain_stats()` loop  
**File**: [crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py#L937), [discovery_spider.py](crawler/spiders/discovery_spider.py#L1047)  
**Impact**: Inflated counters; terminal status overwritten (exhausted → active)

### Issue 3: Force-Kill Progress Loss (High)
**Symptom**: 9 crawl_runs stuck in 'running'; 1000 domains at pages_crawled=0 despite 4,592 crawl_log rows  
**Root Cause**: Stats only persisted in `closed()` callback; SIGKILL bypasses  
**File**: [crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py#L921)  
**Impact**: Wasted crawl time; operator restarts duplicate work; no visibility into in-flight progress

### Issue 4: No Force-Release for Active Claims (Medium)
**Symptom**: Cannot recover from dead workers without waiting 30+ minutes for lease expiry  
**Root Cause**: `expire_stale_claims()` only releases expired claims (`claim_expires_at < NOW()`)  
**File**: [storage/domain_repository.py](storage/domain_repository.py#L777)  
**Impact**: Operational delays; manual DB surgery required

### Issue 5: Stale crawl_runs Tracking (Low)
**Symptom**: 9 runs stuck in 'running' status; no automated cleanup  
**Root Cause**: No mechanism to mark stale runs as failed  
**Impact**: Metrics pollution; unclear operational state

---

## Proposed Solutions

### Workstream B: Fix Double-Count in closed() (Highest Priority)
**Urgency**: Immediate (data integrity)  
**Risk**: Low  
**Scope**: [crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py)

**Implementation**:
1. In `closed()`, track which domains were released via claim protocol
2. Skip generic `update_domain_stats()` loop for those domains
3. Keep fallback path for non-claimed domains (defensive)
4. Preserve terminal status from `release_claim()` (do not overwrite exhausted → active)

**Code Changes**:
```python
# In closed(), after _release_all_claims():
released_domains = set()  # Track claimed domains
if self.enable_claim_protocol and self._claimed_domains:
    released_domains = self._release_all_claims(domain_images_stored)

# Later, in generic update loop:
if self.enable_domain_tracking and self._domain_stats:
    for domain, stats in self._domain_stats.items():
        if domain in released_domains:
            continue  # Skip: already updated via release_claim()
        # ... existing update logic for non-claimed domains
```

**Acceptance Criteria**:
- [ ] One graceful run increments each domain counter exactly once
- [ ] Domains released as exhausted remain exhausted (not overwritten to active)
- [ ] Test: `test_smart_scheduling.py::test_no_double_count_on_close()`

---

### Workstream A: Queue/Claim Isolation (Critical)
**Urgency**: High (concurrency correctness)  
**Risk**: Medium (scheduler behavior change)  
**Scope**: [crawler/settings.py](crawler/settings.py), [crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py), [env_config.py](env_config.py)

**Implementation Strategy**:

**Phase C (smart scheduling + claim protocol enabled)**:
- Switch to local Scrapy scheduler (in-memory queue)
- Disable Redis scheduler entirely for this mode
- Retain checkpoint/resume via DB + Redis frontier checkpoints (already implemented)

**Phase A/B (no claims)**:
- Continue using Redis scheduler (shared queue)
- No behavior change

**Code Changes**:

1. **settings.py**: Conditional scheduler selection
```python
# Phase C: Use local scheduler when claims enabled (per-worker queue)
# Phase A/B: Use Redis scheduler (shared queue)
SCHEDULER = (
    "scrapy.core.scheduler.Scheduler"  # Local scheduler
    if os.getenv("ENABLE_SMART_SCHEDULING") == "true"
       and os.getenv("ENABLE_CLAIM_PROTOCOL") == "true"
    else "scrapy_redis.scheduler.Scheduler"  # Redis scheduler
)
```

2. **discovery_spider.py**: Add startup validation
```python
def __init__(self, *args, **kwargs):
    # ... existing init ...
    
    # Phase C validation: Claim protocol requires smart scheduling
    if self.enable_claim_protocol and not self.enable_smart_scheduling:
        raise ValueError(
            "ENABLE_CLAIM_PROTOCOL requires ENABLE_SMART_SCHEDULING=true"
        )
    
    # Log effective scheduling mode for operator awareness
    scheduler_mode = (
        "local (per-worker queue)"
        if self.enable_smart_scheduling and self.enable_claim_protocol
        else "Redis (shared queue)"
    )
    self.logger.info(f"Scheduler mode: {scheduler_mode}")
```

**Acceptance Criteria**:
- [ ] Multi-worker Phase C canary: no domain has `COUNT(DISTINCT crawl_run_id) > 1` in same time window
- [ ] Claims still distribute across workers with no deadlocks
- [ ] Phase A/B workers continue using Redis scheduler (no regression)
- [ ] Startup fails fast if claim protocol enabled without smart scheduling
- [ ] Test: `test_concurrency.py::test_phase_c_domain_isolation()`

**Risk Mitigation**:
- Local scheduler loses global queue visibility → mitigated by DB-driven candidate selection (already implemented)
- Worker restart loses in-flight queue → mitigated by frontier checkpoints (already implemented)
- Rollback: Set `ENABLE_CLAIM_PROTOCOL=false` to restore shared Redis scheduler

---

### Workstream D: Force-Release Claims CLI (Operational Recovery)
**Urgency**: Medium (enables recovery from deadlocks)  
**Risk**: Low  
**Scope**: [crawler/cli.py](crawler/cli.py), [storage/domain_repository.py](storage/domain_repository.py)

**Implementation**:
Extend `release-stuck-claims` command with operator-controlled force-release:

```python
@click.group()
def cli():
    pass

@cli.command()
@click.option("--dry-run", is_flag=True, help="Preview without releasing")
@click.option("--force", is_flag=True, help="Release active (non-expired) claims")
@click.option("--worker-id", help="Release claims for specific worker")
@click.option("--all-active", is_flag=True, help="Release all active claims (requires --force)")
def release_stuck_claims(dry_run, force, worker_id, all_active):
    """Release stuck or expired domain claims."""
    
    # Safety: --all-active requires explicit --force
    if all_active and not force:
        click.echo("Error: --all-active requires --force flag")
        return 1
    
    # Default behavior: expired claims only (backward compatible)
    if not force:
        count = expire_stale_claims()
        click.echo(f"Released {count} expired claims")
        return
    
    # Force-release logic
    if worker_id:
        # Targeted: release specific worker's claims
        rows = preview_claims_by_worker(worker_id)
        click.echo(f"Would release {len(rows)} claims for worker {worker_id}")
        if not dry_run and click.confirm("Proceed?"):
            force_release_worker_claims(worker_id)
    elif all_active:
        # Global: release all claims
        total = preview_all_active_claims()
        click.echo(f"Would release {total} active claims across all workers")
        if not dry_run and click.confirm("Proceed with global release?"):
            force_release_all_claims()
```

**Storage API Addition** ([storage/domain_repository.py](storage/domain_repository.py)):
```python
def force_release_worker_claims(worker_id: str) -> int:
    """Force-release all claims for a specific worker (active or expired)."""
    with get_cursor() as cur:
        cur.execute("""
            UPDATE domains
            SET claimed_by = NULL, claim_expires_at = NULL
            WHERE claimed_by = %s
            RETURNING id
        """, (worker_id,))
        return cur.rowcount

def force_release_all_claims() -> int:
    """Force-release all active claims (emergency recovery)."""
    with get_cursor() as cur:
        cur.execute("""
            UPDATE domains
            SET claimed_by = NULL, claim_expires_at = NULL
            WHERE claimed_by IS NOT NULL
            RETURNING id
        """)
        return cur.rowcount
```

**Acceptance Criteria**:
- [ ] Default behavior unchanged (expired claims only)
- [ ] `--force --worker-id <id>` releases specific worker's claims
- [ ] `--force --all-active` requires explicit confirmation
- [ ] `--dry-run` previews without modification
- [ ] Test: `test_domain_claim.py::test_force_release_cli()`

---

### Workstream E: Stale crawl_runs Cleanup (Operational Hygiene)
**Urgency**: Low (metrics quality)  
**Risk**: Low  
**Scope**: [crawler/cli.py](crawler/cli.py)

**Implementation**:
```python
@cli.command()
@click.option("--older-than-minutes", default=60, help="Mark runs stale after N minutes")
@click.option("--dry-run", is_flag=True, help="Preview without marking")
def cleanup_stale_runs(older_than_minutes, dry_run):
    """Mark stale crawl_runs as failed."""
    
    with get_cursor() as cur:
        # Find stale runs: no recent activity and still 'running'
        cur.execute("""
            WITH run_activity AS (
                SELECT cr.id, cr.started_at,
                       COALESCE(MAX(cl.crawled_at), cr.started_at) AS last_activity
                FROM crawl_runs cr
                LEFT JOIN crawl_log cl ON cl.crawl_run_id = cr.id
                WHERE cr.status = 'running'
                GROUP BY cr.id, cr.started_at
            )
            SELECT id, started_at, last_activity
            FROM run_activity
            WHERE last_activity < CURRENT_TIMESTAMP - INTERVAL '%s minutes'
        """ % older_than_minutes)
        
        stale_runs = cur.fetchall()
        
        if not stale_runs:
            click.echo("No stale runs found")
            return
        
        click.echo(f"Found {len(stale_runs)} stale runs:")
        for run_id, started, last_activity in stale_runs:
            click.echo(f"  Run {run_id}: started {started}, last activity {last_activity}")
        
        if dry_run:
            return
        
        if not click.confirm("Mark these runs as failed?"):
            return
        
        # Mark as failed
        run_ids = [r[0] for r in stale_runs]
        cur.execute("""
            UPDATE crawl_runs
            SET status = 'failed',
                completed_at = CURRENT_TIMESTAMP,
                error_message = 'Marked stale: no activity timeout'
            WHERE id = ANY(%s)
        """, (run_ids,))
        
        click.echo(f"Marked {cur.rowcount} runs as failed")
```

**Acceptance Criteria**:
- [ ] Stale runs (no activity > threshold) marked as failed
- [ ] Active runs (recent crawl_log activity) not touched
- [ ] `--dry-run` safe preview
- [ ] Test: `test_cli.py::test_cleanup_stale_runs()`

---

### Workstream C: Mid-Crawl State Flushing (Crash Resilience)
**Urgency**: Medium (progress visibility)  
**Risk**: Medium (performance impact from frequent DB writes)  
**Scope**: [crawler/spiders/discovery_spider.py](crawler/spiders/discovery_spider.py), [storage/domain_repository.py](storage/domain_repository.py), [env_config.py](env_config.py)

**Implementation Strategy**:

**Goal**: Persist per-domain progress every N pages to survive force-kill without losing all work.

**Design**:
1. Add `DOMAIN_STATS_FLUSH_INTERVAL_PAGES` env var (default: 100, 0=disabled)
2. Track per-domain flushed vs. unflushed deltas in spider memory
3. On flush threshold, write deltas to DB immediately
4. Update `crawl_runs.pages_crawled/images_found/images_downloaded` incrementally
5. On `closed()`, persist only unflushed remainder

**Code Changes**:

1. **env_config.py**: New config
```python
def get_domain_stats_flush_interval() -> int:
    """Get periodic domain stats flush interval (0 = disabled)."""
    return int(os.getenv("DOMAIN_STATS_FLUSH_INTERVAL_PAGES", "100"))
```

2. **discovery_spider.py**: Flush logic
```python
class DiscoverySpider(Spider):
    def __init__(self, *args, **kwargs):
        # ... existing init ...
        self.flush_interval = get_domain_stats_flush_interval()
        self._domain_flushed_stats = {}  # Track last flushed values
    
    def parse(self, response: Response) -> Iterator[...]:
        # ... existing parse logic ...
        
        # After updating _domain_stats for this page:
        if self.flush_interval > 0 and self.enable_domain_tracking:
            self._maybe_flush_domain_stats(domain)
    
    def _maybe_flush_domain_stats(self, domain: str) -> None:
        """Flush domain stats to DB if threshold reached."""
        canonical = canonicalize_domain(domain, self.strip_subdomains)
        
        stats = self._domain_stats.get(canonical, {})
        flushed = self._domain_flushed_stats.get(canonical, {"pages": 0, "images_found": 0})
        
        unflushed_pages = stats.get("pages", 0) - flushed["pages"]
        
        if unflushed_pages < self.flush_interval:
            return  # Below threshold
        
        # Compute deltas since last flush
        pages_delta = stats.get("pages", 0) - flushed["pages"]
        images_delta = stats.get("images_found", 0) - flushed["images_found"]
        errors_delta = stats.get("errors", 0) - flushed.get("errors", 0)
        
        try:
            # Phase C: Use claim-safe incremental update
            if self.enable_claim_protocol:
                domain_id = self._claimed_domains.get(canonical, {}).get("domain_id")
                if domain_id:
                    increment_domain_stats_claimed(
                        domain_id=domain_id,
                        pages_crawled_delta=pages_delta,
                        images_found_delta=images_delta,
                        total_error_count_delta=errors_delta,
                        crawl_run_id=self.crawl_run_id
                    )
            else:
                # Phase A/B: Use standard update
                update_domain_stats(
                    domain=canonical,
                    pages_crawled_delta=pages_delta,
                    images_found_delta=images_delta,
                    total_error_count_delta=errors_delta,
                    status="active"
                )
            
            # Update flushed counters
            self._domain_flushed_stats[canonical] = {
                "pages": stats.get("pages", 0),
                "images_found": stats.get("images_found", 0),
                "errors": stats.get("errors", 0)
            }
            
            # Increment run counters incrementally
            if self.crawl_run_id:
                increment_crawl_run_stats(
                    self.crawl_run_id, pages_delta, images_delta
                )
            
        except Exception as e:
            self.logger.warning(f"Failed to flush stats for {domain}: {e}")
    
    def closed(self, reason: str) -> None:
        # ... existing closed logic ...
        
        # Only persist UNFLUSHED remainder in final update
        for domain, stats in self._domain_stats.items():
            flushed = self._domain_flushed_stats.get(domain, {"pages": 0, "images_found": 0})
            remaining_pages = stats.get("pages", 0) - flushed["pages"]
            remaining_images = stats.get("images_found", 0) - flushed["images_found"]
            # ... update with remaining_* deltas only
```

3. **domain_repository.py**: New claim-safe incremental API
```python
def increment_domain_stats_claimed(
    domain_id: int,
    pages_crawled_delta: int,
    images_found_delta: int,
    total_error_count_delta: int,
    crawl_run_id: int | None = None
) -> bool:
    """Increment domain stats for a CLAIMED domain (optimistic locking).
    
    Args:
        domain_id: Domain ID (from claim)
        pages_crawled_delta: Pages to add
        images_found_delta: Images to add
        total_error_count_delta: Errors to add
        crawl_run_id: Current crawl run ID
    
    Returns:
        True if update succeeded, False if claim lost (version conflict)
    """
    try:
        with get_cursor() as cur:
            cur.execute("""
                UPDATE domains
                SET pages_crawled = pages_crawled + %s,
                    images_found = images_found + %s,
                    total_error_count = total_error_count + %s,
                    last_crawled_at = CURRENT_TIMESTAMP,
                    last_crawl_run_id = COALESCE(%s, last_crawl_run_id)
                WHERE id = %s
                  AND claimed_by IS NOT NULL  -- Safety: only claimed domains
                RETURNING id
            """, (
                pages_crawled_delta,
                images_found_delta,
                total_error_count_delta,
                str(crawl_run_id) if crawl_run_id else None,
                domain_id
            ))
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to increment stats for domain {domain_id}: {e}")
        return False

def increment_crawl_run_stats(
    crawl_run_id: int, pages_delta: int, images_delta: int
) -> None:
    """Increment crawl_run stats incrementally (for mid-crawl flushing)."""
    try:
        with get_cursor() as cur:
            cur.execute("""
                UPDATE crawl_runs
                SET pages_crawled = pages_crawled + %s,
                    images_found = images_found + %s
                WHERE id = %s
            """, (pages_delta, images_delta, crawl_run_id))
    except Exception as e:
        logger.error(f"Failed to increment run stats: {e}")
```

**Acceptance Criteria**:
- [ ] Simulated SIGKILL shows non-zero `domains.pages_crawled` for active domains
- [ ] Counters monotonic: no double-counting after restart + close
- [ ] Performance: flush overhead < 5% of total crawl time (measure via profiling)
- [ ] Test: `test_domain_tracking_integration.py::test_mid_crawl_flush()`
- [ ] Test: `test_domain_tracking_integration.py::test_force_kill_recovery()`

**Risk Mitigation**:
- **Performance**: Default 100-page interval limits DB writes to ~1 per domain per minute at 2 pages/sec
- **Double-counting**: Track flushed vs. unflushed deltas explicitly
- **Rollback**: Set `DOMAIN_STATS_FLUSH_INTERVAL_PAGES=0` to disable
- **Claim safety**: New API checks `claimed_by IS NOT NULL` before update

---

### Workstream F: Tests and Verification
**Urgency**: Critical (regression prevention)  
**Risk**: Low  
**Scope**: [tests/test_smart_scheduling.py](tests/test_smart_scheduling.py), [tests/test_domain_claim.py](tests/test_domain_claim.py), [tests/test_concurrency.py](tests/test_concurrency.py), new [tests/test_cli.py](tests/test_cli.py)

**Test Coverage**:

1. **test_smart_scheduling.py**:
   - `test_no_double_count_on_close()`: Verify stats applied once per domain
   - `test_exhausted_status_preserved()`: Terminal status not overwritten

2. **test_concurrency.py**:
   - `test_phase_c_domain_isolation()`: Multi-worker, no domain overlap
   - `test_scheduler_mode_validation()`: Startup fails if claim protocol without smart scheduling

3. **test_domain_claim.py**:
   - `test_force_release_worker_claims()`: Targeted force-release
   - `test_force_release_all_claims()`: Global force-release
   - `test_incremental_stats_update()`: Claim-safe incremental API

4. **test_domain_tracking_integration.py**:
   - `test_mid_crawl_flush()`: Stats persisted at flush interval
   - `test_force_kill_recovery()`: Non-zero progress after simulated SIGKILL

5. **tests/test_cli.py** (new):
   - `test_release_stuck_claims_force()`: CLI force-release options
   - `test_cleanup_stale_runs()`: Stale run detection and marking

**Test Data**:
- Use `tests/conftest.py` fixtures for DB setup
- Synthetic HTML pages via `tests/fixtures.py`
- Mock Redis client for checkpoint testing

**Acceptance Gate**: All tests pass before rollout:
```bash
pytest tests/ --cov=crawler --cov=processor --cov-report=term-missing
mypy crawler/ processor/ storage/
ruff check .
```

---

## Rollout Plan

**Validation Resources**:
- Queries: [scripts/phase_c_validation.sql](scripts/phase_c_validation.sql)
- Evidence template: [scripts/phase_c_hardening_validation_template.md](scripts/phase_c_hardening_validation_template.md)

### Phase 1: Canary (Single Worker, 30-60 minutes)
**Config**:
```bash
ENABLE_SMART_SCHEDULING=true
ENABLE_CLAIM_PROTOCOL=true
DOMAIN_STATS_FLUSH_INTERVAL_PAGES=100
```

**Success Criteria**:
- [ ] No scheduler errors or deadlocks
- [ ] Domain claims acquired and released correctly
- [ ] Mid-crawl flush writes visible in DB during run
- [ ] Graceful shutdown: stats applied once, terminal status preserved

**Verification**:
```sql
-- No cross-worker overlap (single worker, should be 0)
SELECT domain, COUNT(DISTINCT crawl_run_id)
FROM crawl_log
WHERE crawled_at > NOW() - INTERVAL '1 hour'
GROUP BY domain
HAVING COUNT(DISTINCT crawl_run_id) > 1;

-- Mid-crawl progress visible
SELECT domain, pages_crawled, images_stored
FROM domains
WHERE last_crawled_at > NOW() - INTERVAL '1 hour'
  AND pages_crawled > 0;
```

### Phase 2: Small Scale (2-3 Workers, 1-2 hours)
**Config**: Same as Phase 1

**Success Criteria**:
- [ ] Claims distribute across workers
- [ ] Zero domain overlap (each domain in only one run per window)
- [ ] No claim timeouts or version conflicts

**Verification**:
```sql
-- Domain overlap check (multi-worker)
SELECT domain, COUNT(DISTINCT crawl_run_id) AS run_count
FROM crawl_log
WHERE crawled_at > NOW() - INTERVAL '2 hours'
GROUP BY domain
HAVING COUNT(DISTINCT crawl_run_id) > 1;
-- Expected: 0 rows
```

### Phase 3: Full Scale (8 Workers)
**Config**: Same as Phase 1

**Success Criteria**:
- [ ] Linear throughput scaling (8 workers ≈ 8x pages/min vs 1 worker)
- [ ] No stale claims or runs after 4+ hours
- [ ] Operator tools functional (`release-stuck-claims --force`, `cleanup-stale-runs`)

**Verification**:
```sql
-- Throughput check
SELECT
    DATE_TRUNC('minute', crawled_at) AS minute,
    COUNT(*) AS pages_per_minute
FROM crawl_log
WHERE crawled_at > NOW() - INTERVAL '10 minutes'
GROUP BY minute
ORDER BY minute DESC;

-- Stale run check
SELECT id, started_at, status
FROM crawl_runs
WHERE status = 'running'
  AND started_at < NOW() - INTERVAL '1 hour';
-- Expected: 0 rows (or cleanable via CLI)
```

### Emergency Rollback
**If critical issues occur at any phase**:
1. Stop all workers: `CTRL+C` (graceful)
2. Disable Phase C:
   ```bash
   ENABLE_CLAIM_PROTOCOL=false
   ENABLE_SMART_SCHEDULING=false  # Or set to false if claim-dependent
   ```
3. Clean up stale claims:
   ```bash
   python crawler/cli.py release-stuck-claims --force --all-active
   python crawler/cli.py cleanup-stale-runs --older-than-minutes 5
   ```
4. Resume in Phase A/B mode (shared Redis scheduler)

**Rollback Safety**: Phases A/B operation unchanged; can revert to pre-hardening behavior instantly.

---

## Risk Assessment

### High Risk
- **Workstream A (Queue Isolation)**: Scheduler behavior change; mitigated by Phase A/B unchanged + rollback path

### Medium Risk
- **Workstream C (Mid-Crawl Flush)**: Performance impact from frequent DB writes; mitigated by configurable interval (default 100 pages ≈ 1 write/min per domain)

### Low Risk
- **Workstream B (Double-Count Fix)**: Logic-only change; no external dependencies
- **Workstream D (Force-Release CLI)**: Operator tool; no automated execution
- **Workstream E (Stale Run Cleanup)**: Operator tool; dry-run required

### Mitigations
- **All changes gated by environment flags**: Instant rollback via config
- **Backward compatibility preserved**: Phases A/B continue working
- **Phased rollout**: Canary → small scale → full scale with verification gates
- **Comprehensive tests**: 225+ existing tests + new coverage for all fixes
- **Operator recovery tools**: CLI commands for manual intervention when needed

---

## Open Questions for Human Review

1. **Flush interval tuning**: Is 100 pages a reasonable default, or should it be domain-dependent (e.g., higher for image-rich domains)?

2. **Incremental run stats**: Should `crawl_runs.pages_crawled` be incremented mid-crawl, or only at close? (Proposal: incremental for visibility)

3. **Force-release safety**: Should `--force --all-active` require a second confirmation prompt, or is one sufficient?

4. **Stale run threshold**: 60 minutes default reasonable, or should it be shorter (30 min) / longer (120 min)?

5. **Phase C scheduler strategy**: Is local scheduler acceptable, or prefer custom Redis queue per claim-owner? (Local is simpler; custom queue adds complexity)

---

## Migration Strategy

### Database Changes
**None required**. All changes are application-level logic and configuration.

### Configuration Changes
**New environment variables**:
- `DOMAIN_STATS_FLUSH_INTERVAL_PAGES` (default: 100)

**Modified behavior** (Phase C only):
- Scheduler switches from Redis to local when `ENABLE_SMART_SCHEDULING=true` and `ENABLE_CLAIM_PROTOCOL=true`

### Deployment Steps
1. Deploy code to staging environment
2. Run full test suite (`pytest tests/`)
3. Start canary worker with Phase C config
4. Monitor for 30-60 minutes, verify success criteria
5. Scale to 2-3 workers, monitor 1-2 hours
6. Scale to 8 workers, monitor 4+ hours
7. If all gates pass, promote to production

### Rollback Plan
- Stop workers (graceful or force-kill)
- Set `ENABLE_CLAIM_PROTOCOL=false` and `ENABLE_SMART_SCHEDULING=false`
- Clean up stale claims/runs via CLI
- Restart in Phase A/B mode

---

## Success Metrics

### Correctness
- [ ] Zero domain overlap in multi-worker Phase C (validation query returns 0 rows)
- [ ] Domain stats applied exactly once per graceful shutdown
- [ ] Terminal status (exhausted, blocked) preserved after release

### Resilience
- [ ] Non-zero `domains.pages_crawled` visible during active crawl (mid-crawl flush working)
- [ ] Force-killed worker: progress recovered on restart (no duplicate work)
- [ ] Stale claims/runs recoverable via CLI without manual DB edits

### Performance
- [ ] Flush overhead < 5% of total crawl time (measure via profiling)
- [ ] Linear throughput scaling: 8 workers ≈ 8x pages/min vs 1 worker

### Operational
- [ ] Operator can force-release claims for dead workers within 1 minute
- [ ] Stale runs cleanable via CLI with dry-run preview

---

## Timeline Estimate

- **Workstream B (Double-Count Fix)**: 2 hours (simple logic change)
- **Workstream D (Force-Release CLI)**: 2 hours (CLI + storage API)
- **Workstream E (Stale Run Cleanup)**: 1 hour (CLI only)
- **Workstream A (Queue Isolation)**: 3 hours (scheduler config + validation)
- **Workstream C (Mid-Crawl Flush)**: 4 hours (flush logic + claim-safe API)
- **Workstream F (Tests)**: 4 hours (5 new tests + fixtures)
- **Documentation Updates**: 1 hour

**Total Development**: ~17 hours  
**Testing + Rollout**: ~4 hours  
**Overall**: ~21 hours (3 engineer-days)

---

## Appendix: Execution Order Rationale

**Recommended sequence**:
1. **Workstream B** (double-count): Immediate data integrity fix, no dependencies
2. **Workstream A** (queue isolation): Fixes critical concurrency bug, enables safe multi-worker Phase C
3. **Workstream D + E** (operator tools): Enables recovery from deadlocks/stale state
4. **Workstream C** (mid-crawl flush): Adds resilience on top of stable concurrency foundation
5. **Workstream F** (tests): Regression prevention for all above fixes

**Rationale**: Fix data integrity first (B), then concurrency correctness (A), then operational recovery (D+E), then crash resilience (C), then lock in with tests (F).

---

**Approval Required**: Architectural changes per AGENTS.md guardrails. Please review alignment to SYSTEM_DESIGN.md, risk assessment, and rollout strategy before implementation.
