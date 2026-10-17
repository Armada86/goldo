"""LOG-ONLY shadow of an automatic mid-day TA re-run (17 Oct 2026).

Idea under test (docs/studies.md, study 25-28): the three scheduled TA runs pick the fade levels from a snapshot, so after a hard move
there may be no level near price (Wed 14 Oct: resistance $18 above, support $28 below, no fade trades for the rest of the day). A re-run
started automatically when price is far from both fade levels, with extra intraday levels (1h swings, session high/low) in the pool,
beat the published levels in the 17 Oct simulation by about $4-$39 over 10 trading days -- too small and noisy a sample to act on.

So nothing is re-run here. Each poll (inside the 7am-5pm ET weekday window) it checks whether gold is more than
TA_RERUN_SHADOW_ATR_MULTIPLE x ATR(14, 15-min) away from BOTH fade levels in force, and if so records one row in `ta_rerun_shadow`:
the price, ATR, the levels in force, and the fade levels a re-run WOULD have produced (the latest forecast's zones plus 1h swings and
session high/low, through the real ta_forecast_job zone code). No TA row, no Telegram, no effect on any broker.

It behaves as if the re-run had happened: once an event is logged, the levels it would have produced are the reference for the next
check (until a real TA run lands), so a trend that keeps going logs a new event only after moving away from those again. A cooldown
(TA_RERUN_SHADOW_COOLDOWN_MINUTES after the last TA run or logged event) and a per-day cap (TA_RERUN_SHADOW_MAX_PER_DAY) bound it.
`differs` says whether the would-be levels actually moved by at least TA_RERUN_SHADOW_MIN_LEVEL_CHANGE (a re-run that picks the same
levels would only have re-armed them and sent a Telegram message).

Cost: no extra Twelve Data calls on a normal poll (the 15-min candles are the poll's own); one 1-hour candle call per logged event
(at most TA_RERUN_SHADOW_MAX_PER_DAY a day) plus two small DB reads per in-window poll.

    python ta_rerun_shadow.py report [days]     # list the logged events (default: last 7 days)
"""

import logging
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import ta_forecast_job as ta
from broker import _within_entry_window
from broker_b import _entry_price_and_invalidation
from config import (
    ATR_PERIOD,
    ADX_PERIOD,
    GOLD_SPOT_SYMBOL,
    RSI_PERIOD,
    TA_RERUN_SHADOW_ATR_MULTIPLE,
    TA_RERUN_SHADOW_COOLDOWN_MINUTES,
    TA_RERUN_SHADOW_MAX_PER_DAY,
    TA_RERUN_SHADOW_MIN_LEVEL_CHANGE,
)
from data_fetcher import fetch_candles, gold_rsi_adx_atr_from_candles
from storage import get_latest_ta_forecast_levels, get_ta_rerun_shadow_since, insert_ta_rerun_shadow

log = logging.getLogger("gold-monitor")

DISPLAY_TZ = ZoneInfo("America/New_York")
H1_BARS = 100  # one 1-hour candle call; 1h swings look at the last SWING_LOOKBACK_H1 completed bars
SWING_LOOKBACK_H1 = 72
SWING_WING_H1 = 3
SESSION_START_ET = time(18, 0)  # the futures session opens 6pm ET the evening before


def fade_levels(levels: dict) -> tuple[float, float] | None:
    """(sell_resistance trigger, buy_support trigger) of a forecast's `levels`, the prices Broker B/F actually trade; None if either is missing."""
    scenarios = {s["name"]: s for s in levels.get("scenarios", [])}
    if "sell_resistance" not in scenarios or "buy_support" not in scenarios:
        return None
    return (_entry_price_and_invalidation("sell_resistance", scenarios["sell_resistance"])[0],
            _entry_price_and_invalidation("buy_support", scenarios["buy_support"])[0])


def _stored_candidates(levels: dict) -> list[tuple[float, str]]:
    """The zones a forecast listed (picked and 'nearby'), turned back into (price, label) candidates for ta._merge_zones()."""
    out: list[tuple[float, str]] = []

    def add(zone: dict) -> None:
        for label in zone["labels"]:
            out.append((zone["low"], label))
            if zone["high"] != zone["low"]:
                out.append((zone["high"], label))

    for zone in levels.get("resistances", []) + levels.get("supports", []):
        add(zone)
        for near in zone.get("nearby", []):
            add(near)
    return out


def _session_start(now: datetime) -> datetime:
    local = now.astimezone(DISPLAY_TZ)
    start = datetime.combine(local.date(), SESSION_START_ET, tzinfo=DISPLAY_TZ)
    if local.time() < SESSION_START_ET:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc)


def intraday_candidates(h1: pd.DataFrame, now: datetime) -> list[tuple[float, str]]:
    """1h swing highs/lows and the session high/low, from COMPLETED 1-hour candles only (the last, in-progress one is dropped)."""
    done = h1.iloc[:-1]
    out: list[tuple[float, str]] = []
    highs, lows = ta._swing_levels(done.tail(SWING_LOOKBACK_H1), wing=SWING_WING_H1)
    out += [(v, "1h swing high") for v in highs] + [(v, "1h swing low") for v in lows]
    session = done[done["datetime"] >= _session_start(now)]
    if len(session):
        out += [(float(session["high"].max()), "session high"), (float(session["low"].min()), "session low")]
    return out


