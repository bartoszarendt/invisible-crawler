"""Centralized environment configuration for InvisibleCrawler.

Loads `.env` once at import time and exposes typed getters used across
crawler, storage, and utility scripts.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
logger = logging.getLogger(__name__)

DEFAULT_APP_ENV = "dev"
DEFAULT_CRAWL_PROFILE = "conservative"
DEFAULT_DATABASE_URL = "postgresql://localhost/invisible"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_CRAWLER_USER_AGENT = "InvisibleCrawler/0.1 (Web crawler for image discovery research)"
DEFAULT_DISCOVERY_REFRESH_AFTER_DAYS = 0
DEFAULT_IMAGE_MIN_WIDTH = 256
DEFAULT_IMAGE_MIN_HEIGHT = 256
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_QUEUE_NAMESPACE = ""

# Conservative defaults tuned for local/dev runs.
DEFAULT_SCRAPY_CONCURRENT_REQUESTS = 16
DEFAULT_SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN = 1
DEFAULT_SCRAPY_DOWNLOAD_DELAY = 1.0
DEFAULT_SCRAPY_RANDOMIZE_DOWNLOAD_DELAY = True
DEFAULT_SCRAPY_AUTOTHROTTLE_ENABLED = True
DEFAULT_SCRAPY_AUTOTHROTTLE_START_DELAY = 1.0
DEFAULT_SCRAPY_AUTOTHROTTLE_MAX_DELAY = 10.0
DEFAULT_SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
DEFAULT_SCRAPY_DOWNLOAD_TIMEOUT = 30
DEFAULT_SCRAPY_RETRY_ENABLED = True
DEFAULT_SCRAPY_RETRY_TIMES = 3

# Broad profile defaults for VPS runs.
BROAD_SCRAPY_CONCURRENT_REQUESTS = 64
BROAD_SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN = 4
BROAD_SCRAPY_DOWNLOAD_DELAY = 0.25
BROAD_SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY = 4.0
BROAD_SCRAPY_DOWNLOAD_TIMEOUT = 20
BROAD_SCRAPY_RETRY_TIMES = 2
DEFAULT_CRAWLER_MAX_PAGES = 10
ALLOWED_APP_ENVS = {"dev", "staging", "prod"}

# Domain tracking feature flags (Phase A)
DEFAULT_ENABLE_DOMAIN_TRACKING = True
DEFAULT_DOMAIN_CANONICALIZATION_STRIP_SUBDOMAINS = False

# Per-domain budget feature flags (Phase B)
DEFAULT_ENABLE_PER_DOMAIN_BUDGET = False
DEFAULT_MAX_PAGES_PER_RUN = 1000

# Smart scheduling feature flags (Phase C)
DEFAULT_ENABLE_SMART_SCHEDULING = False
DEFAULT_ENABLE_CLAIM_PROTOCOL = False

ALLOWED_CRAWL_PROFILES = {"conservative", "broad"}


def get_database_url() -> str:
    """Return database URL from environment with a safe local default."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_redis_url() -> str:
    """Return Redis URL from environment with a safe local default."""
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL)


@lru_cache(maxsize=1)
def get_app_env() -> str:
    """Return the deployment environment name."""
    return get_choice_env("APP_ENV", DEFAULT_APP_ENV, ALLOWED_APP_ENVS)


@lru_cache(maxsize=1)
def get_crawl_profile() -> str:
    """Return crawl tuning profile name."""
    return get_choice_env("CRAWL_PROFILE", DEFAULT_CRAWL_PROFILE, ALLOWED_CRAWL_PROFILES)


def is_broad_crawl_profile() -> bool:
    """Return True when broad crawl profile is active."""
    return get_crawl_profile() == "broad"


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


def get_queue_namespace() -> str:
    """Return optional Redis key namespace prefix."""
    return os.getenv("QUEUE_NAMESPACE", DEFAULT_QUEUE_NAMESPACE).strip().strip(":")


def get_crawler_max_pages() -> int:
    """Return default spider max_pages value used when no CLI arg is provided."""
    return get_int_env("CRAWLER_MAX_PAGES", DEFAULT_CRAWLER_MAX_PAGES)


def get_scrapy_concurrent_requests() -> int:
    """Return Scrapy global request concurrency."""
    profile_default = (
        BROAD_SCRAPY_CONCURRENT_REQUESTS
        if is_broad_crawl_profile()
        else DEFAULT_SCRAPY_CONCURRENT_REQUESTS
    )
    return get_int_env("SCRAPY_CONCURRENT_REQUESTS", profile_default)


def get_scrapy_concurrent_requests_per_domain() -> int:
    """Return Scrapy per-domain request concurrency."""
    profile_default = (
        BROAD_SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN
        if is_broad_crawl_profile()
        else DEFAULT_SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN
    )
    return get_int_env("SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN", profile_default)


def get_scrapy_download_delay() -> float:
    """Return Scrapy DOWNLOAD_DELAY in seconds."""
    profile_default = (
        BROAD_SCRAPY_DOWNLOAD_DELAY if is_broad_crawl_profile() else DEFAULT_SCRAPY_DOWNLOAD_DELAY
    )
    return get_float_env("SCRAPY_DOWNLOAD_DELAY", profile_default)


def get_scrapy_randomize_download_delay() -> bool:
    """Return Scrapy RANDOMIZE_DOWNLOAD_DELAY toggle."""
    return get_bool_env("SCRAPY_RANDOMIZE_DOWNLOAD_DELAY", DEFAULT_SCRAPY_RANDOMIZE_DOWNLOAD_DELAY)


def get_scrapy_autothrottle_enabled() -> bool:
    """Return Scrapy AUTOTHROTTLE_ENABLED toggle."""
    return get_bool_env("SCRAPY_AUTOTHROTTLE_ENABLED", DEFAULT_SCRAPY_AUTOTHROTTLE_ENABLED)


