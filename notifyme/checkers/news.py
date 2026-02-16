"""News/RSS checker for Google Alerts replacement."""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from anthropic import Anthropic

from ..fetcher import fetch_rss, fetch_url
from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)


class NewsChecker(BaseChecker):
    """Checker for news feeds and Google News RSS.

    Config options:
        - filter_condition: If set, uses Claude to filter articles that match
                           this condition. Only matching articles trigger notifications.
                           Example: "The article announces the product is available for purchase"
        - max_age_days: If set, ignore articles older than this many days.
                       Useful for first run to avoid old articles triggering.
    """

    def __init__(self):
        self._client = None

    @property
    def client(self):
        """Lazy-load Anthropic client only when needed for filtering."""
        if self._client is None:
            self._client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        return self._client

    def check(self, monitor: Monitor) -> CheckResult:
        """
        Fetch RSS feed and check for new articles.

        Args:
            monitor: Monitor with RSS feed URL

        Returns:
            CheckResult with new articles in new_items
        """
        feed = fetch_rss(monitor.url)

        # Get previously seen article IDs
        seen_ids: set = set(monitor.last_state.get("seen_ids", []))

        # Process entries
        new_articles = []
        all_ids = []

        for entry in feed.get("entries", []):
            article_id = self._get_article_id(entry)
            all_ids.append(article_id)

            if article_id not in seen_ids:
                new_articles.append({
                    "id": article_id,
                    "title": entry.get("title", "No title"),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "source": self._get_source(entry),
                    "summary": entry.get("summary", "")[:500],
                })

        # Filter by max age if configured (prevents old articles on first run)
        max_age_days = monitor.config.get("max_age_days")
        if max_age_days and new_articles:
            before_count = len(new_articles)
            new_articles = [a for a in new_articles if self._is_article_recent(a, max_age_days)]
            filtered_count = before_count - len(new_articles)
            if filtered_count > 0:
                logger.info(f"Age filter: excluded {filtered_count} articles older than {max_age_days} days")

        # Apply agentic filter if configured
        filter_condition = monitor.config.get("filter_condition")
        if filter_condition and new_articles:
            stop_on_first = monitor.config.get("stop_on_first_match", False)
            new_articles = self._filter_articles(new_articles, filter_condition, stop_on_first)

        # Build result
        has_new = len(new_articles) > 0
        explanation = (
            f"Found {len(new_articles)} new article(s)"
            if has_new
            else "No new articles"
        )

        return CheckResult(
            condition_met=has_new,
            explanation=explanation,
            details={"feed_title": feed.get("feed", {}).get("title", "")},
            new_items=new_articles,
            state_hash=hashlib.sha256(",".join(all_ids).encode()).hexdigest()[:16],
        )

    def _filter_articles(
        self, articles: list[dict], condition: str, stop_on_first: bool = False
    ) -> list[dict]:
        """Filter articles using Claude to check if they match the condition."""
        filtered = []
        checked = 0

        # Rate limit: 1.5s between API calls keeps us under 50/minute
        api_delay = 1.5

        for article in articles:
            checked += 1
            try:
                # Try to fetch full article content
                content = article.get("summary", "")
                if article.get("link"):
                    try:
                        result = fetch_url(article["link"], timeout=15)
                        content = result.text[:10000]  # Limit content size
                    except Exception as e:
                        logger.debug(f"Could not fetch article: {e}")
                        # Fall back to title + summary
                        content = f"Title: {article.get('title', '')}\n\nSummary: {article.get('summary', '')}"

                # Ask Claude if article matches condition
                if self._article_matches_condition(article, content, condition):
                    filtered.append(article)
                    if stop_on_first:
                        logger.info(f"Agentic filter: found match after checking {checked} articles (stop_on_first=True)")
                        return filtered

                # Rate limit delay between API calls
                if checked < len(articles):
                    time.sleep(api_delay)

            except Exception as e:
                logger.warning(f"Error filtering article: {e}")
                # On error, include the article to avoid missing things
                filtered.append(article)
                if stop_on_first:
                    return filtered

        logger.info(f"Agentic filter: {len(filtered)}/{len(articles)} articles matched condition")
        return filtered

    def _article_matches_condition(
        self, article: dict, content: str, condition: str
    ) -> bool:
        """Use Claude to determine if article matches the filter condition."""
        prompt = f"""Does this article match the following condition?

CONDITION: {condition}

ARTICLE TITLE: {article.get('title', 'Unknown')}
SOURCE: {article.get('source', 'Unknown')}

ARTICLE CONTENT:
{content[:8000]}

Answer with JSON only: {{"matches": true or false, "reason": "brief explanation"}}"""

        try:
            response = self.client.messages.create(
                model="claude-3-haiku-20240307",  # Use Haiku for filtering (cheaper)
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = response.content[0].text.strip()

            # Parse response
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            result = json.loads(response_text)
            matches = result.get("matches", False)
            reason = result.get("reason", "")

            logger.debug(f"Article '{article.get('title', '')[:50]}' matches={matches}: {reason}")
            return matches

        except Exception as e:
            logger.warning(f"Error checking article with Claude: {e}")
            return True  # On error, include article to be safe

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        """Notify whenever there are new articles."""
        return len(result.new_items) > 0

    def get_state_for_storage(self, result: CheckResult) -> dict[str, Any]:
        """Store seen article IDs."""
        # Merge new IDs with existing ones
        existing_ids = []
        new_ids = [item["id"] for item in result.new_items]

        # Keep last 500 IDs to prevent unbounded growth
        all_ids = new_ids + existing_ids
        return {
            "condition_met": result.condition_met,
            "seen_ids": all_ids[:500],
            "last_count": len(result.new_items),
        }

    def _get_article_id(self, entry: dict) -> str:
        """Generate unique ID for an article."""
        # Prefer explicit ID, fall back to link hash
        if entry.get("id"):
            return entry["id"]
        if entry.get("link"):
            return hashlib.sha256(entry["link"].encode()).hexdigest()[:16]
        if entry.get("title"):
            return hashlib.sha256(entry["title"].encode()).hexdigest()[:16]
        return hashlib.sha256(str(entry).encode()).hexdigest()[:16]

    def _get_source(self, entry: dict) -> str:
        """Extract source name from entry."""
        # Google News includes source in title like "Article Title - Source Name"
        title = entry.get("title", "")
        if " - " in title:
            return title.rsplit(" - ", 1)[-1]

        # Try source field
        source = entry.get("source", {})
        if isinstance(source, dict):
            return source.get("title", "Unknown")

        return "Unknown"

    def _is_article_recent(self, article: dict, max_age_days: int) -> bool:
        """Check if article is within the max age threshold."""
        published = article.get("published", "")
        if not published:
            # No date available, include it to be safe
            return True

        try:
            # Parse RFC 2822 date format (common in RSS)
            pub_date = parsedate_to_datetime(published)
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            return pub_date >= cutoff
        except Exception as e:
            logger.debug(f"Could not parse article date '{published}': {e}")
            # On parse error, include the article
            return True
