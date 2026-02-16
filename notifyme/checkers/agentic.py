"""Agentic checker using Claude to evaluate complex conditions."""

import json
import logging
import os

from anthropic import Anthropic

from ..fetcher import fetch_url
from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)


class AgenticChecker(BaseChecker):
    """Checker that uses Claude to evaluate natural language conditions."""

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
        result = fetch_url(monitor.url, use_playwright=use_playwright)

        # Truncate content if too long (Claude has context limits)
        content = result.text
        max_chars = monitor.config.get("max_content_chars", 50000)
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n[Content truncated...]"

        # Ask Claude to evaluate
        evaluation = self._evaluate_with_claude(content, monitor.condition, monitor.url)

        return CheckResult(
            condition_met=evaluation.get("condition_met", False),
            explanation=evaluation.get("explanation", "No explanation provided"),
            details=evaluation.get("relevant_details", {}),
            state_hash=result.content_hash,
        )

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
        prompt = f"""Analyze this webpage content and determine if the following condition is met.

CONDITION TO CHECK:
{condition}

URL: {url}

WEBPAGE CONTENT:
{content}

Respond with valid JSON only (no markdown, no explanation outside JSON):
{{
    "condition_met": true or false,
    "explanation": "Brief explanation of your determination",
    "relevant_details": "Any useful information like price, availability date, purchase links, etc."
}}"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=500,
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
