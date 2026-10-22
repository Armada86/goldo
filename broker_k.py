"""Broker K -- an independent paper-trading engine that rides strong gold trends from RSI, ADX and ATR alone.

Completely separate from Broker A/B/F: no alerts, no TA-forecast levels, no `block_rules`, no stop-loss / trailing
settings, no DXY, and (deliberately, at the user's request) it ignores the Telegram "stop trading" / pause commands. Its own
table (`broker_k_trades`), its own open-trade tracking, its own constants in config.py (`K_*`). Paper only: it never places
an order anywhere.

Entry (checked once per poll, 7:00am-5:00pm ET weekdays, one trade at a time, no cooldown -- the next poll after a close can
enter again):
  - ADX(14) >= K_ADX_MIN (35) on gold's 15-min candles (the forming candle included, as the poll's own candle fetch returns
    it; ADX does not need to be rising),
  - ATR(14) between K_ATR_MIN and K_ATR_MAX ($3-12): enough movement to pay for the target, not a chaotic spike,
  - RSI(14) gives the direction: a Buy needs RSI in [50, 70] (momentum up, not yet exhausted), a Sell the mirror image
    [30, 50].
  Rules are named `Trend-buy` / `Trend-sell`. The fill is the live ask (Buy) / bid (Sell) from the FOREX.com demo feed when
  reachable, else the poll's Twelve Data spot price.

Exit (fixed limits, 1 oz): take-profit K_TAKE_PROFIT ($12), stop-loss K_STOP_LOSS ($8), or K_TIME_STOP_MINUTES (60) after the
open, whichever comes first. Like Broker A/B the exit is a 1-minute bar scan (price_bars.fetch_gold_bars(), a Buy exits on the
bid, a Sell on the ask), so a touch between polls still closes at the true level; a bar that touches both limits counts as the
stop; a trade left open past the 5pm ET poll cutoff is closed retroactively by the next morning's scan at the limit it really
hit, or at the time-stop price.

Why these numbers: broker_k_backtest.py --study wide (6 weeks of 1-minute bars). Narrow $2-6 limits and pullback entries lost
money after spread; this strong-trend + wider-limit combination was positive in 6 of 7 weeks at ~$1-2 per trade and stayed
positive with a doubled spread and a longer fill delay -- but on ~65 trades, picked after looking at that same data, so the live
paper record is the real test.
"""

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    ADX_PERIOD,
    ATR_PERIOD,
    K_ADX_MIN,
    K_ATR_MAX,
    K_ATR_MIN,
    K_RSI_BUY_MAX,
    K_RSI_BUY_MIN,
    K_STOP_LOSS,
    K_TAKE_PROFIT,
    K_TIME_STOP_MINUTES,
    RSI_PERIOD,
)
from data_fetcher import gold_adx_rising_from_candles, gold_rsi_adx_atr_from_candles
from notifier import send_telegram_message
from price_bars import fetch_gold_bars
from storage import close_trade_row_k, get_open_trade_k, insert_trade_k

log = logging.getLogger("gold-monitor")

DISPLAY_TZ = ZoneInfo("America/New_York")
ENTRY_WINDOW_START_ET = time(7, 0)
ENTRY_WINDOW_END_ET = time(17, 0)
EXIT_BARS_MAX = 3000  # FOREX.com serves ~4000 per call; a trade is held at most a trading day

# Telegram identity: brown square (A is a blue circle, B a blue square, F a purple square). Closes add a green/red square.
TRADE_ALERT_PREFIX = "\U0001f7eb "
PROFIT_MARKER = "\U0001f7e9 "
LOSS_MARKER = "\U0001f7e5 "

RULE_BUY = "Trend-buy"
RULE_SELL = "Trend-sell"


def _within_entry_window(now_utc: datetime) -> bool:
    local = now_utc.astimezone(DISPLAY_TZ)
    return local.weekday() < 5 and ENTRY_WINDOW_START_ET <= local.time() < ENTRY_WINDOW_END_ET


