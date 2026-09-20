"""Tests for the PenFed import-freshness checker.

Weighted towards two things that would each defeat the point of having it:
emailing every day while an outage continues, and staying silent through a
SECOND outage because the first was already reported. Monarch cannot sync this
card, so a missed alert means nothing at all is recording it.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from notifyme.checkers.penfed import PenFedChecker
from notifyme.models import Monitor, MonitorType


def ago(days):
    """A last-success stamp in the shape SQLite writes: UTC, no zone marker."""
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.strftime("%Y-%m-%d %H:%M:%S")


def freshness(
    last_success=None,
    newest="2026-09-13",
    run_status="success",
    error=None,
    session_alive=None,
    session_state="alive",
):
    return {
        "lastSuccessAt": last_success,
        "lastRunAt": last_success,
        "lastRunStatus": run_status,
        "lastRunError": error,
        "newestTransaction": newest,
        # Default: the keep-alive touched it a minute ago, which is the normal
        # state of a working system.
        "sessionAliveAt": session_alive if session_alive is not None else ago(0),
        "sessionCheckedAt": ago(0),
        "sessionState": session_state,
        "sessionDetail": None,
    }


def monitor(config=None, last_state=None):
    return Monitor(
        name="PenFed Import Freshness",
        type=MonitorType.PENFED,
        url="http://127.0.0.1:8081",
        config=config or {},
        last_state=last_state or {},
    )


@pytest.fixture
def checker():
    with patch.dict("os.environ", {"FINANCE_CENTER_TOKEN": "t"}):
        yield PenFedChecker()


def run(checker, payload, mon=None):
    response = MagicMock()
    response.json.return_value = {"result": payload}
    response.raise_for_status.return_value = None
    with patch("notifyme.checkers.penfed.requests.post", return_value=response):
        return checker.check(mon or monitor())


class TestStatus:
    def test_a_recent_import_is_quiet(self, checker):
        result = run(checker, freshness(ago(2)))
        assert result.details["status"] == "ok"
        assert not result.condition_met

    def test_ten_days_warns(self, checker):
        # One missed weekly run plus slack. The card posts about ten times a
        # month, so a fortnight of silence is indistinguishable from a quiet
        # fortnight — which is exactly why it is the machine that has to notice.
        result = run(checker, freshness(ago(11)))
        assert result.details["status"] == "warning"
        assert result.condition_met

    def test_thirty_days_is_critical(self, checker):
        result = run(checker, freshness(ago(31)))
        assert result.details["status"] == "critical"

    def test_never_having_run_is_critical_not_ok(self, checker):
        # The dangerous reading. No last success is not "nothing to report",
        # it is "nothing has ever been recorded".
        result = run(checker, freshness(None, newest=None))
        assert result.details["status"] == "critical"
        assert result.condition_met

    def test_thresholds_are_configurable(self, checker):
        result = run(checker, freshness(ago(5)), monitor(config={"warn_days": 3}))
        assert result.details["status"] == "warning"


class TestDedupe:
    def test_does_not_re_notify_the_next_day(self, checker):
        # The last-success stamp is FIXED while an outage runs — it only moves
        # when a pull succeeds. So the same stamp coming back tomorrow, and the
        # day after, is what a continuing outage actually looks like, and the
        # event id keyed on it is what stops a daily email.
        stuck = ago(11)
        first = run(checker, freshness(stuck))
        assert first.condition_met
        state = checker.get_state_for_storage(first, monitor())
        second = run(checker, freshness(stuck), monitor(last_state=state))
        assert not second.condition_met

    def test_escalation_to_critical_re_notifies(self, checker):
        # Same frozen stamp, three weeks further on: the status changes even
        # though the id's date part does not, so it speaks a second time.
        stuck = ago(11)
        first = run(checker, freshness(stuck))
        state = checker.get_state_for_storage(first, monitor())
        worse = run(
            checker, freshness(stuck), monitor(config={"critical_days": 10}, last_state=state)
        )
        assert worse.condition_met
        assert worse.details["status"] == "critical"

    def test_a_LATER_outage_notifies_again(self, checker):
        # The bug this guards: keying on status alone would mark 'penfed:warning'
        # seen forever, and the next outage — months later, after the card had
        # been recording fine — would be swallowed in silence.
        first = run(checker, freshness(ago(11)))
        state = checker.get_state_for_storage(first, monitor())
        recovered = run(checker, freshness(ago(1)), monitor(last_state=state))
        assert not recovered.condition_met
        later = run(checker, freshness(ago(15)), monitor(last_state=state))
        assert later.condition_met

    def test_state_is_bounded(self, checker):
        state = {"seen_ids": [f"penfed:2026-01-{d:02d}:warning" for d in range(1, 29)]}
        result = run(checker, freshness(ago(11)), monitor(last_state=state))
        stored = checker.get_state_for_storage(result, monitor(last_state=state))
        assert len(stored["seen_ids"]) <= 50


class TestExplanation:
    def test_says_what_to_do_about_it(self, checker):
        result = run(checker, freshness(ago(11)))
        assert "login.mjs" in result.explanation
        assert "Monarch cannot sync this card" in result.explanation

    def test_carries_the_last_error(self, checker):
        result = run(
            checker,
            freshness(ago(11), run_status="refused", error="TRNTYPE DEBIT disagrees with the sign"),
        )
        assert "disagrees with the sign" in result.explanation

    def test_quiet_message_still_states_the_facts(self, checker):
        result = run(checker, freshness(ago(1)))
        assert "2026-09-13" in result.explanation


class TestPlumbing:
    def test_missing_token_is_a_clear_error(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="FINANCE_CENTER_TOKEN"):
                PenFedChecker().check(monitor())

    def test_an_unparseable_stamp_is_treated_as_never(self, checker):
        # Fail towards noticing. A stamp we cannot read must not read as fresh.
        result = run(checker, freshness("not a date"))
        assert result.details["status"] == "critical"


class TestSession:
    """The session is the fragile part — PenFed drops it after ~15 idle
    minutes, which is why a keep-alive touches it every 10. These are about
    hearing that it died within the hour instead of next week."""

    def test_a_live_session_is_quiet(self, checker):
        result = run(checker, freshness(ago(1)))
        assert result.details["status"] == "ok"
        assert not result.condition_met

    def test_a_session_gone_two_hours_warns(self, checker):
        # Twelve consecutive keep-alive failures. Past a locked profile, a
        # reboot, or the weekly pull holding the browser.
        result = run(checker, freshness(ago(1), session_alive=ago(0.2), session_state="dead"))
        assert result.condition_met
        assert "session has been gone" in result.explanation

    def test_a_brief_gap_does_not_warn(self, checker):
        # One missed run — the pull itself holds the profile for a minute.
        result = run(checker, freshness(ago(1), session_alive=ago(0.02)))
        assert not result.condition_met

    def test_silent_until_the_session_has_EVER_been_alive(self, checker):
        # Before the first login there is nothing to have lost. Nagging about a
        # setup step already written down is how an alert channel gets ignored.
        payload = freshness(ago(1))
        payload["sessionAliveAt"] = None
        result = run(checker, payload)
        assert not result.condition_met

    def test_a_dead_session_and_a_stale_import_are_separate_alerts(self, checker):
        # They are different failures needing different fixes — a login versus
        # reading pull.log — so neither may hide the other.
        result = run(checker, freshness(ago(20), session_alive=ago(1), session_state="dead"))
        assert result.details["new"] == 2
        kinds = {i.split(":")[1] for i in result.details["event_ids"]}
        assert kinds == {"session", "import"}

    def test_fixing_the_session_still_leaves_the_import_alert_standing(self, checker):
        both = run(checker, freshness(ago(20), session_alive=ago(1), session_state="dead"))
        state = checker.get_state_for_storage(both, monitor())
        # Session restored, import still stale: already reported, so quiet.
        after = run(checker, freshness(ago(20)), monitor(last_state=state))
        assert not after.condition_met
        assert after.details["status"] == "warning"

    def test_the_session_alert_does_not_repeat_each_hour(self, checker):
        payload = freshness(ago(1), session_alive=ago(0.5), session_state="dead")
        first = run(checker, payload)
        assert first.condition_met
        state = checker.get_state_for_storage(first, monitor())
        second = run(checker, payload, monitor(last_state=state))
        assert not second.condition_met