def get_scrapy_autothrottle_start_delay() -> float:
    """Return Scrapy AUTOTHROTTLE_START_DELAY in seconds."""
    return get_float_env("SCRAPY_AUTOTHROTTLE_START_DELAY", DEFAULT_SCRAPY_AUTOTHROTTLE_START_DELAY)


def get_scrapy_autothrottle_max_delay() -> float:
    """Return Scrapy AUTOTHROTTLE_MAX_DELAY in seconds."""
    return get_float_env("SCRAPY_AUTOTHROTTLE_MAX_DELAY", DEFAULT_SCRAPY_AUTOTHROTTLE_MAX_DELAY)


def get_scrapy_autothrottle_target_concurrency() -> float:
    """Return Scrapy AUTOTHROTTLE_TARGET_CONCURRENCY."""
    profile_default = (
        BROAD_SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY
        if is_broad_crawl_profile()
        else DEFAULT_SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY
    )
    return get_float_env("SCRAPY_AUTOTHROTTLE_TARGET_CONCURRENCY", profile_default)


def get_scrapy_download_timeout() -> int:
    """Return Scrapy DOWNLOAD_TIMEOUT in seconds."""
    profile_default = (
        BROAD_SCRAPY_DOWNLOAD_TIMEOUT
        if is_broad_crawl_profile()
        else DEFAULT_SCRAPY_DOWNLOAD_TIMEOUT
    )
    return get_int_env("SCRAPY_DOWNLOAD_TIMEOUT", profile_default)


def get_scrapy_retry_enabled() -> bool:
    """Return Scrapy RETRY_ENABLED toggle."""
    return get_bool_env("SCRAPY_RETRY_ENABLED", DEFAULT_SCRAPY_RETRY_ENABLED)


def get_scrapy_retry_times() -> int:
    """Return Scrapy RETRY_TIMES."""
    profile_default = (
        BROAD_SCRAPY_RETRY_TIMES if is_broad_crawl_profile() else DEFAULT_SCRAPY_RETRY_TIMES
    )
    return get_int_env("SCRAPY_RETRY_TIMES", profile_default)


def get_int_env(name: str, default: int) -> int:
    """Parse integer environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_float_env(name: str, default: float) -> float:
    """Parse float environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_bool_env(name: str, default: bool) -> bool:
    """Parse bool environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def get_choice_env(name: str, default: str, allowed: set[str]) -> str:
    """Parse enum-like env values with fallback to default on invalid input."""
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in allowed:
        return value

    logger.warning(
        "Invalid %s value '%s'. Allowed values: %s. Falling back to '%s'.",
        name,
        raw,
        ", ".join(sorted(allowed)),
        default,
    )
    return default


def get_enable_domain_tracking() -> bool:
    """Return whether domain tracking is enabled.

    When enabled, the spider will:
    - Upsert domain rows during start_requests
    - Update domain stats in closed()

    Default: True
    """
    return get_bool_env("ENABLE_DOMAIN_TRACKING", DEFAULT_ENABLE_DOMAIN_TRACKING)


def get_domain_canonicalization_strip_subdomains() -> bool:
    """Return whether to strip subdomains during canonicalization.

    When True: blog.example.com -> example.com
    When False: blog.example.com -> blog.example.com (keeps full subdomain)

    Default: False (keep full subdomains)
    """
    return get_bool_env(
        "DOMAIN_CANONICALIZATION_STRIP_SUBDOMAINS",
        DEFAULT_DOMAIN_CANONICALIZATION_STRIP_SUBDOMAINS,
    )


def get_enable_per_domain_budget() -> bool:
    """Return whether per-domain budget enforcement is enabled (Phase B).

    When enabled:
    - Each domain gets max_pages_per_run pages instead of global max_pages
    - Frontier checkpoints are saved when budget is exhausted
    - Domains can resume from checkpoint on next run

    Default: False (use global max_pages for backward compatibility)
    """
    return get_bool_env("ENABLE_PER_DOMAIN_BUDGET", DEFAULT_ENABLE_PER_DOMAIN_BUDGET)


def get_default_max_pages_per_run() -> int:
    """Return default maximum pages to crawl per domain per run.

    Only applies when per-domain budgets are enabled.

    Default: 1000
    """
    return get_int_env("MAX_PAGES_PER_RUN", DEFAULT_MAX_PAGES_PER_RUN)


def get_enable_smart_scheduling() -> bool:
    """Return whether smart scheduling is enabled (Phase C).

    When enabled:
    - Spider queries domains table for crawl candidates instead of reading seeds
    - Candidates selected by priority score (high-yield domains first)
    - Active domains prioritized over pending (resume support)

    Default: False (use seed-driven scheduling for backward compatibility)

    WARNING: Must be deployed to ALL workers atomically. Cannot coexist with
    Phase A/B workers.
    """
    return get_bool_env("ENABLE_SMART_SCHEDULING", DEFAULT_ENABLE_SMART_SCHEDULING)


def get_enable_claim_protocol() -> bool:
    """Return whether domain claim protocol is enabled (Phase C).

    When enabled:
    - Workers claim domains before crawling (30-minute lease)
    - Prevents duplicate work in multi-worker deployments
    - Uses FOR UPDATE SKIP LOCKED for atomic claim acquisition
    - Includes heartbeat renewal (every 10 minutes)

    Default: False (for backward compatibility)

    Must be enabled together with ENABLE_SMART_SCHEDULING for Phase C.
    """
    return get_bool_env("ENABLE_CLAIM_PROTOCOL", DEFAULT_ENABLE_CLAIM_PROTOCOL)
