"""Helpers for the Broker subagent's end-of-day review (weekdays, after the 5:00 PM ET close).

The analysis itself is written by the read-only `broker` subagent, run as a Claude Code Routine -- see
`.claude/agents/broker.md` ("End-of-day review workflow") and CLAUDE.md. This module is the only
project code that touches the `broker_daily_reviews` table, so the Routine never hand-writes SQL:

  python broker_review.py trades [YYYY-MM-DD]     # print that ET day's Broker A + B trades (default: today)
  python broker_review.py save YYYY-MM-DD FILE    # FILE = JSON {"summary": str, "trades": [{"broker","id","good","bad","improve"}]}
                                                  # -> saves the review to Neon and sends it to Telegram, once

Analysis only: nothing here opens, closes or modifies a trade, or any code/config.
"""

import json
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from notifier import send_telegram_message
from storage import broker_daily_review_exists, get_connection, init_db, insert_broker_daily_review

DISPLAY_TZ = ZoneInfo("America/New_York")
REVIEW_PREFIX = "🔵🟦 BROKER DAILY REVIEW"
TELEGRAM_MAX_CHARS = 4000


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=DISPLAY_TZ)
    return start, datetime.combine(d + timedelta(days=1), time.min, tzinfo=DISPLAY_TZ)


def get_day_trades(d: date) -> list[dict]:
    """Every Broker A/B trade that opened or closed on ET day `d`, oldest first, with entry_context."""
    start, end = day_bounds(d)
    rows = []
    with get_connection() as conn, conn.cursor() as cur:
        for broker, table, extra in (("A", "trades", "NULL"), ("B", "broker_b_trades", "ta_forecast_id")):
            cur.execute(
                f"SELECT id, rule_name, trade_type, entry_price, open_ts, triggering_alerts, exit_price, "
                f"close_ts, pnl, status, entry_context, {extra} FROM {table} "
                f"WHERE (open_ts >= %s AND open_ts < %s) OR (close_ts >= %s AND close_ts < %s)",
                (start, end, start, end),
            )
            for r in cur.fetchall():
                rows.append({
                    "broker": broker, "id": r[0], "rule_name": r[1], "trade_type": r[2],
                    "entry_price": r[3], "open_ts": r[4], "triggering_alerts": r[5],
                    "exit_price": r[6], "close_ts": r[7], "pnl": r[8], "status": r[9],
                    "entry_context": r[10], "ta_forecast_id": r[11],
                })
    return sorted(rows, key=lambda t: t["open_ts"])


def _fmt_money(v: float | None) -> str:
    return "—" if v is None else f"{'+' if v >= 0 else '-'}${abs(v):.2f}"


def format_review(d: date, summary: str, trades: list[dict], total_pnl: float | None) -> str:
    lines = [f"{REVIEW_PREFIX} — {d:%a %d %b %Y}", f"Trades: {len(trades)} | Net P/L: {_fmt_money(total_pnl)}", "", summary.strip()]
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


def save_review(d: date, payload: dict) -> bool:
    """Saves to Neon first (so a Telegram failure never loses it), then sends. No-op if already saved."""
    init_db()
    if broker_daily_review_exists(d):
        print(f"Review for {d} already exists; nothing to do.")
        return False
    trades = payload.get("trades", [])
    day = get_day_trades(d)
    total = sum(t["pnl"] for t in day if t["pnl"] is not None and t["close_ts"] is not None
                and day_bounds(d)[0] <= t["close_ts"] < day_bounds(d)[1]) if day else None
    if not insert_broker_daily_review(d, len(day), total, payload["summary"], trades):
        return False
    for chunk in _split(format_review(d, payload["summary"], trades, total)):
        send_telegram_message(chunk)
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "trades":
        d = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(DISPLAY_TZ).date()
        print(json.dumps(get_day_trades(d), indent=2, default=str))
    elif cmd == "save" and len(sys.argv) == 4:
        with open(sys.argv[3], encoding="utf-8") as f:
            save_review(date.fromisoformat(sys.argv[2]), json.load(f))
    else:
        sys.exit(__doc__)
