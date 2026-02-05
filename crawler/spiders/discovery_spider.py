"""Discovery spider for InvisibleCrawler.

This spider crawls domains from a seed file, extracting image URLs
from HTML pages and following same-domain links.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from scrapy import Spider
from scrapy.http import Request, Response


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
        self.images_found: int = 0
        self.pages_crawled: int = 0
        self._domains: list[str] = []

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

        # Extract image URLs from the page
        image_urls = self._extract_image_urls(response, current_domain)
        self.images_found += len(image_urls)

        self.logger.info(f"Found {len(image_urls)} images on {response.url}")

        # Yield image data for processing
        for img_url in image_urls:
            yield {
                "type": "image",
                "url": img_url,
                "source_page": response.url,
                "source_domain": current_domain,
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

        return True  # Be permissive in Phase 1

    def handle_error(self, failure: Any) -> None:
        """Handle request errors.

        Args:
            failure: Twisted Failure object.
        """
        self.logger.error(f"Request failed: {failure.getErrorMessage()}")
        self.logger.error(
            f"Failed URL: {failure.request.url if hasattr(failure, 'request') else 'unknown'}"
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
