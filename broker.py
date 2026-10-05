"""Automated paper-trading engine for Broker A's rules (Consensus5of7).

This module is the spec and what executes it every poll; `CLAUDE.md` holds the narrative history.
`.claude/agents/broker.md` only lists rule names (stable ids stored in `rule_name`) and tables, so keep
those names stable.
See broker_b.py for Broker B -- a completely independent second engine (own table, own Telegram
identity) trading the TA forecast's four price levels instead of this module's alert-consensus signal,
sharing this module's exit logic (_pnl/_exit_levels/_scan_exit_crossing/_find_exit) but deliberately
NOT any TA-forecast bias gate -- neither engine applies one (Broker A's was removed 30 Sep 2026); Broker B
trades whichever level price reaches regardless of the forecast's overall directional read.

Every trade lives only in the `trades` table in Postgres (see storage.py) -- there is deliberately no
markdown/doc mirror to keep in sync, so a trade never requires a repo commit.

**Exit check is a candle scan, not a point sample.** check_broker_trades() only ever runs once per
poll (every 5 minutes), so a naive "compare entry price to this poll's spot price" check can miss a
real crossing entirely: if gold spikes past the $10 target and reverses before the next poll, the
point-in-time price at that next poll may be back under the target, and the trade would wrongly stay
open with the missed profit unrecorded. _scan_exit_crossing() fixes this by fetching real 1-minute
OHLC candles (fetch_candles(), Twelve Data) covering the window since the trade opened, and scanning
each bar's high/low for the first point that actually crossed the stop level (the stop-loss, then a trailing stop -- see TRAILING_STOP_* below)
-- catching a spike-and-reverse the next poll's point sample alone would have missed, and closing at
the true crossing price/time rather than whatever the spot price happens to be at poll time. This
still only *detects* the crossing at the next poll (up to ~5 minutes after the real event) -- it fixes
correctness (the right exit price gets recorded, the trade actually closes), not notification latency.
If the candle fetch fails or the API confirms no crossing occurred, this falls back to the previous
point-price check unchanged, so a transient Twelve Data hiccup never leaves the Broker unable to close
a trade at all.

**ADX filters (1 Oct 2026, both engines)** -- ADX(14) on the same 15-min gold candles as RSI
(`data_fetcher.fetch_gold_rsi_adx()`, one candle fetch for both), thresholds in `config.py`
(`ADX_TRENDING_THRESHOLD` 25, `ADX_CHOP_THRESHOLD` 20; textbook values, to be calibrated from the `adx14`
logged in `entry_context`). Broker A: `_adx_confirms()` blocks a Consensus5of7 entry when ADX < 20 (no trend
for a momentum consensus to ride), and `_rsi_confirms()` waives its RSI block when ADX >= 25 (in a strong
trend an extreme RSI is continuation, not exhaustion). Unknown ADX fails open for the ADX gate and keeps
the RSI block on. Blocks use the normal deduplicated Telegram notice.

**Two entry filters, added 26 Sep 2026 after analyzing a live loss** (Consensus5of7-sell sold $4,264.94
at 14:06:57 UTC on 25 Sep, the exact poll gold dropped $12.04 in five minutes and RSI(14) alerted
"entered oversold territory" -- all 7 of 7 indicators flagged off that single spike, DXY only barely
cleared its own 10-min threshold and had stalled within minutes; price mean-reverted through the $10
stop by 14:47): (1) **RSI exhaustion** (`_rsi_confirms()`) -- a Sell is skipped if gold's RSI(14) is
already <= `RSI_OVERSOLD_THRESHOLD` (30), a Buy skipped if already >= `RSI_OVERBOUGHT_THRESHOLD` (70) --
don't chase a move that's already technically exhausted, the same check `broker_b._rsi_confirms()` uses
for its two breakout rules. (2) **DXY confirmation on the real 15-min move** (`_dxy_confirms()`) -- since
Consensus5of7 only needs 5 of 7 named indicators to flag on any of their own 5/10/15-min windows, DXY
could be the omitted 2, or could have flagged on a thin 5-minute blip that isn't real confirmation; this
requires DXY's own net move over the trailing `DXY_CONFIRM_WINDOW_MINUTES` (15) to independently clear
its own calibrated 15-min companion-swing threshold (`config.INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][15]`)
in the trade's favor, regardless of which window(s) actually flagged. Unlike broker_b's same-named
function (which only blocks a *clear opposing* move), this one requires genuine *confirmation* -- a
stricter bar, since DXY here is just one of seven alert sources rather than the dedicated signal Broker
B fades. Both fail open (no block) on missing/insufficient data, same fail-open convention as the rest of this module's filters.
A blocked signal sends a deduplicated Telegram notice (`_notify_blocked()`,
`storage.record_broker_a_blocked_if_new()`) rather than failing silently, same idea as Broker B's own
blocked-entry notice -- deduplicated by `(rule_name, reasons, since_ts)` where `since_ts` is this
module's own entry watermark (`get_last_trade_open_ts()`), since Broker A has no forecast row to scope
by the way Broker B does; the watermark advancing when a trade actually opens is what lets the same
notice fire again on a later, separate occasion instead of never again.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from config import (
    ADX_CHOP_THRESHOLD,
    ADX_PERIOD,
    ADX_TRENDING_THRESHOLD,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_OVERSOLD_THRESHOLD,
    RSI_PERIOD,
)
from data_fetcher import fetch_gold_rsi_adx
from entry_context import build_entry_context
from price_bars import fetch_gold_bars
from notifier import send_telegram_message
from trading_control import trading_pause_reason
from storage import (
    close_trade_row,
    get_last_blocked_signal_ts_a,
    get_last_trade_open_ts,
    get_open_trade,
    get_recent_alerts,
    get_recent_readings,
    get_stop_loss_override,
    get_trailing_stop_override,
    insert_trade,
    record_broker_a_blocked_if_new,
)

# Consensus5of7-buy / Consensus5of7-sell entry window and exit target -- see
# .claude/agents/broker.md.
ENTRY_WINDOW_MINUTES = 10
EXIT_THRESHOLD = 10.0  # NOT used by Broker A/B any more (their fixed take-profit was removed 2 Oct 2026); forex_broker.py still uses it for both its TP and SL
STOP_LOSS_THRESHOLD = 10.0  # default stop-loss, $ per troy ounce -- was widened to $15 on 29 Sep 2026, back to $10 on 30 Sep 2026; Broker A and B only. The Telegram "make SL <n>" command overrides it (stop_loss_threshold())

# Trailing stop (2 Oct 2026, both brokers; replaces the fixed $10 take-profit): the stop starts at the stop-loss
# (STOP_LOSS_THRESHOLD / the Telegram "make SL" value) and, once a trade has been TRAILING_STOP_ACTIVATION in
# profit, follows TRAILING_STOP_DISTANCE behind the best price reached -- so after activation it sits at
# breakeven or better and only moves in the trade's favour. $ per troy ounce.
TRAILING_STOP_ACTIVATION = 7.0  # defaults; the Telegram "make trail <n>" command overrides both (trailing_stop_params())
TRAILING_STOP_DISTANCE = 7.0

# How many minutes of 1-min candles the exit check pulls each poll -- comfortably more than one
# 5-min poll interval, so a slightly late-firing poll still has full coverage back to the last
# check. See _scan_exit_crossing() / module docstring.
# Broker B fade trades only (5 Oct 2026): once price reaches the OPPOSITE fade level (a TA-Zone-sell reaching the forecast's first
# support, a TA-Zone-buy reaching its first resistance), the stop jumps to that level (locking that profit) and then trails this
# many $ behind the best price, so it is never worse than the level; price coming back through the level closes the trade there.
FADE_LOCK_TRAIL_DISTANCE = 5.0
EXIT_CANDLE_LOOKBACK_MINUTES = 20
EXIT_CANDLE_MAX_BARS = 3000  # the trailing stop needs every bar since entry, so a long-held trade fetches more

# How far back the DXY confirmation check looks for a net move backing the trade -- see module
# docstring's entry-filter entry. Matches broker_b._dxy_confirms()'s own window.
DXY_CONFIRM_WINDOW_MINUTES = 15

# Prefix for every Broker open/close Telegram message -- distinguishes trade alerts from
# XAU/USD price alerts (rules.XAUUSD_ALERT_PREFIX) in the chat. See rules.py's module comment
# for why an emoji, not real text color -- Telegram's Bot API doesn't support that.
TRADE_ALERT_PREFIX = "\U0001f535 "  # blue circle

# Second marker on every Broker A close message, right after its own prefix: green for a profit,
# red for a loss. Broker A uses circles only (Broker B uses squares -- see broker_b.py).
PROFIT_MARKER = "\U0001f7e2 "  # green circle
LOSS_MARKER = "\U0001f534 "  # red circle
BLOCKED_MARKER = "⛔ "  # no-entry sign -- a signal was reached but a filter stopped the trade

# Same zone/format dashboard.py's to_display_str() and ta_forecast_job.py use for every other
# human-facing timestamp in this project. Trade open/close messages need it because open_ts/close_ts
# are the real candle-scan crossing times -- up to ~5 minutes earlier than when the poll that
# notices them actually sends the Telegram message -- so the message states the tick time
# explicitly rather than leaving the reader to assume "now" is when it happened. Shared with
# broker_b.py (imported alongside _find_exit()/_result_marker()) so both engines format it the same way.
DISPLAY_TZ = ZoneInfo("America/New_York")

# Trading-hours window for fresh entries -- shared by Broker A (here) and Broker B (broker_b.py imports these,
# so the two engines can't drift): 7:00am-5:00pm America/New_York, weekdays only. Gates entries only, never
# exits. Matches market_hours.is_overnight_polling_pause() (the cloud poll doesn't even run outside it).
ENTRY_WINDOW_START_ET = time(7, 0)
ENTRY_WINDOW_END_ET = time(17, 0)


def _within_entry_window(now_utc: datetime) -> bool:
    """True on a weekday between ENTRY_WINDOW_START_ET and ENTRY_WINDOW_END_ET (America/New_York)."""
    local = now_utc.astimezone(DISPLAY_TZ)
    return local.weekday() < 5 and ENTRY_WINDOW_START_ET <= local.time() < ENTRY_WINDOW_END_ET


def _result_marker(pnl: float, profit: str = PROFIT_MARKER, loss: str = LOSS_MARKER) -> str:
    return profit if pnl >= 0 else loss


def _format_ts(ts: datetime) -> str:
    return ts.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

# The six physically/mining-correlated gold ETFs must flag the same direction gold itself is
# presumed to be moving; dxy (inversely correlated with gold) must flag the opposite direction.
# us10y was dropped from the Broker's indicator set entirely (it's still alerted/frequency-tested
# like the others -- see rules.py/frequency_test.py -- just no longer part of this rule). A trade
# only needs MIN_FLAGGING_COUNT of these seven to actually flag, not all of them, and each can
# flag from any of its own 15/10/5-min windows -- see .claude/agents/broker.md's Consensus5of7
# rules.
GOLD_DIRECTION_NAMES = ["gld", "iau", "gldm", "gdx", "gdxj", "ring"]
INVERSE_DIRECTION_NAMES = ["dxy"]
MIN_FLAGGING_COUNT = 5


def _adx_confirms(adx_value: float | None) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- Consensus5of7 is a momentum signal, so it needs an actual trend to
    ride: blocked when gold's ADX(14) is below ADX_CHOP_THRESHOLD (a 5-of-7 flag in a directionless
    market is more likely noise). `category` has no live number (dedup key); `detail` carries the
    reading, for the Telegram text only. None (couldn't compute) fails open."""
    if adx_value is None or adx_value >= ADX_CHOP_THRESHOLD:
        return True, None, None
    return (
        False,
        f"ADX({ADX_PERIOD}) shows no trend (threshold < {ADX_CHOP_THRESHOLD})",
        f"ADX({ADX_PERIOD}) shows no trend: {adx_value:.1f} (< {ADX_CHOP_THRESHOLD})",
    )


