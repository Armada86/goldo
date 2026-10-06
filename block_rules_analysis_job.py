"""One-shot block rules analysis, run by .github/workflows/block_rules_analysis.yml when you send "start block
rules analysis" or "start BRA" on Telegram (the Worker dispatches the workflow, same as "start SLA").

Replays every Broker B touch we know of (trades that opened, plus touches blocked by the DXY/ADX/RSI/ATR filters)
against candidate filter values with block_rules_analysis.py, sends the report to Telegram, and writes the
resulting rules as a new row in the `block_rules` table, which Broker B reads on its next poll. A row is written
on every run, changed or not, so the table doubles as a log of runs; the newest row is the active one.

    python block_rules_analysis_job.py             # analyse, write the table, send to Telegram
    python block_rules_analysis_job.py --dry-run   # analyse and print only (no table write, no Telegram)"""

import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

import block_rules_analysis as bra
import broker
import stop_loss_analysis_job as sla_job
from block_rules import get_block_rules
from broker_b import ZONE_SCENARIOS
from data_fetcher import GOLD_SPOT_SYMBOL, compute_adx, compute_atr, compute_rsi, fetch_candles
from notifier import send_telegram_message
from storage import (
    get_broker_b_blocked_touches,
    get_closed_trades_for_analysis,
    get_readings_since,
    insert_block_rules_row,
)

LOOKBACK_DAYS = sla_job.LOOKBACK_DAYS
CANDLE_OUTPUTSIZE = 5000  # 15-min candles, ~52 days: one Twelve Data call
DXY_WINDOW_MINUTES = 15
DUPLICATE_TOUCH_MINUTES = 30  # blocked notices for the same level this close together are one touch
RULE_TO_SCENARIO = {rule: (scenario, trade_type) for scenario, (trade_type, rule) in ZONE_SCENARIOS.items()}


def indicator_frame() -> pd.DataFrame:
    """15-min gold candles with RSI/ADX/ATR(14) columns, indexed by candle start time."""
    candles = fetch_candles(GOLD_SPOT_SYMBOL, interval="15min", outputsize=CANDLE_OUTPUTSIZE).reset_index(drop=True)
    candles["rsi"] = compute_rsi(candles["close"])
    candles["adx"] = compute_adx(candles)
    candles["atr"] = compute_atr(candles)
    candles["rising"] = candles["adx"].diff() > 0
    return candles.set_index("datetime")


def indicators_at(frame: pd.DataFrame, ts) -> tuple[float | None, float | None, float | None]:
    """(rsi, adx, atr) of the latest candle at or before ts; None where not computable."""
    row = frame[frame.index <= ts]
    if row.empty:
        return None, None, None
    last = row.iloc[-1]
    return tuple(None if pd.isna(last[c]) else float(last[c]) for c in ("rsi", "adx", "atr"))


def adx_rising_at(frame: pd.DataFrame, ts) -> bool | None:
    """True/False: ADX of the latest candle at or before ts is above/not above the previous candle's; None if unknown."""
    row = frame[frame.index <= ts]
    if len(row) < 2 or pd.isna(row.iloc[-1]["adx"]) or pd.isna(row.iloc[-2]["adx"]):
        return None
    return bool(row.iloc[-1]["rising"])


def dxy_change_at(readings: list, ts) -> float | None:
    """DXY's net move over the 15 minutes up to ts (last reading minus first, same as broker_b's gate)."""
    window = [p for t, p in readings if ts - timedelta(minutes=DXY_WINDOW_MINUTES) < t <= ts]
    return window[-1] - window[0] if len(window) >= 2 else None


def dedupe_blocked(touches: list[dict]) -> list[dict]:
    """One touch per (level, close-together run of notices): the same touch is stored once per distinct reason."""
    kept, last_seen = [], {}
    for t in touches:
        key = (t["rule_name"], round(t["trigger_price"], 2))
        prev = last_seen.get(key)
        if prev is not None and (t["touch_ts"] - prev).total_seconds() < DUPLICATE_TOUCH_MINUTES * 60:
            continue
        last_seen[key] = t["touch_ts"]
        kept.append(t)
    return kept


def build_events(trades: list[dict], blocked: list[dict], first_ts, now) -> tuple[list, int]:
    """(events, n_skipped): paths and entry conditions for every trade and blocked touch."""
    raw = [
        {"broker": "B", "id": t["id"], "trade_type": t["trade_type"], "entry_price": t["entry_price"],
         "open_ts": t["open_ts"], "rule_name": t["rule_name"], "was_trade": True,
         "label": f"B #{t['id']} {t['rule_name']}"}
        for t in trades
    ]
    for i, b in enumerate(blocked):
        if b["rule_name"] not in RULE_TO_SCENARIO:
            continue
        raw.append({"broker": "B", "id": f"blocked-{i}", "trade_type": RULE_TO_SCENARIO[b["rule_name"]][1],
                    "entry_price": b["trigger_price"], "open_ts": b["touch_ts"], "rule_name": b["rule_name"],
                    "was_trade": False, "label": f"blocked {b['rule_name']} {b['touch_ts']:%d %b %H:%M}Z"})
    raw.sort(key=lambda r: r["open_ts"])
    if not raw:
        return [], 0

    forex = sla_job.fetch_forex_bars()
    td = sla_job.fetch_twelve_data_bars(raw[0]["open_ts"] - timedelta(minutes=5), now)
    frame = indicator_frame()
    dxy = get_readings_since("dxy", raw[0]["open_ts"] - timedelta(minutes=DXY_WINDOW_MINUTES + 5))

    events, n_skipped = [], 0
    for r in raw:
        paths, _, _, skipped = sla_job.build_paths([r], forex, td)
        if skipped or not paths:
            n_skipped += 1
            continue
        path = paths[0]
        path.label = r["label"]
        rsi, adx, atr = indicators_at(frame, r["open_ts"])
        scenario = RULE_TO_SCENARIO[r["rule_name"]][0]
        events.append(bra.Event(r["label"], scenario, r["trade_type"], r["was_trade"], rsi, adx, atr,
                                dxy_change_at(dxy, r["open_ts"]), path, adx_rising_at(frame, r["open_ts"])))
    return events, n_skipped


def run(dry_run: bool = False) -> None:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=LOOKBACK_DAYS)
    trades = [t for t in get_closed_trades_for_analysis(since) if t["broker"] == "B"]
    blocked = dedupe_blocked(get_broker_b_blocked_touches(since))
    events, n_skipped = build_events(trades, blocked, since, now)

    current = get_block_rules()
    activation, distance = broker.trailing_stop_params()
    result = bra.analyse(events, current, broker.stop_loss_threshold(), activation, distance)
    first_ts = min([t["open_ts"] for t in trades] + [b["touch_ts"] for b in blocked], default=now)
    report = bra.format_report(result, first_ts, now, n_skipped)
    print(report)
    if dry_run:
        return
    note = ("BRA: rules changed" if result["changed"] else "BRA: reviewed, no change") if result["enough"] else "BRA: not enough data"
    row_id = insert_block_rules_row(result["new_rules"], "BRA", result["changed"], result["n_events"], note)
    print(f"[bra] block_rules row {row_id} written ({note})")
    send_telegram_message(report)


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
