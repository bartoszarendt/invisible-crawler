# Crawler Architecture for OneMark

## 1. Purpose of This Document

This document defines the **system design and architectural foundations** for the OneMark Web Crawler.

Its role is to serve as:
- a **shared mental model** for all contributors,
- a **starting point for implementation**,
- a **stable reference** that can evolve as the system scales.

The crawler is designed to support the OneMark / InvisibleID ecosystem by building and continuously expanding a large-scale, self-hosted index of images discovered across the public web.

---

## 2. High-Level Goals

### Primary Goals
- Discover and fetch image assets from the public web at scale
- Build a continuously growing image corpus
- Generate stable fingerprints for each image
- Store provenance and historical metadata
- Prepare the system for future InvisibleID detection

### Explicit Non-Goals (Initial Phase)
- No watermark / InvisibleID detection in Phase 1
- No JavaScript-heavy rendering by default
- No dependence on external crawling SaaS
- No real-time enforcement or takedown logic

---

## 3. Core Design Principles

### 3.1 Separation of Concerns

The crawler is a **standalone service**, independent from InvisibleID encoding/detection.

Benefits:
- Independent development velocity
- Clear operational boundaries
- Easier scaling and fault isolation
- Ability to evolve crawl strategy without touching core IP

### 3.2 Incremental Complexity

The system is intentionally designed to:
- start simple (discovery + fetch + fingerprint),
- evolve into more advanced detection and similarity systems later.

### 3.3 Self-Hosted & Auditable

All components must be:
- deployable on owned infrastructure,
- observable and auditable,
- replaceable without vendor lock-in.

---

## 4. System Overview

### 4.1 Logical Architecture

```
Seed Sources
   ↓
URL Frontier (Scheduler / Queue)
   ↓
Scrapy Spiders (Discovery)
   ↓
Image Fetching
   ↓
Normalization & Fingerprinting
   ↓
Metadata & Index Storage
   ↓
(Future) InvisibleID Detection
```

---

## 5. Seed Domains & Discovery Strategy

### 5.1 Seed Sources

Initial crawl seeds are derived from **static, reproducible datasets**, not live services.

Recommended sources:
- Tranco Top Sites (primary backbone)
- Majestic Million (content-heavy domains)
- Curated media & e-commerce domain lists
- Country- or language-specific domain subsets

Seeds are versioned and stored locally.

### 5.2 Discovery Modes

Two complementary crawl modes are defined:

#### Discovery Crawl
- Purpose: find new pages and new images
- Triggered less frequently
- Allowed to follow in-domain links and selected out-of-domain links

#### Refresh Crawl
- Purpose: detect changes and new assets on known pages
- Triggered frequently
- Limited scope and lower cost

---

## 6. URL Frontier & Scheduling

### 6.1 Frontier Responsibilities

The URL frontier manages:
- URL deduplication
- Crawl prioritization
- Per-domain rate limiting
- Crawl depth policies

### 6.2 Scheduling Strategy

- Per-domain queues
- Separate priority lanes for discovery vs refresh
- Backoff strategies for errors and rate limits

Redis-backed scheduling is recommended for early phases.

---

## 7. Crawling & Parsing Layer (Scrapy)

### 7.1 Why Scrapy

Scrapy is used as the core crawling framework because it:
- is battle-tested at scale
- provides fine-grained crawl control
- integrates naturally with pipelines and queues

### 7.2 Page Parsing

From each HTML page, the crawler extracts:
- `<img src>` and `srcset`
- `<picture>` sources
- `meta[property="og:image"]`
- basic CSS background-image URLs (non-rendered)

JavaScript execution is intentionally avoided in Phase 1.

---

## 8. Image Fetching & Normalization

### 8.1 Fetching Rules

Images are fetched only if:
- content-type is whitelisted (image/*)
- size exceeds a minimum threshold
- URL passes deduplication checks

### 8.2 Normalization Pipeline

Each fetched image is normalized to ensure stable downstream processing:
- decode and re-encode to a canonical format
- strip EXIF and metadata
- generate standardized resized variants
- normalize colorspace if required

Normalized versions are used for fingerprinting, originals may be retained optionally.

---

## 9. Fingerprinting Strategy

### 9.1 Binary Hashing

For exact deduplication:
- SHA-256 hash of original binary

### 9.2 Perceptual Hashing

For similarity detection and future matching:
- pHash / dHash / aHash (configurable)
- hashes generated from normalized image variant

Multiple hashes may be stored per image to support future experiments.

---

## 10. Data Model (Conceptual)

### 10.1 Images

Core image entity stores:
- image_id (internal)
- canonical_image_url
- binary_sha256
- perceptual_hashes
- width, height, format
- first_seen_at, last_seen_at

### 10.2 Provenance

Each image keeps provenance records:
- source_page_url
- source_domain
- discovery_type (discovery / refresh)
- http_status

### 10.3 InvisibleID (Future Fields)

Reserved but initially empty:
- invisible_id_detected
- invisible_id_payload
- invisible_id_confidence
- invisible_id_version
- detected_at

---

## 11. Storage Choices

### Metadata
- PostgreSQL (initially)
- Designed for strong indexing and joins

### Large-Scale Analytics (Future)
- ClickHouse or similar columnar store

### Binary Assets
- S3-compatible object storage (e.g. MinIO)
- Content-addressable paths based on hashes

---

## 12. Separation from InvisibleID Service

The crawler does **not** encode or detect InvisibleID in Phase 1.

Integration model:
- shared image identifiers
- optional event or queue handoff
- detection workers can be added later without changing crawl logic

This separation protects core IP and simplifies operations.

---

## 13. Operational Considerations

### 13.1 Crawl Politeness

- robots.txt respected by default
- conservative per-domain rate limits
- exponential backoff on errors

### 13.2 Resilience

- retry policies
- partial failure tolerance
- idempotent processing

### 13.3 Infrastructure Flexibility

Crawler nodes are replaceable and disposable.
IP rotation or infrastructure changes are treated as operational details, not core design assumptions.

---

## 14. Evolution Path

Planned future extensions:
- InvisibleID detection workers
- Similarity search index
- Domain trust scoring
- Incremental recrawl heuristics
- Selective JS rendering (only where justified)

---

## 15. Summary

This crawler architecture prioritizes:
- long-term scalability
- architectural clarity
- separation of concerns
- readiness for InvisibleID integration

It is intentionally conservative in Phase 1 to maximize learning, stability, and control before introducing more complex detection logic.

This document is the **baseline** — not the final state.