def _rsi_confirms(
    trade_type: str, rsi_value: float | None, adx_value: float | None = None
) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ok unless gold's RSI(14) is already past the threshold this trade
    would be chasing further: a Sell wants RSI not already oversold (<= RSI_OVERSOLD_THRESHOLD), a Buy
    wants RSI not already overbought (>= RSI_OVERBOUGHT_THRESHOLD). See module docstring's "RSI
    exhaustion" entry -- same computation/thresholds broker_b._rsi_confirms() uses for its two
    breakout rules. `category` is a fixed string with no live number (safe as the blocked-entry dedup
    key -- see _notify_blocked()); `detail` carries the actual reading, for the Telegram text only.
    `rsi_value` is the caller's single RSI(14) computation for this poll -- None if it couldn't be
    computed, which fails this open (ok=True). In a strong trend (ADX >= ADX_TRENDING_THRESHOLD) RSI
    can stay stretched for a long time and an extreme reading is continuation, not exhaustion, so the
    block is waived; an unknown ADX keeps the RSI block on."""
    if rsi_value is None:
        return True, None, None
    if adx_value is not None and adx_value >= ADX_TRENDING_THRESHOLD:
        return True, None, None
    if trade_type == "Sell":
        if rsi_value <= RSI_OVERSOLD_THRESHOLD:
            return (
                False,
                f"RSI(14) already oversold (threshold <= {RSI_OVERSOLD_THRESHOLD})",
                f"RSI(14) already oversold: {rsi_value:.1f} (<= {RSI_OVERSOLD_THRESHOLD})",
            )
        return True, None, None
    if rsi_value >= RSI_OVERBOUGHT_THRESHOLD:
        return (
            False,
            f"RSI(14) already overbought (threshold >= {RSI_OVERBOUGHT_THRESHOLD})",
            f"RSI(14) already overbought: {rsi_value:.1f} (>= {RSI_OVERBOUGHT_THRESHOLD})",
        )
    return True, None, None


def _dxy_confirms(trade_type: str, readings: list) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ok unless DXY's own net move over the trailing
    DXY_CONFIRM_WINDOW_MINUTES minutes fails to independently clear its own calibrated 15-min
    companion-swing threshold in the direction this trade needs -- see module docstring's "DXY
    confirmation" entry. Stricter than broker_b._dxy_confirms() (which only blocks a clear *opposing*
    move): this requires genuine confirmation, since Consensus5of7 only needs 5 of 7 indicators to
    flag on any of their own windows, so DXY might not have flagged at all, or only on a thin 5-min
    blip. `category`/`detail` follow _rsi_confirms()'s convention. `readings` is the caller's single
    get_recent_readings("dxy", DXY_CONFIRM_WINDOW_MINUTES) fetch. Fails open (ok=True) on
    missing/insufficient data, same fail-open convention as the other filters."""
    if not readings or len(readings) < 2:
        return True, None, None
    try:
        threshold = INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][DXY_CONFIRM_WINDOW_MINUTES]
    except Exception:
        return True, None, None
    change = readings[-1][1] - readings[0][1]
    # Gold and DXY move inversely: a Sell needs DXY to have genuinely risen, a Buy needs it to have
    # genuinely fallen, each by at least its own calibrated 15-min move.
    if trade_type == "Sell":
        if change < threshold:
            return (
                False,
                "DXY's 15-min move doesn't confirm the Sell",
                f"DXY moved only {change:+.4f} in {DXY_CONFIRM_WINDOW_MINUTES} min "
                f"(needs >= {threshold:.4f} to confirm)",
            )
        return True, None, None
    if change > -threshold:
        return (
            False,
            "DXY's 15-min move doesn't confirm the Buy",
            f"DXY moved only {change:+.4f} in {DXY_CONFIRM_WINDOW_MINUTES} min "
            f"(needs <= {-threshold:.4f} to confirm)",
        )
    return True, None, None


