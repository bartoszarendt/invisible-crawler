-- Phase C Hardening Validation Queries
--
-- Replace interval values and crawl_run_id filters as needed for each canary phase.

-- 1) Cross-worker overlap check (domains touched by multiple crawl runs in window)
SELECT
    domain,
    COUNT(DISTINCT crawl_run_id) AS run_count,
    COUNT(*) AS page_count
FROM crawl_log
WHERE crawled_at >= NOW() - INTERVAL '30 minutes'
GROUP BY domain
HAVING COUNT(DISTINCT crawl_run_id) > 1
ORDER BY run_count DESC, page_count DESC;

-- 2) Active claim inventory (sanity check)
SELECT
    claimed_by,
    COUNT(*) AS claim_count,
    MIN(claim_expires_at) AS earliest_expiry,
    MAX(claim_expires_at) AS latest_expiry
FROM domains
WHERE claimed_by IS NOT NULL
  AND claim_expires_at > CURRENT_TIMESTAMP
GROUP BY claimed_by
ORDER BY claim_count DESC;

-- 3) Stale crawl runs (running but no recent activity)
SELECT
    r.id,
    r.started_at,
    COALESCE(MAX(l.crawled_at), r.started_at) AS last_activity_at,
    r.mode,
    r.status
FROM crawl_runs r
LEFT JOIN crawl_log l ON l.crawl_run_id = r.id
WHERE r.status = 'running'
GROUP BY r.id, r.started_at, r.mode, r.status
HAVING COALESCE(MAX(l.crawled_at), r.started_at) < NOW() - INTERVAL '60 minutes'
ORDER BY last_activity_at ASC;

-- 4) In-flight visibility (domains with recent progress and active claims)
SELECT
    d.domain,
    d.pages_crawled,
    d.images_found,
    d.last_crawled_at,
    d.claimed_by,
    d.claim_expires_at
FROM domains d
WHERE d.claimed_by IS NOT NULL
  AND d.last_crawled_at >= NOW() - INTERVAL '30 minutes'
ORDER BY d.last_crawled_at DESC;

-- 5) Throughput trend (pages and images per minute)
SELECT
    date_trunc('minute', crawled_at) AS minute_bucket,
    COUNT(*) AS pages_crawled,
    COALESCE(SUM(images_found), 0) AS images_found
FROM crawl_log
WHERE crawled_at >= NOW() - INTERVAL '60 minutes'
GROUP BY minute_bucket
ORDER BY minute_bucket ASC;

-- 6) Optional flush overhead proxy (requires pg_stat_user_tables)
-- Capture this snapshot before and after a canary window to estimate update rates.
SELECT
    relname,
    n_tup_upd,
    n_tup_ins,
    n_tup_del
FROM pg_stat_user_tables
WHERE relname IN ('domains', 'crawl_log')
ORDER BY relname;
