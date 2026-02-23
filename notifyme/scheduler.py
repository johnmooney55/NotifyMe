"""Check orchestration and scheduling logic."""

import logging
from datetime import datetime
from typing import Callable

from .checkers import AgenticChecker, CreditsChecker, NewsChecker, PriceChecker, WebpageChecker
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

        try:
            result = checker.check(monitor)

            # Determine if we should notify
            should_notify = checker.should_notify(monitor, result)

            if should_notify:
                logger.info(f"Condition met for {monitor.name}, sending notification")
                notification = self.notifier.send(monitor, result, dry_run=self.dry_run)
                self.db.add_notification(notification)

            # Update monitor state
            monitor.last_checked = datetime.now()
            monitor.last_state = checker.get_state_for_storage(result, monitor)
            monitor.last_state_hash = result.state_hash
            self.db.update_monitor(monitor)

            if on_result:
                on_result(monitor, result)

            return result

        except Exception as e:
            logger.error(f"Error checking {monitor.name}: {e}")
            raise

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
