"""Replay of a proposed "cool-off after a winning fade" entry filter over every closed Broker B trade. NOT live -- research only.

The idea (from the Broker F #20 / Broker B #89 review, 19 Oct 2026): a fade in the OPPOSITE direction shortly after a winning trade
on the same ET day often meets a market that has just left the first level (B #74/#79/#85/#89 each lost the full $15 this way). The
filter would block a fade (`TA-Zone-*`) when the most recent trade the filter itself let through closed as a win of at least
`MIN_WIN` dollars within the last `window` hours, on the same ET day, in the opposite direction.

Sequential: a blocked trade is dropped and never becomes the reference for the next one, so the replay does not credit a win the
filter would have prevented. Breakouts are never blocked but do count as the reference trade. Prints each blocked trade and a
summary per setting. Read-only: talks to Neon over its HTTPS SQL endpoint (see broker_review.py), writes nothing.

    python cool_off_replay.py

Limits: removing a trade does not model what would have opened instead (one position at a time, so a later touch might have filled),
it only covers closed Broker B trades (Broker F mirrors them; blocked touches are not in `broker_b_trades`), and the sample is small
(about 55 fades since 30 Sep 2026): a hint, not a calibration."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from broker_review import _sql

DISPLAY_TZ = ZoneInfo("America/New_York")
WINDOW_HOURS = (1, 2, 3)
MIN_WINS = (0.01, 3.0)  # any win / broker_b.REARM_MIN_WIN_PNL
SINCE = "2026-09-30"  # trades before this have no entry_context; kept for the all-history view too


def _dt(value) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("+00", "+00:00"))


def load_trades() -> list[dict]:
    rows = []
    for r in _sql(
        "SELECT id, rule_name, trade_type, open_ts, close_ts, pnl FROM broker_b_trades "
        "WHERE status = 'Closed' AND close_ts IS NOT NULL ORDER BY open_ts"
    ):
        rows.append({
            "id": r["id"], "rule": r["rule_name"], "side": r["trade_type"], "open": _dt(r["open_ts"]),
            "close": _dt(r["close_ts"]), "pnl": float(r["pnl"]),
            "fade": r["rule_name"].startswith("TA-Zone"),
        })
    return rows


def blocked_by_cool_off(trades: list[dict], window: timedelta, min_win: float) -> list[dict]:
    """Trades the filter would have blocked, replayed in order against the trades it let through."""
    blocked, last_kept = [], None
    for t in trades:
        if (t["fade"] and last_kept is not None and last_kept["side"] != t["side"] and last_kept["pnl"] >= min_win
                and timedelta(0) <= t["open"] - last_kept["close"] <= window
                and t["open"].astimezone(DISPLAY_TZ).date() == last_kept["close"].astimezone(DISPLAY_TZ).date()):
            blocked.append(t)
            continue
        last_kept = t
    return blocked


def summarise(label: str, trades: list[dict], blocked: list[dict]) -> None:
    b_pnl = sum(t["pnl"] for t in blocked)
    wins = sum(1 for t in blocked if t["pnl"] > 0)
    total = sum(t["pnl"] for t in trades)
    print(f"{label:>22}{len(blocked):>9}{b_pnl:>13.2f}{wins:>14}{total - b_pnl:>10.2f}{-b_pnl:>+9.2f}")


def main() -> None:
    all_trades = load_trades()
    for title, trades in (("all history", all_trades), (f"since {SINCE}", [t for t in all_trades if str(t["open"]) >= SINCE])):
        print(f"\n=== {title}: {len(trades)} closed Broker B trades, {sum(t['fade'] for t in trades)} fades, "
              f"P/L {sum(t['pnl'] for t in trades):+.2f} ===")
        print(f"{'setting':>22}{'blocked':>9}{'blocked P/L':>13}{'blocked wins':>14}{'kept P/L':>10}{'change':>9}")
        for hours in WINDOW_HOURS:
            for min_win in MIN_WINS:
                summarise(f"{hours}h, win>={min_win:g}", trades,
                          blocked_by_cool_off(trades, timedelta(hours=hours), min_win))
    chosen = blocked_by_cool_off(all_trades, timedelta(hours=2), 3.0)
    print("\nBlocked at 2h, win>=3:")
    for t in chosen:
        print(f"  B#{t['id']:<4}{t['rule']:<14}{str(t['open'].astimezone(DISPLAY_TZ))[:16]} ET  P/L {t['pnl']:+.2f}")


if __name__ == "__main__":
    main()