def would_be_levels(levels: dict, h1: pd.DataFrame, price: float, now: datetime):
    """(new resistance trigger, new support trigger, labels of those two zones) a TA re-run at `price` would have produced with the
    intraday candidates added, using the real zone code (ta_forecast_job); (None, None, None) if it finds no zone on a side."""
    zones = ta._merge_zones(_stored_candidates(levels) + intraday_candidates(h1, now))
    min_gap = ta.MIN_LEVEL_GAP_ATR * levels["indicators"]["atr14_1d"]
    resistances, supports = ta._split_zones(zones, price, min_gap)
    scenarios = {s["name"]: s for s in ta.build_scenarios(resistances, supports, levels["buffer"])}
    if "sell_resistance" not in scenarios or "buy_support" not in scenarios:
        return None, None, None
    labels = {"resistance": resistances[0]["labels"], "support": supports[0]["labels"]}
    return (_entry_price_and_invalidation("sell_resistance", scenarios["sell_resistance"])[0],
            _entry_price_and_invalidation("buy_support", scenarios["buy_support"])[0], labels)


def check_ta_rerun_shadow(prices: dict, candles: pd.DataFrame | None, now: datetime | None = None) -> int | None:
    """Called once per poll. Logs a would-be re-run (returns the row id) or does nothing (returns None). Never trades, never sends."""
    gold = prices.get("gold")
    if gold is None or candles is None:
        return None
    now = now or datetime.now(timezone.utc)
    if not _within_entry_window(now):
        return None
    forecast = get_latest_ta_forecast_levels()
    if forecast is None or forecast["forecast_date"] != now.astimezone(DISPLAY_TZ).date():
        return None  # no TA run yet today: Broker B/F arm nothing either
    levels = forecast["levels"]
    ref = fade_levels(levels)
    atr = gold_rsi_adx_atr_from_candles(candles, RSI_PERIOD, ADX_PERIOD, ATR_PERIOD)[2]
    if ref is None or atr is None:
        return None

    day_start = datetime.combine(now.astimezone(DISPLAY_TZ).date(), time(0, 0), tzinfo=DISPLAY_TZ).astimezone(timezone.utc)
    events = get_ta_rerun_shadow_since(day_start)
    ref_source, last_run_ts = "published", forecast["ts"]
    if events and events[-1]["ts"] > forecast["ts"] and events[-1]["new_r"] is not None:
        ref = (events[-1]["new_r"], events[-1]["new_s"])  # behave as if that re-run had happened
        ref_source, last_run_ts = "shadow", events[-1]["ts"]
    if len(events) >= TA_RERUN_SHADOW_MAX_PER_DAY or now - last_run_ts < timedelta(minutes=TA_RERUN_SHADOW_COOLDOWN_MINUTES):
        return None
    dist_r, dist_s = abs(gold - ref[0]), abs(gold - ref[1])
    if min(dist_r, dist_s) <= TA_RERUN_SHADOW_ATR_MULTIPLE * atr:
        return None

    h1 = fetch_candles(GOLD_SPOT_SYMBOL, "1h", H1_BARS)
    new_r, new_s, labels = would_be_levels(levels, h1, gold, now)
    differs = new_r is not None and (abs(new_r - ref[0]) >= TA_RERUN_SHADOW_MIN_LEVEL_CHANGE
                                     or abs(new_s - ref[1]) >= TA_RERUN_SHADOW_MIN_LEVEL_CHANGE)
    row_id = insert_ta_rerun_shadow({
        "price": gold, "atr15": atr, "multiple": TA_RERUN_SHADOW_ATR_MULTIPLE, "ref_source": ref_source,
        "forecast_id": forecast["id"], "ref_r": ref[0], "ref_s": ref[1], "dist_r": dist_r, "dist_s": dist_s,
        "new_r": new_r, "new_s": new_s, "differs": bool(differs), "new_labels": labels,
    })
    log.info("TA re-run shadow #%s: gold %.2f is %.1f / %.1f from the %s fade levels %.2f / %.2f (ATR15 %.2f); a re-run would give %s / %s%s",
             row_id, gold, dist_r, dist_s, ref_source, ref[0], ref[1], atr, new_r, new_s, "" if differs else " (no real change)")
    return row_id


def report(days: int = 7) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = get_ta_rerun_shadow_since(since)
    print(f"{len(rows)} logged would-be TA re-runs in the last {days} days; {sum(1 for r in rows if r['differs'])} would have changed the levels")
    for r in rows:
        when = r["ts"].astimezone(DISPLAY_TZ).strftime("%a %m-%d %H:%M ET")
        new = f"{r['new_r']:.2f} / {r['new_s']:.2f}" if r["new_r"] is not None else "no levels found"
        print(f"{when}  gold {r['price']:.2f}  ATR15 {r['atr15']:.2f}  vs {r['ref_source']} {r['ref_r']:.2f} / {r['ref_s']:.2f} "
              f"(away {r['dist_r']:.1f} / {r['dist_s']:.1f})  ->  would be {new}  {'CHANGED' if r['differs'] else 'same'}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
    else:
        print(__doc__)
