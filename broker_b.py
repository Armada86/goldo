"""Automated paper-trading engine for Broker B's rules -- an independent twin of broker.py's Broker
A, trading the latest XAU/USD technical-analysis forecast's price zones instead of Broker A's
alert-consensus signal. See .claude/agents/broker.md's "Broker B" rules section for the human-readable
spec (kept in sync with this code by hand, same convention as Broker A's own rules).

Entirely separate from Broker A: its own `broker_b_trades` table, its own open-trade tracking, its own
Telegram identity (green circle vs. Broker A's blue) -- the two engines' positions never interact, and
neither can stack a trade while it already has one open. They do share two things, deliberately, so
the two can't drift apart the way forex_broker.py already shares Broker A's exit logic: the exit
mechanics (broker._pnl()/_exit_levels()/_scan_exit_crossing()/_find_exit(), imported directly) and the
TA-forecast bias gate (broker._bias_allows()).

**Entry is a candle scan, mirroring Broker A's exit scan exactly (same technique, same reasoning).**
check_broker_b_trades() runs once per poll (every 5 minutes); a naive "is the live spot price inside
the zone right now" check could miss a real touch entirely if price dipped into the zone and back out
between polls. _scan_zone_entry() fetches real 1-minute OHLC candles (fetch_candles(), Twelve Data)
covering the last ENTRY_CANDLE_LOOKBACK_MINUTES and scans each bar's high/low for the first point that
actually reached the zone's near edge -- the trade opens at that edge price (the level a resting limit
order would have filled at, the same way a real platform would fill it -- see broker._exit_levels()'s
"exit_price is the level, not the overshoot" convention, which this mirrors for entries), not at
whatever the live spot price happens to be when the poll notices. If that edge was reached only after
price already blew through the zone's far side (the scenario's own `stop`), the fade is no longer
valid and no trade opens.
"""

from datetime import datetime, timezone

from broker import _bias_allows, _find_exit
from config import GOLD_SPOT_SYMBOL
from data_fetcher import fetch_candles
from notifier import send_telegram_message
from storage import (
    close_trade_row_b,
    get_latest_ta_forecast,
    get_open_trade_b,
    insert_trade_b,
    trade_b_exists_for_forecast,
)

# How many minutes of 1-min candles the entry scan pulls each poll -- same value/reasoning as
# broker.py's EXIT_CANDLE_LOOKBACK_MINUTES: comfortably more than one 5-min poll interval, so a
# slightly late-firing poll still has full coverage back to the last check.
ENTRY_CANDLE_LOOKBACK_MINUTES = 20

# Prefix for every Broker B open/close Telegram message -- green, distinct from Broker A's blue
# (broker.TRADE_ALERT_PREFIX) so the two are easy to tell apart in the chat at a glance.
TRADE_ALERT_PREFIX = "\U0001f7e2 "  # green circle

# The two "fade the nearest zone" scenarios ta_forecast_job.py's build_scenarios() produces --
# see .claude/agents/broker.md's Broker B rules for why the breakout/breakdown mirror scenarios
# (bull_breakout/bear_breakdown) are deliberately not traded (yet).
ZONE_SCENARIOS = {"sell_resistance": "Sell", "buy_support": "Buy"}


def _zone_entry_price(trade_type: str, scenario: dict) -> tuple[float, float]:
    """(trigger_price, invalidation_price) for fading this zone -- the near edge price is approached
    from below for a Sell (zone's low) and from above for a Buy (zone's high); invalidation is the
    scenario's own stop, past which this is no longer a clean fade."""
    entry = scenario["entry"]
    if trade_type == "Sell":
        return entry["low"], scenario["stop"]
    return entry["high"], scenario["stop"]


