"""Tests for CheckOrchestrator failure / recovery alerting."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from notifyme.models import CheckResult, Monitor, MonitorType
from notifyme.scheduler import CheckOrchestrator


def make_monitor(**overrides) -> Monitor:
    """Build an agentic monitor with sensible defaults for tests."""
    defaults = dict(
        name="Test Site",
        type=MonitorType.AGENTIC,
        url="https://example.com",
        condition="Site is down",
    )
    defaults.update(overrides)
    return Monitor(**defaults)


def make_orchestrator(checker: MagicMock) -> tuple[CheckOrchestrator, MagicMock, MagicMock]:
    """Build an orchestrator with a mocked db, notifier, and pre-wired checker."""
    db = MagicMock()
    notifier = MagicMock()
    orch = CheckOrchestrator(db=db, notifier=notifier)
    # Pre-populate the checker cache so get_checker() returns our mock.
    orch._checkers[MonitorType.AGENTIC] = checker
    return orch, db, notifier


def success_checker() -> MagicMock:
    """A checker whose check() succeeds and never asks to notify."""
    checker = MagicMock()
    checker.check.return_value = CheckResult(
        condition_met=False, explanation="ok", state_hash="hash1"
    )
    checker.should_notify.return_value = False
    checker.get_state_for_storage.return_value = {"condition_met": False}
    return checker


def failing_checker() -> MagicMock:
    """A checker whose check() raises, simulating a timeout/connection error."""
    checker = MagicMock()
    checker.check.side_effect = TimeoutError("Page.goto: Timeout 30000ms exceeded.")
    return checker


class TestCheckFailureAlerts:
    """Check failures (the check itself erroring out) should alert."""

    def test_alerts_on_first_failure(self):
        checker = failing_checker()
        orch, db, notifier = make_orchestrator(checker)
        monitor = make_monitor()  # no prior failure state

        with pytest.raises(TimeoutError):
            orch.check_monitor(monitor)

        notifier.send_check_failure.assert_called_once()
        db.add_notification.assert_called_once()
        # State now tracks the failure and last_checked has advanced.
        assert monitor.last_state["consecutive_failures"] == 1
        assert "first_failure_at" in monitor.last_state
        assert monitor.last_checked is not None
        db.update_monitor.assert_called_once_with(monitor)

    def test_suppresses_repeat_alert_within_window(self):
        checker = failing_checker()
        orch, db, notifier = make_orchestrator(checker)
        monitor = make_monitor(
            last_state={
                "consecutive_failures": 1,
                "first_failure_at": "2026-05-15T09:00:00",
                "last_error_alert_at": datetime.now().isoformat(),
            }
        )

        with pytest.raises(TimeoutError):
            orch.check_monitor(monitor)

        # Already alerted recently -> no new alert, but failure count climbs.
        notifier.send_check_failure.assert_not_called()
        assert monitor.last_state["consecutive_failures"] == 2

    def test_realerts_after_window_elapsed(self):
        checker = failing_checker()
        orch, db, notifier = make_orchestrator(checker)
        stale = (datetime.now() - timedelta(hours=25)).isoformat()
        monitor = make_monitor(
            last_state={
                "consecutive_failures": 5,
                "first_failure_at": "2026-05-14T09:00:00",
                "last_error_alert_at": stale,
            }
        )

        with pytest.raises(TimeoutError):
            orch.check_monitor(monitor)

        notifier.send_check_failure.assert_called_once()
        assert monitor.last_state["consecutive_failures"] == 6

    def test_alert_on_error_can_be_disabled(self):
        checker = failing_checker()
        orch, db, notifier = make_orchestrator(checker)
        monitor = make_monitor(config={"alert_on_error": False})

        with pytest.raises(TimeoutError):
            orch.check_monitor(monitor)

        notifier.send_check_failure.assert_not_called()
        # Failure is still recorded even when alerting is off.
        assert monitor.last_state["consecutive_failures"] == 1

    def test_first_failure_at_is_preserved_across_failures(self):
        checker = failing_checker()
        orch, db, notifier = make_orchestrator(checker)
        monitor = make_monitor(
            last_state={
                "consecutive_failures": 2,
                "first_failure_at": "2026-05-15T08:00:00",
                "last_error_alert_at": datetime.now().isoformat(),
            }
        )

        with pytest.raises(TimeoutError):
            orch.check_monitor(monitor)

        assert monitor.last_state["first_failure_at"] == "2026-05-15T08:00:00"


class TestRecoveryAlerts:
    """A successful check after failures should send a recovery alert."""

    def test_recovery_alert_sent_after_failures(self):
        checker = success_checker()
        orch, db, notifier = make_orchestrator(checker)
        monitor = make_monitor(
            last_state={"condition_met": False, "consecutive_failures": 3}
        )

        orch.check_monitor(monitor)

        notifier.send_recovery.assert_called_once()
        assert notifier.send_recovery.call_args[0][1] == 3  # failed_checks
        # Stored state comes from the checker -> failure keys cleared.
        assert "consecutive_failures" not in monitor.last_state

    def test_no_recovery_alert_on_normal_success(self):
        checker = success_checker()
        orch, db, notifier = make_orchestrator(checker)
        monitor = make_monitor(last_state={"condition_met": False})

        orch.check_monitor(monitor)

        notifier.send_recovery.assert_not_called()

    def test_recovery_alert_respects_alert_on_error_flag(self):
        checker = success_checker()
        orch, db, notifier = make_orchestrator(checker)
        monitor = make_monitor(
            config={"alert_on_error": False},
            last_state={"condition_met": False, "consecutive_failures": 3},
        )

        orch.check_monitor(monitor)

        notifier.send_recovery.assert_not_called()
