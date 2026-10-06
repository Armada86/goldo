"""Automated paper-trading engine for Broker B's rules -- an independent twin of broker.py's Broker
A, trading the latest XAU/USD technical-analysis forecast's price levels instead of Broker A's
alert-consensus signal. This module is the spec; `CLAUDE.md` holds the history, and
.claude/agents/broker.md lists the rule names.

Entirely separate from Broker A: its own `broker_b_trades` table, its own open-trade tracking, its own
Telegram identity (blue square vs. Broker A's blue circle). They share one thing, deliberately, so the
two can't drift apart the way forex_broker.py already shares Broker A's exit logic: the exit mechanics
(broker._pnl()/_exit_levels()/_scan_exit_crossing()/_find_exit(), imported directly). Unlike Broker A,
Broker B does **not** apply a TA-forecast bias gate (Broker A's, `broker._bias_allows()`, was removed 30 Sep 2026) -- it trades whichever of
the forecast's four levels price actually reaches, buy or sell, regardless of the forecast's overall
directional read. That gate is Broker A-only.

**Trades all four of the forecast's levels**, not just the two fade zones: the two "fade the nearest
zone" scenarios (`sell_resistance`/`buy_support`) *and* their mirrored breakout scenarios
(`bull_breakout`/`bear_breakdown`) -- see ZONE_SCENARIOS below for the name/trade-type/rule-name
mapping. Only one trade open at a time, across all four rules, same as Broker A -- a fresh entry is
never opened while a Broker B position is already open, no matter which of the four levels it is.
**Each level re-arms after a win**: up to MAX_TRADES_PER_LEVEL (2) trades per (forecast, level), but
the first stop-out at a level retires it for that forecast (trade_b_level_history()). A re-arm only
fires on a genuine fresh touch -- price must be observed back on the away side of the trigger at
some point after the previous trade closed before the next touch counts (see
_scan_zone_entry()'s approach-side check). Without this, a level that broke out and kept running
(never came back) would have every later candle's high/low still trivially satisfy "touched",
opening back-to-back phantom trades at the same stale price on successive polls -- observed live on
25 Sep 2026: TA-Breakout-buy opened three "Buy @ $4293.21" trades in ~30 minutes although the real
price never returned below $4293.21 after the first one closed; the second and third were bogus.

**Entry is a candle scan, mirroring Broker A's exit scan exactly (same technique, same reasoning).**
check_broker_b_trades() runs once per poll (every 5 minutes); a naive "is the live spot price at the
level right now" check could miss a real touch entirely if price crossed it and back between polls.
_scan_zone_entry() fetches real 1-minute OHLC candles (fetch_candles(), Twelve Data) covering the last
ENTRY_CANDLE_LOOKBACK_MINUTES and scans each bar's high/low for the first point that actually reached
the level -- the trade opens at that exact level (the price a resting order would have filled at, the
same way a real platform would fill it -- see broker._exit_levels()'s "exit_price is the level, not
the overshoot" convention, which this mirrors for entries), not at whatever the live spot price happens
to be when the poll notices. Candles at or before the last Broker B trade's close **and** at or before
the current forecast's own `ts` are ignored, so a touch can never open a back-dated trade -- the first
guards a re-armed level re-firing on the very touch that opened its previous trade (still inside the
20-min lookback); the second guards a brand-new forecast whose levels happen to coincide with a price
the market already touched minutes earlier (common, since levels are usually derived from recent swing
points near the current price) from retroactively "finding" that already-past touch and opening a
trade timestamped before the forecast that supposedly produced it even existed. Fixed 25 Sep 2026 after
exactly that: the Midday forecast (ts 16:00:39 UTC) landed with a `buy_support` zone at
$4283.21/stop $4273.21 -- prices gold had already touched at 15:43-15:51 UTC, 9-17 minutes earlier --
and `TA-Zone-buy`/`TA-Breakout-sell` both opened citing that forecast with `open_ts` stamped before it
was generated. For the two fade scenarios only, a level reached
after price already blew through the zone's far side (the scenario's own `stop`) without a clean touch
first invalidates that fade -- see _entry_price_and_invalidation(). The two breakout scenarios have no
such invalidation: crossing the trigger is the entire signal.

An earlier version (24 Sep 2026) added a $2 entry tolerance, treating a level as reached once price
came within $2 of it, to cover the routine $1-2 gap between Twelve Data (this engine's feed) and a
broker platform's own feed. Removed at the user's explicit request the next day -- back to an exact
touch.

**Three entry filters, added 25 Sep 2026 after analyzing a live double-loss** (TA-Zone-sell sold
$4,283.21 resistance at 9:01pm ET while DXY was already sliding -- a real tailwind, not a fakeout --
so price ran through the zone to $4,295.53 before the immediate TA-Breakout-buy also stopped out on
the round-trip back down, all inside the 9-11pm ET window, the market's thinnest liquidity stretch):

1. **Trading-hours window** (`_within_entry_window()`): no *new* entries outside
   `ENTRY_WINDOW_START_ET`-`ENTRY_WINDOW_END_ET` (7:00am-5:00pm ET, weekdays only).
   Both incident trades opened at 9pm ET -- outside this window alone would have blocked both. Exits
   are never gated by this -- an open Broker B trade still gets managed to its $10 take-profit / $10 stop-loss exit at any hour,
   the same way Broker A's exits and forex_broker.py's close-check both run around the clock; only a
   *fresh* entry waits for the window.
2. **DXY confirmation** (`_dxy_confirms()`): a Buy is skipped if DXY has risen by at least its own
   calibrated 15-minute swing threshold (`config.INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][15]`, from
   `intrahour_swing_thresholds.json` -- reusing the project's existing calibration rather than a new
   arbitrary number) over the trailing 15 minutes; a Sell is skipped if DXY has *fallen* by that much.
   Gold and DXY move inversely, so this blocks a fade into a real, live macro headwind/tailwind --
   exactly what let the incident's short get run over (DXY was already easing before that Sell fired).
3. **RSI exhaustion, breakout rules only** (`_rsi_confirms()`): `TA-Breakout-buy` is skipped if gold's
   RSI(14) (`data_fetcher.fetch_gold_rsi_adx()`, `config.RSI_PERIOD`, the same 15-min-candle
   computation `rules.check_rsi_alerts()` uses) is already >= `RSI_OVERBOUGHT_THRESHOLD` (70) --
   don't chase a rally that's already stretched. `TA-Breakout-sell` is skipped, mirrored, if RSI is
   already <= `RSI_OVERSOLD_THRESHOLD` (30). Left off the two fade rules (`TA-Zone-sell`/`TA-Zone-buy`):
   an extended RSI at the level being faded is not obviously wrong for a fade the way it is for a
   breakout being chased.

4. **ADX regime switch, all four rules** (`_adx_confirms()`, 1 Oct 2026): ADX(14) on the same 15-min
   candles as RSI (`data_fetcher.fetch_gold_rsi_adx()`; `config.ADX_TRENDING_THRESHOLD` 25 /
   `ADX_CHOP_THRESHOLD` 20). The two fade rules are skipped when ADX >= 25 *and still rising* (6 Oct 2026: a strong but fading ADX
   lets the fade through; ADX vs the previous 15-min candle); the two breakout rules are skipped when ADX < 20 (no trend to follow through).
   The RSI exhaustion gate above is NOT waived by ADX (waiver removed 6 Oct 2026, after #45). Blocks send the normal deduplicated Telegram notice.

5. **RSI exhaustion on the fades + high-volatility (ATR) gate** (3 Oct 2026, after Friday 2 Oct's review: 4 of 5
   trades stopped out, all with ATR(14) $11-13; both fade-sells taken at RSI >= ~69 lost). `_rsi_confirms()` now
   also blocks `TA-Zone-sell` at RSI >= `FADE_RSI_OVERBOUGHT_THRESHOLD` (68) and `TA-Zone-buy` at RSI <=
   `FADE_RSI_OVERSOLD_THRESHOLD` (32) (no ADX waiver);
   `_atr_confirms()` blocks all four rules when 15-min ATR(14) >= `ATR_HIGH_VOLATILITY_THRESHOLD` ($12).
   Cutoffs are hand-picked from a 15-trade sample -- calibrate from `entry_context` (`rsi14`, `atr14`) / `start SLA`.
   Every block sends the normal deduplicated ⛔ Telegram notice.

**The DXY/ADX/RSI/ATR thresholds are not read from config.py directly (4 Oct 2026)**: they come from the newest row of the Postgres
`block_rules` table via `block_rules.get_block_rules()` (once per poll, passed to the four `_*_confirms()` gates as `rules`), which the Telegram
`start BRA` analysis (`block_rules_analysis.py`) rewrites; config.py's values are the first seed and the per-value fallback. The numbers quoted
in this docstring are those defaults.

All filters fail open (no block) on a DB/API hiccup or missing data, the same convention as Broker A's
filters -- a data problem should degrade Broker B toward its old
unfiltered behavior, never toward refusing to trade at all. Applied per-candidate touch, earliest
first: if the earliest touch fails a gate, the next-earliest touch (a different rule) is tried instead
of giving up the whole poll.

**A Telegram notice when a level is reached but blocked, added 25 Sep 2026** (same request as the
filters above): whenever a level would otherwise have opened a trade but a filter stopped it, one
message names which level, and which of timing/DXY/RSI stopped it, e.g. "TA-Zone-sell level $4283.21
reached but blocked -- DXY fell -0.0680 in 15 min (fresh tailwind, threshold 0.0532)." Two paths:
- **DXY/RSI blocks** (`_notify_gate_block()`): raised from the normal per-touch loop below, using the
  real candle-scan touch already computed -- no extra cost.
- **Timing blocks** (`_notify_timing_block()`): raised when outside the entry window, using a cheap
  point check against this poll's already-fetched spot price (`prices["gold"]`) instead of a real
  candle scan -- fetching 1-minute candles on every one of the ~16 off-hours a day just to report a
  timing block would add ~190 Twelve Data calls/day, most of this project's 800/day free-tier cap,
  for a notice that isn't opening a trade anyway. This point check is coarser than the real scan (no
  invalidation check against the scenario's own stop), which is fine for a heads-up notice but means
  it isn't a claim that a real touch definitely happened.

Both are deduplicated in Postgres (`storage.record_broker_b_blocked_if_new()`, keyed on
`(ta_forecast_id, rule_name, reasons)`, `broker_b_blocked` table) so a level sitting past its trigger
for hours -- the exact scenario that motivated this, price idling outside trading hours -- sends one
notice, not one every 5-minute poll; a *different* reasons string (DXY blocks it, then later RSI does)
still gets its own notice, since that's genuinely new information.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from broker import (
    ENTRY_WINDOW_END_ET,
    ENTRY_WINDOW_START_ET,
    _find_exit,
    _format_ts,
    _result_marker,
    _pnl,
    _within_entry_window,
)
from trading_control import trading_pause_reason
from config import (
    ADX_PERIOD,
    ATR_PERIOD,
    RSI_PERIOD,
)
from block_rules import default_rules, get_block_rules
from data_fetcher import (
    fetch_gold_candles,
    gold_rsi_adx_atr_from_candles,
    gold_adx_rising_from_candles,
)
from entry_context import build_entry_context, level_distances
from price_bars import fetch_gold_bars
from notifier import send_telegram_message
from storage import (
    close_trade_row_b,
    get_latest_ta_forecast,
    get_last_close_ts_b,
    get_open_trade_b,
    get_ta_forecast_levels,
    get_recent_readings,
    insert_trade_b,
    get_last_blocked_touch_ts_b,
    record_broker_b_blocked_if_new,
    trade_b_level_history,
)

DISPLAY_TZ = ZoneInfo("America/New_York")

# No *new* Broker B entry outside this window (weekdays only) -- see module docstring's "Trading-hours
# window" entry. Existing open trades are exempt; only fresh entries wait for it.

# How far back the DXY confirmation check looks for a net move against the trade -- see module
# docstring's "DXY confirmation" entry. Matches the fastest calibrated companion-swing window
# (INTRAHOUR_SWING_WINDOWS_MINUTES' shortest longer tier) rather than a made-up number.
DXY_CONFIRM_WINDOW_MINUTES = 15

# How many minutes of 1-min candles the entry scan pulls each poll -- same value/reasoning as
# broker.py's EXIT_CANDLE_LOOKBACK_MINUTES: comfortably more than one 5-min poll interval, so a
# slightly late-firing poll still has full coverage back to the last check.
ENTRY_CANDLE_LOOKBACK_MINUTES = 20

# A level can be traded up to this many times off one forecast row -- but only re-arms after a
# winning trade there. The first stop-out at a level means it broke, and it's never re-traded off
# that forecast (otherwise a fade stopped out above resistance would re-enter immediately, with
# price still above the level).
MAX_TRADES_PER_LEVEL = 2

# A touch is only filled if the poll noticing it is at most this old (poll interval + slack for a slow
# run) -- a touch older than that was missed or blocked on an earlier poll, and filling it now means a
# stale price at a stale timestamp. Observed live 30 Sep 2026: TA-Breakout-buy filled at 9:04 ET a
# touch stamped 8:44 (see the blocked-touch consumption note in check_broker_b_trades()).
ENTRY_MAX_TOUCH_AGE_MINUTES = 7

# Prefix for every Broker B open/close Telegram message -- a deep-blue square, the same blue family
# as Broker A's circle (broker.TRADE_ALERT_PREFIX) but a different shape so the two are easy to tell
# apart in the chat at a glance. (There's no darker-blue circle emoji.) Broker B uses squares only:
# close messages add a green/red square for profit/loss after it (vs. Broker A's circles).
TRADE_ALERT_PREFIX = "\U0001f7e6 "  # blue square
PROFIT_MARKER = "\U0001f7e9 "  # green square
LOSS_MARKER = "\U0001f7e5 "  # red square
BLOCKED_MARKER = "⛔ "  # no-entry sign -- a level was reached but a filter stopped the trade

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


def _entry_side(trade_type: str) -> str:
    """Which side of the spread a level touch is judged on: a Buy fills on the ask, a Sell on the bid."""
    return "ask" if trade_type == "Buy" else "bid"


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


def _scan_zone_entry(
    scenario_name: str, scenario: dict, candles
) -> tuple[float, datetime] | None:
    """Scans 1-min candles in chronological order for the first bar whose high/low actually reached
    this scenario's level without the same or an earlier bar already invalidating it -- same
    technique as broker._scan_exit_crossing(), applied to an entry instead of an exit. Returns
    (trigger_price, trigger_ts) at the first qualifying bar, or None if the level was never cleanly
    reached (or was invalidated before/without one) in `candles`.

    A touch only counts once price has first been seen on the approach side of the trigger somewhere
    in `candles` (below it for the rising-approach scenarios, above it for the falling ones), for every
    rule and every trade, first or re-armed. Otherwise price already past the level (e.g. when the
    forecast landed, or after a breakout that kept running) would have every bar's high/low still
    satisfy "touched" and open a phantom trade at the stale trigger price. The touching bar's own
    low/high counts as the approach, so a genuine fresh cross within one bar still fires."""
    trigger_price, invalidation_price = _entry_price_and_invalidation(scenario_name, scenario)
    rising = scenario_name in RISING_APPROACH_SCENARIOS
    retreated = False
    for _, bar in candles.iterrows():
        if rising:
            touched = bar["high"] >= trigger_price
            invalidated = invalidation_price is not None and bar["high"] >= invalidation_price
            away = bar["low"] < trigger_price
        else:
            touched = bar["low"] <= trigger_price
            invalidated = invalidation_price is not None and bar["low"] <= invalidation_price
            away = bar["high"] > trigger_price
        if away:
            retreated = True
        if touched and not invalidated and retreated:
            return trigger_price, bar["datetime"].to_pydatetime()
        if invalidated:
            return None
    return None


def _dxy_confirms(
    trade_type: str, readings: list, rules: dict | None = None
) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ok unless DXY has just made a real move against this trade, see
    module docstring's "DXY confirmation" entry; both set only when ok is False. `category` is a
    fixed string with no live numbers in it, safe to use as the blocked-entry dedup key (see
    _notify_blocked() -- a reason string that changes every poll, e.g. by embedding the live DXY
    delta, would defeat that dedup and re-send every poll); `detail` carries the actual numbers, for
    the Telegram text only. `readings` is the caller's single shared get_recent_readings("dxy",
    DXY_CONFIRM_WINDOW_MINUTES) fetch (a poll may check several touches; fetching once and passing it
    in avoids repeating that DB read per touch). Fails open (ok=True) on missing/insufficient data,
    the same fail-open convention as the other filters. `rules` is the active block_rules dict (block_rules.py);
    None means the config.py defaults."""
    if not readings or len(readings) < 2:
        return True, None, None
    try:
        threshold = (rules or default_rules())["dxy_threshold"]
    except Exception:
        return True, None, None
    change = readings[-1][1] - readings[0][1]
    # Gold and DXY move inversely: a Buy wants DXY not rising (no fresh headwind), a Sell wants DXY
    # not falling (no fresh tailwind).
    if trade_type == "Buy":
        if change >= threshold:
            return (
                False,
                "DXY rose against the Buy (fresh headwind)",
                f"DXY rose {change:+.4f} in {DXY_CONFIRM_WINDOW_MINUTES} min "
                f"(fresh headwind, threshold {threshold:.4f})",
            )
        return True, None, None
    if change <= -threshold:
        return (
            False,
            "DXY fell against the Sell (fresh tailwind)",
            f"DXY fell {change:+.4f} in {DXY_CONFIRM_WINDOW_MINUTES} min "
            f"(fresh tailwind, threshold {threshold:.4f})",
        )
    return True, None, None


