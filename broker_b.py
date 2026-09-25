"""Automated paper-trading engine for Broker B's rules -- an independent twin of broker.py's Broker
A, trading the latest XAU/USD technical-analysis forecast's price levels instead of Broker A's
alert-consensus signal. See .claude/agents/broker.md's "Broker B" rules section for the human-readable
spec (kept in sync with this code by hand, same convention as Broker A's own rules).

Entirely separate from Broker A: its own `broker_b_trades` table, its own open-trade tracking, its own
Telegram identity (blue square vs. Broker A's blue circle). They share one thing, deliberately, so the
two can't drift apart the way forex_broker.py already shares Broker A's exit logic: the exit mechanics
(broker._pnl()/_exit_levels()/_scan_exit_crossing()/_find_exit(), imported directly). Unlike Broker A,
Broker B does **not** apply the TA-forecast bias gate (broker._bias_allows()) -- it trades whichever of
the forecast's four levels price actually reaches, buy or sell, regardless of the forecast's overall
directional read. That gate is Broker A-only.

**Trades all four of the forecast's levels**, not just the two fade zones: the two "fade the nearest
zone" scenarios (`sell_resistance`/`buy_support`) *and* their mirrored breakout scenarios
(`bull_breakout`/`bear_breakdown`) -- see ZONE_SCENARIOS below for the name/trade-type/rule-name
mapping. Only one trade open at a time, across all four rules, same as Broker A -- a fresh entry is
never opened while a Broker B position is already open, no matter which of the four levels it is.
**Each level re-arms after a win**: up to MAX_TRADES_PER_LEVEL (3) trades per (forecast, level), but
the first stop-out at a level retires it for that forecast (trade_b_level_history()).

**Entry is a candle scan, mirroring Broker A's exit scan exactly (same technique, same reasoning).**
check_broker_b_trades() runs once per poll (every 5 minutes); a naive "is the live spot price at the
level right now" check could miss a real touch entirely if price crossed it and back between polls.
_scan_zone_entry() fetches real 1-minute OHLC candles (fetch_candles(), Twelve Data) covering the last
ENTRY_CANDLE_LOOKBACK_MINUTES and scans each bar's high/low for the first point that actually reached
the level -- the trade opens at that exact level (the price a resting order would have filled at, the
same way a real platform would fill it -- see broker._exit_levels()'s "exit_price is the level, not
the overshoot" convention, which this mirrors for entries), not at whatever the live spot price happens
to be when the poll notices. Candles at or before the last Broker B trade's close are ignored, so a
touch can never open a back-dated trade (a re-armed level re-firing on the very touch that opened its
previous trade, still inside the 20-min lookback). For the two fade scenarios only, a level reached
after price already blew through the zone's far side (the scenario's own `stop`) without a clean touch
first invalidates that fade -- see _entry_price_and_invalidation(). The two breakout scenarios have no
such invalidation: crossing the trigger is the entire signal.

An earlier version (24 Sep 2026) added a $2 entry tolerance, treating a level as reached once price
came within $2 of it, to cover the routine $1-2 gap between Twelve Data (this engine's feed) and a
broker platform's own feed. Removed at the user's explicit request the next day -- back to an exact
touch.
"""

from datetime import datetime, timezone

from broker import _find_exit, _result_marker
from config import GOLD_SPOT_SYMBOL
from data_fetcher import fetch_candles
from notifier import send_telegram_message
from storage import (
    close_trade_row_b,
    get_latest_ta_forecast,
    get_last_close_ts_b,
    get_open_trade_b,
    insert_trade_b,
    trade_b_level_history,
)

# How many minutes of 1-min candles the entry scan pulls each poll -- same value/reasoning as
# broker.py's EXIT_CANDLE_LOOKBACK_MINUTES: comfortably more than one 5-min poll interval, so a
# slightly late-firing poll still has full coverage back to the last check.
ENTRY_CANDLE_LOOKBACK_MINUTES = 20