def _has_alert(alerts: list[tuple[datetime, str]], name: str, direction: str) -> bool:
    prefix = f"{name.upper()} moved {direction} "
    return any(message.startswith(prefix) for _, message in alerts)


def _first_alert(alerts: list[tuple[datetime, str]], name: str, direction: str) -> str | None:
    prefix = f"{name.upper()} moved {direction} "
    for _, message in alerts:
        if message.startswith(prefix):
            return message
    return None


def _entry_direction_map(trade_type: str) -> dict[str, str]:
    """Required alert direction per indicator for this trade_type: the gold-correlated names in
    GOLD_DIRECTION_NAMES move with gold, the inversely-correlated names in
    INVERSE_DIRECTION_NAMES move against it."""
    same = "up" if trade_type == "Buy" else "down"
    opposite = "down" if trade_type == "Buy" else "up"
    return {
        **{name: same for name in GOLD_DIRECTION_NAMES},
        **{name: opposite for name in INVERSE_DIRECTION_NAMES},
    }


def _count_flagging(alerts: list[tuple[datetime, str]], direction_map: dict[str, str]) -> int:
    return sum(1 for name, direction in direction_map.items() if _has_alert(alerts, name, direction))


def _match_entry_rule(alerts: list[tuple[datetime, str]]):
    """Returns (trade_type, rule_name), or (None, None) if neither direction has at least
    MIN_FLAGGING_COUNT of the seven indicators flagging in the required direction. If both
    directions independently reach the threshold at once (a genuine conflict in the alert
    stream), no trade opens either way -- an incoherent signal is not acted on."""
    buy_count = _count_flagging(alerts, _entry_direction_map("Buy"))
    sell_count = _count_flagging(alerts, _entry_direction_map("Sell"))
    buy_ok = buy_count >= MIN_FLAGGING_COUNT
    sell_ok = sell_count >= MIN_FLAGGING_COUNT
    if buy_ok and not sell_ok:
        return "Buy", "Consensus5of7-buy"
    if sell_ok and not buy_ok:
        return "Sell", "Consensus5of7-sell"
    return None, None


