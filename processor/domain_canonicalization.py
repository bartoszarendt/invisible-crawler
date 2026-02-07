"""Domain canonicalization utilities for InvisibleCrawler.

This module provides domain normalization functions to ensure consistent
domain identity across the crawler system.

Canonicalization Rules:
- Strip scheme (not part of domain identity)
- Strip www prefix (treat www/non-www as same domain)
- Strip port if default (80/443)
- Lowercase all characters
- Strip trailing dot
- Convert IDN to punycode
- Keep full subdomains by default (configurable via strip_subdomains)
"""

import logging
from urllib.parse import urlparse

import idna
from publicsuffix2 import get_sld

logger = logging.getLogger(__name__)


def canonicalize_domain(url: str | None, strip_subdomains: bool = False) -> str:
    """Canonicalize domain from URL.

    Extracts and normalizes the domain from a URL or domain string,
    applying consistent rules to prevent duplicate domain entries.

    Args:
        url: Full URL (https://example.com/path) or domain (example.com)
        strip_subdomains: If True, reduce to registrable domain (example.com).
                         If False, keep full subdomain (blog.example.com).

    Returns:
        Canonical domain string (e.g., "example.com" or "blog.example.com")

    Examples:
        >>> canonicalize_domain("https://Example.COM/path")
        'example.com'
        >>> canonicalize_domain("www.example.com")
        'example.com'
        >>> canonicalize_domain("example.com:443")
        'example.com'
        >>> canonicalize_domain("münchen.de")
        'xn--mnchen-3ya.de'
        >>> canonicalize_domain("blog.example.com", strip_subdomains=False)
        'blog.example.com'
        >>> canonicalize_domain("blog.example.com", strip_subdomains=True)
        'example.com'
    """
    # Handle empty or invalid input
    if not url or not isinstance(url, str):
        raise ValueError(f"Invalid URL: {url!r}")

    # Add scheme if missing to make urlparse work correctly
    if "://" not in url:
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
    except ValueError as e:
        logger.warning(f"Failed to parse URL {url!r}: {e}")
        raise ValueError(f"Invalid URL: {url!r}") from e

    # Strip default ports only (80/443); keep non-default ports for identity
    if ":" in domain and not domain.startswith("["):
        host, _, port = domain.rpartition(":")
        if port in ("80", "443"):
            domain = host

    # Strip trailing dot
    domain = domain.rstrip(".")

    # Strip www prefix
    if domain.startswith("www."):
        domain = domain[4:]

    # Handle IDN (Internationalized Domain Name) - convert to punycode
    if domain:
        try:
            # idna.encode returns bytes, decode to string
            encoded = idna.encode(domain, uts46=True)
            domain = encoded.decode("ascii")
        except (idna.IDNAError, UnicodeError, UnicodeDecodeError) as e:
            # If encoding fails, keep the original domain
            logger.debug(f"IDN encoding failed for {domain!r}: {e}")
            pass

    # Optionally strip to registrable domain
    if strip_subdomains and domain:
        try:
            registrable = get_sld(domain)
            if registrable:
                domain = registrable
        except Exception as e:
            # If reduction fails, keep the full domain
            logger.debug(f"Domain reduction failed for {domain!r}: {e}")
            pass

    return domain


def extract_domain_from_url(url: str | None) -> str | None:
    """Extract domain from URL without full canonicalization.

    This is a lightweight version that just extracts the netloc
    without applying all canonicalization rules. Useful for logging.

    Args:
        url: Full URL

    Returns:
        Domain string or None if extraction fails
    """
    if not url or not isinstance(url, str):
        return None

    try:
        if "://" not in url:
            url = f"https://{url}"
        parsed = urlparse(url)
        return parsed.netloc.lower() or None
    except Exception:
        return None
