"""Companion-swing frequency test for rules.check_intrahour_swing_alerts thresholds.

Replaces the old target-event-rate search entirely. Methodology: find every moment gold spot
itself swung GOLD_SWING_THRESHOLDS[window] within a trailing window (e.g. $5 in 5 minutes),
rising-edge deduped so a sustained move counts once, restricted to the trading hours shared by
all eight intrahour-swing indicators (COMMON_SESSION_START_ET-COMMON_SESSION_END_ET,
America/New_York, weekdays -- gld/iau/gldm/gdx/gdxj/ring/dxy/us10y each trade a different
subset of the day, so a gold move outside their common window can't be compared against all
eight). At each of those moments, measure each indicator's own high-low swing over that
identical window and average it across every such moment -- that average *is* the indicator's
new threshold for that window. There is no search step and no target event count: the
threshold is a direct, data-derived average, recomputed fresh (FREQUENCY_TEST_LOOKBACK_DAYS,
a rolling window) every time this runs.

Data sources: gold spot and the six gold ETFs (gld/iau/gldm/gdx/gdxj/ring) come from Twelve
Data at true 1-minute resolution, paginated back FREQUENCY_TEST_LOOKBACK_DAYS days --
yfinance's 1-minute bars are capped at ~7-8 days by Yahoo itself, too short for this lookback.
dxy/us10y stay on yfinance's 5-minute bars: neither Twelve Data nor FMP has a genuine Dollar
Index or intraday Treasury-yield instrument at any plan tier evaluated (see
docs/data-sources.md) -- Twelve Data's "USDX"/"DX" symbols look plausible but resolve to
unrelated tickers (confirmed via its own symbol_search), not the Dollar Index.

run_frequency_test() also returns a directional co-flagging distribution per window (how many
of the eight indicators, at each gold event, both crossed their own threshold AND moved in the
direction broker.py's Consensus6of8 rule actually requires -- same direction as gold for the
six ETFs, opposite for dxy/us10y). This replaced an earlier magnitude-only co-flagging analysis
that counted a "flag" regardless of direction, which overstated real co-flagging since it
credited an indicator crossing its threshold in the wrong direction -- not a signal the Broker
would ever act on.

Run: python frequency_test.py
"""

import bisect
import os
import time as _time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from config import (
    COMMON_SESSION_END_ET,
    COMMON_SESSION_START_ET,
    DOLLAR_UNIT_NAMES,
    FREQUENCY_TEST_LOOKBACK_DAYS,
    GOLD_SPOT_SYMBOL,
    GOLD_SWING_THRESHOLDS,
    INDICATORS,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    INTRAHOUR_SWING_WINDOWS_MINUTES,
)

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")

# The six gold ETFs share Twelve Data's true 1-minute resolution with gold spot; dxy/us10y
# have no such source available (see module docstring) and stay on yfinance's 5-minute bars.
TWELVE_DATA_NAMES = ["gld", "iau", "gldm", "gdx", "gdxj", "ring"]
YFINANCE_NAMES = ["dxy", "us10y"]

ET = ZoneInfo("America/New_York")
PREVIOUS_WINDOW_GAP = timedelta(minutes=5)  # one poll interval, same rising-edge convention as rules.py

# broker.py's Consensus6of8 rule requires these six to move the SAME direction gold itself
# moved, and dxy/us10y to move the OPPOSITE direction -- see co_flagging_distribution() below,
# which uses this same split to decide whether a threshold-crossing is a coherent "flag" or
# just a same-magnitude move in the wrong direction (broker.GOLD_DIRECTION_NAMES/
# INVERSE_DIRECTION_NAMES duplicate these; kept separate since frequency_test.py has no
# dependency on broker.py).
SAME_DIRECTION_NAMES = ["gld", "iau", "gldm", "gdx", "gdxj", "ring"]
INVERSE_DIRECTION_NAMES = ["dxy", "us10y"]


