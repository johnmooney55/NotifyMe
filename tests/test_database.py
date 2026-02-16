"""Tests for database operations."""

import tempfile
from pathlib import Path

import pytest

from notifyme.database import Database
from notifyme.models import Monitor, MonitorType, NotificationLog


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield Database(db_path)


class TestMonitorCRUD:
    """Test monitor CRUD operations."""

    def test_add_monitor(self, db):
        """Test adding a new monitor."""
        monitor = Monitor(
            name="Test Monitor",
            type=MonitorType.AGENTIC,
            url="https://example.com",
            condition="Test condition",
        )

        result = db.add_monitor(monitor)

        assert result.id == monitor.id
        assert result.name == "Test Monitor"

    def test_get_monitor(self, db):
        """Test retrieving a monitor by ID."""
        monitor = Monitor(
            name="Test Monitor",
            type=MonitorType.NEWS,
            url="https://news.google.com/rss",
        )
        db.add_monitor(monitor)

        result = db.get_monitor(monitor.id)

        assert result is not None
        assert result.name == "Test Monitor"
        assert result.type == MonitorType.NEWS

    def test_get_monitor_by_name(self, db):
        """Test retrieving a monitor by name (case-insensitive)."""
        monitor = Monitor(
            name="MSI Monitor",
            type=MonitorType.AGENTIC,
            url="https://msi.com",
        )
        db.add_monitor(monitor)

        result = db.get_monitor_by_name("msi monitor")

        assert result is not None
        assert result.id == monitor.id

    def test_list_monitors(self, db):
        """Test listing all monitors."""
        db.add_monitor(Monitor(name="Monitor 1", type=MonitorType.NEWS, url="https://a.com"))
        db.add_monitor(Monitor(name="Monitor 2", type=MonitorType.WEBPAGE, url="https://b.com"))

        result = db.list_monitors()

        assert len(result) == 2

    def test_list_active_only(self, db):
        """Test listing only active monitors."""
        m1 = Monitor(name="Active", type=MonitorType.NEWS, url="https://a.com")
        m2 = Monitor(name="Inactive", type=MonitorType.NEWS, url="https://b.com", is_active=False)
        db.add_monitor(m1)
        db.add_monitor(m2)

        result = db.list_monitors(active_only=True)

        assert len(result) == 1
        assert result[0].name == "Active"

    def test_update_monitor(self, db):
        """Test updating a monitor."""
        monitor = Monitor(name="Original", type=MonitorType.NEWS, url="https://a.com")
        db.add_monitor(monitor)

        monitor.name = "Updated"
        monitor.last_state = {"seen_ids": ["abc123"]}
        db.update_monitor(monitor)

        result = db.get_monitor(monitor.id)
        assert result.name == "Updated"
        assert result.last_state == {"seen_ids": ["abc123"]}

    def test_delete_monitor(self, db):
        """Test deleting a monitor."""
        monitor = Monitor(name="To Delete", type=MonitorType.NEWS, url="https://a.com")
        db.add_monitor(monitor)

        deleted = db.delete_monitor(monitor.id)

        assert deleted is True
        assert db.get_monitor(monitor.id) is None

    def test_pause_resume_monitor(self, db):
        """Test pausing and resuming a monitor."""
        monitor = Monitor(name="Test", type=MonitorType.NEWS, url="https://a.com")
        db.add_monitor(monitor)

        db.set_monitor_active(monitor.id, False)
        paused = db.get_monitor(monitor.id)
        assert paused.is_active is False

        db.set_monitor_active(monitor.id, True)
        resumed = db.get_monitor(monitor.id)
        assert resumed.is_active is True


class TestNotifications:
    """Test notification logging."""

    def test_add_notification(self, db):
        """Test logging a notification."""
        monitor = Monitor(name="Test", type=MonitorType.NEWS, url="https://a.com")
        db.add_monitor(monitor)

        notification = NotificationLog(
            monitor_id=monitor.id,
            message="Found 3 new articles",
            details={"count": 3},
        )
        db.add_notification(notification)

        result = db.get_notifications(monitor.id)
        assert len(result) == 1
        assert result[0].message == "Found 3 new articles"

    def test_get_last_notification(self, db):
        """Test getting the most recent notification."""
        monitor = Monitor(name="Test", type=MonitorType.NEWS, url="https://a.com")
        db.add_monitor(monitor)

        db.add_notification(NotificationLog(monitor_id=monitor.id, message="First"))
        db.add_notification(NotificationLog(monitor_id=monitor.id, message="Second"))

        result = db.get_last_notification(monitor.id)
        assert result.message == "Second"


class TestDueForCheck:
    """Test monitors due for checking."""

    def test_new_monitor_is_due(self, db):
        """Test that a new monitor (never checked) is due."""
        monitor = Monitor(name="New", type=MonitorType.NEWS, url="https://a.com")
        db.add_monitor(monitor)

        due = db.get_monitors_due_for_check()
        assert len(due) == 1
        assert due[0].id == monitor.id

    def test_recently_checked_not_due(self, db):
        """Test that a recently checked monitor is not due."""
        from datetime import datetime

        monitor = Monitor(
            name="Recent",
            type=MonitorType.NEWS,
            url="https://a.com",
            check_interval_minutes=60,
        )
        monitor.last_checked = datetime.now()
        db.add_monitor(monitor)

        due = db.get_monitors_due_for_check()
        assert len(due) == 0
