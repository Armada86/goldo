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
the first stop-out at a level retires it for that forecast (trade_b_level_history()). A re-arm only
fires on a genuine fresh touch -- price must be observed back on the away side of the trigger at
some point after the previous trade closed before the next touch counts (see
_scan_zone_entry()'s `require_retreat`). Without this, a level that broke out and kept running
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

**Three entry filters, added 25 Sep 2026 after analyzing a live double-loss** (TA-Zone-sell sold
$4,283.21 resistance at 9:01pm ET while DXY was already sliding -- a real tailwind, not a fakeout --
so price ran through the zone to $4,295.53 before the immediate TA-Breakout-buy also stopped out on
the round-trip back down, all inside the 9-11pm ET window, the market's thinnest liquidity stretch):

1. **Trading-hours window** (`_within_entry_window()`): no *new* entries outside
   `ENTRY_WINDOW_START_ET`-`ENTRY_WINDOW_END_ET` (8:00am-4:00pm ET, the NY cash close, weekdays only).
   Both incident trades opened at 9pm ET -- outside this window alone would have blocked both. Exits
   are never gated by this -- an open Broker B trade still gets managed to its $10 exit at any hour,
   the same way Broker A's exits and forex_broker.py's close-check both run around the clock; only a
   *fresh* entry waits for the window.
2. **DXY confirmation** (`_dxy_confirms()`): a Buy is skipped if DXY has risen by at least its own
   calibrated 15-minute swing threshold (`config.INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][15]`, from
   `intrahour_swing_thresholds.json` -- reusing the project's existing calibration rather than a new
   arbitrary number) over the trailing 15 minutes; a Sell is skipped if DXY has *fallen* by that much.
   Gold and DXY move inversely, so this blocks a fade into a real, live macro headwind/tailwind --
   exactly what let the incident's short get run over (DXY was already easing before that Sell fired).
3. **RSI exhaustion, breakout rules only** (`_rsi_confirms()`): `TA-Breakout-buy` is skipped if gold's
   RSI(14) (`data_fetcher.fetch_gold_candles()`/`compute_rsi()`, `config.RSI_PERIOD`, the same 15-min-candle
   computation `rules.check_rsi_alerts()` uses) is already >= `RSI_OVERBOUGHT_THRESHOLD` (70) --
   don't chase a rally that's already stretched. `TA-Breakout-sell` is skipped, mirrored, if RSI is
   already <= `RSI_OVERSOLD_THRESHOLD` (30). Left off the two fade rules (`TA-Zone-sell`/`TA-Zone-buy`):
   an extended RSI at the level being faded is not obviously wrong for a fade the way it is for a
   breakout being chased.

All three fail open (no block) on a DB/API hiccup or missing data, same convention as Broker A's own
`_bias_allows()`/`_latest_bias_score()` -- a data problem should degrade Broker B toward its old
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

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from broker import _find_exit, _result_marker
from config import (
    GOLD_SPOT_SYMBOL,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_OVERSOLD_THRESHOLD,
    RSI_PERIOD,
)
from data_fetcher import compute_rsi, fetch_candles, fetch_gold_candles
from notifier import send_telegram_message
from storage import (
    close_trade_row_b,
    get_latest_ta_forecast,
    get_last_close_ts_b,
    get_open_trade_b,
    get_recent_readings,
    insert_trade_b,
    record_broker_b_blocked_if_new,
    trade_b_level_history,
)

DISPLAY_TZ = ZoneInfo("America/New_York")

# No *new* Broker B entry outside this window (weekdays only) -- see module docstring's "Trading-hours
# window" entry. Existing open trades are exempt; only fresh entries wait for it.
ENTRY_WINDOW_START_ET = time(8, 0)
ENTRY_WINDOW_END_ET = time(16, 0)  # the NY cash close

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
MAX_TRADES_PER_LEVEL = 3

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
    scenario_name: str, scenario: dict, candles, require_retreat: bool
) -> tuple[float, datetime] | None:
    """Scans 1-min candles in chronological order for the first bar whose high/low actually reached
    this scenario's level without the same or an earlier bar already invalidating it -- same
    technique as broker._scan_exit_crossing(), applied to an entry instead of an exit. Returns
    (trigger_price, trigger_ts) at the first qualifying bar, or None if the level was never cleanly
    reached (or was invalidated before/without one) in `candles`.

    `require_retreat` guards re-armed levels (see MAX_TRADES_PER_LEVEL): when True, a touch only
    counts once price has first been seen back on the away side of the trigger somewhere in
    `candles` -- otherwise a level that broke out and simply kept running (never came back) would
    have its every subsequent bar's high/low still satisfy "touched" and get treated as a brand-new
    touch on each poll, opening phantom trades at the stale trigger price. A virgin level
    (require_retreat=False) still fires on its first-ever touch, no retreat needed."""
    trigger_price, invalidation_price = _entry_price_and_invalidation(scenario_name, scenario)
    rising = scenario_name in RISING_APPROACH_SCENARIOS
    retreated = not require_retreat
    for _, bar in candles.iterrows():
        if rising:
            touched = bar["high"] >= trigger_price
            invalidated = invalidation_price is not None and bar["high"] >= invalidation_price
            away = bar["low"] < trigger_price
        else:
            touched = bar["low"] <= trigger_price
            invalidated = invalidation_price is not None and bar["low"] <= invalidation_price
            away = bar["high"] > trigger_price
        if touched and not invalidated and retreated:
            return trigger_price, bar["datetime"].to_pydatetime()
        if invalidated:
            return None
        if away:
            retreated = True
    return None


def _within_entry_window(now_utc: datetime) -> bool:
    """True on a weekday between ENTRY_WINDOW_START_ET and ENTRY_WINDOW_END_ET -- see module
    docstring's "Trading-hours window" entry. Only gates fresh entries; an already-open trade's exit
    is checked unconditionally by check_broker_b_trades() regardless of this."""
    local = now_utc.astimezone(DISPLAY_TZ)
    return local.weekday() < 5 and ENTRY_WINDOW_START_ET <= local.time() < ENTRY_WINDOW_END_ET


def _dxy_confirms(trade_type: str, readings: list) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ok unless DXY has just made a real move against this trade, see
    module docstring's "DXY confirmation" entry; both set only when ok is False. `category` is a
    fixed string with no live numbers in it, safe to use as the blocked-entry dedup key (see
    _notify_blocked() -- a reason string that changes every poll, e.g. by embedding the live DXY
    delta, would defeat that dedup and re-send every poll); `detail` carries the actual numbers, for
    the Telegram text only. `readings` is the caller's single shared get_recent_readings("dxy",
    DXY_CONFIRM_WINDOW_MINUTES) fetch (a poll may check several touches; fetching once and passing it
    in avoids repeating that DB read per touch). Fails open (ok=True) on missing/insufficient data,
    same convention as broker._latest_bias_score()."""
    if not readings or len(readings) < 2:
        return True, None, None
    try:
        threshold = INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][DXY_CONFIRM_WINDOW_MINUTES]
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


def _rsi_confirms(scenario_name: str, rsi_value: float | None) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ok unless a breakout scenario would chase gold's RSI(14) already
    past the overbought/oversold threshold, see module docstring's "RSI exhaustion" entry; both set
    only when ok is False. `category` has no live number (dedup key, see _dxy_confirms()'s docstring
    for why); `detail` carries the actual reading, for the Telegram text only. Only gates the two
    breakout rules; the two fade rules always pass. `rsi_value` is the caller's single shared RSI(14)
    computation (see check_broker_b_trades) -- None if it couldn't be computed this poll, which fails
    this open (ok=True)."""
    if scenario_name == "bull_breakout":
        threshold, over = RSI_OVERBOUGHT_THRESHOLD, True
    elif scenario_name == "bear_breakdown":
        threshold, over = RSI_OVERSOLD_THRESHOLD, False
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
        f"RSI(14) already {word} (threshold {cmp} {threshold})",
        f"RSI(14) already {word}: {rsi_value:.1f} ({cmp} {threshold})",
    )