def _adx_confirms(
    scenario_name: str, adx_value: float | None, rules: dict | None = None, adx_rising: bool | None = None
) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ADX(14) as a regime switch: the two fade rules bet on a level holding,
    so they're blocked in a real trend (ADX >= ADX_TRENDING_THRESHOLD); the two breakout rules bet on
    a level breaking with follow-through, so they're blocked when there's no trend (ADX <
    ADX_CHOP_THRESHOLD). The fade block only applies while the trend is still
    strengthening (6 Oct 2026): `adx_rising` (data_fetcher.gold_adx_rising_from_candles(): ADX above the
    previous 15-min candle's) is False when ADX is flat/falling, i.e. the trend is exhausting, and a fade
    is then allowed (ADX direction is not price direction -- a fade enters after price has run into the
    level, so +DI/-DI would nearly always read as "against" it). Unknown (None) keeps the block.
    `category` has no live number (dedup key, see _dxy_confirms()); `detail`
    carries the reading for the Telegram text only. None (couldn't compute) fails open. `rules`: see _dxy_confirms()."""
    if adx_value is None:
        return True, None, None
    rules = rules or default_rules()
    adx_chop, adx_trending = rules["adx_chop"], rules["adx_trending"]
    if scenario_name in ("bull_breakout", "bear_breakdown"):
        if adx_value >= adx_chop:
            return True, None, None
        return (
            False,
            f"ADX({ADX_PERIOD}) shows no trend for a breakout (threshold < {adx_chop:g})",
            f"ADX({ADX_PERIOD}) shows no trend for a breakout: {adx_value:.1f} (< {adx_chop:g})",
        )
    if adx_value < adx_trending:
        return True, None, None
    if adx_rising is False:
        return True, None, None  # strong but fading trend
    return (
        False,
        f"ADX({ADX_PERIOD}) shows a strong, strengthening trend against a fade (threshold >= {adx_trending:g})",
        f"ADX({ADX_PERIOD}) shows a strong, strengthening trend against a fade: {adx_value:.1f} (>= {adx_trending:g}, rising)",
    )


def _rsi_confirms(
    scenario_name: str, rsi_value: float | None, adx_value: float | None = None, rules: dict | None = None
) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ok unless a breakout scenario would chase gold's RSI(14) already
    past the overbought/oversold threshold, see module docstring's "RSI exhaustion" entry; both set
    only when ok is False. `category` has no live number (dedup key, see _dxy_confirms()'s docstring
    for why); `detail` carries the actual reading, for the Telegram text only. Only gates the two
    breakout rules at RSI_OVERBOUGHT/OVERSOLD_THRESHOLD and, since 3 Oct 2026, the two fade rules at the
    tighter FADE_RSI_OVERBOUGHT/OVERSOLD_THRESHOLD (a sell fade into stretched upside momentum, or a buy
    fade into stretched downside, kept losing). `rsi_value` is the caller's single shared RSI(14)
    computation (see check_broker_b_trades) -- None if it couldn't be computed this poll, which fails
    this open (ok=True). There is no ADX waiver (removed 6 Oct 2026); `adx_value` is accepted but unused. `rules`: see
    _dxy_confirms()."""
    rules = rules or default_rules()
    if scenario_name == "bull_breakout":
        threshold, over = rules["rsi_overbought"], True
    elif scenario_name == "bear_breakdown":
        threshold, over = rules["rsi_oversold"], False
    elif scenario_name == "sell_resistance":
        threshold, over = rules["fade_rsi_overbought"], True
    elif scenario_name == "buy_support":
        threshold, over = rules["fade_rsi_oversold"], False
    else:
        return True, None, None
    if rsi_value is None:
        return True, None, None
    ok = rsi_value < threshold if over else rsi_value > threshold
    if ok:
        return True, None, None
    word, cmp = ("overbought", ">=") if over else ("oversold", "<=")
    return (
        False,
        f"RSI(14) already {word} (threshold {cmp} {threshold:g})",
        f"RSI(14) already {word}: {rsi_value:.1f} ({cmp} {threshold:g})",
    )


def _atr_confirms(atr_value: float | None, rules: dict | None = None) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- blocks every rule when gold's ATR(14) on 15-min candles is at/above
    the atr_max rule: the $10 initial stop is then about one ATR wide, inside normal noise
    (Friday 2 Oct 2026: ATR $11-13, four of five trades stopped out). `category` has no live number
    (dedup key, see _dxy_confirms()); `detail` carries the reading for the Telegram text only. None
    (couldn't compute) fails open. `rules`: see _dxy_confirms()."""
    atr_max = (rules or default_rules())["atr_max"]
    if atr_value is None or atr_value < atr_max:
        return True, None, None
    return (
        False,
        f"volatility too high for the stop (ATR({ATR_PERIOD}) threshold >= ${atr_max:g})",
        f"volatility too high for the stop: ATR({ATR_PERIOD}) ${atr_value:.2f} "
        f"(>= ${atr_max:g})",
    )


# A fade trade's lock level is the entry price of the OPPOSITE fade scenario in the forecast row it came from: a TA-Zone-sell locks
# at the buy_support level, a TA-Zone-buy at the sell_resistance level (see broker._scan_exit_crossing()).
FADE_OPPOSITE_SCENARIO = {"TA-Zone-sell": "buy_support", "TA-Zone-buy": "sell_resistance"}


def _fade_lock_level(trade: dict) -> float | None:
    """The price at which an open fade trade locks its profit (see FADE_OPPOSITE_SCENARIO), or None for any other trade
    (breakouts, Telegram-opened) or if no forecast can be read -- those simply keep the ordinary trailing stop.

    Uses the LATEST forecast's opposite fade level, not the one from the forecast the trade opened under (changed 5 Oct
    2026: B #44 bought TA2's support, TA3 then moved the resistance from 4150 to 4139.78 and price reached it without the
    lock firing). Falls back to the trade's own forecast if the latest can't be read. A level that is not beyond the entry
    in the trade's favour is ignored, so a moved level can never turn the lock into a tighter-than-initial stop."""
    scenario_name = FADE_OPPOSITE_SCENARIO.get(trade.get("rule_name"))
    if scenario_name is None:
        return None
    entry = float(trade["entry_price"])
    sources = []
    try:
        latest = get_latest_ta_forecast()
        if latest and latest.get("levels"):
            sources.append(latest["levels"])
    except Exception:
        pass
    if trade.get("ta_forecast_id"):
        try:
            sources.append(get_ta_forecast_levels(trade["ta_forecast_id"]) or {})
        except Exception:
            pass
    for levels in sources:
        try:
            scenario = next(sc for sc in levels.get("scenarios", []) if sc.get("name") == scenario_name)
            level = float(_entry_price_and_invalidation(scenario_name, scenario)[0])
        except Exception:
            continue
        favourable = level - entry if trade["trade_type"] == "Buy" else entry - level
        if favourable > 0:
            return level
    return None


def _blocked_message(rule_name: str, price: float, message_reasons: str) -> str:
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{BLOCKED_MARKER}BROKER B: {rule_name} level ${price:.2f} "
        f"reached but blocked -- {message_reasons}."
    )


def _notify_blocked(
    forecast: dict,
    rule_name: str,
    trigger_price: float,
    dedup_reasons: str,
    message_reasons: str | None = None,
    touch_ts: datetime | None = None,
) -> None:
    """Sends the blocked-entry Telegram notice, unless this exact (forecast, rule, dedup_reasons) was
    already notified (see module docstring). Shared by both the real candle-scan touch path (DXY/RSI
    gates) and the cheap point-price timing-block path.

    `dedup_reasons` is what gets stored/compared for the anti-spam check -- it must stay the same
    across polls describing the *same* ongoing block, so it must never embed anything that changes
    every poll (a live clock reading, a live DXY delta) or the dedup silently never fires twice in a
    row and every poll re-sends. `message_reasons` (defaulting to `dedup_reasons` when the reason text
    is already poll-invariant, e.g. RSI/DXY's numbers are fixed to the touch that triggered them) is
    what the Telegram text actually shows, which may safely include such detail since only the first
    qualifying poll's copy is ever sent.

    `touch_ts` is when the blocked touch happened (the candle's time, or "now" for the point check);
    it's recorded even on a duplicate notice so the scan can skip that touch on later polls -- a
    blocked touch is a missed signal, never filled retroactively once the filter clears."""
    if record_broker_b_blocked_if_new(forecast["id"], rule_name, trigger_price, dedup_reasons, touch_ts):
        send_telegram_message(_blocked_message(rule_name, trigger_price, message_reasons or dedup_reasons))


def _notify_timing_block(candidates: list, gold_price: float, forecast: dict, now: datetime) -> None:
    """Cheap point-price check (this poll's already-fetched spot price, no extra API call) for
    whether price has reached one of the candidates' levels while outside the entry window -- purely
    to notify, never to open a trade (that still needs the precise candle scan, which only runs inside
    the window; see module docstring for why this path stays cheap). Dedup'd the same way as
    _notify_gate_block()."""
    local = now.astimezone(DISPLAY_TZ)
    dedup_reasons = (
        f"outside trading hours (window is "
        f"{ENTRY_WINDOW_START_ET:%H:%M}-{ENTRY_WINDOW_END_ET:%H:%M} ET, weekdays)"
    )
    message_reasons = (
        f"outside trading hours (now {local:%H:%M} ET; window is "
        f"{ENTRY_WINDOW_START_ET:%H:%M}-{ENTRY_WINDOW_END_ET:%H:%M} ET, weekdays)"
    )
    for scenario_name, _trade_type, rule_name, scenario in candidates:
        trigger_price, _invalidation = _entry_price_and_invalidation(scenario_name, scenario)
        rising = scenario_name in RISING_APPROACH_SCENARIOS
        reached = gold_price >= trigger_price if rising else gold_price <= trigger_price
        if reached:
            _notify_blocked(forecast, rule_name, trigger_price, dedup_reasons, message_reasons, touch_ts=now)


def _open_message(
    trade_id: int, trade_type: str, rule_name: str, price: float, session: str, forecast_date, open_ts: datetime
) -> str:
    return (
        f"{TRADE_ALERT_PREFIX}BROKER B #{trade_id}: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: {session} TA forecast level, {forecast_date}\n"
        f"Filled: {_format_ts(open_ts)}"
    )


def _close_message(trade: dict, exit_price: float, exit_ts: datetime, pnl: float) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{_result_marker(pnl, PROFIT_MARKER, LOSS_MARKER)}BROKER B #{trade['id']}: closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}) -- "
        f"{result} of ${abs(pnl):.2f}\n"
        f"Filled: {_format_ts(exit_ts)}"
    )


