"""
Extracts article text and cover image from a URL.
- Text: trafilatura (best-in-class content extraction)
- Image: og:image meta tag via BeautifulSoup
"""

import logging
import urllib.request
from dataclasses import dataclass
from typing import Optional

import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


@dataclass
class ParsedArticle:
    url: str
    text: str
    image_url: Optional[str] = None
    title: Optional[str] = None


def fetch_html(url: str) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Failed to fetch URL: {e}")
        return None


def extract_og_image(html: str) -> Optional[str]:
    """Extract og:image from page HTML."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("meta", property="og:image")
        if tag and tag.get("content"):
            return tag["content"]
        # fallback: twitter:image
        tag = soup.find("meta", attrs={"name": "twitter:image"})
        if tag and tag.get("content"):
            return tag["content"]
    except Exception as e:
        logger.warning(f"og:image extraction failed: {e}")
    return None


def extract_og_title(html: str) -> Optional[str]:
    """Extract og:title from page HTML."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("meta", property="og:title")
        if tag and tag.get("content"):
            return tag["content"].strip()
    except Exception:
        pass
    return None


def parse_article(url: str) -> Optional[ParsedArticle]:
    """Fetch and parse an article: text + image + title."""
    html = fetch_html(url)
    if not html:
        return None

    # Extract main text with trafilatura
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    if not text or len(text) < 100:
        logger.warning(f"trafilatura returned too little text for {url}")
        return None

    image_url = extract_og_image(html)
    title = extract_og_title(html)

    logger.info(f"✅ Parsed: {len(text)} chars, image={'yes' if image_url else 'no'}")
    return ParsedArticle(url=url, text=text[:3000], image_url=image_url, title=title)
