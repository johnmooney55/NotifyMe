"""Tests for checker implementations."""

from unittest.mock import MagicMock, patch

import pytest

from notifyme.checkers.agentic import AgenticChecker
from notifyme.checkers.news import NewsChecker
from notifyme.checkers.webpage import WebpageChecker
from notifyme.models import Monitor, MonitorType


class TestNewsChecker:
    """Test news/RSS checker."""

    @pytest.fixture
    def mock_feed(self):
        """Sample RSS feed data."""
        return {
            "feed": {"title": "Test Feed"},
            "entries": [
                {
                    "id": "article1",
                    "title": "First Article - Source A",
                    "link": "https://example.com/1",
                    "published": "2024-01-15",
                    "summary": "Article summary",
                },
                {
                    "id": "article2",
                    "title": "Second Article - Source B",
                    "link": "https://example.com/2",
                    "published": "2024-01-14",
                    "summary": "Another summary",
                },
            ],
        }

    def test_first_check_finds_all_articles(self, mock_feed):
        """First check should find all articles as new."""
        monitor = Monitor(
            name="News Test",
            type=MonitorType.NEWS,
            url="https://news.google.com/rss",
        )

        checker = NewsChecker()
        with patch("notifyme.checkers.news.fetch_rss", return_value=mock_feed):
            result = checker.check(monitor)

        assert result.condition_met is True
        assert len(result.new_items) == 2
        assert result.new_items[0]["title"] == "First Article - Source A"

    def test_second_check_no_new_articles(self, mock_feed):
        """Second check with same articles should find nothing new."""
        monitor = Monitor(
            name="News Test",
            type=MonitorType.NEWS,
            url="https://news.google.com/rss",
            last_state={"seen_ids": ["article1", "article2"]},
        )

        checker = NewsChecker()
        with patch("notifyme.checkers.news.fetch_rss", return_value=mock_feed):
            result = checker.check(monitor)

        assert result.condition_met is False
        assert len(result.new_items) == 0

    def test_check_finds_new_articles(self, mock_feed):
        """Check should find only new articles."""
        monitor = Monitor(
            name="News Test",
            type=MonitorType.NEWS,
            url="https://news.google.com/rss",
            last_state={"seen_ids": ["article1"]},  # Only seen first article
        )

        checker = NewsChecker()
        with patch("notifyme.checkers.news.fetch_rss", return_value=mock_feed):
            result = checker.check(monitor)

        assert result.condition_met is True
        assert len(result.new_items) == 1
        assert result.new_items[0]["id"] == "article2"

    def test_should_notify_on_new_articles(self):
        """Should notify when there are new articles."""
        from notifyme.models import CheckResult

        monitor = Monitor(name="Test", type=MonitorType.NEWS, url="https://a.com")
        checker = NewsChecker()

        result_with_new = CheckResult(
            condition_met=True,
            explanation="Found 2 new articles",
            new_items=[{"id": "1"}, {"id": "2"}],
        )
        assert checker.should_notify(monitor, result_with_new) is True

        result_without_new = CheckResult(
            condition_met=False,
            explanation="No new articles",
            new_items=[],
        )
        assert checker.should_notify(monitor, result_without_new) is False


class TestWebpageChecker:
    """Test webpage change detection."""

    @pytest.fixture
    def mock_fetch_result(self):
        """Mock fetch result."""
        from notifyme.fetcher import FetchResult
        return FetchResult(
            url="https://example.com",
            html="<html><body>Test content</body></html>",
            text="Test content",
            status_code=200,
            content_hash="abc123",
        )

    def test_first_check_establishes_baseline(self, mock_fetch_result):
        """First check should not trigger notification."""
        monitor = Monitor(
            name="Page Test",
            type=MonitorType.WEBPAGE,
            url="https://example.com",
        )

        checker = WebpageChecker()
        with patch("notifyme.checkers.webpage.fetch_url", return_value=mock_fetch_result):
            result = checker.check(monitor)

        # First check establishes baseline, no notification
        assert result.condition_met is False
        assert "baseline" in result.explanation.lower()
        assert checker.should_notify(monitor, result) is False

    def test_no_change_detected(self, mock_fetch_result):
        """No notification when content hasn't changed."""
        monitor = Monitor(
            name="Page Test",
            type=MonitorType.WEBPAGE,
            url="https://example.com",
            last_state={"hash": "abc123"},  # Same hash
        )

        checker = WebpageChecker()
        with patch("notifyme.checkers.webpage.fetch_url", return_value=mock_fetch_result):
            result = checker.check(monitor)

        assert result.condition_met is False
        assert checker.should_notify(monitor, result) is False

    def test_change_detected(self, mock_fetch_result):
        """Notification when content changes."""
        monitor = Monitor(
            name="Page Test",
            type=MonitorType.WEBPAGE,
            url="https://example.com",
            last_state={"hash": "different_hash"},
        )

        checker = WebpageChecker()
        with patch("notifyme.checkers.webpage.fetch_url", return_value=mock_fetch_result):
            result = checker.check(monitor)

        assert result.condition_met is True
        assert checker.should_notify(monitor, result) is True


class TestAgenticChecker:
    """Test agentic (Claude-powered) checker."""

    def test_evaluate_condition_met(self):
        """Test when Claude determines condition is met."""
        monitor = Monitor(
            name="MSI Monitor",
            type=MonitorType.AGENTIC,
            url="https://msi.com/monitor",
            condition="Product is available for purchase",
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"condition_met": true, "explanation": "Buy button found", "relevant_details": "Price: $999"}')]

        checker = AgenticChecker(api_key="test-key")
        with patch("notifyme.checkers.agentic.fetch_url") as mock_fetch:
            mock_fetch.return_value = MagicMock(text="Page with buy button", content_hash="abc123")
            with patch.object(checker.client.messages, "create", return_value=mock_response):
                result = checker.check(monitor)

        assert result.condition_met is True
        assert "Buy button" in result.explanation

    def test_evaluate_condition_not_met(self):
        """Test when Claude determines condition is not met."""
        monitor = Monitor(
            name="MSI Monitor",
            type=MonitorType.AGENTIC,
            url="https://msi.com/monitor",
            condition="Product is available for purchase",
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"condition_met": false, "explanation": "Coming soon page", "relevant_details": "No purchase option"}')]

        checker = AgenticChecker(api_key="test-key")
        with patch("notifyme.checkers.agentic.fetch_url") as mock_fetch:
            mock_fetch.return_value = MagicMock(text="Coming soon", content_hash="abc123")
            with patch.object(checker.client.messages, "create", return_value=mock_response):
                result = checker.check(monitor)

        assert result.condition_met is False

    def test_should_notify_on_state_change(self):
        """Should only notify when condition transitions to true."""
        from notifyme.models import CheckResult

        checker = AgenticChecker(api_key="test-key")

        # First time condition met (previous was false/unknown)
        monitor = Monitor(
            name="Test",
            type=MonitorType.AGENTIC,
            url="https://a.com",
            last_state={"condition_met": False},
        )
        result = CheckResult(condition_met=True, explanation="Now available")
        assert checker.should_notify(monitor, result) is True

        # Already met, should not notify again
        monitor.last_state = {"condition_met": True}
        assert checker.should_notify(monitor, result) is False

        # Condition not met, should not notify
        result = CheckResult(condition_met=False, explanation="Still not available")
        monitor.last_state = {"condition_met": False}
        assert checker.should_notify(monitor, result) is False
