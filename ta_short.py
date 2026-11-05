"""Short-range XAU/USD technical analysis for Broker R (5 Nov 2026).

A much tighter cousin of ta_forecast_job.py: instead of daily/4h structure it looks only at the last few hours of 5-minute
candles, and it only keeps a resistance and a support that are close to price -- between R_LEVEL_MIN_DISTANCE ($3) and
R_LEVEL_MAX_DISTANCE ($10) away -- because Broker R fades them for a strict +$5 / -$5. A new analysis is written every
R_TA_INTERVAL_MINUTES (30) during the 7am-5pm ET weekday window (`maybe_run()` is called from the 5-minute poll and writes a row
when the current half-hour slot has none yet); `python ta_short.py` runs one by hand, `--dry-run` prints it without touching the DB.

Level candidates (each with a weight; a cluster's weight is the sum of its labels'):
  - 5-minute swing highs / lows of the last ~12 hours (wing 3)          weight 2
  - the last hour's high / low                                          weight 2
  - today's (ET) high / low so far                                      weight 2
  - round numbers every $10                                             weight 2
  - round numbers every $5 (R_LEVEL_STEP)                               weight 1  (guarantees a level each side)
Candidates within CLUSTER_DOLLARS of each other are merged. The chosen resistance is the NEAREST cluster whose near edge is in
the $3-$10 band above price; the support is its mirror. Only these two are kept and drawn. The trade level is the edge price reaches first
(a resistance's low, a support's high). The stored `levels` mirror ta_forecast_job.py's shape (`price`, `resistances`,
`supports`, `scenarios`) so the diagram renderer and Broker B's scenario helpers can be reused; only the two fade scenarios
exist -- no breakouts.

Not sent to Telegram (twenty runs a day would be noise); Broker R's trades are.
"""

import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    GOLD_SPOT_SYMBOL,
    R_LEVEL_MAX_DISTANCE,
    R_LEVEL_MIN_DISTANCE,
    R_LEVEL_STEP,
    R_STOP_LOSS,
    R_TA_INTERVAL_MINUTES,
    R_TAKE_PROFIT,
)

DISPLAY_TZ = ZoneInfo("America/New_York")
WINDOW_START_HOUR_ET = 7
WINDOW_END_HOUR_ET = 17
CANDLE_BARS = 200          # 5-minute bars fetched (~16 hours incl. the overnight session)
SWING_BARS = 144           # swing points are searched in the last 12 hours
SWING_WING = 3             # a swing high/low must beat this many 5-min bars on each side
LAST_HOUR_BARS = 12
CLUSTER_DOLLARS = 1.5


def in_window(now_utc: datetime) -> bool:
    local = now_utc.astimezone(DISPLAY_TZ)
    return local.weekday() < 5 and WINDOW_START_HOUR_ET <= local.hour < WINDOW_END_HOUR_ET


