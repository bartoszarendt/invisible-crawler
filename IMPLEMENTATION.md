# Implementation Prompt for Autonomous Coding Agent

## Context

You are an autonomous coding agent tasked with building **InvisibleCrawler**, a self-hosted, large-scale web crawler for discovering and indexing images across the public web. This is a greenfield project currently in the design phase with comprehensive architectural documentation but no implementation code.

## Available Documentation

Before starting, carefully read:
1. **SYSTEM_DESIGN.md** – Complete architectural blueprint with goals, principles, data flow, and storage choices
2. **AGENTS.md** – Repository structure, conventions, guardrails, and operational guidelines

## Your Mission: Phase 1 Implementation

Implement the **minimal viable crawler** that can:
1. Accept a small seed list of domains
2. Fetch HTML pages respecting robots.txt and rate limits
3. Extract image URLs from `<img>` tags
4. Download images and generate SHA-256 fingerprints
5. Store metadata in PostgreSQL
6. Run end-to-end on a single domain successfully

## Implementation Priority Order

### Step 1: Project Scaffolding
- Create Python project structure matching the expected layout in AGENTS.md
- Set up `requirements.txt` with core dependencies: Scrapy, psycopg2, Pillow, requests
- Add development dependencies: pytest, black, ruff, mypy
- Create `.gitignore` for Python projects
- Set up basic `README.md` with quick-start instructions

### Step 2: Database Foundation
- Create `storage/schema.sql` with minimal tables:
  - `images` table (id, url, sha256_hash, width, height, discovered_at)
  - `crawl_log` table (id, page_url, crawled_at, status)
- Set up Alembic for migrations
- Create initial migration from schema
- Add database connection utility module

### Step 3: Basic Scrapy Spider
- Create `crawler/spiders/discovery_spider.py`
- Implement simple HTML parser to extract `<img src>` URLs
- Add basic URL filtering (same-domain only for Phase 1)
- Configure Scrapy settings for politeness (1 req/sec, robots.txt obey)
- Create custom Scrapy pipeline for processing discovered images

### Step 4: Image Processing Pipeline
- Create `processor/fetcher.py` for downloading images
- Add `processor/fingerprint.py` with SHA-256 hash generation
- Implement basic image validation (content-type check, minimum size)
- Add error handling for corrupt/inaccessible images

### Step 5: Integration & Testing
- Write integration test crawling a mock HTTP server
- Create `config/test_seeds.txt` with 3-5 safe test domains
- Add end-to-end test script that runs full pipeline
- Document how to run the crawler locally

### Step 6: Observability Basics
- Add structured logging throughout pipeline stages
- Create simple crawl statistics output (images found, downloaded, errors)
- Add basic exception handling and retry logic

## Critical Constraints

**Must Follow:**
- Use Python 3.11+ with type hints everywhere
- Follow conventions in AGENTS.md (Black formatting, Google-style docstrings)
- Do NOT implement InvisibleID detection (explicitly out of scope)
- Do NOT add JavaScript rendering (Scrapy static HTML only)
- Keep it simple: no distributed systems, no advanced queuing yet
- Make rate limiting conservative (1 req/sec default, configurable)

**Must NOT Do:**
- Do not modify `SYSTEM_DESIGN.md` or `AGENTS.md` without explicit approval
- Do not add external SaaS dependencies
- Do not implement features beyond minimal MVP scope
- Do not create complex abstractions prematurely

## Technical Decisions

**Use These Technologies:**
- **Framework**: Scrapy 2.11+
- **Database**: PostgreSQL 15+ (use psycopg3 or psycopg2)
- **Image Processing**: Pillow (PIL Fork)
- **Testing**: pytest with coverage plugin
- **Migrations**: Alembic
- **Linting**: Ruff + Black + mypy

**Database Connection Pattern:**
```python
# Use environment variables for credentials
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/crawler")
```