def _notify_crossed_during_trade(
    trade: dict, exit_price: float | None, exit_ts: datetime, still_open: bool = False
) -> None:
    """Best-effort notice, sent right after a trade closes, for any *other* still-armed level of the
    latest forecast that price crossed while `trade` held Broker B's single position slot. Such a
    level is deliberately never filled retroactively (its trigger price is already stale, and a fresh
    entry needs a new approach from the correct side -- see _scan_zone_entry()), so this only makes the
    skip visible. Deduplicated per (forecast, rule, this trade) via _notify_blocked(); one extra 1-min
    candle fetch. Called on every poll while the trade is still open (`still_open=True`, `exit_ts` = now) so the
    notice arrives when the level is crossed, and once more when the trade closes; both use the same dedup key,
    so only the first one is sent. Never raises -- a failure here must not affect the trade or the close."""
    try:
        forecast = get_latest_ta_forecast()
        if forecast is None or not forecast.get("levels"):
            return
        scenarios = {s["name"]: s for s in forecast["levels"].get("scenarios", [])}
        armed = []
        for scenario_name, (_trade_type, rule_name) in ZONE_SCENARIOS.items():
            scenario = scenarios.get(scenario_name)
            if scenario is None or rule_name == trade["rule_name"]:
                continue
            history = trade_b_level_history(forecast["id"], rule_name)
            if history["stopped_out"] or history["count"] >= MAX_TRADES_PER_LEVEL:
                continue
            armed.append((scenario_name, rule_name, scenario))
        if not armed:
            return
        minutes = int((exit_ts - trade["open_ts"]).total_seconds() // 60) + 3
        bars_by_side = {}
        for scenario_name, rule_name, scenario in armed:
            trigger_price, _invalidation = _entry_price_and_invalidation(scenario_name, scenario)
            side = _entry_side(ZONE_SCENARIOS[scenario_name][0])
            if side not in bars_by_side:
                bars = fetch_gold_bars(min(max(minutes, 5), 500), side)
                bars_by_side[side] = bars[(bars["datetime"] >= trade["open_ts"]) & (bars["datetime"] <= exit_ts)]
            candles = bars_by_side[side]
            if candles.empty:
                continue
            if scenario_name in RISING_APPROACH_SCENARIOS:
                crossed = candles["high"].max() >= trigger_price
            else:
                crossed = candles["low"].min() <= trigger_price
            if crossed:
                # One fixed dedup string for the "still open" and "closed" notices (the live numbers only go in the text).
                dedup_reason = f"crossed while {trade['rule_name']} trade #{trade['id']} was open; not filled retroactively"
                if still_open:
                    message = f"crossed while {trade['rule_name']} trade #{trade['id']} is open; not filled retroactively"
                else:
                    message = (
                        f"crossed while {trade['rule_name']} trade #{trade['id']} was open "
                        f"(closed @ ${exit_price:.2f}); not filled retroactively"
                    )
                _notify_blocked(forecast, rule_name, trigger_price, dedup_reason, message)
    except Exception:
        return


def check_broker_b_trades(prices: dict[str, float]) -> None:
    """Runs once per poll, independent of Broker A. Closes the open trade (if any) at the $10
    take-profit / $10 stop-loss (identical mechanism to Broker A -- see broker._find_exit()), then looks
    for a fresh entry on any of the four TA forecast levels (TA-Zone-sell/-buy,
    TA-Breakout-sell/-buy) -- no bias gate, price actually reaching a level is the entire signal --
    up to MAX_TRADES_PER_LEVEL times per (forecast, level) pair, re-arming only
    after a win there (trade_b_level_history()), and only while no Broker B trade is already open. A
    fresh entry additionally requires: the trading-hours window (_within_entry_window()), DXY not
    having just moved against the trade (_dxy_confirms()), and, for the two breakout rules only, RSI
    not already past the level being chased (_rsi_confirms()) -- see module docstring. Whenever a
    level is actually reached but one of these blocks it, a deduplicated Telegram notice names the
    level and the reason(s) (_notify_blocked()/_notify_timing_block()). See .claude/agents/broker.md's
    Broker B rules."""
    gold_price = prices.get("gold")
    if gold_price is None:
        return

    now = datetime.now(timezone.utc)
    open_trade = get_open_trade_b()

    # Telegram "stop trading" / scheduled pause (trading_control.py): close anything open at market
    # and open nothing.
    if trading_pause_reason(now) is not None:
        if open_trade is not None:
            pnl = _pnl(open_trade, gold_price)
            close_trade_row_b(open_trade["id"], gold_price, now, pnl)
            send_telegram_message(_close_message(open_trade, gold_price, now, pnl))
        return

    if open_trade is not None:
        exit_result = _find_exit(open_trade, gold_price, now, _fade_lock_level(open_trade))
        if exit_result is not None:
            exit_price, exit_ts, pnl = exit_result
            close_trade_row_b(open_trade["id"], exit_price, exit_ts, pnl)
            send_telegram_message(_close_message(open_trade, exit_price, exit_ts, pnl))
            _notify_crossed_during_trade(open_trade, exit_price, exit_ts)
            open_trade = None

    if open_trade is not None:
        # Still open: if another still-armed level was crossed meanwhile, say so now rather than only when the trade closes.
        _notify_crossed_during_trade(open_trade, None, now, still_open=True)
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

    if not _within_entry_window(now):
        # Outside 7am-5pm ET weekdays -- no candle fetch (see module docstring for the API-budget
        # reasoning), just a cheap point-price check purely to notify if a level looks reached.
        _notify_timing_block(candidates, gold_price, forecast, now)
        return

    # Entry scans use the price a resting order would fill on: the ask for a Buy, the bid for a Sell
    # (fetched at most once per side per poll) -- see price_bars.py.
    bars_by_side = {}
    for _name, _type, _rule, _scenario in candidates:
        side = _entry_side(_type)
        if side not in bars_by_side:
            try:
                bars_by_side[side] = fetch_gold_bars(ENTRY_CANDLE_LOOKBACK_MINUTES, side)
            except Exception:
                return
    # Only touches after the last Broker B trade closed can open a new one -- otherwise a re-armed
    # level would re-fire on the very touch that opened its previous trade (still inside the 20-min
    # lookback), and any level could open a back-dated trade from a touch made while flat was false.
    # Also only touches after *this* forecast's own ts -- otherwise a brand-new forecast whose levels
    # happen to coincide with a price the market already touched minutes earlier (common, since levels
    # are usually derived from recent swing points near the current price) retroactively "finds" that
    # already-past touch and opens a trade timestamped before the forecast that supposedly produced it
    # even existed. Observed live 25 Sep 2026: the Midday forecast (ts 16:00:39 UTC) landed with a
    # buy_support zone at $4283.21/stop $4273.21 -- prices gold had already touched at 15:43-15:51 UTC,
    # 9-17 minutes earlier -- and TA-Zone-buy/TA-Breakout-sell both opened citing that forecast with
    # open_ts stamped before it was generated.
    floor_ts = forecast["ts"]
    last_close_ts = get_last_close_ts_b()
    if last_close_ts is not None and last_close_ts > floor_ts:
        floor_ts = last_close_ts
    bars_by_side = {side: bars[bars["datetime"] > floor_ts] for side, bars in bars_by_side.items()}

    # A touch a filter already blocked (DXY/RSI notice, or a timing block) is a missed signal, not a
    # pending one: skip every candle at or before that rule's latest blocked touch, so it can't be
    # filled a few polls later -- at the stale trigger price and time -- just because the filter
    # cleared. Observed live 29 Sep 2026: TA-Breakout-buy's 16:40 touch was blocked at 16:46 (RSI
    # 70.9), then opened at 16:51 stamped 16:40. The level then needs a fresh approach and touch.
    try:
        blocked_floor = get_last_blocked_touch_ts_b(forecast["id"])
    except Exception:
        blocked_floor = {}

    touches = []
    for scenario_name, trade_type, rule_name, scenario in candidates:
        rule_bars = bars_by_side[_entry_side(trade_type)]
        if rule_name in blocked_floor:
            rule_bars = rule_bars[rule_bars["datetime"] > blocked_floor[rule_name]]
        touch = _scan_zone_entry(scenario_name, scenario, rule_bars)
        if touch is not None:
            trigger_price, trigger_ts = touch
            touches.append((trigger_ts, trigger_price, trade_type, rule_name, scenario_name))

    if not touches:
        return

    # DXY readings and gold's RSI are each fetched at most once per poll, however many touches there
    # are to check, rather than once per touch.
    try:
        dxy_readings = get_recent_readings("dxy", DXY_CONFIRM_WINDOW_MINUTES)
    except Exception:
        dxy_readings = None
    # One candle fetch supplies both RSI (breakout rules) and ADX (all four rules).
    # (one candle fetch also gives whether ADX is rising, for the fade gate)
    try:
        gold_candles = fetch_gold_candles()
        rsi_value, adx_value, atr_value = gold_rsi_adx_atr_from_candles(
            gold_candles, RSI_PERIOD, ADX_PERIOD, ATR_PERIOD
        )
        adx_rising = gold_adx_rising_from_candles(gold_candles, ADX_PERIOD)
    except Exception:
        rsi_value = adx_value = atr_value = adx_rising = None
    # The active block rules (block_rules table, newest row; config.py values for anything it can't supply).
    rules = get_block_rules()

    # Earliest touch first; a touch that fails a confirmation gate gets a blocked-entry notice and is
    # skipped in favor of the next-earliest one (a different rule) rather than giving up the whole
    # poll on it.
    touches.sort(key=lambda t: t[0])
    selected = None
    for trigger_ts, trigger_price, trade_type, rule_name, scenario_name in touches:
        # A touch that is blocked (filter) or too old is consumed *together with every newer bar this
        # poll already saw*, not just itself: a level chopping around its trigger produces a touch
        # nearly every minute, and consuming only the first one left a backlog that later polls worked
        # through one per poll -- the first poll where the filters cleared then filled a touch ~20
        # minutes old (observed live 30 Sep 2026: stamped 8:44, filled 9:04). The level needs a fresh
        # touch after this poll's newest bar to trade again.
        seen_bars = bars_by_side[_entry_side(trade_type)]
        consumed_through = seen_bars["datetime"].max().to_pydatetime() if len(seen_bars) else trigger_ts
        age_minutes = (now - trigger_ts).total_seconds() / 60
        if age_minutes > ENTRY_MAX_TOUCH_AGE_MINUTES:
            _notify_blocked(
                forecast,
                rule_name,
                trigger_price,
                f"touch older than {ENTRY_MAX_TOUCH_AGE_MINUTES} min when noticed (never filled retroactively)",
                f"touched {age_minutes:.0f} min ago (over {ENTRY_MAX_TOUCH_AGE_MINUTES}); "
                f"not filled retroactively",
                touch_ts=consumed_through,
            )
            continue
        dxy_ok, dxy_category, dxy_detail = _dxy_confirms(trade_type, dxy_readings, rules)
        rsi_ok, rsi_category, rsi_detail = _rsi_confirms(scenario_name, rsi_value, adx_value, rules)
        adx_ok, adx_category, adx_detail = _adx_confirms(scenario_name, adx_value, rules, adx_rising)
        atr_ok, atr_category, atr_detail = _atr_confirms(atr_value, rules)
        if dxy_ok and rsi_ok and adx_ok and atr_ok:
            selected = (trigger_ts, trigger_price, trade_type, rule_name, scenario_name)
            break
        dedup_reasons = "; ".join(r for r in (dxy_category, rsi_category, adx_category, atr_category) if r)
        message_reasons = "; ".join(r for r in (dxy_detail, rsi_detail, adx_detail, atr_detail) if r)
        _notify_blocked(
            forecast, rule_name, trigger_price, dedup_reasons, message_reasons, touch_ts=consumed_through
        )
    if selected is None:
        return
    trigger_ts, trigger_price, trade_type, rule_name, scenario_name = selected

    session = levels.get("session", "?")
    trigger_text = f"{session} TA forecast {forecast['forecast_date']}, {scenario_name} @ ${trigger_price:.2f}"
    context = build_entry_context(
        now,
        rsi_value=rsi_value,
        dxy_readings=dxy_readings,
        forecast=forecast,
        extra={
            "scenario": scenario_name,
            "trigger_price": round(float(trigger_price), 2),
            "spot_at_poll": round(float(gold_price), 2),
            "minutes_since_touch": round((now - trigger_ts).total_seconds() / 60, 1),
            **level_distances(trigger_price, forecast),
        },
    )
    trade_id = insert_trade_b(
        rule_name, trade_type, trigger_price, trigger_ts, trigger_text, forecast["id"], context
    )
    send_telegram_message(
        _open_message(trade_id, trade_type, rule_name, trigger_price, session, forecast["forecast_date"], trigger_ts)
    )
