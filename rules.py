"""Alert conditions: % change since last poll, and SMA crossover on gold."""

from datetime import timedelta

from config import (
    ABS_CHANGE_ALERT_THRESHOLD,
    DOLLAR_UNIT_NAMES,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    INTRAHOUR_SWING_WINDOWS_MINUTES,
    PCT_CHANGE_ALERT_THRESHOLD,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_OVERSOLD_THRESHOLD,
    RSI_PERIOD,
    SMA_LONG,
    SMA_SHORT,
    VALUE_CHANGE_ALERT_NAMES,
)
from data_fetcher import compute_rsi, fetch_daily_history, fetch_gold_candles
from storage import get_previous_reading, get_recent_readings

# Prefix for every Telegram alert about spot gold (XAU/USD) itself -- check_abs_change_alerts,
# check_sma_crossover, check_rsi_alerts. Telegram's Bot API has no real text-color support, so a
# colored-circle emoji is the practical substitute for visually distinguishing alert categories
# in the chat. Broker trade alerts use their own prefix -- see broker.TRADE_ALERT_PREFIX.
XAUUSD_ALERT_PREFIX = "\U0001f7e1 "  # yellow circle


def check_value_change_alerts(prices: dict[str, float]) -> list[str]:
    """Alert on any change since the previous poll (used for indicators
    where a % threshold breaks down, e.g. financial_stress oscillating near
    zero). The relative % change is included too where it's meaningful
    (skipped when previous is 0, since a % change off zero is undefined)."""
    alerts = []
    for name in VALUE_CHANGE_ALERT_NAMES:
        if name not in prices:
            continue
        price = prices[name]
        previous = get_previous_reading(name)
        if previous is not None and price != previous:
            direction = "up" if price > previous else "down"
            if previous != 0:
                pct_change = (price - previous) / previous * 100
                alerts.append(
                    f"{name.upper()} changed {direction} {pct_change:+.2f}%: "
                    f"{previous:+.4f} -> {price:+.4f}"
                )
            else:
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
    """Like check_pct_change_alerts, but a fixed move since the previous
    poll instead of a percentage — used where a flat threshold is more
    meaningful than a %: gold (dollars, vs. a ~$4,300 price) and us10y
    (yield points, which oscillate near zero so a % threshold is noisy)."""
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
            unit = "$" if name == "gold" else ""
            label = "XAU/USD" if name == "gold" else name.upper()
            prefix = XAUUSD_ALERT_PREFIX if name == "gold" else ""
            alerts.append(
                f"{prefix}{label} moved {direction} {unit}{abs(change):.2f} "
                f"(now {price:.2f})"
            )
    return alerts


def check_intrahour_swing_alerts(prices: dict[str, float]) -> list[str]:
    """Alert once per window when its trailing high-low range crosses above
    its threshold — a rising-edge check (current window over threshold, the
    window as of one poll ago wasn't) so a sustained swing alerts once
    instead of every 5 minutes for the rest of the window. Each indicator is
    checked independently against every window in
    INTRAHOUR_SWING_WINDOWS_MINUTES (15/10/5 min), each with its own
    threshold, so a single poll can produce up to one alert per window."""
    alerts = []
    max_window = max(INTRAHOUR_SWING_WINDOWS_MINUTES)
    for name, thresholds_by_window in INTRAHOUR_SWING_ALERT_THRESHOLD.items():
        if name not in prices:
            continue
        readings = get_recent_readings(name, minutes=max_window + 5)
        if len(readings) < 2:
            continue
        now_ts = readings[-1][0]

        for window in INTRAHOUR_SWING_WINDOWS_MINUTES:
            threshold = thresholds_by_window.get(window)
            if threshold is None:
                continue

            current_window = [p for ts, p in readings if ts >= now_ts - timedelta(minutes=window)]
            previous_window = [
                p for ts, p in readings
                if now_ts - timedelta(minutes=window + 5) <= ts <= now_ts - timedelta(minutes=5)
            ]
            current_swing = max(current_window) - min(current_window) if current_window else 0.0
            previous_swing = max(previous_window) - min(previous_window) if previous_window else 0.0

            if current_swing >= threshold and previous_swing < threshold:
                direction = "up" if current_window[-1] >= current_window[0] else "down"
                unit = "$" if name in DOLLAR_UNIT_NAMES else ""
                decimals = 2 if name in DOLLAR_UNIT_NAMES else 4
                alerts.append(
                    f"{name.upper()} moved {direction} {unit}{current_swing:.{decimals}f} in the "
                    f"last {window} min (threshold {unit}{threshold:.{decimals}f}, "
                    f"now {unit}{prices[name]:.2f})"
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
        return [f"{XAUUSD_ALERT_PREFIX}XAU/USD: {SMA_SHORT}-day SMA crossed above {SMA_LONG}-day SMA (bullish)"]
    if prev_diff >= 0 > curr_diff:
        return [f"{XAUUSD_ALERT_PREFIX}XAU/USD: {SMA_SHORT}-day SMA crossed below {SMA_LONG}-day SMA (bearish)"]
    return []


def check_rsi_alerts() -> list[str]:
    """RSI(14) on gold spot only (15-min candles from Twelve Data). Like
    check_sma_crossover, this is a crossing check — it compares the two most
    recent RSI values so it fires once when RSI crosses into overbought/
    oversold territory, not on every poll spent past the threshold."""
    candles = fetch_gold_candles()
    rsi = compute_rsi(candles["close"], period=RSI_PERIOD).dropna()
    if len(rsi) < 2:
        return []

    prev_rsi, curr_rsi = rsi.iloc[-2], rsi.iloc[-1]
    alerts = []
    if prev_rsi < RSI_OVERBOUGHT_THRESHOLD <= curr_rsi:
        alerts.append(
            f"{XAUUSD_ALERT_PREFIX}XAU/USD: RSI({RSI_PERIOD}) entered overbought territory: {curr_rsi:.1f} "
            f"(>= {RSI_OVERBOUGHT_THRESHOLD})"
        )
    if prev_rsi > RSI_OVERSOLD_THRESHOLD >= curr_rsi:
        alerts.append(
            f"{XAUUSD_ALERT_PREFIX}XAU/USD: RSI({RSI_PERIOD}) entered oversold territory: {curr_rsi:.1f} "
            f"(<= {RSI_OVERSOLD_THRESHOLD})"
        )
    return alerts