def _triggering_text(alerts: list[tuple[datetime, str]], trade_type: str) -> str:
    """Full triggering alert text (indicator, swing size, threshold, price) -- stored in the
    `trades` table's `triggering_alerts` column only, for the broker subagent's later analysis.
    Deliberately NOT what goes in the Telegram open message -- see _triggering_names() below."""
    direction_map = _entry_direction_map(trade_type)
    parts = [_first_alert(alerts, name, direction) for name, direction in direction_map.items()]
    return "; ".join(part for part in parts if part)


def _triggering_names(alerts: list[tuple[datetime, str]], trade_type: str) -> str:
    """Just the names of the indicators that flagged (e.g. "GLD, GDX, DXY"), no prices/swing
    sizes/thresholds -- what the Telegram open message shows, so it stays short and doesn't spell
    out multiple ETF/DXY price levels. The full detail still goes to the `trades` table via
    _triggering_text() above."""
    direction_map = _entry_direction_map(trade_type)
    names = [name.upper() for name, direction in direction_map.items() if _has_alert(alerts, name, direction)]
    return ", ".join(names)


def _pnl(trade: dict, current_price: float) -> float:
    if trade["trade_type"] == "Buy":
        return current_price - trade["entry_price"]
    return trade["entry_price"] - current_price


