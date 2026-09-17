"""Historical frequency test for rules.check_intrahour_swing_alerts: how many
times, over the last FREQUENCY_TEST_LOOKBACK_DAYS days, would each
indicator/window combination in config.INTRAHOUR_SWING_ALERT_THRESHOLD have
crossed its threshold?

Pulls 5-minute bars directly from yfinance (not the project's Postgres
readings, which only cover however much history the poll job has
accumulated) and replays the exact same trailing-window rising-edge logic
rules.check_intrahour_swing_alerts uses against our own stored readings --
same window bounds (per-window current window, previous window offset by one
5-min poll), same rising-edge dedup (a sustained swing counts once, not
once per bar), same direction rule.

The lookback is capped at 60 days (FREQUENCY_TEST_LOOKBACK_DAYS) because
that's the most 5-minute-resolution history yfinance serves for intraday
bars -- asking for more raises an error rather than silently truncating.

Unlike a fixed bar-count rolling window, this uses actual elapsed time
between bars, so it stays correct across gaps in intraday data (weekends,
after-hours, us10y's thin bond-market-hours-only quoting) instead of
silently spanning more real minutes than the window right after a gap.

Run: python frequency_test.py
"""

import bisect
from datetime import timedelta

import yfinance as yf

from config import (
    FREQUENCY_TEST_LOOKBACK_DAYS,
    INDICATORS,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    INTRAHOUR_SWING_WINDOWS_MINUTES,
)

LOOKBACK = f"{FREQUENCY_TEST_LOOKBACK_DAYS}d"
BAR_INTERVAL = "5m"
PREVIOUS_WINDOW_END = timedelta(minutes=5)   # one poll interval


def _swing(prices: list[float], lo: int, hi: int) -> float:
    """High-low range of prices[lo:hi] (empty -> 0.0, matching rules.py)."""
    window = prices[lo:hi]
    return max(window) - min(window) if window else 0.0


def find_events(
    timestamps: list, prices: list[float], window_minutes: int, threshold: float
) -> list[dict]:
    """Rising-edge events for one window size: current window >= threshold,
    the window as of one poll (5 min) earlier wasn't. Mirrors
    rules.check_intrahour_swing_alerts exactly, replayed at every bar."""
    current_window_span = timedelta(minutes=window_minutes)
    previous_window_start = timedelta(minutes=window_minutes + 5)

    events = []
    for i, now in enumerate(timestamps):
        cur_lo = bisect.bisect_left(timestamps, now - current_window_span)
        current_window = prices[cur_lo:i + 1]
        current_swing = max(current_window) - min(current_window) if current_window else 0.0

        prev_lo = bisect.bisect_left(timestamps, now - previous_window_start)
        prev_hi = bisect.bisect_right(timestamps, now - PREVIOUS_WINDOW_END)
        previous_swing = _swing(prices, prev_lo, prev_hi)

        if current_swing >= threshold and previous_swing < threshold:
            direction = "up" if current_window[-1] >= current_window[0] else "down"
            events.append({"timestamp": now, "swing": current_swing, "direction": direction})
    return events


def fetch_series(name: str) -> tuple[list, list[float]]:
    """(timestamps, prices) for `name` over LOOKBACK, from yfinance 5-min bars."""
    symbol = INDICATORS[name]
    history = yf.Ticker(symbol).history(period=LOOKBACK, interval=BAR_INTERVAL)
    close = history["Close"].dropna()
    return list(close.index), list(close.values)


def run_frequency_test() -> dict[str, dict]:
    """Returns {indicator: {"series": (timestamps, prices), "windows": {window_minutes: [events]}}}.
    The raw series is included so callers (e.g. frequency_check_job.py) can
    re-search a new threshold for an off-target window without re-fetching."""
    results = {}
    for name, thresholds_by_window in INTRAHOUR_SWING_ALERT_THRESHOLD.items():
        if name not in INDICATORS:
            print(f"{name.upper()}: no yfinance symbol in config.INDICATORS, skipped")
            continue

        timestamps, prices = fetch_series(name)
        windows = {}

        unit = "$" if name == "gld" else ""
        print(f"\n{name.upper()} -- {len(prices)} bars, {timestamps[0]} to {timestamps[-1]}")
        for window in INTRAHOUR_SWING_WINDOWS_MINUTES:
            threshold = thresholds_by_window.get(window)
            if threshold is None:
                continue
            events = find_events(timestamps, prices, window, threshold)
            windows[window] = events
            print(f"  {window}-min window, threshold {unit}{threshold:.4f}: "
                  f"{len(events)} rising-edge events in the last {FREQUENCY_TEST_LOOKBACK_DAYS} days")
            for e in events:
                print(f"    {e['timestamp']}  moved {e['direction']:>4s} {unit}{e['swing']:.4f}")

        results[name] = {"series": (timestamps, prices), "windows": windows}

    return results


if __name__ == "__main__":
    run_frequency_test()