# A level can be traded up to this many times off one forecast row -- but only re-arms after a
# winning trade there. The first stop-out at a level means it broke, and it's never re-traded off
# that forecast (otherwise a fade stopped out above resistance would re-enter immediately, with
# price still above the level).
MAX_TRADES_PER_LEVEL = 3

# Prefix for every Broker B open/close Telegram message -- a deep-blue square, the same blue family
# as Broker A's circle (broker.TRADE_ALERT_PREFIX) but a different shape so the two are easy to tell
# apart in the chat at a glance. (There's no darker-blue circle emoji.) Broker B uses squares only:
# close messages add a green/red square for profit/loss after it (vs. Broker A's circles).
TRADE_ALERT_PREFIX = "\U0001f7e6 "  # blue square
PROFIT_MARKER = "\U0001f7e9 "  # green square
LOSS_MARKER = "\U0001f7e5 "  # red square

# All four scenarios ta_forecast_job.py's build_scenarios() produces, each mapped to its trade
# direction and a stable, distinct rule name -- see .claude/agents/broker.md's Broker B rules.
# sell_resistance/buy_support keep their original names (trades already exist against them); the
# breakout mirrors get their own names rather than sharing "TA-Zone-buy/-sell" with the fade rules,
# so the per-forecast dedup below can't confuse a fade trade with a breakout trade at a different price.
ZONE_SCENARIOS = {
    "sell_resistance": ("Sell", "TA-Zone-sell"),
    "buy_support": ("Buy", "TA-Zone-buy"),
    "bull_breakout": ("Buy", "TA-Breakout-buy"),
    "bear_breakdown": ("Sell", "TA-Breakout-sell"),
}

# Scenarios whose level is reached by a rising price (checked against each candle's high); the other
# two (buy_support, bear_breakdown) are reached by a falling price (checked against each candle's
# low). sell_resistance and bull_breakout are literally the same price (a fade's zone and its own
# breakout mirror share one trigger level, approached from the same direction), and likewise for
# buy_support/bear_breakdown -- see ta_forecast_job.py's build_scenarios().
RISING_APPROACH_SCENARIOS = {"sell_resistance", "bull_breakout"}


def _entry_price_and_invalidation(scenario_name: str, scenario: dict) -> tuple[float, float | None]:
    """(trigger_price, invalidation_price) for this scenario. The two fade scenarios have an `entry`
    zone (its near edge is the trigger) and their own `stop` as the invalidation level -- price
    already broke the zone's far side without a clean touch first, so it's no longer a valid fade.
    The two breakout scenarios have a `trigger` level directly and no invalidation -- crossing it is
    the entire signal, nothing before it can invalidate the setup."""
    if scenario_name == "sell_resistance":
        return scenario["entry"]["low"], scenario["stop"]
    if scenario_name == "buy_support":
        return scenario["entry"]["high"], scenario["stop"]
    return scenario["trigger"], None  # bull_breakout / bear_breakdown


def _scan_zone_entry(scenario_name: str, scenario: dict, candles) -> tuple[float, datetime] | None:
    """Scans 1-min candles in chronological order for the first bar whose high/low actually reached
    this scenario's level without the same or an earlier bar already invalidating it -- same
    technique as broker._scan_exit_crossing(), applied to an entry instead of an exit. Returns
    (trigger_price, trigger_ts) at the first qualifying bar, or None if the level was never cleanly
    reached (or was invalidated before/without one) in `candles`."""
    trigger_price, invalidation_price = _entry_price_and_invalidation(scenario_name, scenario)
    rising = scenario_name in RISING_APPROACH_SCENARIOS
    for _, bar in candles.iterrows():
        if rising:
            touched = bar["high"] >= trigger_price
            invalidated = invalidation_price is not None and bar["high"] >= invalidation_price
        else:
            touched = bar["low"] <= trigger_price
            invalidated = invalidation_price is not None and bar["low"] <= invalidation_price
        if touched and not invalidated:
            return trigger_price, bar["datetime"].to_pydatetime()
        if invalidated:
            return None
    return None


