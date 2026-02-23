"""Price monitoring checker."""

import logging
import re
from typing import Any

from ..fetcher import fetch_url
from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)


class PriceChecker(BaseChecker):
    """Checker that monitors prices and alerts on drops below threshold."""

    def check(self, monitor: Monitor) -> CheckResult:
        """
        Fetch webpage and extract price to compare against threshold.

        Config options:
            - selector: CSS selector for price element
            - threshold: Price threshold to alert below
            - currency: Expected currency symbol (default: $)

        Args:
            monitor: Monitor with URL and price config

        Returns:
            CheckResult indicating if price is below threshold
        """
        use_playwright = monitor.config.get("use_playwright", False)
        selector = monitor.config.get("selector")
        threshold = monitor.config.get("threshold")
        currency = monitor.config.get("currency", "$")

        if not selector:
            raise ValueError(f"Price monitor {monitor.name} requires 'selector' in config")
        if threshold is None:
            raise ValueError(f"Price monitor {monitor.name} requires 'threshold' in config")

        result = fetch_url(monitor.url, use_playwright=use_playwright)
        soup = result.soup

        # Find price element
        element = soup.select_one(selector)
        if not element:
            return CheckResult(
                condition_met=False,
                explanation=f"Price element not found with selector: {selector}",
                details={"error": "selector_not_found"},
            )

        # Extract and parse price
        price_text = element.get_text(strip=True)
        price = self._parse_price(price_text, currency)

        if price is None:
            return CheckResult(
                condition_met=False,
                explanation=f"Could not parse price from: {price_text}",
                details={"raw_text": price_text},
            )

        # Compare to threshold
        below_threshold = price < threshold
        previous_below = monitor.last_state.get("below_threshold", False)

        if below_threshold:
            explanation = f"Price ${price:.2f} is below threshold ${threshold:.2f}"
        else:
            explanation = f"Price ${price:.2f} is at or above threshold ${threshold:.2f}"

        return CheckResult(
            condition_met=below_threshold,
            explanation=explanation,
            details={
                "price": price,
                "threshold": threshold,
                "price_text": price_text,
            },
        )

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        """Notify when price drops below threshold (transition only)."""
        if not result.condition_met:
            return False

        previous_below = monitor.last_state.get("below_threshold", False)
        return not previous_below  # Only notify on transition to below

    def get_state_for_storage(self, result: CheckResult, monitor: Monitor) -> dict[str, Any]:
        """Store price and threshold status."""
        return {
            "condition_met": result.condition_met,
            "below_threshold": result.condition_met,
            "last_price": result.details.get("price"),
        }

    def _parse_price(self, text: str, currency: str = "$") -> float | None:
        """
        Parse price from text.

        Handles formats like:
            - $1,234.56
            - 1234.56
            - $1234
            - USD 1,234.56
        """
        # Remove currency symbols and common prefixes
        cleaned = text.replace(currency, "").replace("USD", "").replace(",", "").strip()

        # Extract numeric value
        match = re.search(r"(\d+(?:\.\d{2})?)", cleaned)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None
