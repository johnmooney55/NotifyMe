"""Check orchestration and scheduling logic."""

import logging
from datetime import datetime, timedelta
from typing import Callable

from .checkers import (
    AgenticChecker,
    CreditsChecker,
    FinanceCenterChecker,
    NewsChecker,
    PriceChecker,
    WebpageChecker,
)
from .checkers.base import BaseChecker
from .database import Database
from .models import CheckResult, Monitor, MonitorType
from .notifier import EmailNotifier

logger = logging.getLogger(__name__)


# Map monitor types to checker classes
CHECKER_MAP: dict[MonitorType, type[BaseChecker]] = {
    MonitorType.AGENTIC: AgenticChecker,
    MonitorType.CREDITS: CreditsChecker,
    MonitorType.NEWS: NewsChecker,
    MonitorType.WEBPAGE: WebpageChecker,
    MonitorType.PRICE: PriceChecker,
    MonitorType.RSS: NewsChecker,  # RSS uses same checker as news
    MonitorType.FINANCE_CENTER: FinanceCenterChecker,
}


class CheckOrchestrator:
    """Orchestrates monitor checking and notifications."""

    def __init__(
        self,
        db: Database | None = None,
        notifier: EmailNotifier | None = None,
        dry_run: bool = False,
    ):
        self.db = db or Database()
        self.notifier = notifier or EmailNotifier()
        self.dry_run = dry_run
        self._checkers: dict[MonitorType, BaseChecker] = {}

    def get_checker(self, monitor_type: MonitorType) -> BaseChecker:
        """Get or create checker instance for monitor type."""
        if monitor_type not in self._checkers:
            checker_class = CHECKER_MAP.get(monitor_type)
            if not checker_class:
                raise ValueError(f"No checker available for type: {monitor_type}")
            self._checkers[monitor_type] = checker_class()
        return self._checkers[monitor_type]

    def check_monitor(
        self,
        monitor: Monitor,
        on_result: Callable[[Monitor, CheckResult], None] | None = None,
    ) -> CheckResult:
        """
        Check a single monitor and handle notification if needed.

        Args:
            monitor: Monitor to check
            on_result: Optional callback for result

        Returns:
            CheckResult from the check
        """
        logger.info(f"Checking monitor: {monitor.name} ({monitor.type.value})")

        checker = self.get_checker(monitor.type)

        # Failure tracking carried over from the previous check.
        prior_failures = monitor.last_state.get("consecutive_failures", 0)

        try:
            result = checker.check(monitor)
        except Exception as e:
            logger.error(f"Error checking {monitor.name}: {e}")
            self._handle_check_failure(monitor, e, prior_failures)
            raise

        # The check succeeded. If it had been failing, send a recovery alert.
        if prior_failures > 0:
            logger.info(
                f"{monitor.name} recovered after {prior_failures} failed check(s)"
            )
            self._notify_recovery(monitor, prior_failures)

        # Determine if we should notify
        should_notify = checker.should_notify(monitor, result)

        if should_notify:
            logger.info(f"Condition met for {monitor.name}, sending notification")
            notification = self.notifier.send(monitor, result, dry_run=self.dry_run)
            self.db.add_notification(notification)

        # Update monitor state. A successful check's stored state carries no
        # failure keys, so consecutive_failures naturally resets to 0.
        monitor.last_checked = datetime.now()
        monitor.last_state = checker.get_state_for_storage(result, monitor)
        monitor.last_state_hash = result.state_hash
        self.db.update_monitor(monitor)

        if on_result:
            on_result(monitor, result)

        return result

    def _handle_check_failure(
        self, monitor: Monitor, error: Exception, prior_failures: int
    ) -> None:
        """
        Record a failed check and alert on it.

        A "check failure" is the check itself erroring out (timeout, connection
        refused, DNS failure, etc.) rather than the monitored condition being
        met. To avoid flooding, an alert is sent on the first failure of an
        outage and then at most once per ``error_realert_hours`` while it
        persists. ``last_checked`` is always advanced so a failing monitor
        still respects its normal interval.
        """
        now = datetime.now()
        consecutive_failures = prior_failures + 1
        error_text = str(error).strip()[:1000]

        first_failure_at = monitor.last_state.get("first_failure_at") or now.isoformat()

        # Preserve the prior real state; layer failure tracking on top so the
        # last good condition state survives the outage. last_error_alert_at is
        # carried over by the copy unless we send a fresh alert below.
        new_state = dict(monitor.last_state)
        new_state["consecutive_failures"] = consecutive_failures
        new_state["first_failure_at"] = first_failure_at
        new_state["last_error"] = error_text

        alerts_enabled = monitor.config.get("alert_on_error", True)
        if alerts_enabled and self._should_alert_failure(monitor, prior_failures):
            logger.info(
                f"Sending check-failure alert for {monitor.name} "
                f"({consecutive_failures} consecutive failure(s))"
            )
            try:
                notification = self.notifier.send_check_failure(
                    monitor,
                    error_text,
                    consecutive_failures,
                    first_failure_at,
                    dry_run=self.dry_run,
                )
                self.db.add_notification(notification)
                new_state["last_error_alert_at"] = now.isoformat()
            except Exception as notify_error:
                logger.error(
                    f"Failed to send check-failure alert for {monitor.name}: "
                    f"{notify_error}"
                )
        else:
            logger.info(
                f"{monitor.name} still failing ({consecutive_failures} consecutive); "
                "alert suppressed"
            )

        monitor.last_checked = now
        monitor.last_state = new_state
        self.db.update_monitor(monitor)

    def _should_alert_failure(self, monitor: Monitor, prior_failures: int) -> bool:
        """Decide whether to send an alert for an ongoing check failure."""
        # Always alert on the first failure of an outage.
        if prior_failures == 0:
            return True

        # Otherwise re-alert at most once per the re-alert window.
        last_alert_at = monitor.last_state.get("last_error_alert_at")
        if not last_alert_at:
            return True

        realert_hours = monitor.config.get("error_realert_hours", 24)
        try:
            elapsed = datetime.now() - datetime.fromisoformat(last_alert_at)
        except (ValueError, TypeError):
            return True
        return elapsed >= timedelta(hours=realert_hours)

    def _notify_recovery(self, monitor: Monitor, failed_checks: int) -> None:
        """Send a recovery alert for a monitor that was previously failing."""
        if not monitor.config.get("alert_on_error", True):
            return
        try:
            notification = self.notifier.send_recovery(
                monitor, failed_checks, dry_run=self.dry_run
            )
            self.db.add_notification(notification)
        except Exception as notify_error:
            logger.error(
                f"Failed to send recovery alert for {monitor.name}: {notify_error}"
            )

    def check_all_due(
        self,
        on_result: Callable[[Monitor, CheckResult], None] | None = None,
    ) -> list[tuple[Monitor, CheckResult]]:
        """
        Check all monitors that are due for checking.

        Args:
            on_result: Optional callback for each result

        Returns:
            List of (monitor, result) tuples
        """
        due_monitors = self.db.get_monitors_due_for_check()
        logger.info(f"Found {len(due_monitors)} monitor(s) due for checking")

        results = []
        for monitor in due_monitors:
            try:
                result = self.check_monitor(monitor, on_result)
                results.append((monitor, result))
            except Exception as e:
                logger.error(f"Failed to check {monitor.name}: {e}")
                # Continue with other monitors

        return results

    def check_all(
        self,
        on_result: Callable[[Monitor, CheckResult], None] | None = None,
    ) -> list[tuple[Monitor, CheckResult]]:
        """
        Check all active monitors regardless of schedule.

        Args:
            on_result: Optional callback for each result

        Returns:
            List of (monitor, result) tuples
        """
        monitors = self.db.list_monitors(active_only=True)
        logger.info(f"Checking all {len(monitors)} active monitor(s)")

        results = []
        for monitor in monitors:
            try:
                result = self.check_monitor(monitor, on_result)
                results.append((monitor, result))
            except Exception as e:
                logger.error(f"Failed to check {monitor.name}: {e}")

        return results