def _open_message(trade_type: str, rule_name: str, price: float, session: str, forecast_date) -> str:
    return (
        f"{TRADE_ALERT_PREFIX}BROKER B: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: {session} TA forecast level, {forecast_date}"
    )


def _close_message(trade: dict, exit_price: float, pnl: float) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{_result_marker(pnl, PROFIT_MARKER, LOSS_MARKER)}BROKER B: closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}) -- "
        f"{result} of ${abs(pnl):.2f}"
    )


def check_broker_b_trades(prices: dict[str, float]) -> None:
    """Runs once per poll, independent of Broker A. Closes the open trade (if any) at the $10
    take-profit/stop-loss (identical mechanism to Broker A -- see broker._find_exit()), then looks
    for a fresh entry on any of the four TA forecast levels (TA-Zone-sell/-buy,
    TA-Breakout-sell/-buy) -- no bias gate, price actually reaching a level is the entire signal --
    up to MAX_TRADES_PER_LEVEL times per (forecast, level) pair, re-arming only
    after a win there (trade_b_level_history()), and only while no Broker B trade is already open. See .claude/agents/broker.md's Broker B rules."""
    gold_price = prices.get("gold")
    if gold_price is None:
        return

    now = datetime.now(timezone.utc)
    open_trade = get_open_trade_b()

    if open_trade is not None:
        exit_result = _find_exit(open_trade, gold_price, now)
        if exit_result is not None:
            exit_price, exit_ts, pnl = exit_result
            close_trade_row_b(open_trade["id"], exit_price, exit_ts, pnl)
            send_telegram_message(_close_message(open_trade, exit_price, pnl))
            open_trade = None

    if open_trade is not None:
        return  # still open -- only one Broker B position at a time, across all four rules

    try:
        forecast = get_latest_ta_forecast()
    except Exception:
        return  # best-effort: a DB hiccup here just skips this poll's entry check
    if forecast is None or not forecast.get("levels"):
        return

    levels = forecast["levels"]
    scenarios = {s["name"]: s for s in levels.get("scenarios", [])}

    candidates = []
    for scenario_name, (trade_type, rule_name) in ZONE_SCENARIOS.items():
        scenario = scenarios.get(scenario_name)
        if scenario is None:
            continue
        history = trade_b_level_history(forecast["id"], rule_name)
        if history["stopped_out"] or history["count"] >= MAX_TRADES_PER_LEVEL:
            continue
        candidates.append((scenario_name, trade_type, rule_name, scenario))

    if not candidates:
        return

    try:
        candles = fetch_candles(GOLD_SPOT_SYMBOL, interval="1min", outputsize=ENTRY_CANDLE_LOOKBACK_MINUTES)
    except Exception:
        return
    # Only touches after the last Broker B trade closed can open a new one -- otherwise a re-armed
    # level would re-fire on the very touch that opened its previous trade (still inside the 20-min
    # lookback), and any level could open a back-dated trade from a touch made while flat was false.
    last_close_ts = get_last_close_ts_b()
    if last_close_ts is not None:
        candles = candles[candles["datetime"] > last_close_ts]

    touches = []
    for scenario_name, trade_type, rule_name, scenario in candidates:
        touch = _scan_zone_entry(scenario_name, scenario, candles)
        if touch is not None:
            trigger_price, trigger_ts = touch
            touches.append((trigger_ts, trigger_price, trade_type, rule_name, scenario_name))

    if not touches:
        return

    trigger_ts, trigger_price, trade_type, rule_name, scenario_name = min(touches, key=lambda t: t[0])
    session = levels.get("session", "?")
    trigger_text = f"{session} TA forecast {forecast['forecast_date']}, {scenario_name} @ ${trigger_price:.2f}"
    insert_trade_b(rule_name, trade_type, trigger_price, trigger_ts, trigger_text, forecast["id"])
    send_telegram_message(_open_message(trade_type, rule_name, trigger_price, session, forecast["forecast_date"]))