def _format_ts(ts: datetime) -> str:
    return ts.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def entry_signal(rsi: float | None, adx: float | None, atr: float | None) -> str | None:
    """'Buy' / 'Sell' / None from one RSI/ADX/ATR reading. Any missing value means no signal (K has nothing to fall back on)."""
    if rsi is None or adx is None or atr is None:
        return None
    if adx < K_ADX_MIN or not (K_ATR_MIN <= atr <= K_ATR_MAX):
        return None
    if K_RSI_BUY_MIN <= rsi <= K_RSI_BUY_MAX:
        return "Buy"
    if 100 - K_RSI_BUY_MAX <= rsi <= 100 - K_RSI_BUY_MIN:
        return "Sell"
    return None


def _pnl(trade_type: str, entry_price: float, price: float) -> float:
    return price - entry_price if trade_type == "Buy" else entry_price - price


def scan_exit(
    trade_type: str, entry_price: float, open_ts: datetime, bars: pd.DataFrame, now: datetime,
    take_profit: float = K_TAKE_PROFIT, stop_loss: float = K_STOP_LOSS, time_stop_minutes: int = K_TIME_STOP_MINUTES,
) -> tuple[float, datetime, str] | None:
    """(exit_price, exit_ts, reason) if the trade should be closed by `now`, else None. `bars` are 1-minute bars priced on the
    exit side (bid for a Buy, ask for a Sell). Only bars starting after `open_ts` and before the time-stop deadline count; the
    first bar to touch a limit closes the trade at that limit (the stop if one bar touches both); with no touch, the trade closes
    at the last in-window bar's close once the deadline has passed."""
    deadline = open_ts + timedelta(minutes=time_stop_minutes)
    window = bars[(bars["datetime"] > open_ts) & (bars["datetime"] < deadline)]
    sign = 1.0 if trade_type == "Buy" else -1.0
    for row in window.itertuples():
        best = (row.high - entry_price) if sign > 0 else (entry_price - row.low)  # most favourable excursion in the bar
        worst = (entry_price - row.low) if sign > 0 else (row.high - entry_price)  # most adverse
        if worst >= stop_loss:
            return entry_price - sign * stop_loss, row.datetime.to_pydatetime(), "SL"
        if best >= take_profit:
            return entry_price + sign * take_profit, row.datetime.to_pydatetime(), "TP"
    if now >= deadline and not window.empty:
        return float(window["close"].iloc[-1]), deadline, "Time"
    return None


def _find_exit(trade: dict, spot: float, now: datetime) -> tuple[float, datetime, str] | None:
    """The exit scan over real 1-minute bars; falls back to a point check on the poll's spot price if the bars can't be fetched
    (so a data hiccup can't leave a trade open forever -- a later good scan is not needed for correctness of the fallback close)."""
    try:
        side = "bid" if trade["trade_type"] == "Buy" else "ask"
        minutes = int((now - trade["open_ts"]).total_seconds() // 60) + 5
        bars = fetch_gold_bars(max(10, min(minutes, EXIT_BARS_MAX)), side)
        return scan_exit(trade["trade_type"], trade["entry_price"], trade["open_ts"], bars, now)
    except Exception:
        log.exception("Broker K: 1-minute bars unavailable -- point check on spot")
    pnl = _pnl(trade["trade_type"], trade["entry_price"], spot)
    if pnl <= -K_STOP_LOSS:
        return spot, now, "SL"
    if pnl >= K_TAKE_PROFIT:
        return spot, now, "TP"
    if now >= trade["open_ts"] + timedelta(minutes=K_TIME_STOP_MINUTES):
        return spot, now, "Time"
    return None


def _fill_price(trade_type: str, spot: float) -> tuple[float, float | None]:
    """(fill price, spread): the live ask for a Buy / bid for a Sell from the FOREX.com demo feed, the poll's spot price if
    that is unavailable (spread then None)."""
    try:
        bid = float(fetch_gold_bars(2, "bid")["close"].iloc[-1])
        ask = float(fetch_gold_bars(2, "ask")["close"].iloc[-1])
        return (ask if trade_type == "Buy" else bid), round(ask - bid, 3)
    except Exception:
        log.info("Broker K: bid/ask unavailable -- filling at spot")
        return spot, None


def _open_message(trade_id: int, trade_type: str, price: float, rsi: float, adx: float, atr: float, open_ts: datetime) -> str:
    return (
        f"{TRADE_ALERT_PREFIX}BROKER K #{trade_id}: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} "
        f"(TP ${K_TAKE_PROFIT:g} / SL ${K_STOP_LOSS:g} / {K_TIME_STOP_MINUTES} min).\n"
        f"Trend: ADX {adx:.1f}, RSI {rsi:.1f}, ATR ${atr:.2f}\n"
        f"Filled: {_format_ts(open_ts)}"
    )


def _close_message(trade: dict, exit_price: float, exit_ts: datetime, pnl: float, reason: str) -> str:
    marker = PROFIT_MARKER if pnl >= 0 else LOSS_MARKER
    why = {"TP": "take-profit", "SL": "stop-loss", "Time": "time stop"}[reason]
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{marker}BROKER K #{trade['id']}: closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, {why}) -- {'profit' if pnl >= 0 else 'loss'} of ${abs(pnl):.2f}\n"
        f"Filled: {_format_ts(exit_ts)}"
    )


