"""Tests for media policy module."""


from processor.media_policy import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_EXTENSIONS,
    is_allowed_content_type,
    is_allowed_url_extension,
    is_rejected_content_type,
)


class TestMediaPolicy:
    """Test media policy configuration."""

    def test_allowed_content_types(self) -> None:
        """Test that only JPEG, PNG, and WEBP are allowed."""
        assert "image/jpeg" in ALLOWED_CONTENT_TYPES
        assert "image/jpg" in ALLOWED_CONTENT_TYPES
        assert "image/png" in ALLOWED_CONTENT_TYPES
        assert "image/webp" in ALLOWED_CONTENT_TYPES

        # Explicitly excluded types
        assert "image/svg+xml" not in ALLOWED_CONTENT_TYPES
        assert "image/gif" not in ALLOWED_CONTENT_TYPES
        assert "image/bmp" not in ALLOWED_CONTENT_TYPES
        assert "image/tiff" not in ALLOWED_CONTENT_TYPES

    def test_allowed_extensions(self) -> None:
        """Test that only supported extensions are allowed."""
        assert ".jpg" in ALLOWED_EXTENSIONS
        assert ".jpeg" in ALLOWED_EXTENSIONS
        assert ".png" in ALLOWED_EXTENSIONS
        assert ".webp" in ALLOWED_EXTENSIONS

        # Explicitly excluded extensions
        assert ".svg" not in ALLOWED_EXTENSIONS
        assert ".gif" not in ALLOWED_EXTENSIONS
        assert ".bmp" not in ALLOWED_EXTENSIONS

    def test_is_allowed_content_type_valid(self) -> None:
        """Test allowed content type validation."""
        assert is_allowed_content_type("image/jpeg") is True
        assert is_allowed_content_type("image/jpg") is True
        assert is_allowed_content_type("image/png") is True
        assert is_allowed_content_type("image/webp") is True

        # Case-insensitive
        assert is_allowed_content_type("IMAGE/JPEG") is True
        assert is_allowed_content_type("Image/PNG") is True

        # With parameters
        assert is_allowed_content_type("image/jpeg; charset=utf-8") is True

    def test_is_allowed_content_type_invalid(self) -> None:
        """Test rejection of unsupported content types."""
        assert is_allowed_content_type("image/svg+xml") is False
        assert is_allowed_content_type("image/gif") is False
        assert is_allowed_content_type("image/bmp") is False
        assert is_allowed_content_type("image/tiff") is False
        assert is_allowed_content_type("text/html") is False
        assert is_allowed_content_type("application/pdf") is False

    def test_is_allowed_url_extension_valid(self) -> None:
        """Test URL extension validation for allowed types."""
        assert is_allowed_url_extension("https://example.com/photo.jpg") is True
        assert is_allowed_url_extension("https://example.com/photo.jpeg") is True
        assert is_allowed_url_extension("https://example.com/photo.png") is True
        assert is_allowed_url_extension("https://example.com/photo.webp") is True

        # Case-insensitive
        assert is_allowed_url_extension("https://example.com/PHOTO.JPG") is True

    def test_is_allowed_url_extension_invalid(self) -> None:
        """Test URL extension rejection for unsupported types."""
        assert is_allowed_url_extension("https://example.com/icon.svg") is False
        assert is_allowed_url_extension("https://example.com/anim.gif") is False
        assert is_allowed_url_extension("https://example.com/image.bmp") is False
        assert is_allowed_url_extension("https://example.com/doc.pdf") is False

    def test_is_rejected_content_type(self) -> None:
        """Test explicit rejection list."""
        assert is_rejected_content_type("image/svg+xml") is True
        assert is_rejected_content_type("image/gif") is True
        assert is_rejected_content_type("image/bmp") is True
        assert is_rejected_content_type("image/tiff") is True

        # Allowed types should not be in rejection list
        assert is_rejected_content_type("image/jpeg") is False
        assert is_rejected_content_type("image/png") is False

    def test_rejection_reason_constants(self) -> None:
        """Test that rejection reason constants are defined."""
        from processor.media_policy import (
            REJECTION_REASON_FILE_TOO_LARGE,
            REJECTION_REASON_FILE_TOO_SMALL,
            REJECTION_REASON_HTTP_ERROR,
            REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL,
            REJECTION_REASON_INVALID_IMAGE_PAYLOAD,
            REJECTION_REASON_MISSING_RESPONSE,
            REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE,
        )

        # Verify constants are strings
        assert isinstance(REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE, str)
        assert isinstance(REJECTION_REASON_FILE_TOO_SMALL, str)
        assert isinstance(REJECTION_REASON_FILE_TOO_LARGE, str)
        assert isinstance(REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL, str)
        assert isinstance(REJECTION_REASON_INVALID_IMAGE_PAYLOAD, str)
        assert isinstance(REJECTION_REASON_HTTP_ERROR, str)
        assert isinstance(REJECTION_REASON_MISSING_RESPONSE, str)
