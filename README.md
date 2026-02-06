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

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your local credentials/endpoints
   ```
   
   > **Security Note:** Never commit `.env`. Runtime config is loaded from `.env` and environment variables via `env_config.py`.

3. **Run migrations:**
   ```bash
   # Alembic reads DATABASE_URL from .env/environment automatically
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

## Docker Compose (VPS/Long-Running)

```bash
# Copy environment template
cp .env.prod.example .env.prod

# Apply DB migrations
docker compose --env-file .env.prod -f docker-compose.yml run --rm --profile ops migrate

# (Optional) ingest seeds
docker compose --env-file .env.prod -f docker-compose.yml run --rm --profile ops seed_ingest

# Start stack
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for full architecture, update workflow, and rollback procedure.

## Development

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://localhost/invisible` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string for scheduler/CLI |
| `CRAWLER_USER_AGENT` | `InvisibleCrawler/0.1 (...)` | User-Agent for crawler/fetcher requests |
| `DISCOVERY_REFRESH_AFTER_DAYS` | `0` | Refresh age threshold (days) for discovery dedup logic |
| `IMAGE_MIN_WIDTH` | `256` | Minimum accepted image width |
| `IMAGE_MIN_HEIGHT` | `256` | Minimum accepted image height |
| `LOG_LEVEL` | `INFO` | Application log verbosity |
| `APP_ENV` | `dev` | Deployment environment label (`dev`, `staging`, `prod`) |
| `CRAWL_PROFILE` | `conservative` | Crawl behavior profile (`conservative`, `broad`) |
| `QUEUE_NAMESPACE` | `` | Optional Redis key prefix for queue/version isolation |

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
* [IMPLEMENTATION.md](IMPLEMENTATION.md) – Phase 2 implementation details, runbook, and known gaps
* [DEPLOYMENT.md](DEPLOYMENT.md) – Docker Compose deployment architecture and operations runbook
* [AGENTS.md](AGENTS.md) – Agent guidelines and project conventions

## License

TBD
