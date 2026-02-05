"""Image fingerprinting module for InvisibleCrawler.

Generates stable fingerprints for images to enable deduplication
and future similarity matching.
"""

import hashlib
import logging
from io import BytesIO
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)


class ImageFingerprinter:
    """Generates fingerprints for image content.

    Implements multiple fingerprinting strategies:
    - Binary hash (SHA-256): For exact deduplication
    - Perceptual hash (pHash/dHash): For similarity detection (Phase 2)

    Attributes:
        normalize_size: Size to resize images before perceptual hashing.
    """

    def __init__(self, normalize_size: tuple[int, int] = (64, 64)) -> None:
        """Initialize the fingerprinter.

        Args:
            normalize_size: Target size for normalization before hashing.
        """
        self.normalize_size = normalize_size

    def binary_hash(self, content: bytes) -> str:
        """Generate SHA-256 hash of image content.

        This provides exact deduplication - two images with
        identical binary content will have identical hashes.

        Args:
            content: Raw binary image data.

        Returns:
            Hexadecimal SHA-256 hash string.
        """
        return hashlib.sha256(content).hexdigest()

    def get_image_info(self, content: bytes) -> dict[str, Any]:
        """Extract basic image information.

        Args:
            content: Raw binary image data.

        Returns:
            Dictionary with image metadata:
            - width: Image width in pixels
            - height: Image height in pixels
            - format: Image format (e.g., 'JPEG', 'PNG')
            - mode: Color mode (e.g., 'RGB', 'RGBA')

        Raises:
            ValueError: If content is not a valid image.
        """
        try:
            img = Image.open(BytesIO(content))
            return {
                "width": img.width,
                "height": img.height,
                "format": img.format,
                "mode": img.mode,
            }
        except Exception as e:
            logger.error(f"Failed to parse image: {e}")
            raise ValueError(f"Invalid image content: {e}")

    def normalize_image(self, content: bytes) -> bytes:
        """Normalize image to canonical format.

        Normalization steps:
        1. Decode image
        2. Convert to RGB colorspace
        3. Strip EXIF metadata
        4. Re-encode to PNG (lossless)

        Args:
            content: Raw binary image data.

        Returns:
            Normalized image as PNG bytes.

        Raises:
            ValueError: If content cannot be normalized.
        """
        try:
            img = Image.open(BytesIO(content))

            # Convert to RGB (handles RGBA, palette, etc.)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Strip EXIF by not saving it
            output = BytesIO()
            img.save(output, format="PNG", optimize=True)
            output.seek(0)

            return output.read()

        except Exception as e:
            logger.error(f"Failed to normalize image: {e}")
            raise ValueError(f"Image normalization failed: {e}")

    def normalized_hash(self, content: bytes) -> str:
        """Generate hash of normalized image.

        This is more resilient to format variations than binary_hash.
        Two images with identical visual content but different encodings
        should produce the same normalized hash.

        Args:
            content: Raw binary image data.

        Returns:
            Hexadecimal SHA-256 hash of normalized image.
        """
        normalized = self.normalize_image(content)
        return self.binary_hash(normalized)


def compute_sha256(content: bytes) -> str:
    """Compute SHA-256 hash of content.

    Convenience function for simple hashing without class instantiation.

    Args:
        content: Binary content to hash.

    Returns:
        Hexadecimal SHA-256 hash.
    """
    return hashlib.sha256(content).hexdigest()