def slot_start(now_utc: datetime) -> datetime:
    """Start (UTC) of the current half-hour slot, aligned to ET :00 / :30."""
    local = now_utc.astimezone(DISPLAY_TZ)
    minute = (local.minute // R_TA_INTERVAL_MINUTES) * R_TA_INTERVAL_MINUTES
    return local.replace(minute=minute, second=0, microsecond=0).astimezone(timezone.utc)


def _swings(bars: pd.DataFrame) -> tuple[list[float], list[float]]:
    highs, lows = [], []
    h, l = bars["high"].tolist(), bars["low"].tolist()
    for i in range(SWING_WING, len(bars) - SWING_WING):
        window_h = h[i - SWING_WING : i + SWING_WING + 1]
        window_l = l[i - SWING_WING : i + SWING_WING + 1]
        if h[i] == max(window_h) and window_h.count(h[i]) == 1:
            highs.append(h[i])
        if l[i] == min(window_l) and window_l.count(l[i]) == 1:
            lows.append(l[i])
    return highs, lows


def candidate_levels(bars: pd.DataFrame, price: float, now_utc: datetime) -> list[tuple[float, str, int]]:
    """(value, label, weight) for every candidate level in the neighbourhood of `price`."""
    out: list[tuple[float, str, int]] = []
    highs, lows = _swings(bars.tail(SWING_BARS))
    out += [(v, "5m swing high", 2) for v in highs] + [(v, "5m swing low", 2) for v in lows]
    last_hour = bars.tail(LAST_HOUR_BARS)
    out += [(float(last_hour["high"].max()), "1h high", 2), (float(last_hour["low"].min()), "1h low", 2)]
    local_day = now_utc.astimezone(DISPLAY_TZ).date()
    today = bars[bars["datetime"].dt.tz_convert(DISPLAY_TZ).dt.date == local_day]
    if not today.empty:
        out += [(float(today["high"].max()), "day high", 2), (float(today["low"].min()), "day low", 2)]
    lo = int((price - R_LEVEL_MAX_DISTANCE - R_LEVEL_STEP) // R_LEVEL_STEP) * int(R_LEVEL_STEP)
    hi = int((price + R_LEVEL_MAX_DISTANCE + R_LEVEL_STEP) // R_LEVEL_STEP + 1) * int(R_LEVEL_STEP)
    for v in range(lo, hi + 1, int(R_LEVEL_STEP)):
        if v % 10 == 0:
            out.append((float(v), "round $10", 2))
        else:
            out.append((float(v), "round $5", 1))
    return out


def _cluster(cands: list[tuple[float, str, int]]) -> list[dict]:
    zones: list[dict] = []
    for value, label, weight in sorted(cands):
        if zones and value - zones[-1]["low"] <= CLUSTER_DOLLARS:  # total span capped, no chaining
            z = zones[-1]
            z["high"] = round(value, 2)
            if label not in z["labels"]:
                z["labels"].append(label)
                z["weight"] += weight
        else:
            zones.append({"low": round(value, 2), "high": round(value, 2), "labels": [label], "weight": weight, "nearby": []})
    return zones


def pick_levels(cands: list[tuple[float, str, int]], price: float) -> tuple[list[dict], list[dict]]:
    """(resistances, supports): exactly the NEAREST resistance and the NEAREST support whose near edge is
    R_LEVEL_MIN_DISTANCE..R_LEVEL_MAX_DISTANCE from price (a list of at most one zone each; empty if none in range). These are
    the only two fade levels Broker R trades and the only two its chart shows (user, 5 Nov 2026)."""
    zones = _cluster(cands)
    res = [z for z in zones if R_LEVEL_MIN_DISTANCE <= z["low"] - price <= R_LEVEL_MAX_DISTANCE]
    sup = [z for z in zones if R_LEVEL_MIN_DISTANCE <= price - z["high"] <= R_LEVEL_MAX_DISTANCE]
    res.sort(key=lambda z: z["low"] - price)
    sup.sort(key=lambda z: price - z["high"])
    return res[:1], sup[:1]


def build_scenarios(resistances: list[dict], supports: list[dict]) -> list[dict]:
    """Fades only. The trade level is the zone edge price reaches first; the stop/target are the strict +/- $5 of broker_r."""
    scenarios = []
    if resistances:
        level = resistances[0]["low"]
        scenarios.append({
            "name": "sell_resistance", "direction": "short", "entry": {"low": level, "high": level},
            "stop": round(level + R_STOP_LOSS, 2), "target": round(level - R_TAKE_PROFIT, 2),
        })
    if supports:
        level = supports[0]["high"]
        scenarios.append({
            "name": "buy_support", "direction": "long", "entry": {"low": level, "high": level},
            "stop": round(level - R_STOP_LOSS, 2), "target": round(level + R_TAKE_PROFIT, 2),
        })
    return scenarios


def render_text(run_name: str, price: float, now: datetime, resistances, supports, scenarios) -> str:
    def line(z):
        return f"  {z['low']:.2f}" + (f"-{z['high']:.2f}" if z["high"] != z["low"] else "") + f" ({', '.join(z['labels'])}; weight {z['weight']})"

    lines = [
        f"{run_name} SHORT-RANGE XAU/USD (Broker R) -- {now.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M %Z}",
        f"Price ${price:,.2f}. Levels {R_LEVEL_MIN_DISTANCE:g}-{R_LEVEL_MAX_DISTANCE:g} dollars from price; exits strictly +{R_TAKE_PROFIT:g} / -{R_STOP_LOSS:g}.",
        "", "RESISTANCE" if resistances else "RESISTANCE: none in range",
        *[line(z) for z in resistances], "", "SUPPORT" if supports else "SUPPORT: none in range", *[line(z) for z in supports], "", "PLAN",
    ]
    for sc in scenarios:
        side = "Sell" if sc["name"] == "sell_resistance" else "Buy"
        lines.append(f"  {side} at {sc['entry']['low']:.2f}: take-profit {sc['target']:.2f}, stop {sc['stop']:.2f}")
    if not scenarios:
        lines.append("  no trade level in range")
    return "\n".join(lines)


def compute(price: float, bars: pd.DataFrame, now: datetime, run_name: str = "R-TA") -> tuple[str, dict]:
    resistances, supports = pick_levels(candidate_levels(bars, price, now), price)
    scenarios = build_scenarios(resistances, supports)
    analysis = render_text(run_name, price, now, resistances, supports, scenarios)
    levels = {
        "session": run_name, "price": round(float(price), 2), "slot_ts": slot_start(now).isoformat(),
        "resistances": resistances, "supports": supports, "scenarios": scenarios,
        "take_profit": R_TAKE_PROFIT, "stop_loss": R_STOP_LOSS,
    }
    return analysis, levels


def maybe_run(spot: float | None, now: datetime | None = None) -> bool:
    """Called from every poll. Writes a short-range forecast if this is inside the 7am-5pm ET weekday window and the current
    half-hour slot has none yet. Returns True if a row was written. Costs one 5-minute candle call per half hour, nothing else."""
    from data_fetcher import fetch_candles
    from storage import count_ta_short_forecasts_for_date, get_latest_ta_short_forecast, insert_ta_short_forecast

    now = now or datetime.now(timezone.utc)
    if spot is None or not in_window(now):
        return False
    latest = get_latest_ta_short_forecast()
    slot = slot_start(now)
    if latest is not None and latest["ts"] >= slot:
        return False
    bars = fetch_candles(GOLD_SPOT_SYMBOL, "5min", CANDLE_BARS)
    local_date = now.astimezone(DISPLAY_TZ).date()
    run_number = count_ta_short_forecasts_for_date(local_date) + 1
    analysis, levels = compute(spot, bars, now, f"R{run_number}")
    insert_ta_short_forecast(now, local_date, analysis, levels)
    return True


def main(dry_run: bool) -> None:
    from data_fetcher import fetch_candles

    now = datetime.now(timezone.utc)
    bars = fetch_candles(GOLD_SPOT_SYMBOL, "5min", CANDLE_BARS)
    price = float(bars["close"].iloc[-1])
    analysis, levels = compute(price, bars, now)
    print(analysis)
    if dry_run:
        return
    from storage import init_db, insert_ta_short_forecast

    init_db()
    insert_ta_short_forecast(now, now.astimezone(DISPLAY_TZ).date(), analysis, levels)
    print("[ta_short] Saved to ta_short_forecasts")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