def _blocked_message(rule_name: str, price: float, message_reasons: str) -> str:
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{BLOCKED_MARKER}BROKER B: {rule_name} level ${price:.2f} "
        f"reached but blocked -- {message_reasons}."
    )


def _notify_blocked(
    forecast: dict, rule_name: str, trigger_price: float, dedup_reasons: str, message_reasons: str | None = None
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
    qualifying poll's copy is ever sent."""
    if record_broker_b_blocked_if_new(forecast["id"], rule_name, trigger_price, dedup_reasons):
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
    for scenario_name, _trade_type, rule_name, scenario, _require_retreat in candidates:
        trigger_price, _invalidation = _entry_price_and_invalidation(scenario_name, scenario)
        rising = scenario_name in RISING_APPROACH_SCENARIOS
        reached = gold_price >= trigger_price if rising else gold_price <= trigger_price
        if reached:
            _notify_blocked(forecast, rule_name, trigger_price, dedup_reasons, message_reasons)


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
        candidates.append((scenario_name, trade_type, rule_name, scenario, history["count"] > 0))

    if not candidates:
        return

    if not _within_entry_window(now):
        # Outside 8am-4pm ET weekdays -- no candle fetch (see module docstring for the API-budget
        # reasoning), just a cheap point-price check purely to notify if a level looks reached.
        _notify_timing_block(candidates, gold_price, forecast, now)
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
    for scenario_name, trade_type, rule_name, scenario, require_retreat in candidates:
        touch = _scan_zone_entry(scenario_name, scenario, candles, require_retreat)
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
    rsi_value = None
    if any(t[4] in ("bull_breakout", "bear_breakdown") for t in touches):
        try:
            rsi_value = compute_rsi(fetch_gold_candles()["close"], period=RSI_PERIOD).dropna().iloc[-1]
        except Exception:
            rsi_value = None

    # Earliest touch first; a touch that fails a confirmation gate gets a blocked-entry notice and is
    # skipped in favor of the next-earliest one (a different rule) rather than giving up the whole
    # poll on it.
    touches.sort(key=lambda t: t[0])
    selected = None
    for trigger_ts, trigger_price, trade_type, rule_name, scenario_name in touches:
        dxy_ok, dxy_category, dxy_detail = _dxy_confirms(trade_type, dxy_readings)
        rsi_ok, rsi_category, rsi_detail = _rsi_confirms(scenario_name, rsi_value)
        if dxy_ok and rsi_ok:
            selected = (trigger_ts, trigger_price, trade_type, rule_name, scenario_name)
            break
        dedup_reasons = "; ".join(r for r in (dxy_category, rsi_category) if r)
        message_reasons = "; ".join(r for r in (dxy_detail, rsi_detail) if r)
        _notify_blocked(forecast, rule_name, trigger_price, dedup_reasons, message_reasons)
    if selected is None:
        return
    trigger_ts, trigger_price, trade_type, rule_name, scenario_name = selected

    session = levels.get("session", "?")
    trigger_text = f"{session} TA forecast {forecast['forecast_date']}, {scenario_name} @ ${trigger_price:.2f}"
    insert_trade_b(rule_name, trade_type, trigger_price, trigger_ts, trigger_text, forecast["id"])
    send_telegram_message(_open_message(trade_type, rule_name, trigger_price, session, forecast["forecast_date"]))