def stop_loss_threshold() -> float:
    """The stop-loss distance ($ per oz) both brokers use right now: the value last set with the
    Telegram "make SL <n>" command if there is one (it applies to open trades too), else
    STOP_LOSS_THRESHOLD. A DB error falls back to the default rather than blocking an exit."""
    try:
        override = get_stop_loss_override()
    except Exception:
        return STOP_LOSS_THRESHOLD
    return override if override is not None and override > 0 else STOP_LOSS_THRESHOLD


def trailing_stop_params() -> tuple[float, float]:
    """(activation, distance) both brokers use right now: the values last set with the Telegram
    "make trail <n>" command if there are any (they apply to open trades too), else the
    TRAILING_STOP_* defaults. A DB error falls back to the defaults rather than blocking an exit."""
    try:
        override = get_trailing_stop_override()
    except Exception:
        return TRAILING_STOP_ACTIVATION, TRAILING_STOP_DISTANCE
    if override is not None and override[0] > 0 and override[1] > 0:
        return override
    return TRAILING_STOP_ACTIVATION, TRAILING_STOP_DISTANCE


def _exit_stop_offset(
    peak: float, stop_loss_distance: float, activation: float | None = None, distance: float | None = None
) -> float:
    """Where the stop sits relative to entry, in $ per oz in the trade's favour (negative = below
    entry for a Buy / above entry for a Sell), given the best profit `peak` reached so far. It starts
    at -stop_loss_distance; once the trade has been `activation` in profit it follows
    `distance` behind the peak (trailing_stop_params() when not given) (so it never sits below entry after activation) and only
    ever moves in the trade's favour."""
    if activation is None or distance is None:
        activation, distance = trailing_stop_params()
    if peak < activation:
        return -stop_loss_distance
    return max(-stop_loss_distance, peak - distance)


