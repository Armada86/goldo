"""Alert conditions: % change since last poll, and SMA crossover on gold."""

from datetime import timedelta

from config import (
    ABS_CHANGE_ALERT_THRESHOLD,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    PCT_CHANGE_ALERT_THRESHOLD,
    SMA_LONG,
    SMA_SHORT,
    VALUE_CHANGE_ALERT_NAMES,
)
from data_fetcher import fetch_daily_history
from storage import get_previous_reading, get_recent_readings


def check_value_change_alerts(prices: dict[str, float]) -> list[str]:
    alerts = []
    for name in VALUE_CHANGE_ALERT_NAMES:
        if name not in prices:
            continue
        price = prices[name]
        previous = get_previous_reading(name)
        if previous is not None and price != previous:
            alerts.append(f"{name.upper()} changed: {previous:+.4f} -> {price:+.4f}")
    return alerts


def check_pct_change_alerts(prices: dict[str, float]) -> list[str]:
    alerts = []
    for name, price in prices.items():
        previous = get_previous_reading(name)
        if previous is None or previous == 0:
            continue
        pct_change = (price - previous) / previous * 100
        threshold = PCT_CHANGE_ALERT_THRESHOLD.get(name)
        if threshold is not None and abs(pct_change) >= threshold:
            direction = "up" if pct_change > 0 else "down"
            alerts.append(
                f"{name.upper()} moved {direction} {pct_change:+.2f}% "
                f"(now {price:.2f})"
            )
    return alerts


def check_abs_change_alerts(prices: dict[str, float]) -> list[str]:
    """Like check_pct_change_alerts, but a fixed dollar move since the
    previous poll instead of a percentage — used for gold, where a flat
    threshold is more meaningful than a % of a ~$4,300 price."""
    alerts = []
    for name, threshold in ABS_CHANGE_ALERT_THRESHOLD.items():
        if name not in prices:
            continue
        price = prices[name]
        previous = get_previous_reading(name)
        if previous is None:
            continue
        change = price - previous
        if abs(change) >= threshold:
            direction = "up" if change > 0 else "down"
            alerts.append(
                f"{name.upper()} moved {direction} ${abs(change):.2f} "
                f"(now {price:.2f})"
            )
    return alerts


def check_intrahour_swing_alerts(prices: dict[str, float]) -> list[str]:
    """Alert once when the trailing-60-min high-low range crosses above its
    threshold — a rising-edge check (current window over threshold, the
    window as of one poll ago wasn't) so a sustained swing alerts once
    instead of every 5 minutes for the rest of the hour."""
    alerts = []
    for name, threshold in INTRAHOUR_SWING_ALERT_THRESHOLD.items():
        if name not in prices:
            continue
        readings = get_recent_readings(name, minutes=65)
        if len(readings) < 2:
            continue

        now_ts = readings[-1][0]
        current_window = [p for ts, p in readings if ts >= now_ts - timedelta(minutes=60)]
        previous_window = [
            p for ts, p in readings
            if now_ts - timedelta(minutes=65) <= ts <= now_ts - timedelta(minutes=5)
        ]
        current_swing = max(current_window) - min(current_window) if current_window else 0.0
        previous_swing = max(previous_window) - min(previous_window) if previous_window else 0.0

        if current_swing >= threshold and previous_swing < threshold:
            alerts.append(
                f"{name.upper()} swung ${current_swing:.2f} in the last hour "
                f"(threshold ${threshold:.2f})"
            )
    return alerts


def check_sma_crossover() -> list[str]:
    history = fetch_daily_history("gold", period="6mo")
    if len(history) < SMA_LONG + 1:
        return []

    close = history["Close"]
    sma_short = close.rolling(SMA_SHORT).mean()
    sma_long = close.rolling(SMA_LONG).mean()

    prev_diff = sma_short.iloc[-2] - sma_long.iloc[-2]
    curr_diff = sma_short.iloc[-1] - sma_long.iloc[-1]

    if prev_diff <= 0 < curr_diff:
        return [f"GOLD: {SMA_SHORT}-day SMA crossed above {SMA_LONG}-day SMA (bullish)"]
    if prev_diff >= 0 > curr_diff:
        return [f"GOLD: {SMA_SHORT}-day SMA crossed below {SMA_LONG}-day SMA (bearish)"]
    return []
