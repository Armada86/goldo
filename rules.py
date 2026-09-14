"""Alert conditions: % change since last poll, and SMA crossover on gold."""

from config import PCT_CHANGE_ALERT_THRESHOLD, SMA_LONG, SMA_SHORT, VALUE_CHANGE_ALERT_NAMES
from data_fetcher import fetch_daily_history
from storage import get_previous_reading


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
