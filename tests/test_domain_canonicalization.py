"""Tests for domain canonicalization.

Tests domain normalization rules including:
- Scheme stripping
- www prefix removal
- Port removal
- Lowercasing
- IDN/punycode conversion
- Subdomain handling
"""

import pytest

from processor.domain_canonicalization import canonicalize_domain, extract_domain_from_url


class TestCanonicalizeDomain:
    """Test domain canonicalization function."""

    def test_basic_domain(self):
        """Test basic domain extraction."""
        assert canonicalize_domain("example.com") == "example.com"

    def test_with_scheme(self):
        """Test that scheme is stripped."""
        assert canonicalize_domain("https://example.com") == "example.com"
        assert canonicalize_domain("http://example.com") == "example.com"

    def test_with_path(self):
        """Test that path is stripped."""
        assert canonicalize_domain("https://example.com/path/to/page") == "example.com"

    def test_www_prefix_stripped(self):
        """Test that www prefix is removed."""
        assert canonicalize_domain("www.example.com") == "example.com"
        assert canonicalize_domain("https://www.example.com") == "example.com"
        assert canonicalize_domain("http://www.example.com/path") == "example.com"

    def test_case_lowercased(self):
        """Test that domain is lowercased."""
        assert canonicalize_domain("Example.COM") == "example.com"
        assert canonicalize_domain("HTTPS://EXAMPLE.COM") == "example.com"
        assert canonicalize_domain("WWW.EXAMPLE.COM") == "example.com"

    def test_port_stripped(self):
        """Test that default ports are stripped."""
        assert canonicalize_domain("example.com:443") == "example.com"
        assert canonicalize_domain("example.com:80") == "example.com"
        assert canonicalize_domain("https://example.com:443/path") == "example.com"

    def test_trailing_dot_stripped(self):
        """Test that trailing dot is removed."""
        assert canonicalize_domain("example.com.") == "example.com"
        assert canonicalize_domain("www.example.com.") == "example.com"

    def test_subdomain_kept_by_default(self):
        """Test that subdomains are kept by default."""
        assert canonicalize_domain("blog.example.com") == "blog.example.com"
        assert canonicalize_domain("shop.example.com") == "shop.example.com"
        assert canonicalize_domain("a.b.c.example.com") == "a.b.c.example.com"

    def test_subdomain_stripped_when_requested(self):
        """Test that subdomains can be stripped to registrable domain."""
        result = canonicalize_domain("blog.example.com", strip_subdomains=True)
        # Should reduce to registrable domain (public suffix + 1)
        assert result == "example.com"

    def test_idn_punycode_conversion(self):
        """Test IDN to punycode conversion."""
        # German umlaut domain
        assert canonicalize_domain("münchen.de") == "xn--mnchen-3ya.de"
        # Russian Cyrillic domain
        assert canonicalize_domain("пример.рф") == "xn--e1afmkfd.xn--p1ai"

    def test_idn_with_www(self):
        """Test IDN with www prefix."""
        assert canonicalize_domain("www.münchen.de") == "xn--mnchen-3ya.de"

    def test_combined_transformations(self):
        """Test multiple transformations together."""
        result = canonicalize_domain("HTTPS://WWW.Example.COM:443/Path?query=1")
        assert result == "example.com"

    def test_empty_input_raises(self):
        """Test that empty input raises ValueError."""
        with pytest.raises(ValueError, match="Invalid URL"):
            canonicalize_domain("")

    def test_none_input_raises(self):
        """Test that None input raises ValueError."""
        with pytest.raises(ValueError, match="Invalid URL"):
            canonicalize_domain(None)

    def test_invalid_url_raises(self):
        """Test that invalid URLs raise ValueError."""
        # Empty string should raise ValueError
        with pytest.raises(ValueError, match="Invalid URL"):
            canonicalize_domain("")


class TestExtractDomainFromUrl:
    """Test domain extraction without full canonicalization."""

    def test_extract_basic(self):
        """Test basic extraction."""
        assert extract_domain_from_url("https://example.com/path") == "example.com"

    def test_extract_returns_lowercase(self):
        """Test that extracted domain is lowercased."""
        assert extract_domain_from_url("https://Example.COM/path") == "example.com"

    def test_extract_no_scheme(self):
        """Test extraction without scheme."""
        assert extract_domain_from_url("example.com/path") == "example.com"

    def test_extract_with_port(self):
        """Test extraction preserves port."""
        result = extract_domain_from_url("https://example.com:8080/path")
        assert ":8080" in result

    def test_extract_empty_returns_none(self):
        """Test that empty input returns None."""
        assert extract_domain_from_url("") is None
        assert extract_domain_from_url(None) is None