def _fetch_twelve_data_1min(symbol: str, lookback_days: int) -> tuple[list[datetime], list[float]]:
    """Paginated 1-minute close series from Twelve Data, UTC timestamps, covering the last
    lookback_days days. Explicit timezone=UTC -- Twelve Data's default (no timezone param) is
    not UTC, confirmed empirically to be offset by several hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows: dict[str, float] = {}
    end_date = None
    for _ in range(12):  # safety cap; each call covers a few days depending on how many hours/day the symbol trades
        params = {
            "symbol": symbol, "interval": "1min", "outputsize": 5000,
            "apikey": TWELVE_DATA_API_KEY, "timezone": "UTC",
        }
        if end_date:
            params["end_date"] = end_date
        response = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=20)
        payload = response.json()
        if payload.get("status") != "ok":
            raise RuntimeError(f"Twelve Data error for {symbol}: {payload}")
        values = payload["values"]
        for v in values:
            rows[v["datetime"]] = float(v["close"])
        oldest = datetime.strptime(values[-1]["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if oldest <= cutoff:
            break
        end_date = (oldest - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        _time.sleep(9)  # stay under Twelve Data's free-tier 8-credits/minute cap

    items = sorted(rows.items())
    timestamps = [datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) for t, _ in items]
    prices = [c for _, c in items]
    lo = bisect.bisect_left(timestamps, cutoff)
    return timestamps[lo:], prices[lo:]


def _fetch_yfinance_5min(name: str, lookback_days: int) -> tuple[list[datetime], list[float]]:
    """5-minute close series from yfinance, UTC timestamps -- for dxy/us10y, which have no
    reachable 1-minute source over a lookback this long (see module docstring)."""
    symbol = INDICATORS[name]
    history = yf.Ticker(symbol).history(period=f"{lookback_days + 5}d", interval="5m")
    close = history["Close"].dropna()
    timestamps = list(close.index.tz_convert("UTC").to_pydatetime())
    prices = list(close.values)
    return timestamps, prices


def _swing(prices: list[float], lo: int, hi: int) -> float:
    window = prices[lo:hi]
    return max(window) - min(window) if window else 0.0


def find_gold_events(
    timestamps: list[datetime], prices: list[float], window_minutes: int, threshold: float
) -> list[datetime]:
    """Rising-edge timestamps where gold's trailing window_minutes swing crosses threshold,
    having been below it one poll interval (5 min) earlier -- same dedup convention as
    rules.check_intrahour_swing_alerts, so a sustained move counts once."""
    span = timedelta(minutes=window_minutes)
    prev_span = timedelta(minutes=window_minutes + 5)
    events = []
    for i, now in enumerate(timestamps):
        cur_lo = bisect.bisect_left(timestamps, now - span)
        current_swing = _swing(prices, cur_lo, i + 1)

        prev_lo = bisect.bisect_left(timestamps, now - prev_span)
        prev_hi = bisect.bisect_right(timestamps, now - PREVIOUS_WINDOW_GAP)
        previous_swing = _swing(prices, prev_lo, prev_hi)

        if current_swing >= threshold and previous_swing < threshold:
            events.append(now)
    return events


def in_common_session(t_utc: datetime, window_minutes: int) -> bool:
    """True only if the whole [t - window, t] interval falls within the trading hours shared
    by all eight intrahour-swing indicators (COMMON_SESSION_START_ET-COMMON_SESSION_END_ET,
    weekdays) -- so every indicator actually had a chance to move during that window."""
    t_et = t_utc.astimezone(ET)
    start_et = (t_utc - timedelta(minutes=window_minutes)).astimezone(ET)
    if t_et.weekday() >= 5 or start_et.weekday() >= 5:
        return False
    if t_et.date() != start_et.date():
        return False
    return COMMON_SESSION_START_ET <= start_et.time() and t_et.time() <= COMMON_SESSION_END_ET


def swing_in_window(
    timestamps: list[datetime], prices: list[float], now: datetime, window_minutes: int, min_bars: int = 2
) -> float | None:
    """High-low range of `prices` within [now - window_minutes, now]. None if fewer than
    min_bars points fall in the window -- too little data to call it a measured swing."""
    lo = bisect.bisect_left(timestamps, now - timedelta(minutes=window_minutes))
    hi = bisect.bisect_right(timestamps, now)
    window = prices[lo:hi]
    if len(window) < min_bars:
        return None
    return max(window) - min(window)


def direction_and_swing_in_window(
    timestamps: list[datetime], prices: list[float], now: datetime, window_minutes: int, min_bars: int = 2
) -> tuple[int | None, float | None]:
    """Like swing_in_window, but also returns net direction over the identical window: +1 (net
    up), -1 (net down), or None (perfectly flat, or too little data) -- last close vs first
    close within the [now - window_minutes, now] slice."""
    lo = bisect.bisect_left(timestamps, now - timedelta(minutes=window_minutes))
    hi = bisect.bisect_right(timestamps, now)
    window = prices[lo:hi]
    if len(window) < min_bars:
        return None, None
    swing = max(window) - min(window)
    net = window[-1] - window[0]
    direction = 1 if net > 0 else (-1 if net < 0 else None)
    return direction, swing


def co_flagging_distribution(
    gold_ts: list[datetime],
    gold_px: list[float],
    events: list[datetime],
    window_minutes: int,
    series: dict[str, tuple[list[datetime], list[float]]],
    thresholds: dict[str, float | None],
) -> dict:
    """At each gold event, counts how many of the eight indicators both crossed their own
    threshold for this window AND moved in the direction broker.py's Consensus6of8 rule
    actually requires (SAME_DIRECTION_NAMES with gold, INVERSE_DIRECTION_NAMES against it) --
    unlike a plain magnitude-only co-flag count, an indicator that crossed its threshold in the
    wrong direction does not count as a flag here, since the Broker would not credit it either.
    Returns {"n_events", "distribution" (>=1..>=8 -> count), "per_indicator" (name -> count)}."""
    per_event_flags = []
    per_indicator = {name: 0 for name in series}
    for t in events:
        gold_dir, _ = direction_and_swing_in_window(gold_ts, gold_px, t, window_minutes)
        flag_count = 0
        if gold_dir is not None:
            for name, (ts, px) in series.items():
                threshold = thresholds.get(name)
                if threshold is None:
                    continue
                d, s = direction_and_swing_in_window(ts, px, t, window_minutes)
                if s is None or d is None:
                    continue
                direction_ok = (d == gold_dir) if name in SAME_DIRECTION_NAMES else (d == -gold_dir)
                if s >= threshold and direction_ok:
                    flag_count += 1
                    per_indicator[name] += 1
        per_event_flags.append(flag_count)

    distribution = {n: sum(1 for f in per_event_flags if f >= n) for n in range(1, 9)}
    return {"n_events": len(events), "distribution": distribution, "per_indicator": per_indicator}


def run_frequency_test() -> tuple[dict[str, dict[int, dict]], dict[int, dict]]:
    """Returns {name: {window_minutes: {"avg": float | None, "n_used": int, "n_total": int}}}
    for every name in INTRAHOUR_SWING_ALERT_THRESHOLD. "avg" is the new threshold itself --
    there is no separate search/tuning step."""
    print(f"Fetching gold spot 1-min history ({FREQUENCY_TEST_LOOKBACK_DAYS} days, Twelve Data)...")
    gold_ts, gold_px = _fetch_twelve_data_1min(GOLD_SPOT_SYMBOL, FREQUENCY_TEST_LOOKBACK_DAYS)
    print(f"  {len(gold_ts)} bars, {gold_ts[0]} -> {gold_ts[-1]}")

    gold_events: dict[int, list[datetime]] = {}
    for window in INTRAHOUR_SWING_WINDOWS_MINUTES:
        threshold = GOLD_SWING_THRESHOLDS[window]
        raw_events = find_gold_events(gold_ts, gold_px, window, threshold)
        common_events = [t for t in raw_events if in_common_session(t, window)]
        gold_events[window] = common_events
        print(f"  gold {window}-min >= ${threshold}: {len(raw_events)} raw events, "
              f"{len(common_events)} within common all-8-trading session")

    series: dict[str, tuple[list[datetime], list[float]]] = {}
    for name in TWELVE_DATA_NAMES:
        if name not in INTRAHOUR_SWING_ALERT_THRESHOLD:
            continue
        print(f"Fetching {name.upper()} 1-min history (Twelve Data)...")
        ts, px = _fetch_twelve_data_1min(INDICATORS[name], FREQUENCY_TEST_LOOKBACK_DAYS)
        series[name] = (ts, px)
        print(f"  {len(ts)} bars")
        _time.sleep(9)
    for name in YFINANCE_NAMES:
        if name not in INTRAHOUR_SWING_ALERT_THRESHOLD:
            continue
        print(f"Fetching {name.upper()} 5-min history (yfinance)...")
        series[name] = _fetch_yfinance_5min(name, FREQUENCY_TEST_LOOKBACK_DAYS)
        print(f"  {len(series[name][0])} bars")

    results: dict[str, dict[int, dict]] = {name: {} for name in series}
    for name, (ts, px) in series.items():
        unit = "$" if name in DOLLAR_UNIT_NAMES else ""
        print(f"\n{name.upper()}")
        for window in INTRAHOUR_SWING_WINDOWS_MINUTES:
            events = gold_events[window]
            swings = [s for t in events if (s := swing_in_window(ts, px, t, window)) is not None]
            avg = sum(swings) / len(swings) if swings else None
            results[name][window] = {"avg": avg, "n_used": len(swings), "n_total": len(events)}
            if avg is not None:
                print(f"  {window:2d} min: avg {unit}{avg:.4f} (n={len(swings)}/{len(events)})")
            else:
                print(f"  {window:2d} min: no usable data (n=0/{len(events)})")

    co_flags: dict[int, dict] = {}
    print("\nCo-flagging (magnitude AND direction coherent with gold, per broker.py's Consensus6of8 rule):")
    for window in INTRAHOUR_SWING_WINDOWS_MINUTES:
        thresholds = {name: results[name][window]["avg"] for name in series}
        co_flags[window] = co_flagging_distribution(
            gold_ts, gold_px, gold_events[window], window, series, thresholds
        )
        dist = co_flags[window]["distribution"]
        print(f"  {window:2d} min ({co_flags[window]['n_events']} events): " +
              ", ".join(f">={n}: {dist[n]}" for n in range(1, 9)))

    return results, co_flags


if __name__ == "__main__":
    run_frequency_test()
