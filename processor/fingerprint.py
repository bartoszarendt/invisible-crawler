"""Image fingerprinting module for InvisibleCrawler.

Generates stable fingerprints for images to enable deduplication
and future similarity matching.
"""

import hashlib
import logging
from io import BytesIO
from typing import Any

import imagehash
from PIL import Image

logger = logging.getLogger(__name__)


class ImageFingerprinter:
    """Generates fingerprints for image content.

    Implements multiple fingerprinting strategies:
    - Binary hash (SHA-256): For exact deduplication
    - Perceptual hash (pHash/dHash): For similarity detection (Phase 2)

    Attributes:
        normalize_size: Size to resize images before perceptual hashing.
        hash_size: Size for perceptual hash computation.
    """

    def __init__(self, normalize_size: tuple[int, int] = (64, 64), hash_size: int = 8) -> None:
        """Initialize the fingerprinter.

        Args:
            normalize_size: Target size for normalization before hashing.
            hash_size: Size for perceptual hash (8 = 64-bit hash, 16 = 256-bit hash).
        """
        self.normalize_size = normalize_size
        self.hash_size = hash_size

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

    def compute_phash(self, content: bytes) -> str | None:
        """Compute perceptual hash (pHash) of image.

        pHash is robust to minor modifications like compression
        artifacts, resizing, and small edits.

        Args:
            content: Raw binary image data.

        Returns:
            Hexadecimal hash string or None if computation fails.
        """
        try:
            img = Image.open(BytesIO(content))
            # Convert to RGB if necessary
            img_rgb = img.convert("RGB") if img.mode != "RGB" else img

            phash = imagehash.phash(img_rgb, hash_size=self.hash_size)
            return str(phash)
        except Exception as e:
            logger.debug(f"Failed to compute pHash: {e}")
            return None

    def compute_dhash(self, content: bytes) -> str | None:
        """Compute difference hash (dHash) of image.

        dHash is good for detecting image modifications and
        is faster than pHash for large datasets.

        Args:
            content: Raw binary image data.

        Returns:
            Hexadecimal hash string or None if computation fails.
        """
        try:
            img = Image.open(BytesIO(content))
            # Convert to RGB if necessary
            img_rgb = img.convert("RGB") if img.mode != "RGB" else img

            dhash = imagehash.dhash(img_rgb, hash_size=self.hash_size)
            return str(dhash)
        except Exception as e:
            logger.debug(f"Failed to compute dHash: {e}")
            return None

    def compute_all_hashes(self, content: bytes) -> dict[str, Any]:
        """Compute all available hashes for an image.

        Args:
            content: Raw binary image data.

        Returns:
            Dictionary containing:
                - sha256: SHA-256 binary hash
                - phash: Perceptual hash (pHash)
                - dhash: Difference hash (dHash)
                - width: Image width
                - height: Image height
                - format: Image format
        """
        result: dict[str, Any] = {
            "sha256": self.binary_hash(content),
            "phash": self.compute_phash(content),
            "dhash": self.compute_dhash(content),
        }

        # Get image info
        try:
            img = Image.open(BytesIO(content))
            result["width"] = img.width
            result["height"] = img.height
            result["format"] = img.format
            result["mode"] = img.mode
        except Exception as e:
            logger.debug(f"Failed to get image info: {e}")
            result["width"] = None
            result["height"] = None
            result["format"] = None
            result["mode"] = None

        return result

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
            img_rgb = img.convert("RGB") if img.mode != "RGB" else img

            # Strip EXIF by not saving it
            output = BytesIO()
            img_rgb.save(output, format="PNG", optimize=True)
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


def compute_perceptual_hashes(content: bytes) -> dict[str, str | None]:
    """Compute perceptual hashes for an image.

    Convenience function that computes pHash and dHash without
    requiring class instantiation.

    Args:
        content: Raw binary image data.

    Returns:
        Dictionary with 'phash' and 'dhash' keys.
    """
    fingerprinter = ImageFingerprinter()
    return {
        "phash": fingerprinter.compute_phash(content),
        "dhash": fingerprinter.compute_dhash(content),
    }
