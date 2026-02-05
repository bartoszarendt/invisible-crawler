"""Tests for the discovery spider."""

import pytest
from scrapy.http import HtmlResponse, Request

from crawler.spiders.discovery_spider import DiscoverySpider
from tests.fixtures import SAMPLE_HTML


class TestDiscoverySpider:
    """Test cases for DiscoverySpider."""

    @pytest.fixture
    def spider(self) -> DiscoverySpider:
        """Create a spider instance for testing."""
        return DiscoverySpider(seeds="config/test_seeds.txt")

    def test_spider_name(self, spider: DiscoverySpider) -> None:
        """Test spider has correct name."""
        assert spider.name == "discovery"

    def test_extract_image_urls_from_img_tags(self, spider: DiscoverySpider) -> None:
        """Test extraction of image URLs from <img> tags."""
        request = Request(url="https://example.com/")
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=SAMPLE_HTML.encode("utf-8"),
        )

        images = spider._extract_image_urls(response, "example.com")

        # Should find images from src attributes
        assert any("photo1.jpg" in url for url in images)
        assert any("photo2.png" in url for url in images)
        assert any("photo3.webp" in url for url in images)

    def test_extract_image_urls_from_srcset(self, spider: DiscoverySpider) -> None:
        """Test extraction of image URLs from srcset attributes."""
        request = Request(url="https://example.com/")
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=SAMPLE_HTML.encode("utf-8"),
        )

        images = spider._extract_image_urls(response, "example.com")

        # Should find images from srcset
        assert any("responsive-400.jpg" in url for url in images)
        assert any("responsive-800.jpg" in url for url in images)

    def test_extract_image_urls_from_og_meta(self, spider: DiscoverySpider) -> None:
        """Test extraction of image URLs from Open Graph meta tags."""
        request = Request(url="https://example.com/")
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=SAMPLE_HTML.encode("utf-8"),
        )

        images = spider._extract_image_urls(response, "example.com")

        # Should find OG image
        assert any("og-image.jpg" in url for url in images)

    def test_extract_same_domain_links(self, spider: DiscoverySpider) -> None:
        """Test extraction of same-domain links."""
        request = Request(url="https://example.com/")
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=SAMPLE_HTML.encode("utf-8"),
        )

        links = spider._extract_links(response, "example.com")

        # Should find same-domain links
        assert any("about" in url for url in links)
        assert any("contact" in url for url in links)
        assert any("page2" in url for url in links)

        # Should NOT include external links
        assert not any("other-domain.com" in url for url in links)

    def test_parse_yields_image_items(self, spider: DiscoverySpider) -> None:
        """Test that parse yields image Request objects."""
        request = Request(url="https://example.com/")
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=SAMPLE_HTML.encode("utf-8"),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

        items = list(spider.parse(response))

        # Should yield Request objects for images (not items)
        image_requests = [
            item for item in items if isinstance(item, Request) and "/image" in item.url
        ]
        assert len(image_requests) > 0

        # Image requests should have callback and metadata
        for req in image_requests:
            assert req.callback is not None
            assert "source_page" in req.meta
            assert "source_domain" in req.meta

    def test_parse_yields_follow_requests(self, spider: DiscoverySpider) -> None:
        """Test that parse yields follow-up requests."""
        request = Request(url="https://example.com/")
        response = HtmlResponse(
            url="https://example.com/",
            request=request,
            body=SAMPLE_HTML.encode("utf-8"),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

        items = list(spider.parse(response))

        # Should yield requests to follow links
        requests = [item for item in items if isinstance(item, Request)]
        assert len(requests) > 0

    def test_is_valid_image_url(self, spider: DiscoverySpider) -> None:
        """Test image URL validation."""
        # Valid image URLs
        assert spider._is_valid_image_url("https://example.com/image.jpg")
        assert spider._is_valid_image_url("https://example.com/image.png")
        assert spider._is_valid_image_url("https://example.com/image.webp")

        # Invalid URLs
        assert not spider._is_valid_image_url("")
        assert not spider._is_valid_image_url("/relative/path.jpg")  # No scheme
        assert not spider._is_valid_image_url("https://")  # No netloc

    def test_parse_srcset(self, spider: DiscoverySpider) -> None:
        """Test srcset parsing."""
        srcset = "image-400.jpg 400w, image-800.jpg 800w, image-1200.jpg 1200w"
        urls = spider._parse_srcset(srcset)

        assert "image-400.jpg" in urls
        assert "image-800.jpg" in urls
        assert "image-1200.jpg" in urls
        assert len(urls) == 3
