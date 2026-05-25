"""Per-call Anthropic API usage logging — NotifyMe edition.

Wraps every ``client.messages.create(...)`` via ``tracked_create``. Records
a row to ``~/.notifyme/api_usage.db`` with input/output/cache tokens, web-
search count, and a dollar estimate computed from the static price table.

Recording is best-effort: any failure here is caught and logged but never
re-raised — usage tracking must not break the API call path.

ServiceDeck reads this DB directly to render the per-app spend panel.
Mirror of ``taskme/api_usage.py``; kept duplicated so the two apps stay
independently shippable.

CLI: ``python -m notifyme.api_usage report``
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import RateLimitError

logger = logging.getLogger("notifyme.api_usage")

DB_PATH = Path.home() / ".notifyme" / "api_usage.db"

# Per-Mtok prices (input, output) in USD. Keep in sync with
# taskme/api_usage.py:MODEL_PRICES — both apps may use any of these.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-7":                 (15.0, 75.0),
    "claude-sonnet-4-6":               (3.0,  15.0),
    "claude-haiku-4-5-20251001":       (1.0,  5.0),
    # Historical-only rows below — TaskMe's weekly_maintenance check
    # skips lines tagged with the marker below when scanning for
    # deprecated model literals.
    "claude-sonnet-4-20250514":        (3.0,  15.0),   # maintenance:ignore-model
    "claude-3-haiku-20240307":         (0.25, 1.25),   # maintenance:ignore-model
}

WEB_SEARCH_COST_PER_THOUSAND = 10.0
CACHE_READ_RATE = 0.10
CACHE_CREATE_RATE = 1.25

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    feature TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    web_search_count INTEGER DEFAULT 0,
    est_cost_usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_calls_ts ON api_calls(ts);
CREATE INDEX IF NOT EXISTS idx_api_calls_feature ON api_calls(feature);
"""


def tracked_create(client, *, feature: str, **kwargs):
    """Drop-in for ``client.messages.create(...)`` that records usage.

    `RateLimitError` is detected here: record + (dedup'd) email alert,
    then re-raise so existing per-caller handling runs unchanged.
    """
    model = kwargs.get("model", "unknown")
    try:
        response = client.messages.create(**kwargs)
    except RateLimitError as e:
        from notifyme.rate_limit_alerts import record_and_maybe_alert
        record_and_maybe_alert(
            app="notifyme", model=model, feature=feature,
            error_message=str(e),
        )
        raise
    try:
        _record(feature=feature, model=model, response=response)
    except Exception:
        logger.exception("Failed to record API usage; continuing")
    return response


def _record(*, feature: str, model: str, response: Any) -> None:
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    web_search_count = _count_web_search(getattr(response, "content", []) or [])
    est_cost = _estimate_cost(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation=cache_creation,
        cache_read=cache_read,
        web_search_count=web_search_count,
    )
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(_SCHEMA)
        conn.execute(
            """
            INSERT INTO api_calls (
                ts, feature, model, input_tokens, output_tokens,
                cache_creation_tokens, cache_read_tokens, web_search_count,
                est_cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                feature, model, input_tokens, output_tokens,
                cache_creation, cache_read, web_search_count, est_cost,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _count_web_search(content_blocks: list[Any]) -> int:
    n = 0
    for block in content_blocks:
        if getattr(block, "type", None) == "server_tool_use":
            if getattr(block, "name", None) == "web_search":
                n += 1
            else:
                logger.warning(
                    f"Unknown server_tool_use: name={getattr(block, 'name', None)!r}"
                )
    return n


def _estimate_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation: int,
    cache_read: int,
    web_search_count: int,
) -> float:
    prices = MODEL_PRICES.get(model)
    if prices is None:
        logger.warning(f"No price entry for model {model!r}; cost estimate will be 0.")
        return 0.0
    in_rate, out_rate = prices
    cost = (
        input_tokens * (in_rate / 1_000_000)
        + output_tokens * (out_rate / 1_000_000)
        + cache_creation * (in_rate * CACHE_CREATE_RATE / 1_000_000)
        + cache_read * (in_rate * CACHE_READ_RATE / 1_000_000)
        + web_search_count * (WEB_SEARCH_COST_PER_THOUSAND / 1_000)
    )
    return round(cost, 6)


# ---- CLI -------------------------------------------------------------------


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _format_cost(cost: float) -> str:
    if cost == 0:
        return "$0.00"
    if cost < 0.01:
        return "<$0.01"
    return f"${cost:.2f}"


def _window_summary(conn: sqlite3.Connection, where_clause: str) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0),
            COALESCE(SUM(web_search_count), 0), COALESCE(SUM(est_cost_usd), 0.0)
        FROM api_calls
        WHERE {where_clause}
        """
    ).fetchone()
    return {
        "calls": row[0], "input_tokens": row[1], "output_tokens": row[2],
        "web_search_count": row[3], "cost": row[4],
    }


def _print_window(label: str, s: dict[str, Any]) -> None:
    if s["calls"] == 0:
        print(f"{label}\n  (no calls)\n")
        return
    print(label)
    print(
        f"  {s['calls']} calls · "
        f"{_format_tokens(s['input_tokens'])} in · "
        f"{_format_tokens(s['output_tokens'])} out · "
        f"{s['web_search_count']} web_search · "
        f"{_format_cost(s['cost'])}"
    )
    print()


def _print_top_features(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT feature, COUNT(*), COALESCE(SUM(est_cost_usd), 0.0)
        FROM api_calls
        WHERE ts > datetime('now', 'localtime', '-30 days')
        GROUP BY feature
        ORDER BY 3 DESC
        LIMIT 10
        """
    ).fetchall()
    if not rows:
        return
    print("Top features (30d):")
    name_width = max((len(r[0]) for r in rows), default=20)
    for feature, calls, cost in rows:
        print(f"  {feature.ljust(name_width)}  {calls:>4} calls   {_format_cost(cost)}")
    print()


def _cli_report() -> int:
    if not DB_PATH.exists():
        print(f"No usage DB yet at {DB_PATH}", file=sys.stderr)
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        _print_window("Today (so far)", _window_summary(conn, "date(ts) = date('now', 'localtime')"))
        _print_window("Last 7 days", _window_summary(conn, "ts > datetime('now', 'localtime', '-7 days')"))
        _print_window("Last 30 days", _window_summary(conn, "ts > datetime('now', 'localtime', '-30 days')"))
        _print_top_features(conn)
    finally:
        conn.close()
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "report":
        print("usage: python -m notifyme.api_usage report", file=sys.stderr)
        return 2
    return _cli_report()


if __name__ == "__main__":
    sys.exit(main())
