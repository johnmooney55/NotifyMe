"""News/RSS checker for Google Alerts replacement."""

import hashlib
import logging
from typing import Any

from ..fetcher import fetch_rss
from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)


class NewsChecker(BaseChecker):
    """Checker for news feeds and Google News RSS."""

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