def _scan_exit_crossing(
    trade: dict,
    candles,
    stop_loss_distance: float | None = None,
    activation: float | None = None,
    distance: float | None = None,
    lock_level: float | None = None,
) -> tuple[float, datetime] | None:
    """Replays 1-min OHLC candles (all of them since the trade opened, in order) against this trade's
    trailing stop -- see the module docstring for why a candle scan beats a point-in-time price. There
    is no fixed take-profit (removed 2 Oct 2026): the only exit is the stop, which starts at
    -stop_loss_distance and then trails behind the best price (_exit_stop_offset()). Returns
    (stop_price, bar_time) for the first bar that touched the stop, or None. Each bar is checked
    against the stop as it stood BEFORE that bar's own high/low can raise the peak, the conservative
    ordering when a single bar spans both. `lock_level` (Broker B fade trades, see FADE_LOCK_TRAIL_DISTANCE): once a bar has
    reached that price (the opposite fade level), from the NEXT bar on the stop is at least that level and trails
    FADE_LOCK_TRAIL_DISTANCE behind the best price, whichever is better for the trade."""
    if stop_loss_distance is None:
        stop_loss_distance = stop_loss_threshold()
    if activation is None or distance is None:
        activation, distance = trailing_stop_params()
    is_buy = trade["trade_type"] == "Buy"
    entry = float(trade["entry_price"])
    peak = 0.0  # best profit reached so far, $ per oz
    lock_profit = None  # profit (in $) at the opposite fade level; None = no lock for this trade
    if lock_level is not None:
        lock_profit = (float(lock_level) - entry) if is_buy else (entry - float(lock_level))
        if lock_profit <= 0:
            lock_profit = None  # the level is not in the trade's favour: nothing to lock
    locked = False
    for _, bar in candles.iterrows():
        offset = _exit_stop_offset(peak, stop_loss_distance, activation, distance)
        if locked:
            offset = max(offset, lock_profit, peak - FADE_LOCK_TRAIL_DISTANCE)
        stop_price = entry + offset if is_buy else entry - offset
        hit = bar["low"] <= stop_price if is_buy else bar["high"] >= stop_price
        if hit:
            return stop_price, bar["datetime"].to_pydatetime()
        favourable = bar["high"] - entry if is_buy else entry - bar["low"]
        peak = max(peak, favourable)
        if lock_profit is not None and not locked and favourable >= lock_profit:
            locked = True  # applies from the next bar (the bar that touched the level can't also be stopped by it)
    return None