def _scan_zone_entry(trade_type: str, scenario: dict, candles) -> tuple[float, datetime] | None:
    """Scans 1-min candles in chronological order for the first bar whose high/low actually reached
    this zone's near edge without the same or an earlier bar already blowing through the stop --
    same technique as broker._scan_exit_crossing(), applied to an entry instead of an exit. Returns
    (trigger_price, trigger_ts) at the first qualifying bar, or None if the zone was never cleanly
    touched (or was invalidated before/without one) in `candles`."""
    trigger_price, invalidation_price = _zone_entry_price(trade_type, scenario)
    for _, bar in candles.iterrows():
        if trade_type == "Sell":
            touched = bar["high"] >= trigger_price
            invalidated = bar["high"] >= invalidation_price
        else:
            touched = bar["low"] <= trigger_price
            invalidated = bar["low"] <= invalidation_price
        if touched and not invalidated:
            return trigger_price, bar["datetime"].to_pydatetime()
        if invalidated:
            return None
    return None


def _open_message(trade_type: str, rule_name: str, price: float, session: str, forecast_date) -> str:
    return (
        f"{TRADE_ALERT_PREFIX}BROKER B: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: {session} TA forecast zone, {forecast_date}"
    )


def _close_message(trade: dict, exit_price: float, pnl: float) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"{TRADE_ALERT_PREFIX}BROKER B: closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}) -- "
        f"{result} of ${abs(pnl):.2f}"
    )


def check_broker_b_trades(prices: dict[str, float]) -> None:
    """Runs once per poll, independent of Broker A. Closes the open trade (if any) at the $10
    take-profit/stop-loss (identical mechanism to Broker A -- see broker._find_exit()), then looks
    for a fresh TA-Zone-buy/-sell entry: price reaching the latest ta_forecasts row's resistance/
    support fade zone, gated by that same forecast's overall bias (broker._bias_allows()) and only
    once per (forecast, zone) pair (trade_b_exists_for_forecast()). See .claude/agents/broker.md's
    Broker B rules."""
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
        return  # still open (or just closed and a fresh entry is left to next poll, same as Broker A)

    try:
        forecast = get_latest_ta_forecast()
    except Exception:
        return  # best-effort: a DB hiccup here just skips this poll's entry check
    if forecast is None or not forecast.get("levels"):
        return

    levels = forecast["levels"]
    bias_score = levels.get("bias_score", 0.0)
    scenarios = {s["name"]: s for s in levels.get("scenarios", [])}

    candidates = []
    for scenario_name, trade_type in ZONE_SCENARIOS.items():
        scenario = scenarios.get(scenario_name)
        if scenario is None or not _bias_allows(trade_type, bias_score):
            continue
        rule_name = f"TA-Zone-{trade_type.lower()}"
        if trade_b_exists_for_forecast(forecast["id"], rule_name):
            continue
        candidates.append((trade_type, rule_name, scenario))

    if not candidates:
        return

    try:
        candles = fetch_candles(GOLD_SPOT_SYMBOL, interval="1min", outputsize=ENTRY_CANDLE_LOOKBACK_MINUTES)
    except Exception:
        return

    touches = []
    for trade_type, rule_name, scenario in candidates:
        touch = _scan_zone_entry(trade_type, scenario, candles)
        if touch is not None:
            trigger_price, trigger_ts = touch
            touches.append((trigger_ts, trigger_price, trade_type, rule_name))

    if not touches:
        return

    trigger_ts, trigger_price, trade_type, rule_name = min(touches, key=lambda t: t[0])
    session = levels.get("session", "?")
    scenario_name = "sell_resistance" if trade_type == "Sell" else "buy_support"
    zone = scenarios[scenario_name]["entry"]
    trigger_text = f"{session} TA forecast {forecast['forecast_date']}, zone ${zone['low']:.2f}-${zone['high']:.2f}"
    insert_trade_b(rule_name, trade_type, trigger_price, trigger_ts, trigger_text, forecast["id"])
    send_telegram_message(_open_message(trade_type, rule_name, trigger_price, session, forecast["forecast_date"]))
