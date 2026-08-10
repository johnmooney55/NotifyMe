"""Tests for the Finance Center checker.

Weighted towards dedupe. Finance Center's payload changes every single day
because daysUntilExpiry counts down, so a checker that keys state on the
payload would email daily — which is the failure mode these tests exist to
prevent.
"""

from unittest.mock import MagicMock, patch

import pytest

from notifyme.checkers.finance_center import FinanceCenterChecker
from notifyme.models import Monitor, MonitorType


def item(id_="c1", status="warning", days=5, title="Delta Credit", amount=200.0, category="airline"):
    return {
        "id": id_,
        "type": "credit_expiring",
        "title": title,
        "message": f"{title} expires soon",
        "expirationDate": "2026-08-20",
        "daysUntilExpiry": days,
        "status": status,
        "amount": amount,
        "category": category,
        "cardName": "Amex Platinum",
    }


def monitor(config=None, last_state=None):
    return Monitor(
        name="Finance Center",
        type=MonitorType.FINANCE_CENTER,
        url="http://127.0.0.1:8081",
        config=config or {},
        last_state=last_state or {},
    )


@pytest.fixture
def checker():
    c = FinanceCenterChecker()
    c.token = "test-token"
    return c


def respond(items):
    """Patch requests.post to return the given items."""
    response = MagicMock()
    response.json.return_value = {"result": items}
    response.raise_for_status.return_value = None
    return patch("notifyme.checkers.finance_center.requests.post", return_value=response)


class TestFinanceCenterChecker:
    def test_reports_new_items(self, checker):
        with respond([item()]):
            result = checker.check(monitor())
        assert result.condition_met
        assert result.details["new"] == 1
        assert "Delta Credit" in result.explanation

    def test_no_items_is_not_a_notification(self, checker):
        with respond([]):
            result = checker.check(monitor())
        assert not result.condition_met
        assert not checker.should_notify(monitor(), result)

    def test_same_item_next_day_does_not_re_notify(self, checker):
        """The whole point: daysUntilExpiry ticks down but nothing has changed."""
        m = monitor(last_state={"seen_ids": ["c1:warning"]})
        with respond([item(days=4)]):  # one day later
            result = checker.check(m)
        assert result.details["new"] == 0
        assert not checker.should_notify(m, result)

    def test_escalation_to_critical_re_notifies(self, checker):
        """warning -> critical is a genuinely new event and must get through."""
        m = monitor(last_state={"seen_ids": ["c1:warning"]})
        with respond([item(status="critical", days=1)]):
            result = checker.check(m)
        assert result.details["new"] == 1
        assert checker.should_notify(m, result)
        assert "CRITICAL" in result.explanation

    def test_critical_only_filter(self, checker):
        with respond([item(id_="a", status="warning"), item(id_="b", status="critical")]):
            result = checker.check(monitor(config={"critical_only": True}))
        assert result.details["total_matching"] == 1
        assert result.details["event_ids"] == ["b:critical"]

    def test_category_filter(self, checker):
        with respond([item(id_="a", category="airline"), item(id_="b", category="gift_card")]):
            result = checker.check(monitor(config={"categories": ["gift_card"]}))
        assert result.details["event_ids"] == ["b:warning"]
        assert result.details["total_matching"] == 1

    def test_state_accumulates_and_is_bounded(self, checker):
        m = monitor(last_state={"seen_ids": [f"old{i}:warning" for i in range(500)]})
        with respond([item(id_="new1")]):
            result = checker.check(m)
        state = checker.get_state_for_storage(result, m)
        assert state["seen_ids"][0] == "new1:warning"
        assert len(state["seen_ids"]) == 500  # capped, newest first

    def test_sorted_soonest_first(self, checker):
        with respond([item(id_="far", days=20, title="Far"), item(id_="near", days=1, title="Near")]):
            result = checker.check(monitor())
        assert result.explanation.index("Near") < result.explanation.index("Far")

    def test_missing_token_is_a_clear_error(self):
        c = FinanceCenterChecker()
        c.token = None
        with pytest.raises(RuntimeError, match="FINANCE_CENTER_TOKEN"):
            c.check(monitor())

    def test_expired_item_reads_sensibly(self, checker):
        with respond([item(days=-2)]):
            result = checker.check(monitor())
        assert "expired" in result.explanation
