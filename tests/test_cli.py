"""Tests for CLI commands."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from notifyme.cli import cli


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def temp_db():
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield str(Path(tmpdir) / "test.db")


class TestAddCommand:
    """Test the 'add' command."""

    def test_add_agentic_monitor(self, runner, temp_db):
        """Test adding an agentic monitor."""
        result = runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "MSI Monitor",
                "--type", "agentic",
                "--url", "https://msi.com/monitor",
                "--condition", "Product is available",
                "--interval", "120",
            ],
        )

        assert result.exit_code == 0
        assert "Added monitor: MSI Monitor" in result.output
        assert "Type: agentic" in result.output

    def test_add_news_monitor(self, runner, temp_db):
        """Test adding a news monitor."""
        result = runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "Scale AI News",
                "--type", "news",
                "--url", "https://news.google.com/rss/search?q=Scale+AI",
            ],
        )

        assert result.exit_code == 0
        assert "Added monitor: Scale AI News" in result.output

    def test_add_agentic_requires_condition(self, runner, temp_db):
        """Test that agentic monitors require a condition."""
        result = runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "Test",
                "--type", "agentic",
                "--url", "https://example.com",
            ],
        )

        assert result.exit_code != 0
        assert "require --condition" in result.output

    def test_add_price_requires_selector_and_threshold(self, runner, temp_db):
        """Test that price monitors require selector and threshold."""
        result = runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "Test",
                "--type", "price",
                "--url", "https://example.com",
            ],
        )

        assert result.exit_code != 0
        assert "require --selector" in result.output


class TestListCommand:
    """Test the 'list' command."""

    def test_list_empty(self, runner, temp_db):
        """Test listing with no monitors."""
        result = runner.invoke(cli, ["--db", temp_db, "list"])

        assert result.exit_code == 0
        assert "No monitors found" in result.output

    def test_list_monitors(self, runner, temp_db):
        """Test listing monitors after adding some."""
        # Add a monitor first
        runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "Test Monitor",
                "--type", "news",
                "--url", "https://example.com",
            ],
        )

        result = runner.invoke(cli, ["--db", temp_db, "list"])

        assert result.exit_code == 0
        assert "Test Monitor" in result.output
        assert "news" in result.output


class TestPauseResumeCommands:
    """Test pause and resume commands."""

    def test_pause_and_resume(self, runner, temp_db):
        """Test pausing and resuming a monitor."""
        # Add a monitor
        runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "Test Monitor",
                "--type", "news",
                "--url", "https://example.com",
            ],
        )

        # Pause it
        result = runner.invoke(cli, ["--db", temp_db, "pause", "Test Monitor"])
        assert result.exit_code == 0
        assert "Paused monitor" in result.output

        # Check it's paused
        result = runner.invoke(cli, ["--db", temp_db, "list", "--all"])
        assert "PAUSED" in result.output

        # Resume it
        result = runner.invoke(cli, ["--db", temp_db, "resume", "Test Monitor"])
        assert result.exit_code == 0
        assert "Resumed monitor" in result.output


class TestRemoveCommand:
    """Test the 'remove' command."""

    def test_remove_monitor(self, runner, temp_db):
        """Test removing a monitor."""
        # Add a monitor
        runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "To Remove",
                "--type", "news",
                "--url", "https://example.com",
            ],
        )

        # Remove it with force flag
        result = runner.invoke(cli, ["--db", temp_db, "remove", "To Remove", "--force"])

        assert result.exit_code == 0
        assert "Removed monitor" in result.output

        # Verify it's gone
        result = runner.invoke(cli, ["--db", temp_db, "list"])
        assert "To Remove" not in result.output


class TestCheckCommand:
    """Test the 'check' command."""

    def test_check_no_monitors(self, runner, temp_db):
        """Test checking with no monitors due."""
        result = runner.invoke(cli, ["--db", temp_db, "check"])

        assert result.exit_code == 0
        assert "No monitors due" in result.output or "Checking monitors" in result.output

    def test_check_specific_monitor(self, runner, temp_db):
        """Test checking a specific monitor."""
        # Add a monitor
        runner.invoke(
            cli,
            [
                "--db", temp_db,
                "add",
                "--name", "News Test",
                "--type", "news",
                "--url", "https://news.google.com/rss/search?q=test",
            ],
        )

        # Mock the fetch to avoid network calls
        with patch("notifyme.checkers.news.fetch_rss") as mock_fetch:
            mock_fetch.return_value = {"feed": {}, "entries": []}
            result = runner.invoke(
                cli,
                ["--db", temp_db, "check", "News Test", "--dry-run"],
            )

        assert result.exit_code == 0
        assert "Checking monitor" in result.output


class TestHistoryCommand:
    """Test the 'history' command."""

    def test_history_empty(self, runner, temp_db):
        """Test history with no notifications."""
        result = runner.invoke(cli, ["--db", temp_db, "history"])

        assert result.exit_code == 0
        assert "No notifications found" in result.output
