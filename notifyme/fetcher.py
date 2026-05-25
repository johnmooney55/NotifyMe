"""URL fetching utilities with requests, Playwright, and Browser-Use options."""

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

# Prefer curl_cffi for the requests path — it ships with browser-matched
# TLS fingerprints (JA3) so Akamai/Cloudflare-protected sites that
# 403 plain Python `requests` work. Falls back gracefully if not
# installed. www.mynavyhr.navy.mil was the case that forced this.
try:
    from curl_cffi import requests as _impersonating_requests
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _impersonating_requests = None
    _CURL_CFFI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default headers — mimic a real desktop Chrome navigation. The minimal
# {User-Agent, Accept, Accept-Language} set used to be enough, but a
# growing number of hardened servers (Akamai-fronted .mil/.gov sites,
# Cloudflare-protected pages) inspect the `Sec-Fetch-*` and
# `Upgrade-Insecure-Requests` headers and 403 anything that "doesn't
# look like a browser navigation". www.mynavyhr.navy.mil was the case
# that forced this expansion — a plain Mozilla UA returned 403; only
# the full header set returns the actual page.
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
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
    used_browser_agent: bool = False

    @property
    def soup(self) -> BeautifulSoup:
        """Parse HTML with BeautifulSoup."""
        return BeautifulSoup(self.html, "html.parser")


def fetch_url(
    url: str,
    use_playwright: bool = False,
    use_browser_agent: bool = False,
    browser_task: str | None = None,
    browser_headed: bool = True,
    timeout: int = 30,
    headers: dict | None = None,
) -> FetchResult:
    """
    Fetch a URL and return its content.

    Args:
        url: The URL to fetch
        use_playwright: Force Playwright for JS-rendered content
        use_browser_agent: Use Browser-Use AI agent for anti-bot evasion
        browser_task: Optional task for Browser-Use agent (e.g., "scroll to find price")
        browser_headed: Run browser in headed mode (default True for better bot evasion)
        timeout: Request timeout in seconds
        headers: Optional custom headers

    Returns:
        FetchResult with HTML, text, and content hash
    """
    # Priority: browser_agent > playwright > requests
    if use_browser_agent:
        return _fetch_with_browser_use(url, browser_task, browser_headed, timeout)

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
    """Fetch URL using the HTTP requests path.

    Uses curl_cffi (with Chrome TLS impersonation) when available; falls
    back to plain `requests` otherwise. Akamai/Cloudflare-protected
    sites inspect TLS fingerprints below the header layer and 403
    plain Python `requests`; libcurl's TLS stack matches real Chrome
    closely enough that the impersonating path gets through.
    """
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}

    if _CURL_CFFI_AVAILABLE:
        # impersonate="chrome124" sets the TLS fingerprint, HTTP/2
        # frame ordering, and a few other client characteristics to
        # match Chrome 124's defaults.
        response = _impersonating_requests.get(
            url, headers=merged_headers, timeout=timeout,
            impersonate="chrome124",
        )
    else:
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


def _fetch_with_browser_use(
    url: str,
    task: str | None = None,
    headed: bool = True,
    timeout: int = 60,
) -> FetchResult:
    """Fetch URL using Browser-Use AI agent for anti-bot evasion."""
    try:
        from browser_use import Agent, Browser, ChatAnthropic
    except ImportError:
        raise ImportError(
            "Browser-Use not installed. Install with: "
            "pip install 'notifyme[browser-agent]'"
        )

    # Build the task for the agent
    if task:
        full_task = f"Go to {url}. {task}. Then extract the main text content from the page."
    else:
        full_task = f"Go to {url} and extract the main text content from the page. Return the important visible text."

    logger.info(f"Browser-Use: fetching {url} (headed={headed})")

    async def run_agent():
        # Use browser-use's ChatAnthropic wrapper. Current Sonnet 4.6
        # alias rather than the May-2025 snapshot for the same reason
        # as the agentic checker — deprecation defense + better quality.
        llm = ChatAnthropic(
            model="claude-sonnet-4-6",
        )

        browser = Browser(headless=not headed)

        agent = Agent(
            task=full_task,
            llm=llm,
            browser=browser,
        )

        result = await agent.run()

        # Get the final page HTML from the browser session
        html = ""
        try:
            # Try to get the current page content
            session = agent.browser_session
            if session and session.session_manager:
                page = await session.session_manager.get_current_page()
                if page:
                    html = await page.content()
        except Exception as e:
            logger.debug(f"Could not get page HTML: {e}")

        # Get the text from agent's final result
        final_text = ""
        if result:
            # Try to get extracted content or final result
            final_text = result.extracted_content() if callable(getattr(result, 'extracted_content', None)) else ""
            if not final_text:
                final_result = result.final_result()
                if final_result:
                    final_text = str(final_result)

        return html, final_text, result

    # Run the async agent
    html, agent_text, agent_result = asyncio.run(run_agent())

    # If we got HTML, parse it for clean text
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()
        text = soup.get_text(separator="\n", strip=True)
    else:
        # Fall back to agent's extracted text
        text = agent_text

    # Ensure text is a string
    if isinstance(text, list):
        text = "\n".join(str(item) for item in text)
    elif not isinstance(text, str):
        text = str(text) if text else ""

    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    logger.info(f"Browser-Use: successfully fetched {url} ({len(text)} chars)")

    return FetchResult(
        url=url,
        html=html,
        text=text,
        status_code=200,
        content_hash=content_hash,
        used_browser_agent=True,
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