def _exit_context(now: datetime, trade: dict, reason: str, candles) -> dict:
    ctx: dict = {"reason": reason, "held_minutes": round((now - trade["open_ts"]).total_seconds() / 60, 1)}
    if candles is not None:
        rsi, adx, atr = gold_rsi_adx_atr_from_candles(candles, RSI_PERIOD, ADX_PERIOD, ATR_PERIOD)
        ctx.update({k: round(v, 2) for k, v in (("rsi14", rsi), ("adx14", adx), ("atr14", atr)) if v is not None})
    return ctx


def check_broker_k_trades(prices: dict[str, float], candles: pd.DataFrame | None) -> None:
    """Once per poll. Closes the open trade if a limit was touched or the time stop passed, then -- inside the trading-hours
    window and with no trade open -- opens a Trend-buy/-sell if RSI, ADX and ATR line up. `candles` is the poll's 15-min gold
    candle frame (None if its fetch failed: exits still run, no new entry is considered)."""
    spot = prices.get("gold")
    if spot is None:
        return
    now = datetime.now(timezone.utc)

    trade = get_open_trade_k()
    if trade is not None:
        result = _find_exit(trade, spot, now)
        if result is None:
            return
        exit_price, exit_ts, reason = result
        pnl = _pnl(trade["trade_type"], trade["entry_price"], exit_price)
        close_trade_row_k(trade["id"], exit_price, exit_ts, pnl, reason, _exit_context(now, trade, reason, candles))
        send_telegram_message(_close_message(trade, exit_price, exit_ts, pnl, reason))

    if candles is None or not _within_entry_window(now):
        return
    rsi, adx, atr = gold_rsi_adx_atr_from_candles(candles, RSI_PERIOD, ADX_PERIOD, ATR_PERIOD)
    trade_type = entry_signal(rsi, adx, atr)
    if trade_type is None:
        return
    price, spread = _fill_price(trade_type, spot)
    context = {
        "hour_et": now.astimezone(DISPLAY_TZ).hour, "minute_et": now.astimezone(DISPLAY_TZ).minute,
        "rsi14": round(rsi, 2), "adx14": round(adx, 2), "atr14": round(atr, 2),
        "adx_rising": gold_adx_rising_from_candles(candles, ADX_PERIOD), "spot": round(float(spot), 2),
    }
    if spread is not None:
        context["spread"] = spread
    rule = RULE_BUY if trade_type == "Buy" else RULE_SELL
    trade_id = insert_trade_k(rule, trade_type, price, now, f"ADX {adx:.1f}, RSI {rsi:.1f}, ATR {atr:.2f}", context)
    send_telegram_message(_open_message(trade_id, trade_type, price, rsi, adx, atr, now))
