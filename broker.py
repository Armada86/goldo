"""Automated paper-trading engine for Broker A's rules (Consensus5of7).

This is the code implementation of the rules documented in `.claude/agents/broker.md`'s "Rules"
section -- that file is the human-readable spec, this module is what actually executes it every poll.
The two must be kept in sync by hand when the rules change (same convention as `docs/market.md` vs.
`config.py`): a rule change here without the matching prose update there is an incomplete change.
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
each bar's high/low for the first point that actually crossed the $10 take-profit or $10 stop-loss level
-- catching a spike-and-reverse the next poll's point sample alone would have missed, and closing at
the true crossing price/time rather than whatever the spot price happens to be at poll time. This
still only *detects* the crossing at the next poll (up to ~5 minutes after the real event) -- it fixes
correctness (the right exit price gets recorded, the trade actually closes), not notification latency.
If the candle fetch fails or the API confirms no crossing occurred, this falls back to the previous
point-price check unchanged, so a transient Twelve Data hiccup never leaves the Broker unable to close
a trade at all.

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
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_OVERSOLD_THRESHOLD,
    RSI_PERIOD,
)
from data_fetcher import compute_rsi, fetch_gold_candles
from price_bars import fetch_gold_bars
from notifier import send_telegram_message
from storage import (
    close_trade_row,
    get_last_blocked_signal_ts_a,
    get_last_trade_open_ts,
    get_open_trade,
    get_recent_alerts,
    get_recent_readings,
    insert_trade,
    record_broker_a_blocked_if_new,
)

# Consensus5of7-buy / Consensus5of7-sell entry window and exit target -- see
# .claude/agents/broker.md.
ENTRY_WINDOW_MINUTES = 10
EXIT_THRESHOLD = 10.0  # take-profit, $ per troy ounce (also what forex_broker.py uses for both its TP and SL)
STOP_LOSS_THRESHOLD = 10.0  # stop-loss, $ per troy ounce -- was widened to $15 on 29 Sep 2026, back to $10 on 30 Sep 2026; Broker A and B only

# How many minutes of 1-min candles the exit check pulls each poll -- comfortably more than one
# 5-min poll interval, so a slightly late-firing poll still has full coverage back to the last
# check. See _scan_exit_crossing() / module docstring.
EXIT_CANDLE_LOOKBACK_MINUTES = 20

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


def _rsi_confirms(trade_type: str, rsi_value: float | None) -> tuple[bool, str | None, str | None]:
    """(ok, category, detail) -- ok unless gold's RSI(14) is already past the threshold this trade
    would be chasing further: a Sell wants RSI not already oversold (<= RSI_OVERSOLD_THRESHOLD), a Buy
    wants RSI not already overbought (>= RSI_OVERBOUGHT_THRESHOLD). See module docstring's "RSI
    exhaustion" entry -- same computation/thresholds broker_b._rsi_confirms() uses for its two
    breakout rules. `category` is a fixed string with no live number (safe as the blocked-entry dedup
    key -- see _notify_blocked()); `detail` carries the actual reading, for the Telegram text only.
    `rsi_value` is the caller's single RSI(14) computation for this poll -- None if it couldn't be
    computed, which fails this open (ok=True)."""
    if rsi_value is None:
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


def _exit_levels(trade: dict) -> tuple[float, float]:
    """(take_profit_price, stop_loss_price) for this trade -- EXIT_THRESHOLD ($10) in profit and
    STOP_LOSS_THRESHOLD ($10) against, on opposite sides, mirrored for Buy vs Sell."""
    entry = trade["entry_price"]
    if trade["trade_type"] == "Buy":
        return entry + EXIT_THRESHOLD, entry - STOP_LOSS_THRESHOLD
    return entry - EXIT_THRESHOLD, entry + STOP_LOSS_THRESHOLD


def _scan_exit_crossing(trade: dict, candles) -> tuple[float, datetime] | None:
    """Scans 1-min OHLC candles in chronological order for the first bar whose high/low actually
    touched this trade's take-profit or stop-loss level -- see module docstring for why this beats
    comparing entry price to a single later point-in-time price. Returns (exit_price, exit_ts) at
    the first bar that crossed either level, or None if neither level was touched in `candles`."""
    take_profit, stop_loss = _exit_levels(trade)
    is_buy = trade["trade_type"] == "Buy"
    for _, bar in candles.iterrows():
        hit_tp = bar["high"] >= take_profit if is_buy else bar["low"] <= take_profit
        hit_sl = bar["low"] <= stop_loss if is_buy else bar["high"] >= stop_loss
        if hit_tp and hit_sl:
            # Both levels fall inside the same 1-min bar -- an OHLC bar alone can't tell which was
            # touched first. Conservatively assume the worse outcome for this imaginary trade.
            return stop_loss, bar["datetime"].to_pydatetime()
        if hit_tp:
            return take_profit, bar["datetime"].to_pydatetime()
        if hit_sl:
            return stop_loss, bar["datetime"].to_pydatetime()
    return None


def _find_exit(trade: dict, fallback_price: float, fallback_ts: datetime) -> tuple[float, datetime, float] | None:
    """(exit_price, exit_ts, pnl) if this trade should close now, else None. Tries the real
    intrabar candle path first; falls back to the plain point-price check (the original behavior)
    if the candle fetch fails or turns up no crossing, so a Twelve Data hiccup never blocks a
    trade from closing at all."""
    try:
        # A Buy closes (sells) on the bid, a Sell closes (buys back) on the ask -- see price_bars.py.
        exit_side = "bid" if trade["trade_type"] == "Buy" else "ask"
        candles = fetch_gold_bars(EXIT_CANDLE_LOOKBACK_MINUTES, exit_side)
        # Strictly after open_ts, not >=: the entry candle's own high/low can span a level the
        # entry price sits nowhere near reaching yet (e.g. Broker B fills mid-candle at the near
        # edge of a level, but that same candle's low already touched the take-profit *before* the
        # entry technically happened) -- scanning it for an exit crossing can otherwise close a
        # trade in the same minute it opened, at a P/L it never actually had a chance to earn.
        candles = candles[candles["datetime"] > trade["open_ts"]]
        crossing = _scan_exit_crossing(trade, candles)
    except Exception:
        crossing = None  # best-effort accuracy improvement -- fall back below, don't block on it

    if crossing is not None:
        exit_price, exit_ts = crossing
        return exit_price, exit_ts, _pnl(trade, exit_price)

    pnl = _pnl(trade, fallback_price)
    if pnl >= EXIT_THRESHOLD or pnl <= -STOP_LOSS_THRESHOLD:
        return fallback_price, fallback_ts, pnl
    return None


def _open_message(trade_type: str, rule_name: str, price: float, triggering_names: str, open_ts: datetime) -> str:
    return (
        f"{TRADE_ALERT_PREFIX}BROKER A: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: {triggering_names}\n"
        f"Filled: {_format_ts(open_ts)}"
    )


def _close_message(trade: dict, exit_price: float, exit_ts: datetime, pnl: float) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{_result_marker(pnl)}BROKER A: closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} "
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
    moment its unrealized P/L reaches the $10 take-profit or $10 stop-loss, then looks for a fresh
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
            try:
                rsi_value = compute_rsi(fetch_gold_candles()["close"], period=RSI_PERIOD).dropna().iloc[-1]
            except Exception:
                rsi_value = None
            try:
                dxy_readings = get_recent_readings("dxy", DXY_CONFIRM_WINDOW_MINUTES)
            except Exception:
                dxy_readings = None

            rsi_ok, rsi_category, rsi_detail = _rsi_confirms(trade_type, rsi_value)
            dxy_ok, dxy_category, dxy_detail = _dxy_confirms(trade_type, dxy_readings)

            if rsi_ok and dxy_ok:
                triggering_text = _triggering_text(alerts, trade_type)
                insert_trade(rule_name, trade_type, gold_price, now, triggering_text)
                triggering_names = _triggering_names(alerts, trade_type)
                send_telegram_message(_open_message(trade_type, rule_name, gold_price, triggering_names, now))
            else:
                dedup_reasons = "; ".join(r for r in (rsi_category, dxy_category) if r)
                message_reasons = "; ".join(r for r in (rsi_detail, dxy_detail) if r)
                _notify_blocked(rule_name, gold_price, dedup_reasons, message_reasons, since_ts, signal_ts)
