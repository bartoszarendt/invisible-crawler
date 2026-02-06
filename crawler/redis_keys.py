"""Redis key helpers with optional namespace support.

Centralizes key naming so scheduler, spider, and CLI stay aligned
across environments and deploys.
"""

from env_config import get_queue_namespace


def _with_namespace(key: str) -> str:
    """Prefix a key with QUEUE_NAMESPACE when configured."""
    namespace = get_queue_namespace()
    if not namespace:
        return key
    return f"{namespace}:{key}"


def requests_key_pattern() -> str:
    """Return scheduler requests key pattern."""
    return _with_namespace("%(spider)s:requests")


def dupefilter_key_pattern() -> str:
    """Return scheduler dupefilter key pattern."""
    return _with_namespace("%(spider)s:dupefilter")


def start_urls_key(spider_name: str = "discovery") -> str:
    """Return key containing seed start URLs."""
    return _with_namespace(f"{spider_name}:start_urls")


def requests_key(spider_name: str = "discovery") -> str:
    """Return key containing scheduled requests."""
    return _with_namespace(f"{spider_name}:requests")


def seen_domains_key(spider_name: str = "discovery") -> str:
    """Return key containing seen domains."""
    return _with_namespace(f"{spider_name}:seen_domains")


def domains_key(spider_name: str = "discovery") -> str:
    """Return key containing tracked active domains."""
    return _with_namespace(f"{spider_name}:domains")
