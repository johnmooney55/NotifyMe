"""Base checker class and utilities."""

from abc import ABC, abstractmethod
from typing import Any

from ..models import CheckResult, Monitor


class BaseChecker(ABC):
    """Abstract base class for all monitor checkers."""

    @abstractmethod
    def check(self, monitor: Monitor) -> CheckResult:
        """
        Check the monitor and return the result.

        Args:
            monitor: The monitor to check

        Returns:
            CheckResult indicating whether condition is met and explanation
        """
        pass

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        """
        Determine if a notification should be sent.

        Default implementation: notify on state change from False to True.
        Subclasses can override for different behavior.

        Args:
            monitor: The monitor being checked
            result: The check result

        Returns:
            True if notification should be sent
        """
        previous_met = monitor.last_state.get("condition_met", False)
        return result.condition_met and not previous_met

    def get_state_for_storage(self, result: CheckResult) -> dict[str, Any]:
        """
        Get the state to store after checking.

        Args:
            result: The check result

        Returns:
            Dictionary to store as last_state
        """
        return {
            "condition_met": result.condition_met,
            "explanation": result.explanation,
            "details": result.details,
        }