def _exit_bar_count(trade: dict, now: datetime) -> int:
    """How many 1-min bars to fetch: everything since the trade opened (the trailing stop depends on
    the peak since entry), never fewer than EXIT_CANDLE_LOOKBACK_MINUTES and capped at
    EXIT_CANDLE_MAX_BARS (FOREX.com serves ~4000 per call)."""
    minutes = int((now - trade["open_ts"]).total_seconds() // 60) + 5
    return max(EXIT_CANDLE_LOOKBACK_MINUTES, min(minutes, EXIT_CANDLE_MAX_BARS))


def _find_exit(
    trade: dict, fallback_price: float, fallback_ts: datetime, lock_level: float | None = None
) -> tuple[float, datetime, float] | None:
    """(exit_price, exit_ts, pnl) if this trade should close now, else None. Tries the real
    intrabar candle path first; falls back to the plain point-price check (the original behavior)
    if the candle fetch fails or turns up no crossing, so a Twelve Data hiccup never blocks a
    trade from closing at all."""
    stop_loss_distance = stop_loss_threshold()  # read once, so one check can't straddle a change
    trail_activation, trail_distance = trailing_stop_params()
    try:
        # A Buy closes (sells) on the bid, a Sell closes (buys back) on the ask -- see price_bars.py.
        exit_side = "bid" if trade["trade_type"] == "Buy" else "ask"
        candles = fetch_gold_bars(_exit_bar_count(trade, fallback_ts), exit_side)
        # Strictly after open_ts, not >=: the entry candle's own high/low can span a level the
        # entry price sits nowhere near reaching yet (e.g. Broker B fills mid-candle at the near
        # edge of a level, but that same candle's low already touched the take-profit *before* the
        # entry technically happened) -- scanning it for an exit crossing can otherwise close a
        # trade in the same minute it opened, at a P/L it never actually had a chance to earn.
        candles = candles[candles["datetime"] > trade["open_ts"]]
        crossing = _scan_exit_crossing(trade, candles, stop_loss_distance, trail_activation, trail_distance, lock_level)
    except Exception:
        crossing = None  # best-effort accuracy improvement -- fall back below, don't block on it

    if crossing is not None:
        exit_price, exit_ts = crossing
        return exit_price, exit_ts, _pnl(trade, exit_price)

    pnl = _pnl(trade, fallback_price)
    # Point-price fallback (candle fetch failed): only the initial stop can be judged from one price; the
    # trailing part is picked up by the next successful candle scan, which replays every bar since entry.
    if pnl <= -stop_loss_distance:
        return fallback_price, fallback_ts, pnl
    return None


def _open_message(
    trade_id: int, trade_type: str, rule_name: str, price: float, triggering_names: str, open_ts: datetime
) -> str:
    return (
        f"{TRADE_ALERT_PREFIX}BROKER A #{trade_id}: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: {triggering_names}\n"
        f"Filled: {_format_ts(open_ts)}"
    )


def _close_message(trade: dict, exit_price: float, exit_ts: datetime, pnl: float) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{_result_marker(pnl)}BROKER A #{trade['id']}: closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}) -- "
        f"{result} of ${abs(pnl):.2f}\n"
        f"Filled: {_format_ts(exit_ts)}"
    )


def _blocked_message(rule_name: str, price: float, message_reasons: str) -> str:
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{BLOCKED_MARKER}BROKER A: {rule_name} signal @ ${price:.2f} "
        f"reached but blocked -- {message_reasons}."
    )


def _notify_blocked(
    rule_name: str,
    price: float,
    dedup_reasons: str,
    message_reasons: str,
    since_ts: datetime,
    signal_ts: datetime | None = None,
) -> None:
    """Sends the blocked-entry Telegram notice, unless this exact (rule_name, dedup_reasons) was
    already notified since `since_ts` (see storage.record_broker_a_blocked_if_new()'s docstring for
    why that floor plays the role Broker B's forecast-row id does here). `signal_ts` (the newest alert
    behind the signal) is recorded even on a duplicate notice, so check_broker_trades() can treat the
    blocked signal's alerts as consumed on later polls."""
    if record_broker_a_blocked_if_new(rule_name, price, dedup_reasons, since_ts, signal_ts):
        send_telegram_message(_blocked_message(rule_name, price, message_reasons))


