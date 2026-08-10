"""Finance Center expiring credits and card benefits.

Finance Center runs on the Mac mini and knows which airline credits, gift
cards, store credits and Amex benefits are about to expire. It used to email
about them itself, from the Mac, which meant alerts only went out while the
desktop app happened to be open. NotifyMe already owns notification delivery on
this machine, so it does the sending and Finance Center just answers the
question.

The endpoint is Finance Center's own RPC channel — the same one its UI calls —
so there is nothing bespoke to keep in sync on that side:

    POST {url}/rpc/notifications:getExpiringCredits
    Authorization: Bearer $FINANCE_CENTER_TOKEN

Dedupe is the interesting part. The payload changes every single day, because
`daysUntilExpiry` counts down, so keying state on the payload would email daily.
Instead each item produces an event id of `{id}:{status}`, which is stable while
an item sits at "warning" and changes exactly once when it escalates to
"critical" — so you get told when something enters the danger zone and when it
gets worse, and not otherwise.
"""

import logging
import os
from typing import Any

import requests

from ..models import CheckResult, Monitor
from .base import BaseChecker

logger = logging.getLogger(__name__)

# Keep the state list bounded, as the news checker does. Credits roll over
# constantly, so old ids stop mattering once they have expired.
MAX_SEEN_IDS = 500

DEFAULT_URL = "http://127.0.0.1:8081"


class FinanceCenterChecker(BaseChecker):
    """Checker for Finance Center expiring credits/benefits.

    Config options:
        - critical_only: only alert on items already critical (default: False)
        - categories: list of categories to include; omit for all.
          Values look like "airline", "gift_card", "store", "amex_platinum".
        - timeout: request timeout in seconds (default: 15)

    Environment:
        - FINANCE_CENTER_TOKEN: bearer token (server/.env FC_API_TOKEN on the mini)
    """

    def __init__(self):
        self.token = os.getenv("FINANCE_CENTER_TOKEN")

    def check(self, monitor: Monitor) -> CheckResult:
        base = (monitor.url or DEFAULT_URL).rstrip("/")
        timeout = int(monitor.config.get("timeout", 15))

        if not self.token:
            raise RuntimeError(
                "FINANCE_CENTER_TOKEN is not set — cannot query Finance Center. "
                "It is FC_API_TOKEN in ~/finance-center/server/.env on the mini."
            )

        response = requests.post(
            f"{base}/rpc/notifications:getExpiringCredits",
            json={"args": []},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        items: list[dict[str, Any]] = response.json().get("result") or []

        items = self._filter(items, monitor.config)
        seen = set(monitor.last_state.get("seen_ids", []))
        fresh = [i for i in items if self._event_id(i) not in seen]

        return CheckResult(
            condition_met=bool(fresh),
            explanation=self._explain(fresh, items),
            details={
                "total_matching": len(items),
                "new": len(fresh),
                "event_ids": [self._event_id(i) for i in fresh],
                # Carried so get_state_for_storage does not have to re-filter.
                "_fresh": fresh,
            },
        )

    def _filter(self, items: list[dict], config: dict) -> list[dict]:
        categories = config.get("categories")
        critical_only = bool(config.get("critical_only", False))

        out = []
        for item in items:
            if critical_only and item.get("status") != "critical":
                continue
            if categories and item.get("category") not in categories:
                continue
            out.append(item)
        return out

    @staticmethod
    def _event_id(item: dict) -> str:
        """Stable while an item sits at one status; changes when it escalates."""
        return f"{item.get('id')}:{item.get('status')}"

    def _explain(self, fresh: list[dict], all_matching: list[dict]) -> str:
        if not fresh:
            return f"Nothing new. {len(all_matching)} item(s) still pending, already reported."

        lines = [f"{len(fresh)} expiring item(s) need attention:", ""]
        # Soonest first — that is the order you want to act in.
        for item in sorted(fresh, key=lambda i: i.get("daysUntilExpiry", 999)):
            days = item.get("daysUntilExpiry")
            when = (
                "expired" if days is not None and days < 0
                else "expires today" if days == 0
                else f"{days} days left"
            )
            amount = item.get("amount")
            money = f"${amount:,.2f} · " if isinstance(amount, (int, float)) and amount else ""
            card = item.get("cardName")
            where = f" ({card})" if card else ""
            flag = "CRITICAL: " if item.get("status") == "critical" else ""
            lines.append(f"• {flag}{item.get('title', 'Untitled')}{where} — {money}{when}")

        remaining = len(all_matching) - len(fresh)
        if remaining > 0:
            lines.append("")
            lines.append(f"({remaining} other item(s) already reported.)")
        return "\n".join(lines)

    def should_notify(self, monitor: Monitor, result: CheckResult) -> bool:
        """Notify whenever something has newly become alertable."""
        return bool(result.details.get("new", 0))

    def get_state_for_storage(self, result: CheckResult, monitor: Monitor) -> dict[str, Any]:
        existing = monitor.last_state.get("seen_ids", [])
        new_ids = [self._event_id(i) for i in result.details.get("_fresh", [])]
        return {
            "condition_met": result.condition_met,
            "seen_ids": (new_ids + existing)[:MAX_SEEN_IDS],
            "last_count": result.details.get("total_matching", 0),
        }
