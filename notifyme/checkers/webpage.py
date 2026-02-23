"""Webpage change detection checker."""

import logging
from typing import Any

from ..fetcher import fetch_url
from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)


class WebpageChecker(BaseChecker):
    """Checker that detects any changes to a webpage."""

    def check(self, monitor: Monitor) -> CheckResult:
        """
        Fetch webpage and compare hash to previous state.

        Args:
            monitor: Monitor with URL to check

        Returns:
            CheckResult indicating if page changed
        """
        use_playwright = monitor.config.get("use_playwright", False)
        selector = monitor.config.get("selector")  # Optional CSS selector

        result = fetch_url(monitor.url, use_playwright=use_playwright)

        # If selector specified, only hash that portion
        if selector:
            soup = result.soup
            element = soup.select_one(selector)
            if element:
                import hashlib
                content = element.get_text(strip=True)
                content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            else:
                logger.warning(f"Selector {selector} not found on {monitor.url}")
                content_hash = result.content_hash
        else:
            content_hash = result.content_hash

        # Compare to previous hash
        previous_hash = monitor.last_state.get("hash")
        changed = previous_hash is not None and content_hash != previous_hash

        if previous_hash is None:
            explanation = "First check - baseline recorded"
        elif changed:
            explanation = "Page content has changed"
        else:
            explanation = "No changes detected"

        return CheckResult(
            condition_met=changed,
            explanation=explanation,
            details={"hash": content_hash, "previous_hash": previous_hash},
            state_hash=content_hash,
        )

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        """Notify when page changes (but not on first check)."""
        previous_hash = monitor.last_state.get("hash")
        return previous_hash is not None and result.condition_met

    def get_state_for_storage(self, result: CheckResult, monitor: Monitor) -> dict[str, Any]:
        """Store the content hash."""
        return {
            "condition_met": result.condition_met,
            "hash": result.state_hash,
        }
