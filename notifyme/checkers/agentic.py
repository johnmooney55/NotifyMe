"""Agentic checker using Claude to evaluate complex conditions."""

import hashlib
import json
import logging
import os
from typing import Any

from anthropic import Anthropic

from ..fetcher import fetch_url
from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)


class AgenticChecker(BaseChecker):
    """Checker that uses Claude to evaluate natural language conditions.

    Config options:
        - use_playwright: Use Playwright for JS-rendered pages
        - use_browser_agent: Use Browser-Use AI agent for anti-bot evasion
        - browser_task: Optional task for Browser-Use (e.g., "scroll to find price")
        - browser_headed: Run browser in headed mode (default True)
        - max_content_chars: Max chars to send to Claude (default 50000)
        - notify_on_each: If True, notify on each new match (tracks event_id)
                         Use for recurring events like sports wins
    """

    def __init__(self, api_key: str | None = None):
        self.client = Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def check(self, monitor: Monitor) -> CheckResult:
        """
        Fetch the URL and ask Claude to evaluate the condition.

        Args:
            monitor: Monitor with url and condition fields

        Returns:
            CheckResult with Claude's evaluation
        """
        if not monitor.condition:
            raise ValueError(f"Agentic monitor {monitor.name} has no condition set")

        # Fetch the page content
        use_playwright = monitor.config.get("use_playwright", False)
        use_browser_agent = monitor.config.get("use_browser_agent", False)
        browser_task = monitor.config.get("browser_task")
        browser_headed = monitor.config.get("browser_headed", True)

        result = fetch_url(
            monitor.url,
            use_playwright=use_playwright,
            use_browser_agent=use_browser_agent,
            browser_task=browser_task,
            browser_headed=browser_headed,
        )

        # Truncate content if too long (Claude has context limits)
        content = result.text
        max_chars = monitor.config.get("max_content_chars", 50000)
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n[Content truncated...]"

        # Ask Claude to evaluate
        evaluation = self._evaluate_with_claude(content, monitor.condition, monitor.url)

        # Normalize details to dict
        relevant_details = evaluation.get("relevant_details", {})
        if isinstance(relevant_details, str):
            relevant_details = {"info": relevant_details} if relevant_details else {}

        # Include event_id in details for tracking
        event_id = evaluation.get("event_id", "")
        if event_id:
            relevant_details["event_id"] = event_id

        return CheckResult(
            condition_met=evaluation.get("condition_met", False),
            explanation=evaluation.get("explanation", "No explanation provided"),
            details=relevant_details,
            state_hash=result.content_hash,
        )

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        """
        Determine if notification should be sent.

        If notify_on_each is enabled, notify on each new event (different event_id).
        Otherwise, use default behavior (notify on transition from false to true).
        """
        if not result.condition_met:
            return False

        notify_on_each = monitor.config.get("notify_on_each", False)

        if notify_on_each:
            # Notify if this is a different event than last notified
            current_event_id = result.details.get("event_id", "")
            last_event_id = monitor.last_state.get("last_notified_event_id", "")

            if current_event_id and current_event_id != last_event_id:
                return True
            elif not current_event_id:
                # No event_id, fall back to checking if explanation changed
                current_hash = hashlib.sha256(result.explanation.encode()).hexdigest()[:16]
                last_hash = monitor.last_state.get("last_explanation_hash", "")
                return current_hash != last_hash

            return False
        else:
            # Default: notify on transition from false to true
            previous_met = monitor.last_state.get("condition_met", False)
            return not previous_met

    def get_state_for_storage(self, result: CheckResult) -> dict[str, Any]:
        """Store state including event tracking for notify_on_each mode."""
        state = {
            "condition_met": result.condition_met,
            "explanation": result.explanation,
            "details": result.details,
        }

        # Track event_id for notify_on_each mode
        if result.condition_met:
            event_id = result.details.get("event_id", "")
            if event_id:
                state["last_notified_event_id"] = event_id
            state["last_explanation_hash"] = hashlib.sha256(
                result.explanation.encode()
            ).hexdigest()[:16]

        return state

    def _evaluate_with_claude(
        self, content: str, condition: str, url: str
    ) -> dict:
        """
        Send content to Claude for evaluation.

        Args:
            content: Page text content
            condition: Natural language condition to check
            url: Original URL (for context)

        Returns:
            Dictionary with condition_met, explanation, relevant_details
        """
        prompt = f"""Analyze this webpage and determine if the condition is TRUE or FALSE.

CONDITION: {condition}

WEBPAGE CONTENT:
{content}

IMPORTANT: Set "condition_met" to true ONLY if the condition is satisfied. If the condition is NOT met, set it to false.

Respond with JSON only:
{{"condition_met": true or false, "explanation": "why condition is met or not met", "relevant_details": "key info like scores, dates, prices", "event_id": "YYYY-MM-DD_opponent (e.g., 2025-02-15_Oregon) - must be consistent format for deduplication"}}"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = response.content[0].text.strip()

            # Try to parse JSON from response
            try:
                # Handle case where Claude wraps in markdown code block
                if response_text.startswith("```"):
                    response_text = response_text.split("```")[1]
                    if response_text.startswith("json"):
                        response_text = response_text[4:]
                    response_text = response_text.strip()

                return json.loads(response_text)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse Claude response as JSON: {response_text[:200]}")
                # Try to extract boolean from response
                condition_met = "true" in response_text.lower() and "condition_met" in response_text.lower()
                return {
                    "condition_met": condition_met,
                    "explanation": response_text[:500],
                    "relevant_details": "",
                }

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            raise
