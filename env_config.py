"""Centralized environment configuration for InvisibleCrawler.

Loads `.env` once at import time and exposes typed getters used across
crawler, storage, and utility scripts.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULT_DATABASE_URL = "postgresql://localhost/invisible"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_CRAWLER_USER_AGENT = "InvisibleCrawler/0.1 (Web crawler for image discovery research)"
DEFAULT_DISCOVERY_REFRESH_AFTER_DAYS = 0
DEFAULT_IMAGE_MIN_WIDTH = 256
DEFAULT_IMAGE_MIN_HEIGHT = 256
DEFAULT_LOG_LEVEL = "INFO"


def get_database_url() -> str:
    """Return database URL from environment with a safe local default."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_redis_url() -> str:
    """Return Redis URL from environment with a safe local default."""
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


def get_crawler_user_agent() -> str:
    """Return crawler User-Agent string."""
    return os.getenv("CRAWLER_USER_AGENT", DEFAULT_CRAWLER_USER_AGENT)


def get_discovery_refresh_after_days() -> int:
    """Return discovery refresh age in days."""
    return get_int_env("DISCOVERY_REFRESH_AFTER_DAYS", DEFAULT_DISCOVERY_REFRESH_AFTER_DAYS)


def get_image_min_width() -> int:
    """Return minimum accepted image width in pixels."""
    return get_int_env("IMAGE_MIN_WIDTH", DEFAULT_IMAGE_MIN_WIDTH)


def get_image_min_height() -> int:
    """Return minimum accepted image height in pixels."""
    return get_int_env("IMAGE_MIN_HEIGHT", DEFAULT_IMAGE_MIN_HEIGHT)


def get_log_level() -> str:
    """Return process log level."""
    return os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL)


def get_int_env(name: str, default: int) -> int:
    """Parse integer environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
