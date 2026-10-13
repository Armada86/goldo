"""Helpers for the Broker subagent's end-of-day review (weekdays, after the 5:00 PM ET close).

The analysis itself is written by the read-only `broker` subagent, run as a Claude Code Routine -- see
`.claude/agents/broker.md` ("End-of-day review workflow") and CLAUDE.md. This module is the only
project code that touches the `broker_daily_reviews` table, so the Routine never hand-writes SQL:

  python broker_review.py trades [YYYY-MM-DD]     # print that ET day's Broker A + B trades (default: today)
  python broker_review.py save YYYY-MM-DD FILE    # FILE = JSON {"summary": str, "trades": [{"broker","id","headline","good","bad","improve"}]}
                                                  # -> saves the review to Neon and sends it to Telegram, once
  python broker_review.py trades-f [YYYY-MM-DD]   # same, for Broker F (forex_b_trades, live mode only)
  python broker_review.py save-f YYYY-MM-DD FILE  # Broker F's own review: broker_f_daily_reviews table, 🟪 prefix

Talks to Neon over its HTTPS SQL endpoint (https://<host>/sql, same pooler host as DATABASE_URL), NOT psycopg2:
a Claude Code Routine's cloud sandbox only has an HTTPS proxy, so a raw Postgres TCP connection (port 5432)
hangs there even with full network access. Needs only `requests` and DATABASE_URL.

Analysis only: nothing here opens, closes or modifies a trade, or any code/config.
"""

import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from notifier import send_telegram_message

DISPLAY_TZ = ZoneInfo("America/New_York")
REVIEW_PREFIX = "🔵🟦 BROKER DAILY REVIEW"
REVIEW_PREFIX_F = "🟪 BROKER F DAILY REVIEW"
TELEGRAM_MAX_CHARS = 4000
SQL_TIMEOUT_SECONDS = 30

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS broker_daily_reviews (
    id SERIAL PRIMARY KEY,
    review_date DATE NOT NULL UNIQUE,
    created_ts TIMESTAMPTZ NOT NULL,
    trade_count INTEGER NOT NULL,
    total_pnl DOUBLE PRECISION,
    analysis TEXT NOT NULL,
    trade_reviews JSONB
)
"""

CREATE_TABLE_F = CREATE_TABLE.replace("broker_daily_reviews", "broker_f_daily_reviews")


def _sql(query: str, params: list | None = None) -> list[dict]:
    """One statement over Neon's HTTP endpoint. Values come back as text (None for NULL)."""
    url = os.environ["DATABASE_URL"]
    r = requests.post(
        f"https://{urlparse(url).hostname}/sql",
        headers={"Neon-Connection-String": url, "Content-Type": "application/json"},
        json={"query": query, "params": params or []},
        timeout=SQL_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    return r.json()["rows"]


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=DISPLAY_TZ)
    return start, datetime.combine(d + timedelta(days=1), time.min, tzinfo=DISPLAY_TZ)


def _num(v):
    return None if v is None else float(v)


def _json(v):
    return json.loads(v) if isinstance(v, str) else v


def get_day_trades(d: date, brokers: str = "AB") -> list[dict]:
    """Every trade of the given brokers that opened or closed on ET day `d`, oldest first, with entry_context.

    "AB" = Broker A + B (the original review); "F" = Broker F's live trades (forex_b_trades), which also
    carry exit_reason, exit_context and the platform stop level."""
    start, end = (x.isoformat() for x in day_bounds(d))
    trades = []
    sources = {
        "A": ("trades", "NULL", "NULL", "NULL", ""),
        "B": ("broker_b_trades", "ta_forecast_id", "NULL", "NULL", ""),
        "F": ("forex_b_trades", "ta_forecast_id", "exit_reason", "stop_level", " AND mode = 'live'"),
    }
    for broker in brokers:
        table, extra, exit_reason, stop_level, mode_filter = sources[broker]
        rows = _sql(
            f"SELECT id, rule_name, trade_type, entry_price, open_ts, triggering_alerts, exit_price, "
            f"close_ts, pnl, status, entry_context, {extra} AS ta_forecast_id, "
            f"{exit_reason} AS exit_reason, {stop_level} AS stop_level FROM {table} "
            f"WHERE ((open_ts >= $1::timestamptz AND open_ts < $2::timestamptz) "
            f"OR (close_ts >= $1::timestamptz AND close_ts < $2::timestamptz)){mode_filter}",
            [start, end],
        )
        for r in rows:
            trades.append({
                "broker": broker, "id": int(r["id"]), "rule_name": r["rule_name"],
                "trade_type": r["trade_type"], "entry_price": _num(r["entry_price"]),
                "open_ts": r["open_ts"], "triggering_alerts": r["triggering_alerts"],
                "exit_price": _num(r["exit_price"]), "close_ts": r["close_ts"], "pnl": _num(r["pnl"]),
                "status": r["status"], "entry_context": _json(r["entry_context"]),
                "ta_forecast_id": r["ta_forecast_id"],
                **({"exit_reason": r["exit_reason"], "stop_level": _num(r["stop_level"])} if broker == "F" else {}),
            })
    return sorted(trades, key=lambda t: t["open_ts"])


