"""Anthropic rate-limit detection + dedup'd email alerts.

Mirror of ``taskme/rate_limit_alerts.py`` — kept duplicated for the
same reason ``api_usage.py`` is duplicated: app independence. See the
TaskMe copy for the architectural reasoning; this docstring only
restates what differs.

Called from ``notifyme/api_usage.py:tracked_create`` when a
``RateLimitError`` is caught. Records to ``~/.notifyme/api_usage.db``
and sends via this app's own ``EmailNotifier`` (no cross-import).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from notifyme.api_usage import DB_PATH
from notifyme.notifier import EmailNotifier

logger = logging.getLogger("notifyme.rate_limit_alerts")

ALERT_DEDUP_MINUTES = 15

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_limit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    app TEXT NOT NULL,
    model TEXT,
    feature TEXT,
    error_message TEXT,
    alert_sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rl_ts ON rate_limit_events(ts);
"""


def record_and_maybe_alert(
    *,
    app: str,
    model: str,
    feature: str,
    error_message: str,
) -> None:
    """Record a rate-limit event; email alert if dedup window allows."""
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.executescript(_SCHEMA)
            now = datetime.now()
            should_alert = _within_dedup_window(conn, now) is False

            alert_sent = 0
            if should_alert:
                if _send_alert(
                    app=app, model=model, feature=feature,
                    error_message=error_message, conn=conn,
                ):
                    alert_sent = 1

            conn.execute(
                "INSERT INTO rate_limit_events "
                "(ts, app, model, feature, error_message, alert_sent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    now.isoformat(timespec="seconds"),
                    app, model, feature,
                    (error_message or "")[:2000],
                    alert_sent,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("rate-limit recording failed; continuing")


def _within_dedup_window(conn: sqlite3.Connection, now: datetime) -> bool:
    row = conn.execute(
        "SELECT MAX(ts) FROM rate_limit_events WHERE alert_sent = 1"
    ).fetchone()
    if not row or not row[0]:
        return False
    try:
        last_sent = datetime.fromisoformat(row[0])
    except ValueError:
        return False
    return (now - last_sent) < timedelta(minutes=ALERT_DEDUP_MINUTES)


def _send_alert(
    *,
    app: str,
    model: str,
    feature: str,
    error_message: str,
    conn: sqlite3.Connection,
) -> bool:
    recent_hits_row = conn.execute(
        "SELECT COUNT(*) FROM rate_limit_events "
        "WHERE ts > datetime('now', 'localtime', '-1 hour')"
    ).fetchone()
    recent_hits = (recent_hits_row[0] if recent_hits_row else 0) + 1

    today_cost_row = conn.execute(
        "SELECT COALESCE(SUM(est_cost_usd), 0) FROM api_calls "
        "WHERE date(ts) = date('now', 'localtime')"
    ).fetchone()
    today_cost = today_cost_row[0] if today_cost_row else 0.0

    subject = f"[{app}] Anthropic rate-limit hit: {model}"
    body = (
        f"App:       {app}\n"
        f"Model:     {model}\n"
        f"Feature:   {feature}\n"
        f"Time:      {datetime.now().isoformat(timespec='seconds')}\n\n"
        f"Error:\n  {error_message}\n\n"
        f"Context:\n"
        f"  Rate-limit hits in last hour: {recent_hits}\n"
        f"  Spend today so far:           ${today_cost:.2f}\n\n"
        f"Alerts dedupe at {ALERT_DEDUP_MINUTES}-minute windows — further "
        f"hits in this window will be recorded silently in "
        f"~/.{app}/api_usage.db (rate_limit_events table)."
    )

    notifier = EmailNotifier()
    return notifier.send_admin_alert(subject=subject, body=body)
