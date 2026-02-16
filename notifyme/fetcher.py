"""URL fetching utilities with requests and optional Playwright fallback."""

import hashlib
import logging
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Default headers to mimic a browser
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


@dataclass
class FetchResult:
    """Result of fetching a URL."""

    url: str
    html: str
    text: str
    status_code: int
    content_hash: str
    used_playwright: bool = False

    @property
    def soup(self) -> BeautifulSoup:
        """Parse HTML with BeautifulSoup."""
        return BeautifulSoup(self.html, "html.parser")


def fetch_url(
    url: str,
    use_playwright: bool = False,
    timeout: int = 30,
    headers: dict | None = None,
) -> FetchResult:
    """
    Fetch a URL and return its content.

    Args:
        url: The URL to fetch
        use_playwright: Force Playwright for JS-rendered content
        timeout: Request timeout in seconds
        headers: Optional custom headers

    Returns:
        FetchResult with HTML, text, and content hash
    """
    if use_playwright:
        return _fetch_with_playwright(url, timeout)

    try:
        return _fetch_with_requests(url, timeout, headers)
    except Exception as e:
        logger.warning(f"requests failed for {url}: {e}, trying Playwright")
        try:
            return _fetch_with_playwright(url, timeout)
        except ImportError:
            raise RuntimeError(
                f"Failed to fetch {url} with requests and Playwright is not installed. "
                "Install with: pip install 'notifyme[playwright]' && playwright install chromium"
            )


def _fetch_with_requests(
    url: str, timeout: int, headers: dict | None
) -> FetchResult:
    """Fetch URL using requests library."""
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}

    response = requests.get(url, headers=merged_headers, timeout=timeout)
    response.raise_for_status()

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements for cleaner text
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    text = soup.get_text(separator="\n", strip=True)
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    return FetchResult(
        url=url,
        html=html,
        text=text,
        status_code=response.status_code,
        content_hash=content_hash,
        used_playwright=False,
    )


def _fetch_with_playwright(url: str, timeout: int) -> FetchResult:
    """Fetch URL using Playwright for JS-rendered content."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Playwright not installed. Install with: "
            "pip install 'notifyme[playwright]' && playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers(DEFAULT_HEADERS)

        response = page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
        html = page.content()

        browser.close()

    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements for cleaner text
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()

    text = soup.get_text(separator="\n", strip=True)
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    return FetchResult(
        url=url,
        html=html,
        text=text,
        status_code=response.status if response else 200,
        content_hash=content_hash,
        used_playwright=True,
    )


def fetch_rss(url: str, timeout: int = 30) -> dict:
    """
    Fetch and parse an RSS/Atom feed.

    Returns:
        Parsed feed dictionary with entries
    """
    import feedparser

    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()

    feed = feedparser.parse(response.text)
    return feed
