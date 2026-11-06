"""Broker R -- a short-range fade paper broker (5 Nov 2026).

Broker B's fade logic, shrunk. Differences from Broker B, all by the user's design:
  - Its own, much tighter technical analysis (ta_short.py, a new row every 30 minutes in `ta_short_forecasts`): the resistance and
    support it trades are only $3-$10 from price.
  - Fades only (`R-Fade-sell` at the resistance, `R-Fade-buy` at the support). No breakouts.
  - Strict fixed exits on a 1-oz position: take-profit +$5 or stop-loss -$5 (config R_TAKE_PROFIT / R_STOP_LOSS). No trailing
    stop, no profit lock, no time stop, no fade cap, and `make SL` / `make trail` / SLA do not touch it.
  - No entry filters: no DXY / RSI / ADX / ATR / spike gates, no block rules (BRA), no TA-bias gate, no "bias mode". The only
    conditions are the 7am-5pm ET weekday trading window and Telegram `stop trading` / scheduled pauses (which reach it through the
    global state, since the Worker does not name R).
It borrows Broker B's touch scan unchanged (`broker_b._scan_zone_entry()`): a level counts only once price was first seen on its
approach side, and the fill is at the exact level price on the side-correct bar (ask for a Buy, bid for a Sell). A touch older than
broker_b.ENTRY_MAX_TOUCH_AGE_MINUTES when noticed is never filled. A level is traded at most R_MAX_TRADES_PER_LEVEL times per
forecast row and is retired by its first stop-out (re-arm only after a win), then a new half-hourly analysis supplies new levels.
One position at a time. Paper only; own table `broker_r_trades`, never read by SLA / BRA / the reviews. Telegram: orange square.
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from broker_b import ENTRY_CANDLE_LOOKBACK_MINUTES, ENTRY_MAX_TOUCH_AGE_MINUTES, _entry_side, _scan_zone_entry
from broker import _format_ts
from config import R_MAX_TRADES_PER_LEVEL, R_STOP_LOSS, R_TA_FORECAST_MAX_AGE_MINUTES, R_TAKE_PROFIT
from notifier import send_telegram_message
from price_bars import fetch_gold_bars
from storage import (
    close_trade_row_r,
    get_last_close_ts_r,
    get_latest_ta_short_forecast,
    get_open_trade_r,
    insert_trade_r,
    trade_r_level_history,
)
from ta_short import DISPLAY_TZ, in_window
from trading_control import trading_pause_reason

log = logging.getLogger("gold-monitor")

TRADE_ALERT_PREFIX = "\U0001f7e7 "  # orange square (A blue circle, B blue square, F purple, K brown)
PROFIT_MARKER = "\U0001f7e9 "
LOSS_MARKER = "\U0001f7e5 "
EXIT_BARS_MAX = 3000

SCENARIOS = {"sell_resistance": ("Sell", "R-Fade-sell"), "buy_support": ("Buy", "R-Fade-buy")}


def _pnl(trade_type: str, entry_price: float, price: float) -> float:
    return price - entry_price if trade_type == "Buy" else entry_price - price


def scan_exit(trade_type: str, entry_price: float, open_ts: datetime, bars: pd.DataFrame) -> tuple[float, datetime, str] | None:
    """(exit_price, exit_ts, 'TP'|'SL') for the first 1-minute bar after `open_ts` that reaches +R_TAKE_PROFIT or -R_STOP_LOSS.
    `bars` are priced on the exit side (bid for a Buy, ask for a Sell). Exit is at the limit price exactly; a bar touching both
    counts as the stop. None if neither was reached."""
    sign = 1.0 if trade_type == "Buy" else -1.0
    for row in bars[bars["datetime"] > open_ts].itertuples():
        best = (row.high - entry_price) if sign > 0 else (entry_price - row.low)
        worst = (entry_price - row.low) if sign > 0 else (row.high - entry_price)
        if worst >= R_STOP_LOSS:
            return entry_price - sign * R_STOP_LOSS, row.datetime.to_pydatetime(), "SL"
        if best >= R_TAKE_PROFIT:
            return entry_price + sign * R_TAKE_PROFIT, row.datetime.to_pydatetime(), "TP"
    return None


def _find_exit(trade: dict, spot: float, now: datetime) -> tuple[float, datetime, str] | None:
    try:
        side = "bid" if trade["trade_type"] == "Buy" else "ask"
        minutes = int((now - trade["open_ts"]).total_seconds() // 60) + 5
        bars = fetch_gold_bars(max(10, min(minutes, EXIT_BARS_MAX)), side)
        return scan_exit(trade["trade_type"], trade["entry_price"], trade["open_ts"], bars)
    except Exception:
        log.exception("Broker R: 1-minute bars unavailable -- point check on spot")
    pnl = _pnl(trade["trade_type"], trade["entry_price"], spot)
    if pnl <= -R_STOP_LOSS:
        return spot, now, "SL"
    if pnl >= R_TAKE_PROFIT:
        return spot, now, "TP"
    return None


def _open_message(trade_id: int, trade_type: str, rule: str, price: float, run: str, open_ts: datetime) -> str:
    return (
        f"{TRADE_ALERT_PREFIX}BROKER R #{trade_id}: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule}).\n"
        f"Exits: take-profit +${R_TAKE_PROFIT:g} / stop-loss -${R_STOP_LOSS:g}, nothing else. Level from short-range TA {run}.\n"
        f"Filled: {_format_ts(open_ts)}"
    )


def _close_message(trade: dict, exit_price: float, exit_ts: datetime, pnl: float, reason: str) -> str:
    marker = PROFIT_MARKER if pnl >= 0 else LOSS_MARKER
    why = {"TP": "take-profit", "SL": "stop-loss", "Stop": "closed by stop trading / pause"}[reason]
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{marker}{'P' if pnl >= 0 else 'L'} ${abs(pnl):.2f} BROKER R #{trade['id']}: closed "
        f"{trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} (opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}, {why})\n"
        f"Filled: {_format_ts(exit_ts)}"
    )


def find_fresh_touch(scenario_name: str, scenario: dict, bars: pd.DataFrame, now: datetime):
    """First touch of the level that is still young enough to fill, else None. A touch older than the age limit is skipped, and
    the scan continues after it -- price must retreat and touch again, exactly Broker B's fresh-approach rule."""
    while True:
        touch = _scan_zone_entry(scenario_name, scenario, bars)
        if touch is None:
            return None
        price, ts = touch
        if (now - ts).total_seconds() / 60 <= ENTRY_MAX_TOUCH_AGE_MINUTES:
            return price, ts
        log.info("Broker R: %s touch at %s is older than %d min -- not filled retroactively", scenario_name, ts, ENTRY_MAX_TOUCH_AGE_MINUTES)
        bars = bars[bars["datetime"] > ts]