def check_broker_trades(prices: dict[str, float]) -> None:
    """Runs once per poll, after this cycle's alerts are saved. Closes the open trade (if any) the
    moment the trailing stop (or the initial stop-loss) is hit, then looks for a fresh
    Consensus5of7-buy/-sell entry signal -- at least MIN_FLAGGING_COUNT (5) of the seven
    intrahour-swing indicators, in the required directions, landing in the alerts table within the
    trailing ENTRY_WINDOW_MINUTES (10) minutes -- gated by the trading-hours
    window (_within_entry_window(), 7am-5pm ET weekdays, same as Broker B), which sends a deduplicated
    Telegram notice when it blocks (there is no TA-bias gate anymore -- removed 30 Sep 2026). A
    signal inside the window still needs RSI not already exhausted
    (_rsi_confirms()) and DXY's own 15-min move to genuinely confirm it (_dxy_confirms()) -- see
    module docstring's entry-filter entry; a signal either of these blocks sends a deduplicated
    Telegram notice instead of opening (_notify_blocked()). See .claude/agents/broker.md for the
    rules themselves."""
    gold_price = prices.get("gold")
    if gold_price is None:
        return

    now = datetime.now(timezone.utc)
    open_trade = get_open_trade()

    # Telegram "stop trading" / scheduled pause (trading_control.py): close anything open at market
    # and open nothing.
    if trading_pause_reason(now) is not None:
        if open_trade is not None:
            pnl = _pnl(open_trade, gold_price)
            close_trade_row(open_trade["id"], gold_price, now, pnl)
            send_telegram_message(_close_message(open_trade, gold_price, now, pnl))
        return

    if open_trade is not None:
        exit_result = _find_exit(open_trade, gold_price, now)
        if exit_result is not None:
            exit_price, exit_ts, pnl = exit_result
            close_trade_row(open_trade["id"], exit_price, exit_ts, pnl)
            send_telegram_message(_close_message(open_trade, exit_price, exit_ts, pnl))
            open_trade = None

    if open_trade is None:
        watermark = get_last_trade_open_ts()
        # A signal that a filter blocked is a missed signal, not a pending one: its alerts are consumed,
        # so once the block clears a few polls later (the alerts are still inside the 10-minute
        # window) it isn't opened off the same alerts -- a fresh consensus is needed. The floor is the
        # later of the last trade's open time and the last blocked signal's newest alert.
        try:
            blocked_floor = get_last_blocked_signal_ts_a()
        except Exception:
            blocked_floor = None
        floor = max((t for t in (watermark, blocked_floor) if t is not None), default=None)
        alerts = get_recent_alerts(minutes=ENTRY_WINDOW_MINUTES)
        if floor is not None:
            alerts = [(ts, message) for ts, message in alerts if ts > floor]

        trade_type, rule_name = _match_entry_rule(alerts)
        since_ts = floor if floor is not None else datetime(1970, 1, 1, tzinfo=timezone.utc)
        signal_ts = max((ts for ts, _ in alerts), default=None)
        if trade_type is not None and not _within_entry_window(now):
            local = now.astimezone(DISPLAY_TZ)
            _notify_blocked(
                rule_name, gold_price, "outside trading hours",
                f"outside trading hours (now {local:%H:%M} ET; window is "
                f"{ENTRY_WINDOW_START_ET:%H:%M}-{ENTRY_WINDOW_END_ET:%H:%M} ET, weekdays)",
                since_ts,
                signal_ts,
            )
            trade_type = None
        if trade_type is not None:
            rsi_value, adx_value = fetch_gold_rsi_adx(RSI_PERIOD, ADX_PERIOD)
            try:
                dxy_readings = get_recent_readings("dxy", DXY_CONFIRM_WINDOW_MINUTES)
            except Exception:
                dxy_readings = None

            rsi_ok, rsi_category, rsi_detail = _rsi_confirms(trade_type, rsi_value, adx_value)
            dxy_ok, dxy_category, dxy_detail = _dxy_confirms(trade_type, dxy_readings)
            adx_ok, adx_category, adx_detail = _adx_confirms(adx_value)

            if rsi_ok and dxy_ok and adx_ok:
                triggering_text = _triggering_text(alerts, trade_type)
                flagging = _triggering_names(alerts, trade_type)
                context = build_entry_context(
                    now,
                    rsi_value=rsi_value,
                    dxy_readings=dxy_readings,
                    extra={"flagging": flagging, "signal_price": round(float(gold_price), 2)},
                )
                trade_id = insert_trade(rule_name, trade_type, gold_price, now, triggering_text, context)
                triggering_names = _triggering_names(alerts, trade_type)
                send_telegram_message(_open_message(trade_id, trade_type, rule_name, gold_price, triggering_names, now))
            else:
                dedup_reasons = "; ".join(r for r in (rsi_category, dxy_category, adx_category) if r)
                message_reasons = "; ".join(r for r in (rsi_detail, dxy_detail, adx_detail) if r)
                _notify_blocked(rule_name, gold_price, dedup_reasons, message_reasons, since_ts, signal_ts)
