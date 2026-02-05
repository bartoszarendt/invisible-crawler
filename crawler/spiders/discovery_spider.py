"""Discovery spider for InvisibleCrawler.

This spider crawls domains from a seed file, extracting image URLs
from HTML pages and following same-domain links.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from scrapy import Spider
from scrapy.http import Request, Response, TextResponse

from storage.db import get_cursor


class DiscoverySpider(Spider):
    """Spider for discovering images across multiple domains.

    Reads seed domains from a file and crawls each domain,
    extracting image URLs and following same-domain links.

    Attributes:
        name: Spider identifier for Scrapy.
        allowed_domains: List of domains to crawl (populated from seed file).
        max_pages: Maximum pages to crawl per domain.
        images_found: Counter for statistics.
    """

    name = "discovery"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(self, seeds: str | None = None, **kwargs: Any) -> None:
        """Initialize the spider with seed domains.

        Args:
            seeds: Path to seed file containing domains (one per line).
            **kwargs: Additional Scrapy spider kwargs.
        """
        super().__init__(**kwargs)
        self.seeds_file = seeds
        self.max_pages = int(kwargs.get("max_pages", 10))
        self.allowlist_file = kwargs.get("allowlist", "config/seed_allowlist.txt")
        self.blocklist_file = kwargs.get("blocklist", "config/seed_blocklist.txt")
        self.max_domain_errors = _get_int(kwargs.get("max_domain_errors"), 3)
        self.block_on_login = _get_bool(kwargs.get("block_on_login"), True)
        self.images_found: int = 0
        self.pages_crawled: int = 0
        self._domains: list[str] = []
        self._allowlist = self._load_domain_list(self.allowlist_file)
        self._blocklist = self._load_domain_list(self.blocklist_file)
        self._blocked_domains_runtime: set[str] = set()
        self._domain_error_counts: dict[str, int] = {}

    def start_requests(self) -> Any:
        """Generate initial requests from seed domains.

        Yields:
            Request objects for each seed domain.
        """
        if not self.seeds_file:
            self.logger.error("No seeds file provided. Use -a seeds=config/seeds.txt")
            return

        seeds_path = Path(self.seeds_file)
        if not seeds_path.exists():
            self.logger.error(f"Seeds file not found: {seeds_path}")
            return

        # Read and parse seed domains
        with open(seeds_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue

                # Parse domain and ensure it has a scheme
                domain = line
                if not domain.startswith(("http://", "https://")):
                    domain = f"https://{domain}"

                domain_netloc = urlparse(domain).netloc
                if self._allowlist and domain_netloc not in self._allowlist:
                    self.logger.info(f"Skipping seed (not in allowlist): {domain}")
                    continue
                if domain_netloc in self._blocklist:
                    self.logger.info(f"Skipping seed (blocked): {domain}")
                    continue

                self._domains.append(domain)
                self.logger.info(f"Adding seed domain: {domain}")

                # Start crawling from root
                yield Request(
                    url=domain,
                    callback=self.parse,
                    errback=self.handle_error,
                    meta={"depth": 0, "domain": urlparse(domain).netloc},
                )

    def parse(self, response: Response) -> Any:
        """Parse HTML page and extract images and links.

        Args:
            response: Scrapy Response object.

        Yields:
            Image URLs and follow-up requests.
        """
        self.pages_crawled += 1
        current_domain = response.meta.get("domain", urlparse(response.url).netloc)
        current_depth = response.meta.get("depth", 0)

        self.logger.info(
            f"Parsing [{self.pages_crawled}/{self.max_pages}]: {response.url} "
            f"(depth: {current_depth})"
        )

        if current_domain in self._blocked_domains_runtime:
            self.logger.info(f"Skipping blocked domain: {current_domain}")
            return

        # Check if response is HTML/text before parsing
        content_type = (
            response.headers.get("Content-Type", b"")
            .decode("utf-8", errors="ignore")
            .lower()
        )
        is_text_response = isinstance(response, TextResponse)

        # Be more permissive: try to parse if content-type suggests HTML or if it's a text response
        should_parse = (
            is_text_response or
            (content_type and ("html" in content_type or "text" in content_type)) or
            (not content_type and len(response.body) > 0)  # Fallback for responses without content-type
        )

        if not should_parse:
            self.logger.debug(f"Skipping non-HTML response: {content_type} for {response.url}")
            return

        if self.block_on_login and self._looks_like_login_page(response):
            self._mark_domain_blocked(current_domain, "login_required")
            return

        # Extract image URLs from the page
        image_urls = self._extract_image_urls(response, current_domain)
        self.images_found += len(image_urls)

        self.logger.info(f"Found {len(image_urls)} images on {response.url}")

        # Best-effort crawl log entry (only when running under Scrapy)
        self._log_crawl_entry(
            page_url=response.url,
            domain=current_domain,
            status=response.status,
            images_found=len(image_urls),
        )

        # Yield image data for processing
        for img_url in image_urls:
            yield {
                "type": "image",
                "url": img_url,
                "source_page": response.url,
                "source_domain": current_domain,
                "crawl_type": "discovery",
            }

        # Follow same-domain links if we haven't reached max pages
        if self.pages_crawled < self.max_pages:
            for next_url in self._extract_links(response, current_domain):
                yield Request(
                    url=next_url,
                    callback=self.parse,
                    errback=self.handle_error,
                    meta={
                        "depth": current_depth + 1,
                        "domain": current_domain,
                    },
                )

    def _extract_image_urls(self, response: Response, domain: str) -> list[str]:
        """Extract image URLs from HTML response.

        Extracts from:
        - <img src="...">
        - <img srcset="...">
        - <picture><source srcset="..."></picture>
        - <meta property="og:image" content="...">

        Args:
            response: Scrapy Response object.
            domain: Current domain being crawled.

        Returns:
            List of absolute image URLs.
        """
        image_urls: set[str] = set()

        try:
            # Extract from <img> tags
            for img in response.css("img"):
                src = img.css("::attr(src)").get()
                if src:
                    absolute_url = urljoin(response.url, src)
                    if self._is_valid_image_url(absolute_url):
                        image_urls.add(absolute_url)

                # Extract from srcset
                srcset = img.css("::attr(srcset)").get()
                if srcset:
                    for url in self._parse_srcset(srcset):
                        absolute_url = urljoin(response.url, url)
                        if self._is_valid_image_url(absolute_url):
                            image_urls.add(absolute_url)

            # Extract from <picture> sources
            for source in response.css("picture source"):
                srcset = source.css("::attr(srcset)").get()
                if srcset:
                    for url in self._parse_srcset(srcset):
                        absolute_url = urljoin(response.url, url)
                        if self._is_valid_image_url(absolute_url):
                            image_urls.add(absolute_url)

            # Extract from Open Graph meta tags
            og_image = response.css('meta[property="og:image"]::attr(content)').get()
            if og_image:
                absolute_url = urljoin(response.url, og_image)
                if self._is_valid_image_url(absolute_url):
                    image_urls.add(absolute_url)

        except Exception as e:
            self.logger.warning(f"Failed to parse HTML from {response.url}: {e}")
            # Return empty list if parsing fails
            return []

        return list(image_urls)

    def _extract_links(self, response: Response, domain: str) -> list[str]:
        """Extract same-domain links to follow.

        Args:
            response: Scrapy Response object.
            domain: Current domain to stay within.

        Returns:
            List of absolute URLs to follow.
        """
        links: list[str] = []

        try:
            for href in response.css("a::attr(href)").getall():
                absolute_url = urljoin(response.url, href)
                parsed = urlparse(absolute_url)

                # Only follow same-domain links
                if parsed.netloc == domain:
                    # Skip non-HTML resources
                    if any(
                        parsed.path.lower().endswith(ext)
                        for ext in [".pdf", ".zip", ".exe", ".dmg", ".jpg", ".png", ".gif"]
                    ):
                        continue
                    links.append(absolute_url)
        except Exception as e:
            self.logger.warning(f"Failed to extract links from {response.url}: {e}")
            return []

        return links

    def _parse_srcset(self, srcset: str) -> list[str]:
        """Parse srcset attribute to extract URLs.

        Args:
            srcset: The srcset attribute value.

        Returns:
            List of URLs from the srcset.
        """
        urls: list[str] = []
        for item in srcset.split(","):
            parts = item.strip().split()
            if parts:
                urls.append(parts[0])
        return urls

    def _is_valid_image_url(self, url: str) -> bool:
        """Check if URL points to a valid image.

        Args:
            url: The URL to check.

        Returns:
            True if URL appears to be an image.
        """
        parsed = urlparse(url)

        # Must have a scheme and netloc
        if not parsed.scheme or not parsed.netloc:
            return False

        # Check for common image extensions
        image_extensions = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp")
        path_lower = parsed.path.lower()

        if any(path_lower.endswith(ext) for ext in image_extensions):
            return True

        # Allow URLs that look like images even without extension
        if "image" in path_lower or "/img/" in path_lower:
            return True

        # Reject URLs that don't look like images
        return False

    def _load_domain_list(self, path: str | None) -> set[str]:
        """Load a domain allowlist or blocklist from a file.

        Args:
            path: Path to the list file.

        Returns:
            Set of domain netlocs.
        """
        if not path:
            return set()

        list_path = Path(path)
        if not list_path.exists():
            return set()

        domains: set[str] = set()
        with open(list_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                value = line
                if not value.startswith(("http://", "https://")):
                    value = f"https://{value}"
                domains.add(urlparse(value).netloc)
        return domains

    def _mark_domain_blocked(self, domain: str, reason: str) -> None:
        """Block a domain for the remainder of this crawl."""
        if domain not in self._blocked_domains_runtime:
            self._blocked_domains_runtime.add(domain)
            self.logger.warning(f"Blocking domain {domain}: {reason}")

    def _looks_like_login_page(self, response: Response) -> bool:
        """Heuristic check for login pages."""
        if not isinstance(response, TextResponse):
            return False
        try:
            has_password = bool(response.css('input[type="password"]').get())
            title = (response.css("title::text").get() or "").lower()
            return has_password or "login" in title or "sign in" in title
        except Exception:
            return False

    def handle_error(self, failure: Any) -> None:
        """Handle request errors.

        Args:
            failure: Twisted Failure object.
        """
        self.logger.error(f"Request failed: {failure.getErrorMessage()}")
        self.logger.error(
            f"Failed URL: {failure.request.url if hasattr(failure, 'request') else 'unknown'}"
        )

        status = None
        try:
            if hasattr(failure, "value") and hasattr(failure.value, "response"):
                status = failure.value.response.status
        except Exception:
            status = None

        domain = (
            urlparse(failure.request.url).netloc
            if hasattr(failure, "request")
            else "unknown"
        )
        if status in {403, 429, 503} and domain != "unknown":
            self._domain_error_counts[domain] = self._domain_error_counts.get(domain, 0) + 1
            if self._domain_error_counts[domain] >= self.max_domain_errors:
                self._mark_domain_blocked(domain, f"too_many_errors_{status}")

        # Best-effort crawl log for failures
        self._log_crawl_entry(
            page_url=failure.request.url if hasattr(failure, "request") else "unknown",
            domain=domain,
            status=status,
            images_found=0,
            error_message=failure.getErrorMessage(),
        )

    def closed(self, reason: str) -> None:
        """Called when spider closes.

        Args:
            reason: Why the spider closed.
        """
        self.logger.info("=" * 50)
        self.logger.info(f"Spider closed: {reason}")
        self.logger.info(f"Pages crawled: {self.pages_crawled}")
        self.logger.info(f"Images found: {self.images_found}")
        self.logger.info("=" * 50)

    def _log_crawl_entry(
        self,
        page_url: str,
        domain: str,
        status: int | None,
        images_found: int,
        error_message: str | None = None,
    ) -> None:
        """Write a minimal crawl_log entry (best-effort).

        This is intentionally lightweight and does not interrupt crawling on failure.
        """
        if not getattr(self, "crawler", None):
            return

        try:
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crawl_log (page_url, domain, status, images_found, error_message, crawl_type)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        page_url,
                        domain,
                        status,
                        images_found,
                        error_message,
                        "discovery",
                    ),
                )
        except Exception as exc:
            self.logger.warning(f"Failed to write crawl_log entry: {exc}")


def _get_int(raw: Any, default: int) -> int:
    """Parse int values from spider args."""
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_bool(raw: Any, default: bool) -> bool:
    """Parse bool values from spider args."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default
