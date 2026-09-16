"""Historical frequency test for rules.check_intrahour_swing_alerts: how many
times, over the last 30 days, would each indicator currently in
config.INTRAHOUR_SWING_ALERT_THRESHOLD have crossed its threshold?

Pulls 5-minute bars directly from yfinance (not the project's Postgres
readings, which only cover however much history the poll job has
accumulated) and replays the exact same trailing-60-minute rising-edge logic
rules.check_intrahour_swing_alerts uses against our own stored readings --
same window bounds (60-min current window, previous window offset by one
5-min poll), same rising-edge dedup (a sustained swing counts once, not
once per bar), same direction rule.

Unlike a fixed bar-count rolling window, this uses actual elapsed time
between bars, so it stays correct across gaps in intraday data (weekends,
after-hours, us10y's thin bond-market-hours-only quoting) instead of
silently spanning more than 60 real minutes right after a gap.

Run: python frequency_test.py
"""

import bisect
from datetime import timedelta

import yfinance as yf

from config import INDICATORS, INTRAHOUR_SWING_ALERT_THRESHOLD

LOOKBACK = "1mo"
BAR_INTERVAL = "5m"
CURRENT_WINDOW = timedelta(minutes=60)
PREVIOUS_WINDOW_END = timedelta(minutes=5)   # one poll interval
PREVIOUS_WINDOW_START = timedelta(minutes=65)


def _swing(prices: list[float], lo: int, hi: int) -> float:
    """High-low range of prices[lo:hi] (empty -> 0.0, matching rules.py)."""
    window = prices[lo:hi]
    return max(window) - min(window) if window else 0.0


def find_events(timestamps: list, prices: list[float], threshold: float) -> list[dict]:
    """Rising-edge events: current 60-min window >= threshold, the window as
    of one poll (5 min) earlier wasn't. Mirrors
    rules.check_intrahour_swing_alerts exactly, replayed at every bar."""
    events = []
    for i, now in enumerate(timestamps):
        cur_lo = bisect.bisect_left(timestamps, now - CURRENT_WINDOW)
        current_window = prices[cur_lo:i + 1]
        current_swing = max(current_window) - min(current_window) if current_window else 0.0

        prev_lo = bisect.bisect_left(timestamps, now - PREVIOUS_WINDOW_START)
        prev_hi = bisect.bisect_right(timestamps, now - PREVIOUS_WINDOW_END)
        previous_swing = _swing(prices, prev_lo, prev_hi)

        if current_swing >= threshold and previous_swing < threshold:
            direction = "up" if current_window[-1] >= current_window[0] else "down"
            events.append({"timestamp": now, "swing": current_swing, "direction": direction})
    return events


def run_frequency_test() -> dict[str, list[dict]]:
    results = {}
    for name, threshold in INTRAHOUR_SWING_ALERT_THRESHOLD.items():
        symbol = INDICATORS.get(name)
        if symbol is None:
            print(f"{name.upper()}: no yfinance symbol in config.INDICATORS, skipped")
            continue

        history = yf.Ticker(symbol).history(period=LOOKBACK, interval=BAR_INTERVAL)
        close = history["Close"].dropna()
        timestamps = list(close.index)
        prices = list(close.values)

        events = find_events(timestamps, prices, threshold)
        results[name] = events

        unit = "$" if name == "gld" else ""
        print(f"\n{name.upper()} -- threshold {unit}{threshold:.3f}, {len(close)} bars, "
              f"{timestamps[0]} to {timestamps[-1]}")
        print(f"{len(events)} rising-edge events in the last 30 days")
        for e in events:
            print(f"  {e['timestamp']}  moved {e['direction']:>4s} {unit}{e['swing']:.3f}")

    return results


if __name__ == "__main__":
    run_frequency_test()
