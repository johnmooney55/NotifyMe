"""PenFed import freshness.

The PENFED VISA is the one card Monarch cannot sync — PenFed does not partner
with Plaid and dropped Quicken Direct Connect — so its transactions are pulled
weekly from PenFed's own OFX export by a launchd job on this Mac
(`me.mooney.penfed-sync`). That job depends on a browser session it cannot
renew by itself, and if the session dies nothing breaks loudly: the card simply
stops recording, which looks exactly like a quiet month on a card used ten
times a month.

Finance Center already knows when it last managed a successful import. This
asks it, and mails when the answer gets old:

    POST {url}/rpc/penfed:freshness
    Authorization: Bearer $FINANCE_CENTER_TOKEN

Two things are watched, on very different clocks:

  * **The session.** PenFed logs out after about fifteen idle minutes, so a
    keep-alive touches the account page every ten and records what it found.
    A session that has gone means the next pull cannot work, and that is worth
    knowing within the hour rather than next week.
  * **The import.** Even with a live session the pull can fail — a changed
    page, a refused file — so the age of the last SUCCESSFUL import is
    watched separately, in days.

Each produces at most one alert per episode, the same shape the
expiring-credits checker uses. The event id carries the date the episode
started, so a continuing outage stays quiet and the NEXT one alerts again
rather than being swallowed as already-seen.
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

# Ten days is one missed weekly run plus slack; thirty means it has been broken
# for a month and the tax year is quietly losing a card. Both match the
# threshold /health uses so the dashboard and the mail cannot disagree.
DEFAULT_WARN_DAYS = 10
DEFAULT_CRITICAL_DAYS = 30

# The keep-alive runs every ten minutes, so two hours is a dozen consecutive
# failures — comfortably past a locked profile, a reboot, or the weekly pull
# holding the browser.
DEFAULT_SESSION_HOURS = 2

MAX_SEEN_IDS = 50


class PenFedChecker(BaseChecker):
    """Checker for PenFed import staleness.

    Config options:
        - warn_days: days since the last successful import before warning
          (default: 10)
        - critical_days: days before escalating (default: 30)
        - session_hours: hours the browser session may be gone before warning
          (default: 2)
        - timeout: request timeout in seconds (default: 15)

    Environment:
        - FINANCE_CENTER_TOKEN: bearer token (server/.env FC_API_TOKEN on the mini)
    """

    def __init__(self):
        self.token = os.getenv("FINANCE_CENTER_TOKEN")

    def check(self, monitor: Monitor) -> CheckResult:
        base = (monitor.url or DEFAULT_URL).rstrip("/")
        timeout = int(monitor.config.get("timeout", 15))
        warn_days = int(monitor.config.get("warn_days", DEFAULT_WARN_DAYS))
        critical_days = int(monitor.config.get("critical_days", DEFAULT_CRITICAL_DAYS))

        if not self.token:
            raise RuntimeError(
                "FINANCE_CENTER_TOKEN is not set — cannot query Finance Center. "
                "It is FC_API_TOKEN in ~/finance-center/server/.env on the mini."
            )

        response = requests.post(
            f"{base}/rpc/penfed:freshness",
            json={"args": []},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        freshness: dict[str, Any] = response.json().get("result") or {}

        session_hours = int(monitor.config.get("session_hours", DEFAULT_SESSION_HOURS))

        issues = self._issues(freshness, warn_days, critical_days, session_hours)
        seen = set(monitor.last_state.get("seen_ids", []))
        fresh = [i for i in issues if i["event_id"] not in seen]

        return CheckResult(
            condition_met=bool(fresh),
            explanation=self._explain(freshness, issues, fresh),
            details={
                "status": self._worst(issues),
                "new": len(fresh),
                "event_ids": [i["event_id"] for i in fresh],
                "_fresh": fresh,
                "last_success": freshness.get("lastSuccessAt"),
                "last_run_status": freshness.get("lastRunStatus"),
                "newest_transaction": freshness.get("newestTransaction"),
                "session_state": freshness.get("sessionState"),
                "session_alive_at": freshness.get("sessionAliveAt"),
            },
        )

    def _issues(
        self, freshness: dict[str, Any], warn_days: int, critical_days: int, session_hours: int
    ) -> list[dict[str, Any]]:
        """Everything currently wrong, worst first. Empty means healthy."""
        out: list[dict[str, Any]] = []

        # --- the session -------------------------------------------------
        alive_at = freshness.get("sessionAliveAt")
        if alive_at:
            # Deliberately silent until the session has been alive at least
            # once: before the first login there is nothing to have lost, and
            # nagging about a setup step is how an alert channel gets ignored.
            hours = self._age_days(alive_at)
            hours = None if hours is None else hours * 24
            if hours is None or hours >= session_hours:
                out.append({
                    "id": "session",
                    "status": "warning",
                    "event_id": f"penfed:session:{alive_at[:13]}:warning",
                    "headline": (
                        f"The PenFed browser session has been gone for "
                        f"{hours:.0f} hours." if hours is not None
                        else "The PenFed browser session state cannot be read."
                    ),
                })

        # --- the import --------------------------------------------------
        age = self._age_days(freshness.get("lastSuccessAt"))
        status = self._status(age, warn_days, critical_days)
        if status != "ok":
            when = "has NEVER succeeded" if age is None else f"last succeeded {age:.0f} days ago"
            out.append({
                "id": "import",
                "status": status,
                "event_id": self._event_id(freshness.get("lastSuccessAt"), status),
                "headline": f"The PenFed import {when}.",
            })

        return out

    @staticmethod
    def _worst(issues: list[dict[str, Any]]) -> str:
        if any(i["status"] == "critical" for i in issues):
            return "critical"
        if issues:
            return "warning"
        return "ok"

    @staticmethod
    def _age_days(last_success: str | None) -> float | None:
        """None means it has never succeeded, which is not the same as old."""
        if not last_success:
            return None
        # SQLite writes "YYYY-MM-DD HH:MM:SS" in UTC with no zone marker.
        text = last_success.strip().replace(" ", "T")
        if not text.endswith("Z") and "+" not in text:
            text += "+00:00"
        try:
            when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("penfed: could not parse lastSuccessAt %r", last_success)
            return None
        return (datetime.now(timezone.utc) - when).total_seconds() / 86400

    @staticmethod
    def _status(age: float | None, warn_days: int, critical_days: int) -> str:
        if age is None:
            # Never imported. Real on a fresh install and after a restore, and
            # worth saying out loud either way — nothing is being recorded.
            return "critical"
        if age >= critical_days:
            return "critical"
        if age >= warn_days:
            return "warning"
        return "ok"

    @staticmethod
    def _event_id(last_success: str | None, status: str) -> str:
        """Stable through one episode; a new episode is a new id.

        `penfed:{kind}:{anchor}:{status}` — the same shape the session issue
        uses, so the two can never produce the same string for different
        problems. Anchored on the DATE of the last success rather than on the
        status alone, so a second outage months later is not swallowed as
        already-seen.
        """
        stamp = (last_success or "never")[:10]
        return f"penfed:import:{stamp}:{status}"

    def _explain(
        self, freshness: dict[str, Any], issues: list[dict[str, Any]], fresh: list[dict[str, Any]]
    ) -> str:
        newest = freshness.get("newestTransaction") or "none recorded"
        last_run = freshness.get("lastRunStatus") or "no run recorded"
        error = freshness.get("lastRunError")
        age = self._age_days(freshness.get("lastSuccessAt"))

        if not issues:
            current = f"{age:.1f} days ago" if age is not None else "never"
            return (
                f"PenFed is healthy — session alive, last import {current}, "
                f"newest transaction {newest}."
            )
        if not fresh:
            return f"Nothing new: {len(issues)} issue(s) already reported."

        lines = [i["headline"] for i in fresh]
        lines += [
            "",
            f"Newest PenFed transaction held: {newest}",
            f"Last run: {last_run}" + (f" — {error}" if error else ""),
            f"Session: {freshness.get('sessionState') or 'unknown'}"
            + (f" — {freshness.get('sessionDetail')}" if freshness.get("sessionDetail") else ""),
            "",
            "Monarch cannot sync this card, so nothing else is recording it.",
            "Most likely the browser session expired. On the mini, in a Screen",
            "Sharing session:",
            "",
            "    cd ~/finance-center/penfed-sync && node login.mjs",
            "    launchctl kickstart -k gui/501/me.mooney.penfed-sync",
            "",
            "Then read ~/penfed-sync-logs/pull.log. PenFed keeps only 25 months",
            "of history, so a long outage eventually becomes unrecoverable.",
        ]
        return "\n".join(lines)

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        return bool(result.details.get("new", 0))

    def get_state_for_storage(self, result: CheckResult, monitor: Monitor) -> dict[str, Any]:
        existing = monitor.last_state.get("seen_ids", [])
        new_ids = [i["event_id"] for i in result.details.get("_fresh", [])]
        return {
            "condition_met": result.condition_met,
            "seen_ids": (new_ids + existing)[:MAX_SEEN_IDS],
            "last_status": result.details.get("status"),
        }
