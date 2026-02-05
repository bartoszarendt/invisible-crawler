"""Test HTML for InvisibleCrawler spider validation.

This file contains sample HTML with various image sources
for testing the discovery spider.
"""

SAMPLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta property="og:image" content="https://example.com/og-image.jpg">
    <title>Test Page</title>
</head>
<body>
    <h1>Image Discovery Test</h1>

    <!-- Standard img tags -->
    <img src="/images/photo1.jpg" alt="Photo 1">
    <img src="https://example.com/images/photo2.png" alt="Photo 2">
    <img src="photo3.webp" alt="Photo 3">

    <!-- Images with srcset -->
    <img src="responsive.jpg"
         srcset="responsive-400.jpg 400w, responsive-800.jpg 800w"
         sizes="(max-width: 600px) 400px, 800px"
         alt="Responsive image">

    <!-- Picture element with sources -->
    <picture>
        <source srcset="image-large.jpg 2x, image-small.jpg 1x" type="image/jpeg">
        <img src="fallback.jpg" alt="Picture element">
    </picture>

    <!-- Links to follow -->
    <a href="/about">About</a>
    <a href="/contact">Contact</a>
    <a href="https://example.com/page2">Page 2</a>
    <a href="https://other-domain.com/external">External link (should not follow)</a>

    <!-- Non-image resources to skip -->
    <a href="/document.pdf">PDF Document</a>

    <!-- Same page link -->
    <a href="#section">Same page anchor</a>
</body>
</html>
"""