**Project Layout:**
```
invisible-crawler/
├── crawler/
│   ├── __init__.py
│   ├── spiders/
│   │   ├── __init__.py
│   │   └── discovery_spider.py
│   ├── pipelines.py
│   └── settings.py
├── processor/
│   ├── __init__.py
│   ├── fetcher.py
│   └── fingerprint.py
├── storage/
│   ├── __init__.py
│   ├── schema.sql
│   ├── db.py
│   └── migrations/
├── config/
│   └── test_seeds.txt
├── tests/
│   ├── __init__.py
│   ├── test_spider.py
│   ├── test_processor.py
│   └── test_integration.py
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── .gitignore
└── README.md
```

## Success Criteria

You will have succeeded when:
1. ✅ A developer can clone the repo and run the crawler in <10 minutes
2. ✅ The crawler can successfully discover and download images from a test domain
3. ✅ All tests pass with >80% coverage
4. ✅ Type checking, linting, and formatting checks pass
5. ✅ Database schema is created and migrations run successfully
6. ✅ Basic documentation is in README.md for running locally

## Validation Commands

After implementation, these commands must work:

```bash
# Setup
pip install -r requirements.txt
pip install -r requirements-dev.txt
alembic upgrade head

# Quality checks
black --check .
ruff check .
mypy crawler/ processor/ storage/

# Testing
pytest tests/ --cov=crawler --cov=processor --cov-report=term-missing

# Run crawler (mock mode for testing)
scrapy crawl discovery -a seeds=config/test_seeds.txt -s CLOSESPIDER_PAGECOUNT=10
```

## Guidance for Autonomous Execution

**Planning Phase:**
1. Read both SYSTEM_DESIGN.md and AGENTS.md completely
2. Create a checklist of files to create
3. Identify any ambiguities and document assumptions

**Implementation Phase:**
1. Work through steps 1-6 sequentially
2. Create files in dependency order (schema before migrations, models before spiders)
3. Add tests as you implement each component
4. Run validation commands after each major step

**If You Get Stuck:**
- Simplify: choose the most straightforward implementation
- Document: add TODO comments for future improvements
- Defer: mark advanced features for Phase 2
- Ask: surface any architectural decisions that need human input

## Environment Setup Requirements

Document these prerequisites in README.md:
- Python 3.11+
- PostgreSQL 15+ running locally
- Database created: `createdb crawler`
- Environment variable: `DATABASE_URL=postgresql://user:pass@localhost/crawler`

## Testing Strategy

**Unit Tests:**
- Test fingerprinting logic with sample images
- Test URL extraction from mock HTML
- Test database operations with pytest fixtures

**Integration Tests:**
- Use `pytest-httpserver` or similar for mock web server
- Test full pipeline with synthetic HTML pages
- Verify database writes with test fixtures

**No External Network Access in Tests:**
- All tests must run offline
- Use fixtures and mocks for external dependencies

## Deliverables Checklist

- [ ] Project structure created per AGENTS.md
- [ ] requirements.txt with core dependencies
- [ ] Database schema and Alembic setup
- [ ] Basic Scrapy spider implementation
- [ ] Image fetching and fingerprinting
- [ ] Test suite with >80% coverage
- [ ] README.md with setup and usage instructions
- [ ] .gitignore configured
- [ ] pyproject.toml for Black/Ruff/mypy config
- [ ] All validation commands pass

## Timeline Estimate

For a single autonomous agent session, target:
- **Core implementation**: 2-4 hours
- **Testing & validation**: 1-2 hours
- **Documentation**: 30 minutes
- **Total**: ~4-6 hours of focused work

## Final Notes

This is a **foundation-building exercise**. Prioritize:
1. **Correctness** over performance
2. **Clarity** over cleverness
3. **Working code** over perfect architecture
4. **Tests** as proof of functionality

The goal is a solid foundation that future iterations can build upon, not a production-ready system. Keep it simple, keep it working, keep it tested.

---

**Ready to begin? Start with Step 1: Project Scaffolding.**
