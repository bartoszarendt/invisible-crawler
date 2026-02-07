# Phase C Hardening Validation Evidence

**Date:** YYYY-MM-DD
**Environment:** dev / staging / prod
**Crawl Profile:** conservative / broad
**Worker Count:** 1 / 2-3 / 8
**Run Window:** HH:MM - HH:MM (timezone)
**ENABLE_SMART_SCHEDULING:** true/false
**ENABLE_CLAIM_PROTOCOL:** true/false
**DOMAIN_STATS_FLUSH_INTERVAL_PAGES:** 100

---

## Canary Plan

- Phase 1: 1 worker (baseline)
- Phase 2: 2-3 workers (concurrency)
- Phase 3: 8 workers (stress)

---

## Evidence: Cross-Worker Overlap

Paste output from scripts/phase_c_validation.sql query (1).

---

## Evidence: Active Claims Inventory

Paste output from scripts/phase_c_validation.sql query (2).

---

## Evidence: Stale Runs

Paste output from scripts/phase_c_validation.sql query (3).

---

## Evidence: In-Flight Visibility

Paste output from scripts/phase_c_validation.sql query (4).

---

## Evidence: Throughput Trend

Paste output from scripts/phase_c_validation.sql query (5).

---

## Evidence: Flush Overhead Proxy

Paste output from scripts/phase_c_validation.sql query (6) and note the delta.

---

## Notes / Anomalies

- 

---

## Verdict

- Pass / Fail
- Rationale:
