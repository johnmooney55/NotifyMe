"""Finance Center Transaction Sync Engine — one checker for every connection.

A "connection" is somewhere transactions come from that is not Monarch. PenFed
is the first; the point of this checker is that the second costs no code here
at all.

    POST {url}/rpc/connections:freshness
    Authorization: Bearer $FINANCE_CENTER_TOKEN

Finance Center returns an array of facts and decides nothing. All policy lives
below, which is the division that keeps the two from disagreeing — the repo has
already been bitten once by a threshold written down in two places and wrong in
one of them.

**The reporting rule.** A connection that has just been established confirms
itself for three days, then goes quiet and speaks only when it breaks:

  * healthy and inside the window  -> one success mail per successful day
  * healthy and past the window    -> silence
  * broken                         -> one mail per episode, however long it lasts

The window is anchored on the first success THIS CHECKER observes, not on the
connection's first import ever. That distinction matters because every
connection will be built the same way PenFed was: backfilled by hand, proven,
and only then wired to monitoring. Anchoring on the data's birth would mean a
connection silently skipping its own confirmation, and the three mails exist to
prove the whole chain — pull, import, RPC, checker, SMTP, inbox — not merely
that rows arrived.

**Event ids** are `connection:{name}:{kind}:{anchor}`, and the anchors are
chosen so de-duplication needs no extra bookkeeping:

  * Failure anchors FREEZE during an outage (they hang off `lastSuccessAt`, or
    `lastRunAt`, which stop advancing when things break), so a continuing
    outage mails once and a later, separate outage is not swallowed as
    already-seen.
  * Success anchors hang off `lastSuccessAt` too, so there is exactly one
    confirmation per day on which something actually succeeded — and no
    comparison between the checker's local midnight and SQLite's UTC dates,
    which is the bug that anchoring on `date.today()` would have needed a
    second guard to avoid.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8081"

# Days of confirmation after a connection is first seen working. Three is
# enough to show the schedule running on its own rather than because someone
# was watching.
DEFAULT_CONFIRM_DAYS = 3

# No successful import in this long. Three days because the pulls run daily:
# two missed runs and no more. Matches CONNECTION_STALE_DAYS in
# electron/ipc/penfed.ts, which is what /health uses — deliberately two copies
# in two repos, each commented, rather than one pretending to serve both.
DEFAULT_STALE_DAYS = 3
DEFAULT_CRITICAL_DAYS = 10

# No run AT ALL in this long, successful or not. Distinct from staleness on
# purpose: a job launchd has stopped running leaves `lastRunStatus` sitting at
# 'success' forever, so nothing about the last run ever looks wrong. This is
# the only rule that catches it.
DEFAULT_SILENCE_DAYS = 2

DEFAULT_TIMEOUT = 15

# 500, not the 50 the penfed checker used. The ids are shared across every
# connection now, and the failure ids are the ones that must not age out: an id
# falling off the end mid-outage would re-mail an outage already reported.
MAX_SEEN_IDS = 500


class ConnectionChecker(BaseChecker):
    """Reports every Transaction Sync Engine connection."""

    def __init__(self) -> None:
        self.token = os.getenv("FINANCE_CENTER_TOKEN")

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _age_days(timestamp: str | None) -> float | None:
        """Days since a SQLite UTC timestamp, or None if unreadable."""
        if not timestamp:
            return None
        try:
            parsed = datetime.fromisoformat(f"{timestamp}+00:00")
        except ValueError:
            logger.warning("connection: could not parse timestamp %r", timestamp)
            return None
        return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400

    @staticmethod
    def _day(timestamp: str | None) -> str:
        return (timestamp or "never")[:10]

    @staticmethod
    def _local(timestamp: str | None) -> str:
        """A SQLite UTC timestamp, as local time, with the zone named.

        Finance Center stores UTC and says so nowhere in the string, so these
        headlines used to print it raw. A mail that arrived at 04:17 EDT read
        "since 2026-09-23 06:35:33" and was taken for a report about the 06:30
        job — it was 02:35 local, four hours earlier and a different event
        entirely. Every other timestamp a person sees from this machine is
        local, so this one is too.

        Deliberately NOT used in event ids. Those hang off `_day` and are the
        de-duplication keys; shifting them by a timezone would re-mail every
        outage already reported.
        """
        if not timestamp:
            return "ever"
        try:
            parsed = datetime.fromisoformat(f"{timestamp}+00:00")
        except ValueError:
            # Unparseable is still better shown than swallowed.
            return timestamp
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M %Z")

    # ------------------------------------------------------------------ fetch

    def _fetch(self, monitor: Monitor) -> list[dict[str, Any]]:
        if not self.token:
            raise RuntimeError("FINANCE_CENTER_TOKEN is not set")
        base = (monitor.url or DEFAULT_URL).rstrip("/")
        timeout = monitor.config.get("timeout", DEFAULT_TIMEOUT)
        response = requests.post(
            f"{base}/rpc/connections:freshness",
            json={"args": []},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json().get("result")
        if not isinstance(result, list):
            raise RuntimeError(f"connections:freshness returned {type(result).__name__}, not a list")
        return result

    # ------------------------------------------------------------- the rules

    def _events(self, conn: dict[str, Any], monitor: Monitor, state: dict) -> tuple[list[dict], str | None]:
        """Events for one connection, plus the confirm-window anchor to store."""
        name = conn.get("connection") or "unknown"
        label = conn.get("label") or name
        last_success = conn.get("lastSuccessAt")
        last_run = conn.get("lastRunAt")
        last_status = conn.get("lastRunStatus")

        confirm_days = monitor.config.get("confirm_days", DEFAULT_CONFIRM_DAYS)
        stale_days = monitor.config.get("stale_days", DEFAULT_STALE_DAYS)
        critical_days = monitor.config.get("critical_days", DEFAULT_CRITICAL_DAYS)
        silence_days = monitor.config.get("silence_days", DEFAULT_SILENCE_DAYS)

        # The confirm anchor: the first success this checker ever saw for this
        # connection. Recorded even when nothing is mailed, which is why it is
        # returned rather than written here.
        known = state.get("connections", {}).get(name, {})
        confirm_from = known.get("confirm_from") or last_success

        events: list[dict] = []
        failed = False

        # 1. The job has stopped running at all.
        run_age = self._age_days(last_run)
        if last_run is None or (run_age is not None and run_age >= silence_days):
            failed = True
            events.append({
                "connection": name,
                "status": "critical",
                "event_id": f"connection:{name}:silent:{self._day(last_run)}",
                "headline": (
                    f"{label} has not run at all since {self._local(last_run)}."
                    " The scheduled job is not running — check launchd, not the connection."
                ),
            })

        # 2. The last run failed. Anchored on the last SUCCESS, which is frozen
        #    for the duration, so a run failing every morning mails once.
        elif last_status and last_status != "success":
            failed = True
            events.append({
                "connection": name,
                "status": "warning",
                "event_id": f"connection:{name}:fail:{self._day(last_success)}",
                "headline": (
                    f"{label} last run {last_status}"
                    f"{': ' + conn['lastRunError'] if conn.get('lastRunError') else ''}."
                    f" Last success {self._local(last_success)}."
                ),
            })

        # 3. Nothing has succeeded in too long. Can fire alongside 2 when a
        #    connection has been failing for days; they are different facts.
        success_age = self._age_days(last_success)
        if last_success is None or (success_age is not None and success_age >= stale_days):
            failed = True
            status = "critical" if (success_age is None or success_age >= critical_days) else "warning"
            events.append({
                "connection": name,
                "status": status,
                "event_id": f"connection:{name}:stale:{self._day(last_success)}:{status}",
                "headline": (
                    f"{label} has not imported successfully since {self._local(last_success)}"
                    f"{f' ({success_age:.0f} days)' if success_age is not None else ''}."
                ),
            })

        # 4. Whatever the connection says about its own liveness. The checker
        #    does not know what a 'session' is and must not learn.
        for signal in conn.get("signals") or []:
            if signal.get("state") == "ok":
                continue
            failed = True
            events.append({
                "connection": name,
                "status": "critical" if signal.get("state") == "fail" else "warning",
                "event_id": (
                    f"connection:{name}:{signal.get('name')}:"
                    f"{signal.get('state')}:{(signal.get('since') or 'never')[:13]}"
                ),
                "headline": (
                    f"{label} {signal.get('name')} is {signal.get('state')}"
                    f"{': ' + signal['detail'] if signal.get('detail') else ''}"
                    f" (since {self._local(signal.get('since'))})."
                ),
            })

        # 5. Healthy, and new enough to still be proving itself.
        if not failed and confirm_from and last_success:
            age = self._age_days(confirm_from)
            if age is not None and age < confirm_days:
                day = min(int(age) + 1, confirm_days)
                events.append({
                    "connection": name,
                    "status": "success",
                    "event_id": f"connection:{name}:ok:{self._day(last_success)}",
                    "headline": (
                        f"{label} imported cleanly — day {day} of {confirm_days}"
                        f" since this connection was established."
                        f" Newest data {conn.get('newestData') or 'none'};"
                        f" last run {self._local(last_success)}."
                    ),
                })

        return events, confirm_from

    # ------------------------------------------------------------------ check

    def check(self, monitor: Monitor) -> CheckResult:
        connections = self._fetch(monitor)
        state = monitor.last_state or {}
        seen = set(state.get("seen_ids", []))

        events: list[dict] = []
        anchors: dict[str, dict] = dict(state.get("connections", {}))
        for conn in connections:
            conn_events, confirm_from = self._events(conn, monitor, state)
            events.extend(conn_events)
            name = conn.get("connection") or "unknown"
            if confirm_from:
                anchors[name] = {"confirm_from": confirm_from}

        fresh = [e for e in events if e["event_id"] not in seen]
        problems = [e for e in fresh if e["status"] != "success"]

        if not events:
            explanation = f"All {len(connections)} connection(s) healthy and past their confirmation window."
        elif not fresh:
            explanation = f"Nothing new: {len(events)} item(s) already reported."
        else:
            explanation = "\n".join(e["headline"] for e in fresh)

        return CheckResult(
            # Green only when every fresh item is good news. A failure for one
            # connection must not arrive under a green header because another
            # connection happened to be confirming itself the same morning.
            condition_met=bool(fresh) and not problems,
            explanation=explanation,
            details={
                "new": len(fresh),
                "connections": len(connections),
                "status": "ok" if not problems else max(
                    (e["status"] for e in problems),
                    key=lambda s: {"warning": 1, "critical": 2}.get(s, 0),
                ),
                "_fresh": fresh,
                "_anchors": anchors,
            },
        )

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        return bool(result.details.get("new", 0))

    def get_state_for_storage(self, result: CheckResult, monitor: Monitor) -> dict[str, Any]:
        existing = monitor.last_state.get("seen_ids", []) if monitor.last_state else []
        # Only ids that were actually reported go in, so the list does not fill
        # with events nobody was told about.
        new_ids = [e["event_id"] for e in result.details.get("_fresh", [])]
        return {
            "condition_met": result.condition_met,
            "seen_ids": (new_ids + list(existing))[:MAX_SEEN_IDS],
            "last_status": result.details.get("status"),
            "connections": result.details.get("_anchors", {}),
        }
