# InvisibleCrawler

A self-hosted, large-scale web crawler for discovering and indexing images across the public web. Built to support the OneMark/InvisibleID ecosystem.

## Overview

InvisibleCrawler fetches images from the web, generates stable fingerprints (SHA-256 and perceptual hashes), and stores provenance metadata. Designed for independent evolution and future integration with InvisibleID watermark detection.

## Prerequisites

- Python 3.12+
- PostgreSQL 15+ running on port 5432
- Database `invisible` created with user access

## Quick Start

1. **Clone and setup environment:**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

2. **Configure database:**
   ```bash
   # Edit .env file with your database credentials
   # Never commit credentials to version control!
   DATABASE_URL=postgresql://user:pass@localhost:5432/invisible
   ```
   
   > **Security Note:** Database credentials are read from the `DATABASE_URL` environment variable (set via `.env` file). Both the application code and Alembic migrations read from this environment variable. The `alembic.ini` file contains only a placeholder.

3. **Run migrations:**
   ```bash
   # Alembic reads DATABASE_URL from .env automatically
   alembic upgrade head
   ```

4. **Run tests:**
   ```bash
   pytest tests/ --cov=crawler --cov=processor --cov-report=term-missing
   ```

5. **Run crawler:**
   ```bash
   # Run with page limit
   scrapy crawl discovery -a seeds=config/test_seeds.txt -a max_pages=100
   ```

## Development

### Code Quality

```bash
# Format code
black .

# Lint
ruff check .

# Type check
mypy crawler/ processor/ storage/
```

### Testing

```bash
# Run all tests with coverage
pytest tests/ --cov=crawler --cov=processor --cov-report=term-missing
```

## Documentation

* [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) – Architecture and design principles
* [IMPLEMENTATION.md](IMPLEMENTATION.md) – Phase 1 implementation details and Phase 2 roadmap
* [AGENTS.md](AGENTS.md) – Agent guidelines and project conventions

## License

TBD
