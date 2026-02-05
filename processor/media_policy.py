"""Media policy configuration for InvisibleCrawler.

Centralizes allowed image types across spider and fetchers.
Enforces strict type filtering: JPEG, PNG, and WEBP only.
"""

from typing import Final

# Supported content types for image fetching
# Restricts to formats that support reliable fingerprinting
ALLOWED_CONTENT_TYPES: Final[set[str]] = {
    "image/jpeg",
    "image/jpg",  # Non-standard but sometimes used
    "image/png",
    "image/webp",
}

# Supported file extensions for URL pre-filtering
ALLOWED_EXTENSIONS: Final[tuple[str, ...]] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
)

# Rejected types (for explicit filtering and logging)
REJECTED_CONTENT_TYPES: Final[set[str]] = {
    "image/svg+xml",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/x-icon",
    "image/vnd.microsoft.icon",
}

# Rejection reason constants for structured metrics
REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
REJECTION_REASON_FILE_TOO_SMALL = "file_too_small"
REJECTION_REASON_FILE_TOO_LARGE = "file_too_large"
REJECTION_REASON_IMAGE_DIMENSIONS_TOO_SMALL = "image_dimensions_too_small"
REJECTION_REASON_INVALID_IMAGE_PAYLOAD = "invalid_image_payload"
REJECTION_REASON_HTTP_ERROR = "http_error"
REJECTION_REASON_MISSING_RESPONSE = "missing_response"
REJECTION_REASON_MISSING_CONTENT_TYPE = "missing_content_type"


def is_allowed_content_type(content_type: str) -> bool:
    """Check if content type is allowed.

    Args:
        content_type: MIME type string (e.g., "image/jpeg").

    Returns:
        True if content type is allowed, False otherwise.
    """
    # Normalize and strip parameters
    normalized = content_type.lower().split(";")[0].strip()
    return normalized in ALLOWED_CONTENT_TYPES


def is_allowed_url_extension(url: str) -> bool:
    """Check if URL has an allowed image extension.

    Args:
        url: Image URL to check.

    Returns:
        True if URL has allowed extension, False otherwise.
    """
    url_lower = url.lower()
    return any(url_lower.endswith(ext) for ext in ALLOWED_EXTENSIONS)


def is_rejected_content_type(content_type: str) -> bool:
    """Check if content type is explicitly rejected.

    Args:
        content_type: MIME type string.

    Returns:
        True if content type is in rejection list.
    """
    normalized = content_type.lower().split(";")[0].strip()
    return normalized in REJECTED_CONTENT_TYPES


def format_rejection_reason(reason_key: str, details: str = "") -> str:
    """Format a canonical rejection reason with optional details.

    Args:
        reason_key: One of the REJECTION_REASON_* constants.
        details: Optional additional context (e.g., actual content-type, size).

    Returns:
        Formatted rejection message.
    """
    if details:
        return f"{reason_key}: {details}"
    return reason_key


def validate_content_type(content_type: str | None) -> tuple[bool, str | None]:
    """Validate content type header for image acceptance.

    Args:
        content_type: Content-Type header value (may be None or empty).

    Returns:
        Tuple of (is_valid, error_reason). error_reason is None if valid.
    """
    if not content_type:
        return False, format_rejection_reason(REJECTION_REASON_MISSING_CONTENT_TYPE)

    normalized = content_type.lower().split(";")[0].strip()

    if not normalized:
        return False, format_rejection_reason(REJECTION_REASON_MISSING_CONTENT_TYPE)

    if not is_allowed_content_type(normalized):
        return False, format_rejection_reason(REJECTION_REASON_UNSUPPORTED_CONTENT_TYPE, normalized)

    return True, None
