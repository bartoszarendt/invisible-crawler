"""Discovery spider for InvisibleCrawler.

This spider crawls domains from a seed file, extracting image URLs
from HTML pages and following same-domain links.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from scrapy import Spider, signals
from scrapy.http import Request, Response, TextResponse

from crawler.redis_keys import start_urls_key
from env_config import (
    get_crawler_max_pages,
    get_default_max_pages_per_run,
    get_domain_canonicalization_strip_subdomains,
    get_enable_domain_tracking,
    get_enable_per_domain_budget,
    get_redis_url,
)
from processor.domain_canonicalization import canonicalize_domain
from processor.media_policy import ALLOWED_EXTENSIONS
from storage.db import get_cursor
from storage.domain_repository import (
    clear_frontier_checkpoint,
    get_domain,
    update_domain_stats,
    update_frontier_checkpoint,
    upsert_domain,
)


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

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> "DiscoverySpider":
        """Create spider instance from crawler.

        Args:
            crawler: Scrapy Crawler instance.
            *args: Positional arguments for spider.
            **kwargs: Keyword arguments for spider.

        Returns:
            New spider instance.
        """
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.spider_opened, signal=signals.spider_opened)
        return spider

    def spider_opened(self, spider: Spider) -> None:
        """Signal handler called when spider is opened.

        Creates a crawl run record in the database.

        Args:
            spider: The spider instance.
        """
        try:
            with get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO crawl_runs (mode, status, seed_source)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (self.crawl_type, "running", self.seeds_file or "redis"),
                )
                self.crawl_run_id = cursor.fetchone()[0]
                self.logger.info(
                    f"Created crawl run: {self.crawl_run_id} (mode: {self.crawl_type})"
                )
        except Exception as e:
            self.logger.warning(f"Failed to create crawl run: {e}")
            self.crawl_run_id = None

    def __init__(self, seeds: str | None = None, **kwargs: Any) -> None:
        """Initialize the spider with seed domains.

        Args:
            seeds: Path to seed file containing domains (one per line).
            **kwargs: Additional Scrapy spider kwargs.
        """
        super().__init__(**kwargs)
        self.seeds_file = seeds
        self.max_pages = _get_int(kwargs.get("max_pages"), get_crawler_max_pages())
        self.allowlist_file = kwargs.get("allowlist", "config/seed_allowlist.txt")
        self.blocklist_file = kwargs.get("blocklist", "config/seed_blocklist.txt")
        self.max_domain_errors = _get_int(kwargs.get("max_domain_errors"), 3)
        self.block_on_login = _get_bool(kwargs.get("block_on_login"), True)
        self.crawl_type = kwargs.get("crawl_type", "discovery")  # 'discovery' or 'refresh'
        self.images_found: int = 0
        self.pages_crawled: int = 0
        self.images_downloaded: int = 0  # Track successful downloads for crawl_runs
        self.crawl_run_id = None  # Set in spider_opened
        self._domains: list[str] = []
        self._allowlist = self._load_domain_list(self.allowlist_file)
        self._blocklist = self._load_domain_list(self.blocklist_file)
        self._blocked_domains_runtime: set[str] = set()
        self._domain_error_counts: dict[str, int] = {}
        # Domain tracking (Phase A)
        self.enable_domain_tracking = get_enable_domain_tracking()
        self.strip_subdomains = get_domain_canonicalization_strip_subdomains()
        self._domain_stats: dict[str, dict[str, int]] = {}  # Track per-domain stats
        self._blocked_domains_canonical: set[str] = set()  # Canonicalized blocked domains
        # Per-domain budget tracking (Phase B)
        self.enable_per_domain_budget = get_enable_per_domain_budget()
        self.max_pages_per_run = get_default_max_pages_per_run()
        self._domain_pages_crawled: dict[str, int] = {}  # Pages crawled per domain this run
        self._domain_pending_urls: dict[str, list[dict[str, Any]]] = {}

    def start_requests(self) -> Any:
        """Generate initial requests from seed domains.

        Supports two modes:
        - Phase 2 (Redis): Consume seeds from Redis start_urls sorted set
        - Phase 1 (File): Read seeds from local seed file

        Yields:
            Request objects for each seed domain.
        """
        # Try Redis start_urls first (Phase 2 mode)
        redis_seeds = list(self._get_redis_start_urls())
        if redis_seeds:
            self.logger.info(f"Using Redis start_urls: {len(redis_seeds)} seeds")
            for url in redis_seeds:
                domain_netloc = urlparse(url).netloc
                if self._allowlist and domain_netloc not in self._allowlist:
                    self.logger.info(f"Skipping seed (not in allowlist): {url}")
                    continue
                if domain_netloc in self._blocklist:
                    self.logger.info(f"Skipping seed (blocked): {url}")
                    continue

                self._domains.append(url)
                self.logger.info(f"Adding seed domain: {url}")

                # Domain tracking: upsert domain before yielding
                if self.enable_domain_tracking:
                    try:
                        canonical_domain = canonicalize_domain(url, self.strip_subdomains)
                        upsert_domain(
                            domain=canonical_domain,
                            source="redis",
                            seed_rank=None,
                        )
                        self.logger.debug(f"Upserted domain: {canonical_domain} (source: redis)")
                    except Exception as e:
                        self.logger.warning(f"Failed to upsert domain for {url}: {e}")

                # Yield requests (with checkpoint resume if enabled)
                yield from self._yield_start_requests(url, domain_netloc)
            return

        # Fall back to file-based seeds (Phase 1 mode)
        if not self.seeds_file:
            self.logger.error(
                "No seeds file provided and Redis start_urls empty. Use -a seeds=config/seeds.txt"
            )
            return

        seeds_path = Path(self.seeds_file)
        if not seeds_path.exists():
            self.logger.error(f"Seeds file not found: {seeds_path}")
            return

        self.logger.info(f"Using file-based seeds: {seeds_path}")

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

                # Domain tracking: upsert domain before yielding
                if self.enable_domain_tracking:
                    try:
                        canonical_domain = canonicalize_domain(domain, self.strip_subdomains)
                        upsert_domain(
                            domain=canonical_domain,
                            source=self.seeds_file or "file",
                            seed_rank=None,
                        )
                        self.logger.debug(f"Upserted domain: {canonical_domain} (source: file)")
                    except Exception as e:
                        self.logger.warning(f"Failed to upsert domain for {domain}: {e}")

                # Yield requests (with checkpoint resume if enabled)
                yield from self._yield_start_requests(domain, urlparse(domain).netloc)

    def _get_redis_start_urls(self) -> list[str]:
        """Fetch start URLs from Redis sorted set.

        Returns:
            List of URLs from Redis, or empty list if Redis unavailable.
        """
        redis_url = get_redis_url()

        try:
            import redis

            client = redis.from_url(redis_url, socket_connect_timeout=2)

            # Get URLs from sorted set (ordered by priority)
            queue_key = start_urls_key(self.name)
            # zrange returns members in order of score (priority)
            urls = client.zrange(queue_key, 0, -1)

            # Decode bytes to strings
            return [url.decode("utf-8") if isinstance(url, bytes) else url for url in urls]
        except Exception as e:
            self.logger.debug(f"Could not fetch Redis start_urls: {e}")
            return []

    def _yield_start_requests(self, url: str, domain_netloc: str) -> Any:
        """Yield start requests for a domain, with optional checkpoint resume.

        When per-domain budgets are enabled and a checkpoint exists for the domain,
        loads pending URLs from the checkpoint and yields those instead of the root URL.

        Args:
            url: Root URL of the domain
            domain_netloc: Domain netloc for meta

        Yields:
            Request objects for the domain (from checkpoint or fresh start)
        """
        # Check for checkpoint if per-domain budget is enabled
        if self.enable_per_domain_budget and self.enable_domain_tracking:
            try:
                canonical_domain = canonicalize_domain(url, self.strip_subdomains)
                domain_row = get_domain(canonical_domain)

                if domain_row and domain_row.get("frontier_checkpoint_id"):
                    checkpoint_id = domain_row["frontier_checkpoint_id"]
                    self.logger.info(f"Found checkpoint for {canonical_domain}: {checkpoint_id}")

                    # Load checkpoint from Redis
                    try:
                        import redis

                        redis_client = redis.from_url(get_redis_url(), socket_connect_timeout=2)
                        from storage.frontier_checkpoint import (
                            delete_checkpoint,
                            load_checkpoint,
                        )

                        checkpoint_urls = load_checkpoint(checkpoint_id, redis_client)

                        if checkpoint_urls:
                            self.logger.info(
                                f"Resuming {canonical_domain} from checkpoint: "
                                f"{len(checkpoint_urls)} URLs"
                            )

                            for entry in checkpoint_urls:
                                yield Request(
                                    url=entry["url"],
                                    callback=self.parse,
                                    errback=self.handle_error,
                                    meta={
                                        "depth": entry["depth"],
                                        "domain": domain_netloc,
                                    },
                                )

                            # Clear checkpoint after successful load
                            # NOTE: This deletes the checkpoint immediately, which means
                            # if the spider crashes before processing all resumed URLs,
                            # those URLs are lost. This is acceptable for Phase B because:
                            # 1. Worst case is re-crawling pages from root, not data loss
                            # 2. A new checkpoint will be created if budget is hit again
                            # 3. Phase C will implement more robust claim/lease protocols
                            delete_checkpoint(checkpoint_id, redis_client)
                            clear_frontier_checkpoint(canonical_domain)
                            self.logger.info(
                                f"Cleared checkpoint for {canonical_domain} after resume"
                            )
                            return  # Don't yield root URL, we resumed from checkpoint
                        else:
                            self.logger.warning(
                                f"Checkpoint {checkpoint_id} empty or not found, starting fresh"
                            )
                            clear_frontier_checkpoint(canonical_domain)

                    except Exception as e:
                        self.logger.warning(
                            f"Failed to load checkpoint for {canonical_domain}: {e}. "
                            f"Falling back to root URL."
                        )
                        # Continue to yield root URL below

            except Exception as e:
                self.logger.debug(f"Could not check for checkpoint: {e}")

        # Fresh start: yield root URL
        yield Request(
            url=url,
            callback=self.parse,
            errback=self.handle_error,
            meta={"depth": 0, "domain": domain_netloc},
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
            response.headers.get("Content-Type", b"").decode("utf-8", errors="ignore").lower()
        )
        is_text_response = isinstance(response, TextResponse)

        # Be more permissive: try to parse if content-type suggests HTML or if it's a text response
        should_parse = (
            is_text_response
            or (content_type and ("html" in content_type or "text" in content_type))
            or (
                not content_type and len(response.body) > 0
            )  # Fallback for responses without content-type
        )

        if not should_parse:
            self.logger.debug(f"Skipping non-HTML response: {content_type} for {response.url}")
            return

        if self.block_on_login and self._looks_like_login_page(response):
            self._mark_domain_blocked(current_domain, "login_required")
            # Also track canonicalized version for domain stats
            if self.enable_domain_tracking:
                try:
                    canonical_domain = canonicalize_domain(current_domain, self.strip_subdomains)
                    self._blocked_domains_canonical.add(canonical_domain)
                except Exception:
                    pass
            return

        # Extract image URLs from the page
        image_urls = self._extract_image_urls(response, current_domain)
        self.images_found += len(image_urls)

        # Domain tracking: update per-domain stats
        if self.enable_domain_tracking:
            try:
                canonical_domain = canonicalize_domain(current_domain, self.strip_subdomains)
                if canonical_domain not in self._domain_stats:
                    self._domain_stats[canonical_domain] = {
                        "pages": 0,
                        "images_found": 0,
                    }
                self._domain_stats[canonical_domain]["pages"] += 1
                self._domain_stats[canonical_domain]["images_found"] += len(image_urls)
            except Exception as e:
                self.logger.debug(f"Failed to update domain stats for {current_domain}: {e}")

        self.logger.info(f"Found {len(image_urls)} images on {response.url}")

        # Best-effort crawl log entry (only when running under Scrapy)
        self._log_crawl_entry(
            page_url=response.url,
            domain=current_domain,
            status=response.status,
            images_found=len(image_urls),
            crawl_type=self.crawl_type,
        )

        # Yield image download requests with callback
        for img_url in image_urls:
            yield Request(
                url=img_url,
                callback=self.parse_image,
                errback=self.handle_image_error,
                meta={
                    "source_page": response.url,
                    "source_domain": current_domain,
                    "crawl_type": self.crawl_type,  # Propagate actual crawl type
                    "crawl_run_id": self.crawl_run_id,  # Pass run ID for stats tracking
                },
                priority=response.meta.get("depth", 0) + 1,  # Lower priority than page crawling
                dont_filter=False,
            )

        # Extract links before budget check so we can track them
        extracted_links = self._extract_links(response, current_domain)

        # Check budget BEFORE incrementing counter (so current page is allowed)
        if self.enable_per_domain_budget:
            pages_crawled_before = self._domain_pages_crawled.get(current_domain, 0)
            # 0 means unlimited budget
            if self.max_pages_per_run > 0 and pages_crawled_before >= self.max_pages_per_run:
                self.logger.info(
                    f"Domain {current_domain} reached budget: "
                    f"{pages_crawled_before}/{self.max_pages_per_run} pages"
                )
                should_yield_links = False
            else:
                should_yield_links = True
        elif self.max_pages > 0 and self.pages_crawled >= self.max_pages:
            # Global budget check (Phase A behavior)
            should_yield_links = False
        else:
            should_yield_links = True

        # Now increment the counter for this page
        if self.enable_per_domain_budget:
            self._domain_pages_crawled[current_domain] = (
                self._domain_pages_crawled.get(current_domain, 0) + 1
            )

            # Always track URLs for potential checkpoint
            for next_url in extracted_links:
                next_depth = current_depth + 1
                self._domain_pending_urls.setdefault(current_domain, []).append(
                    {"url": next_url, "depth": next_depth}
                )

        # Yield links if budget permits
        if should_yield_links:
            for next_url in extracted_links:
                next_depth = current_depth + 1
                yield Request(
                    url=next_url,
                    callback=self.parse,
                    errback=self.handle_error,
                    meta={
                        "depth": next_depth,
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

        # Check for allowed image extensions (only JPEG, PNG, WEBP)
        path_lower = parsed.path.lower()

        # Strict extension check only - no heuristics
        if any(path_lower.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            return True

        # Also check if query string contains valid extension (e.g., ?file=image.jpg)
        query_lower = parsed.query.lower()
        if any(ext in query_lower for ext in ALLOWED_EXTENSIONS):
            return True

        # Reject URLs that don't have explicit allowed extensions
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

    def parse_image(self, response: Response) -> Any:
        """Parse downloaded image response.

        Args:
            response: Scrapy Response with image data.

        Yields:
            Item dict with image metadata for pipeline processing.
        """
        # Extract metadata from response
        meta = response.meta
        source_page = meta.get("source_page", "")
        source_domain = meta.get("source_domain", "")
        crawl_type = meta.get("crawl_type", "discovery")
        crawl_run_id = meta.get("crawl_run_id")

        # Yield item with response for pipeline to process
        yield {
            "type": "image",
            "url": response.url,
            "source_page": source_page,
            "source_domain": source_domain,
            "crawl_type": crawl_type,
            "crawl_run_id": crawl_run_id,  # Pass for pipeline stats
            "response": response,  # Attach response for pipeline processing
        }

    def handle_image_error(self, failure: Any) -> None:
        """Handle image download failures.

        Args:
            failure: Twisted Failure object.
        """
        url = failure.request.url if hasattr(failure, "request") else "unknown"
        self.logger.debug(f"Failed to download image {url}: {failure.getErrorMessage()}")
        # Don't re-raise to avoid killing the spider

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

        domain = urlparse(failure.request.url).netloc if hasattr(failure, "request") else "unknown"
        if status in {403, 429, 503} and domain != "unknown":
            self._domain_error_counts[domain] = self._domain_error_counts.get(domain, 0) + 1
            if self._domain_error_counts[domain] >= self.max_domain_errors:
                self._mark_domain_blocked(domain, f"too_many_errors_{status}")
                # Also track canonicalized version for domain stats
                if self.enable_domain_tracking:
                    try:
                        canonical_domain = canonicalize_domain(domain, self.strip_subdomains)
                        self._blocked_domains_canonical.add(canonical_domain)
                    except Exception:
                        pass

        # Best-effort crawl log for failures
        self._log_crawl_entry(
            page_url=failure.request.url if hasattr(failure, "request") else "unknown",
            domain=domain,
            status=status,
            images_found=0,
            error_message=failure.getErrorMessage(),
            crawl_type=self.crawl_type,
        )

    def closed(self, reason: str) -> None:
        """Called when spider closes.

        Args:
            reason: Why the spider closed.
        """
        # Update crawl run if one was created
        if self.crawl_run_id:
            try:
                with get_cursor() as cursor:
                    # Calculate actual images_downloaded from crawl_log
                    cursor.execute(
                        """
                        SELECT COALESCE(SUM(images_downloaded), 0)
                        FROM crawl_log
                        WHERE crawl_run_id = %s
                        """,
                        (self.crawl_run_id,),
                    )
                    total_downloaded = cursor.fetchone()[0]
                    # Update spider counter to match DB value for logging
                    self.images_downloaded = total_downloaded

                    cursor.execute(
                        """
                        UPDATE crawl_runs
                        SET completed_at = CURRENT_TIMESTAMP,
                            status = %s,
                            pages_crawled = %s,
                            images_found = %s,
                            images_downloaded = %s
                        WHERE id = %s
                        """,
                        (
                            "completed" if reason == "finished" else "failed",
                            self.pages_crawled,
                            self.images_found,
                            total_downloaded,
                            self.crawl_run_id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        self.logger.warning(
                            f"Failed to update crawl_run {self.crawl_run_id}: row not found"
                        )
                    else:
                        self.logger.info(
                            f"Updated crawl run: {self.crawl_run_id} (images_downloaded: {total_downloaded})"
                        )
            except Exception as e:
                self.logger.warning(f"Failed to update crawl run: {e}")

        # Phase B: Save frontier checkpoints for domains with pending URLs
        if (
            self.enable_per_domain_budget
            and self.enable_domain_tracking
            and self._domain_pending_urls
        ):
            try:
                import redis

                redis_client = redis.from_url(get_redis_url(), socket_connect_timeout=2)
                from storage.frontier_checkpoint import save_checkpoint

                run_id_str = str(self.crawl_run_id) if self.crawl_run_id else "unknown"

                for domain, pending_urls in self._domain_pending_urls.items():
                    # Only save checkpoint if domain actually hit its budget
                    # (and budget is not unlimited - max_pages_per_run=0 means unlimited)
                    pages_crawled = self._domain_pages_crawled.get(domain, 0)
                    if (
                        pending_urls
                        and self.max_pages_per_run > 0
                        and pages_crawled >= self.max_pages_per_run
                    ):
                        try:
                            canonical_domain = canonicalize_domain(domain, self.strip_subdomains)
                            checkpoint_id = save_checkpoint(
                                canonical_domain, run_id_str, pending_urls, redis_client
                            )
                            update_frontier_checkpoint(
                                canonical_domain, checkpoint_id, len(pending_urls)
                            )
                            self.logger.info(
                                f"Saved frontier checkpoint for {domain}: "
                                f"{len(pending_urls)} URLs (checkpoint: {checkpoint_id})"
                            )
                        except Exception as e:
                            self.logger.warning(f"Failed to save checkpoint for {domain}: {e}")

            except Exception as e:
                self.logger.warning(f"Could not save frontier checkpoints: {e}")

        # Domain tracking: update domain stats for all crawled domains
        if self.enable_domain_tracking and self._domain_stats:
            try:
                self.logger.info(f"Updating domain stats for {len(self._domain_stats)} domains...")
                for domain, stats in self._domain_stats.items():
                    # Determine status based on crawl outcome using canonicalized blocked set
                    if domain in self._blocked_domains_canonical:
                        status = "blocked"
                    else:
                        status = "active"  # Simple for Phase A

                    update_domain_stats(
                        domain=domain,
                        pages_crawled_delta=stats["pages"],
                        images_found_delta=stats["images_found"],
                        status=status,
                        last_crawl_run_id=str(self.crawl_run_id) if self.crawl_run_id else None,
                    )
                    self.logger.debug(
                        f"Updated domain stats: {domain} (pages: {stats['pages']}, images: {stats['images_found']})"
                    )
                self.logger.info(f"Domain stats updated for {len(self._domain_stats)} domains")
            except Exception as e:
                self.logger.warning(f"Failed to update domain stats: {e}")

        self.logger.info("=" * 50)
        self.logger.info(f"Spider closed: {reason}")
        self.logger.info(f"Pages crawled: {self.pages_crawled}")
        self.logger.info(f"Images found: {self.images_found}")
        self.logger.info(f"Images downloaded: {self.images_downloaded}")
        self.logger.info("=" * 50)

    def _log_crawl_entry(
        self,
        page_url: str,
        domain: str,
        status: int | None,
        images_found: int,
        error_message: str | None = None,
        crawl_type: str = "discovery",
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
                    INSERT INTO crawl_log (page_url, domain, status, images_found, error_message, crawl_type, crawl_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        page_url,
                        domain,
                        status,
                        images_found,
                        error_message,
                        crawl_type,
                        self.crawl_run_id,
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
