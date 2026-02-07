"""Scrapy settings for InvisibleCrawler project.

For simplicity, see:
https://docs.scrapy.org/en/latest/topics/settings.html
"""

from pathlib import Path

from crawler.redis_keys import dupefilter_key_pattern, requests_key_pattern
from env_config import (
    get_crawler_user_agent,
    get_enable_claim_protocol,
    get_enable_smart_scheduling,
    get_log_level,
    get_redis_url,
    get_scrapy_autothrottle_enabled,
    get_scrapy_autothrottle_max_delay,
    get_scrapy_autothrottle_start_delay,
    get_scrapy_autothrottle_target_concurrency,
    get_scrapy_concurrent_requests,
    get_scrapy_concurrent_requests_per_domain,
    get_scrapy_download_delay,
    get_scrapy_download_timeout,
    get_scrapy_randomize_download_delay,
    get_scrapy_retry_enabled,
    get_scrapy_retry_times,
)

# Project settings
BOT_NAME = "invisible-crawler"
SPIDER_MODULES = ["crawler.spiders"]
NEWSPIDER_MODULE = "crawler.spiders"

# Crawl responsibly by identifying yourself
USER_AGENT = get_crawler_user_agent()

# Obey robots.txt rules
ROBOTSTXT_OBEY = True

# Configure a delay for requests for the same website (default: 0)
DOWNLOAD_DELAY = get_scrapy_download_delay()
RANDOMIZE_DOWNLOAD_DELAY = get_scrapy_randomize_download_delay()

# Global and per-domain concurrency
CONCURRENT_REQUESTS = get_scrapy_concurrent_requests()

# The download delay setting will honor only one of:
CONCURRENT_REQUESTS_PER_DOMAIN = get_scrapy_concurrent_requests_per_domain()
# CONCURRENT_REQUESTS_PER_IP = 1  # Deprecated, removed to avoid conflicts

# Disable cookies (enabled by default)
COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
TELNETCONSOLE_ENABLED = False

# Override the default request headers:
DEFAULT_REQUEST_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}

# Enable or disable spider middlewares
SPIDER_MIDDLEWARES: dict[str, int] = {
    # "crawler.middlewares.CrawlerSpiderMiddleware": 543,
}

# Enable or disable downloader middlewares
DOWNLOADER_MIDDLEWARES: dict[str, int] = {
    # "crawler.middlewares.CrawlerDownloaderMiddleware": 543,
}

# Configure item pipelines
ITEM_PIPELINES: dict[str, int] = {
    "crawler.pipelines.ImageProcessingPipeline": 300,
}

# Enable and configure AutoThrottle extension (disabled by default)
AUTOTHROTTLE_ENABLED = get_scrapy_autothrottle_enabled()
AUTOTHROTTLE_START_DELAY = get_scrapy_autothrottle_start_delay()
AUTOTHROTTLE_MAX_DELAY = get_scrapy_autothrottle_max_delay()
AUTOTHROTTLE_TARGET_CONCURRENCY = get_scrapy_autothrottle_target_concurrency()
AUTOTHROTTLE_DEBUG = False

# Enable showing throttling stats for every response received
# AUTOTHROTTLE_DEBUG = True

# Configure HTTP caching (disabled by default)
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 0
# HTTPCACHE_DIR = "httpcache"
# HTTPCACHE_IGNORE_HTTP_CODES = []
# HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"

# Retry configuration
RETRY_ENABLED = get_scrapy_retry_enabled()
RETRY_TIMES = get_scrapy_retry_times()
RETRY_HTTP_CODES: list[int] = [500, 502, 503, 504, 408, 429]

# Timeout configuration
DOWNLOAD_TIMEOUT = get_scrapy_download_timeout()

# Logging
LOG_LEVEL = get_log_level()

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
SEEDS_DIR = PROJECT_ROOT / "config"

# ============================================================================
# Redis URL Frontier Configuration (Phase 2)
# ============================================================================

# Scheduler selection: Phase C (smart scheduling + claim protocol) uses local
# scheduler for per-worker queue isolation. Phases A/B use Redis scheduler
# for shared queue across workers.
_is_phase_c = get_enable_smart_scheduling() and get_enable_claim_protocol()

# Enable Redis scheduler for distributed crawling (Phases A/B)
# Phase C uses local scheduler to prevent cross-worker domain overlap
# IMPORTANT: Redis is required when Redis scheduler is enabled
# Use check_redis_available() to verify Redis connectivity before starting crawls
SCHEDULER = (
    "scrapy.core.scheduler.Scheduler"  # Local scheduler (Phase C)
    if _is_phase_c
    else "scrapy_redis.scheduler.Scheduler"  # Redis scheduler (Phases A/B)
)

# DupeFilter for URL deduplication
# Note: Phase C uses local dupefilter; Phase A/B uses Redis dupefilter
DUPEFILTER_CLASS = (
    "scrapy.dupefilters.RFPDupeFilter"  # Local dupefilter (Phase C)
    if _is_phase_c
    else "scrapy_redis.dupefilter.RFPDupeFilter"  # Redis dupefilter (Phases A/B)
)

# Queue class - supports priority and per-domain tracking
# Note: Only used by Redis scheduler (Phases A/B); ignored by local scheduler (Phase C)
SCHEDULER_QUEUE_CLASS = "crawler.scheduler.DomainPriorityQueue"
SCHEDULER_QUEUE_KEY = requests_key_pattern()
DUPEFILTER_KEY = dupefilter_key_pattern()

SCHEDULER_PERSIST = True  # Persist queues on spider close
SCHEDULER_FLUSH_ON_START = False  # Don't flush queues on start (enables resume)
SCHEDULER_IDLE_BEFORE_CLOSE = 0  # Close immediately when queue empty

# Redis connection (from REDIS_URL env var)
REDIS_URL = get_redis_url()

# Serializer for queue items
SCHEDULER_SERIALIZER = "scrapy_redis.picklecompat"

# Redis settings for high availability
REDIS_START_URLS_AS_SET = True
REDIS_ENCODING = "utf-8"

# Retry settings for Redis connection
REDIS_RETRY_ON_TIMEOUT = True
REDIS_SOCKET_CONNECT_TIMEOUT = 5
REDIS_SOCKET_TIMEOUT = 5

# ============================================================================
# Object Storage Configuration (Phase 2)
# ============================================================================

# MinIO/S3 settings (configure via environment variables)
# OBJECT_STORE_ENDPOINT = "http://localhost:9000"
# OBJECT_STORE_BUCKET = "invisible-images"
# OBJECT_STORE_ACCESS_KEY = "minioadmin"
# OBJECT_STORE_SECRET_KEY = "minioadmin"
# OBJECT_STORE_REGION = "us-east-1"
# OBJECT_STORE_SECURE = False

# Content-addressable storage path template
# Images stored at: /{sha256[0:2]}/{sha256[2:4]}/{sha256}
OBJECT_STORE_PATH_TEMPLATE = "{hash_prefix}/{hash_full}"