def armed_scenarios(forecast: dict, now: datetime, history_fn=trade_r_level_history) -> list[tuple[str, str, str, dict]]:
    """(scenario_name, trade_type, rule_name, scenario) for each level of a young, same-day forecast still within its trade budget."""
    if forecast.get("forecast_date") != now.astimezone(DISPLAY_TZ).date():
        return []
    if now - forecast["ts"] > timedelta(minutes=R_TA_FORECAST_MAX_AGE_MINUTES):
        return []
    scenarios = {s["name"]: s for s in (forecast.get("levels") or {}).get("scenarios", [])}
    out = []
    for name, (trade_type, rule) in SCENARIOS.items():
        sc = scenarios.get(name)
        if sc is None:
            continue
        history = history_fn(forecast["id"], rule, 0.0)  # a win is any close at +R_TAKE_PROFIT; a stop-out retires the level
        if history["stopped_out"] or history["count"] >= R_MAX_TRADES_PER_LEVEL:
            continue
        out.append((name, trade_type, rule, sc))
    return out


def check_broker_r_trades(prices: dict[str, float]) -> None:
    """Once per poll: close the open trade at +$5 / -$5 if a 1-minute bar reached either, then (in the trading window, flat) open a
    fade if a young short-range forecast's level was freshly touched."""
    spot = prices.get("gold")
    if spot is None:
        return
    now = datetime.now(timezone.utc)

    trade = get_open_trade_r()
    if trading_pause_reason(now, "R") is not None:
        if trade is not None:
            pnl = _pnl(trade["trade_type"], trade["entry_price"], spot)
            close_trade_row_r(trade["id"], spot, now, pnl, "Stop", {"reason": "Stop"})
            send_telegram_message(_close_message(trade, spot, now, pnl, "Stop"))
        return
    if trade is not None:
        result = _find_exit(trade, spot, now)
        if result is None:
            return
        exit_price, exit_ts, reason = result
        pnl = _pnl(trade["trade_type"], trade["entry_price"], exit_price)
        close_trade_row_r(
            trade["id"], exit_price, exit_ts, pnl, reason,
            {"reason": reason, "held_minutes": round((exit_ts - trade["open_ts"]).total_seconds() / 60, 1)},
        )
        send_telegram_message(_close_message(trade, exit_price, exit_ts, pnl, reason))

    if not in_window(now):
        return
    forecast = get_latest_ta_short_forecast()
    if forecast is None or not forecast.get("levels"):
        return
    armed = armed_scenarios(forecast, now)
    if not armed:
        return

    floor_ts = forecast["ts"]
    last_close = get_last_close_ts_r()
    if last_close is not None and last_close > floor_ts:
        floor_ts = last_close
    bars_by_side: dict[str, pd.DataFrame] = {}
    touches = []
    for name, trade_type, rule, scenario in armed:
        side = _entry_side(trade_type)
        if side not in bars_by_side:
            bars_by_side[side] = fetch_gold_bars(ENTRY_CANDLE_LOOKBACK_MINUTES, side)
        # Like Broker B: only bars after the forecast's own ts and after the last close can open a trade.
        bars = bars_by_side[side]
        bars = bars[bars["datetime"] > floor_ts]
        touch = find_fresh_touch(name, scenario, bars, now)
        if touch is not None:
            touches.append((touch[1], touch[0], trade_type, rule, name))
    if not touches:
        return
    touches.sort(key=lambda t: t[0])
    trigger_ts, trigger_price, trade_type, rule, name = touches[0]
    run = (forecast["levels"] or {}).get("session", "?")
    context = {
        "scenario": name, "trigger_price": round(float(trigger_price), 2), "spot_at_poll": round(float(spot), 2),
        "minutes_since_touch": round((now - trigger_ts).total_seconds() / 60, 1),
        "hour_et": now.astimezone(DISPLAY_TZ).hour, "minute_et": now.astimezone(DISPLAY_TZ).minute,
    }
    trade_id = insert_trade_r(
        rule, trade_type, trigger_price, trigger_ts, f"short-range TA {run}, {name} @ ${trigger_price:.2f}", forecast["id"], context
    )
    send_telegram_message(_open_message(trade_id, trade_type, rule, trigger_price, run, trigger_ts))