def day_pnl(d: date, trades: list[dict]) -> float | None:
    """Net P/L of trades that CLOSED on ET day `d` (a trade opened the day before counts when it closes)."""
    start, end = day_bounds(d)
    closed = [
        t["pnl"] for t in trades
        if t["pnl"] is not None and t["close_ts"]
        and start <= datetime.fromisoformat(t["close_ts"]) < end
    ]
    return sum(closed) if closed else None


def _fmt_money(v: float | None) -> str:
    return "—" if v is None else f"{'+' if v >= 0 else '-'}${abs(v):.2f}"


def format_review(d: date, summary: str, trades: list[dict], total_pnl: float | None, prefix: str = REVIEW_PREFIX) -> str:
    lines = [f"{prefix} — {d:%a %d %b %Y}", f"Trades: {len(trades)} | Net P/L: {_fmt_money(total_pnl)}", "", summary.strip()]
    for t in trades:
        lines += ["", f"BROKER {t['broker']} #{t['id']} — {t.get('headline', '')}".rstrip(" —")]
        for key, label in (("good", "✅ Good"), ("bad", "⚠️ Bad"), ("improve", "🔧 Improve")):
            if t.get(key):
                lines.append(f"{label}: {t[key]}")
    return "\n".join(lines)


def _split(text: str) -> list[str]:
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > TELEGRAM_MAX_CHARS:
            chunks.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    return chunks + [cur] if cur else chunks


def save_review(d: date, payload: dict, brokers: str = "AB") -> bool:
    """Saves to Neon first (so a Telegram failure never loses it), then sends. No-op if already saved.

    brokers "F" writes Broker F's own table (broker_f_daily_reviews) with its own prefix."""
    table, create, prefix = (
        ("broker_f_daily_reviews", CREATE_TABLE_F, REVIEW_PREFIX_F) if brokers == "F"
        else ("broker_daily_reviews", CREATE_TABLE, REVIEW_PREFIX)
    )
    _sql(create)
    day = get_day_trades(d, brokers)
    total = day_pnl(d, day)
    trades = payload.get("trades", [])
    inserted = _sql(
        f"INSERT INTO {table} (review_date, created_ts, trade_count, total_pnl, analysis, trade_reviews) "
        "VALUES ($1::date, $2::timestamptz, $3::int, $4::float8, $5, $6::jsonb) "
        "ON CONFLICT (review_date) DO NOTHING RETURNING id",
        [d.isoformat(), datetime.now(timezone.utc).isoformat(), len(day), total, payload["summary"], json.dumps(trades)],
    )
    if not inserted:
        print(f"Review for {d} already exists; nothing to do.")
        return False
    for chunk in _split(format_review(d, payload["summary"], trades, total, prefix)):
        send_telegram_message(chunk)
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "trades":
        d = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(DISPLAY_TZ).date()
        print(json.dumps(get_day_trades(d), indent=2, default=str))
    elif cmd == "trades-f":
        d = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(DISPLAY_TZ).date()
        print(json.dumps(get_day_trades(d, "F"), indent=2, default=str))
    elif cmd in ("save", "save-f") and len(sys.argv) == 4:
        with open(sys.argv[3], encoding="utf-8") as f:
            save_review(date.fromisoformat(sys.argv[2]), json.load(f), "F" if cmd == "save-f" else "AB")
    else:
        sys.exit(__doc__)
