"""Broker F (the Forex broker): Broker B's rules, watched at tick granularity and traded on the FOREX.com demo account.

Why this exists: broker_b.py runs once per 5-minute GitHub poll and reads 1-minute bars, so it notices a level touch
up to several minutes after it happened and fills it at the level price a resting order WOULD have got. On a real
account a market order is sent when the price is there, and fills at the live bid/ask. This loop watches the live
quote every TICK_SECONDS (10 s) so the touch is noticed within seconds, the trade enters at the actual fill price,
and the stop (trailing stop, fade lock) is evaluated on every tick instead of every poll.

No rule is re-implemented here -- every decision calls the code Broker B already uses:
  - arming / re-arm budget:  broker_b.armed_candidates()   (counting forex_b_trades, not broker_b_trades)
  - touch detection:         broker_b._scan_zone_entry()    (fed ticks instead of 1-min bars: high = low = price)
  - the five entry filters:  broker_b.evaluate_gates()
  - the stop / trail / lock: broker._scan_exit_crossing()   (fed the ticks since entry), lock level from
                             broker_b._fade_lock_level(), fade flag as in check_broker_b_trades()
  - stop settings:           broker.stop_loss_threshold() / trailing_stop_params() (so `make SL` / `make trail` apply)
so a change to Broker B's rules changes this engine too.

Modes (--mode):
  shadow (default) -- records every decision it WOULD make in forex_b_trades (mode = 'shadow') using the live
      entry-/exit-side prices, and sends Telegram messages, but sends NO order to forex.com.
  live -- real orders on the FOREX.com DEMO account (the client is locked to XAU/USD, 1 oz). Entry is a market order the
      moment the touch passes the filters. Right after the fill a stop-only order is attached on the platform at the
      initial stop (so the position is protected even if this process dies); each tick the stop Broker B's exit code
      would use is computed and the platform stop is moved up to it (never down). If the platform stop triggers, the
      position disappears and the close is recorded from forex.com's trade history; if the watcher's own stop is
      crossed first (or trading is paused) it cancels the stop and closes at market. Moving/cancelling a stop are
      verified by forex_live_check.py, which must pass before this mode is used.

Manual commands: the Telegram Worker cannot place forex.com orders itself (the order code and its XAU/USD-only locks live here), so
"buy broker F" / "sell broker F" / "close broker F" are queued in the forex_b_commands table and executed by this loop within one tick
(and the Worker dispatches forex_watch.yml so a loop exists). A command not picked up within 5 minutes expires, never firing late.
"stop trading" needs nothing extra: the loop already closes the position and opens nothing while trading_pause_reason() is set.

Read-only toward Broker B: this never writes to broker_b_trades / broker_b_blocked, so running it cannot consume or
change a Broker B touch. A touch it blocks is remembered in memory only.

Run: `python forex_watcher.py --max-minutes 5` (one burst, what .github/workflows/forex_watch.yml does) or with a large
--max-minutes on any always-on host. It exits at once when there is nothing to watch (market closed, outside the
7am-5pm ET entry window, trading paused) and nothing open.
"""

import argparse
import logging
import time
from datetime import datetime, timezone

import pandas as pd

from block_rules import get_block_rules
from broker import (
    _format_ts,
    _pnl,
    _result_marker,
    _scan_exit,
    _within_entry_window,
    stop_loss_threshold,
    trailing_stop_params,
)
from broker_b import (
    DXY_CONFIRM_WINDOW_MINUTES,
    DXY_READINGS_MINUTES,
    FADE_OPPOSITE_SCENARIO,
    _entry_price_and_invalidation,
    _entry_range,
    _entry_side,
    _fade_lock_level,
    _scan_zone_entry,
    _trailing_readings,
    armed_candidates,
    evaluate_gates,
)
from config import ADX_PERIOD, ATR_PERIOD, RSI_PERIOD
from data_fetcher import fetch_gold_candles, gold_adx_rising_from_candles, gold_rsi_adx_atr_from_candles
from entry_context import build_entry_context, level_distances
from forex_client import TRADABLE_MARKET_ID, ForexClient, ForexClientError, ForexOrderUncertainError
from market_hours import is_market_closed
from notifier import send_telegram_message
from storage import (
    close_forex_b_trade,
    finish_forex_b_command,
    get_pending_forex_b_commands,
    set_forex_b_stop_order,
    forex_b_level_history,
    get_last_close_ts_forex_b,
    get_latest_ta_forecast,
    get_open_forex_b_trade,
    get_recent_readings,
    init_db,
    insert_forex_b_trade,
)
from trading_control import trading_pause_reason

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("forex-watcher")

TICK_SECONDS = 10  # one quote (two ~0.5 s forex.com calls) per tick
SEED_MINUTES = 30  # 1-minute bars loaded at start so a fresh process already knows which side of a level price was on
CONTEXT_REFRESH_SECONDS = 60  # forecast / armed levels / DXY readings / lock level are re-read from Postgres this often
# A touch is acted on only if it is at most this old when seen. 1-minute seed bars carry their minute's start time, so
# this is a little over one tick; anything older (a touch from before the process started, or one a filter already
# consumed) is dropped, never filled late -- the same "no retroactive fills" rule as Broker B.
ENTRY_MAX_TOUCH_AGE_SECONDS = 40
TRADE_PREFIX = "\U0001f7ea "  # purple square -- Broker B is blue, A is a blue circle
BLOCKED_MARKER = "⛔ "
PROFIT_MARKER = "\U0001f7e9 "
LOSS_MARKER = "\U0001f7e5 "

MIN_STOP_STEP = 0.5  # the platform stop is only moved when the new level is at least this much better ($ per oz)

COLUMNS = ["datetime", "open", "high", "low", "close"]


class Feed:
    """Entry/exit-side price history as bar-shaped frames (columns of get_bars()): the seeded 1-minute bars, then one
    row per new tick with open = high = low = close = that tick's price. The existing bar scanners work on this
    unchanged, so a tick that is already through a level counts exactly like a bar that reached it."""

    def __init__(self, client: ForexClient):
        self.frames = {
            side: client.get_bars(SEED_MINUTES, side.upper())[COLUMNS]
            for side in ("bid", "ask")
        }
        self.last_ts = None

    def add(self, quote: dict) -> bool:
        """Appends the quote as a tick row on both sides; False (nothing added) if it is the tick already seen."""
        if self.last_ts is not None and quote["ts"] <= self.last_ts:
            return False
        self.last_ts = quote["ts"]
        for side in ("bid", "ask"):
            price = quote[side]
            row = pd.DataFrame([[pd.Timestamp(quote["ts"]), price, price, price, price]], columns=COLUMNS)
            self.frames[side] = pd.concat([self.frames[side], row], ignore_index=True)
        return True

    def side(self, side: str) -> pd.DataFrame:
        return self.frames[side]


def _exit_side(trade_type: str) -> str:
    """A Buy closes by selling on the bid, a Sell by buying back on the ask (the reverse of _entry_side())."""
    return "bid" if trade_type == "Buy" else "ask"


def _message(trade_id: int, text: str, mode: str, marker: str = "") -> str:
    return f"{TRADE_PREFIX.rstrip()}{marker}BROKER F ({mode}) #{trade_id}: {text}"


def _entry_possible(now: datetime) -> bool:
    """A fresh entry is allowed right now: market open, inside the 7am-5pm ET weekday window, trading not paused."""
    return not is_market_closed() and _within_entry_window(now) and trading_pause_reason(now) is None


def _commands_pending() -> bool:
    try:
        return bool(get_pending_forex_b_commands())
    except Exception:
        return False


class Watcher:
    def __init__(self, client: ForexClient, mode: str):
        self.client = client
        self.mode = mode
        self.feed = Feed(client)
        self.open_trade = get_open_forex_b_trade()
        self.consumed: dict[str, datetime] = {}  # rule_name -> newest tick time a blocked / stale touch used up
        self.last_quote: dict | None = None
        self.context: dict = {}
        self.context_at = 0.0

    # --- cached Postgres reads ------------------------------------------------------------------------------------

    def _refresh_context(self, force: bool = False) -> dict:
        if not force and time.monotonic() - self.context_at < CONTEXT_REFRESH_SECONDS:
            return self.context
        context: dict = {}
        try:
            forecast = get_latest_ta_forecast()
            context["forecast"] = forecast
            context["candidates"] = (
                armed_candidates(forecast, forex_b_level_history) if forecast and forecast.get("levels") else []
            )
            context["last_close_ts"] = get_last_close_ts_forex_b()
        except Exception:
            log.exception("could not refresh forecast / armed levels -- keeping the previous ones")
            context = self.context
        if self.open_trade is not None:
            context["lock_level"] = _fade_lock_level(self.open_trade)
        try:
            context["dxy_readings"] = get_recent_readings("dxy", DXY_READINGS_MINUTES)
        except Exception:
            context["dxy_readings"] = None
        self.context, self.context_at = context, time.monotonic()
        return context

    # --- exit -----------------------------------------------------------------------------------------------------

    def _exit_inputs(self, trade: dict):
        """(stop-scan result, next stop price) on the ticks since the trade opened, with Broker B's exit settings."""
        exit_frame = self.feed.side(_exit_side(trade["trade_type"]))
        ticks = exit_frame[exit_frame["datetime"] > pd.Timestamp(trade["open_ts"])]
        fade = trade["rule_name"] in FADE_OPPOSITE_SCENARIO
        activation, distance = trailing_stop_params()
        return _scan_exit(
            trade, ticks, stop_loss_threshold(), activation, distance, self._refresh_context().get("lock_level"), fade
        )

    def _check_exit(self, now: datetime) -> None:
        trade = self.open_trade
        exit_frame = self.feed.side(_exit_side(trade["trade_type"]))
        latest = exit_frame.iloc[-1]
        price_col = "low" if trade["trade_type"] == "Buy" else "high"
        paused = trading_pause_reason(now) is not None
        position = None
        if self.mode == "live":
            position = self._live_position(trade)
            if position is None:
                self._record_platform_close(trade, now)  # forex.com's own stop closed it
                return
        crossing, next_stop = (None, None) if paused else self._exit_inputs(trade)
        if crossing is None and not paused:
            if position is not None:
                self._sync_platform_stop(trade, position, next_stop)
            return
        if paused:
            stop_level, exit_price, exit_ts, reason = None, float(latest["close"]), now, "trading paused"
        else:
            stop_level, crossed_ts = crossing
            hit = exit_frame[exit_frame["datetime"] == pd.Timestamp(crossed_ts)].iloc[0]
            # The market close happens when the tick is seen: at that tick's price, which is at or beyond the stop.
            exit_price, exit_ts, reason = float(hit[price_col]), crossed_ts, "stop"
        close_order_id = None
        if self.mode == "live":
            closed = self._close_at_market(trade, position)
            if closed is None:
                return  # not confirmed closed: the next tick re-reads the position and decides
            exit_price, close_order_id = closed
            exit_ts = datetime.now(timezone.utc)
        self._finish(trade, exit_price, exit_ts, stop_level, reason, close_order_id)

    def _finish(self, trade, exit_price, exit_ts, stop_level, reason, close_order_id=None, note="") -> None:
        pnl = _pnl(trade, exit_price)
        close_forex_b_trade(trade["id"], exit_price, exit_ts, pnl, stop_level, reason, close_order_id)
        stop_text = f" (stop level ${stop_level:.2f}, slipped ${abs(exit_price - stop_level):.2f})" if stop_level is not None else ""
        send_telegram_message(_message(
            trade["id"],
            f"closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f}{stop_text} (opened @ ${trade['entry_price']:.2f}, "
            f"rule {trade['rule_name']}) -- {'profit' if pnl >= 0 else 'loss'} of ${abs(pnl):.2f}{note}\nFilled: {_format_ts(exit_ts)}",
            self.mode, _result_marker(pnl, PROFIT_MARKER, LOSS_MARKER),
        ))
        log.info("closed #%s %s pnl %+.2f (%s)", trade["id"], trade["rule_name"], pnl, reason)
        self.open_trade = None
        self._refresh_context(force=True)

    # --- live: orders on the forex.com demo account ----------------------------------------------------------------

    def _live_position(self, trade: dict) -> dict | None:
        positions = [p for p in self.client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]
        return next((p for p in positions if str(p.get("OrderId")) == str(trade["forex_order_id"])), None)

    def _sync_platform_stop(self, trade: dict, position: dict, desired_stop: float) -> None:
        """Keeps the platform stop at (or better than) the stop Broker B's exit code wants: attaches one if the position has none,
        otherwise moves it up (a Buy) / down (a Sell) when the desired level is at least MIN_STOP_STEP better."""
        is_buy = trade["trade_type"] == "Buy"
        direction = "buy" if is_buy else "sell"
        stop = position.get("StopOrder")
        try:
            if not stop:
                placed = self.client.attach_stop(trade["forex_order_id"], direction, desired_stop)
                set_forex_b_stop_order(trade["id"], placed["stop_order_id"])
                send_telegram_message(_message(trade["id"], f"stop was missing -- re-attached at ${placed['stop_price']:.2f}.", self.mode))
                return
            current = float(stop["TriggerPrice"])
            better = desired_stop - current if is_buy else current - desired_stop
            if better >= MIN_STOP_STEP:
                moved = self.client.move_stop(trade["forex_order_id"], stop["OrderId"], direction, desired_stop)
                log.info("#%s platform stop %.2f -> %.2f", trade["id"], current, moved["stop_price"])
        except ForexClientError as e:  # incl. ForexOrderUncertainError: the next tick re-reads the position's real stop
            log.warning("could not set the platform stop for #%s: %s", trade["id"], e)

    def _close_at_market(self, trade: dict, position: dict) -> tuple[float, object] | None:
        """Cancels the platform stop (so it cannot fire against the close) and closes at market. Returns (fill price, close
        order id), or None if the close is not confirmed -- the position is then re-read on the next tick."""
        stop = position.get("StopOrder")
        if stop:
            try:
                self.client.cancel_order(stop["OrderId"])
            except ForexClientError as e:
                log.warning("could not cancel stop %s before closing #%s: %s", stop["OrderId"], trade["id"], e)
        try:
            result = self.client.close_position(trade["trade_type"])
        except ForexClientError as e:
            send_telegram_message(_message(trade["id"], f"market close NOT confirmed ({e}) -- CHECK THE DEMO ACCOUNT.", self.mode))
            return None
        leftovers = [p for p in self.client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]
        if leftovers:
            send_telegram_message(_message(
                trade["id"],
                f"after the market close forex.com still shows {len(leftovers)} open XAU/USD position(s) -- CHECK THE DEMO ACCOUNT "
                f"(the close may have opened an opposite position instead of netting).", self.mode,
            ))
        return result["fill_price"], result["order_id"]

    def _record_platform_close(self, trade: dict, now: datetime) -> None:
        """The position is gone from forex.com: its stop order triggered (or someone closed it by hand)."""
        closing = None
        try:
            closing = self.client.find_closing_trade(int(trade["forex_order_id"]))
        except Exception:
            log.exception("could not read the closing trade")
        if closing is not None:
            self._finish(trade, closing["price"], closing["closed_at"] or now, None, "platform stop", closing["order_id"])
            return
        exit_side = self.feed.side(_exit_side(trade["trade_type"]))
        estimate = float(exit_side.iloc[-1]["close"])
        self._finish(
            trade, estimate, now, None, "platform stop (price estimated)", None,
            note="\n(exit price ESTIMATED from the live quote -- not found in forex.com trade history)",
        )

    # --- entry ----------------------------------------------------------------------------------------------------

    def _check_entry(self, now: datetime) -> None:
        context = self._refresh_context()
        forecast, candidates = context.get("forecast"), context.get("candidates") or []
        if not candidates:
            return
        floor = pd.Timestamp(forecast["ts"])
        if context.get("last_close_ts") is not None:
            floor = max(floor, pd.Timestamp(context["last_close_ts"]))
        touches = []
        for scenario_name, trade_type, rule_name, scenario in candidates:
            frame = self.feed.side(_entry_side(trade_type))
            rule_floor = max(floor, pd.Timestamp(self.consumed[rule_name])) if rule_name in self.consumed else floor
            touch = _scan_zone_entry(scenario_name, scenario, frame[frame["datetime"] > rule_floor])
            if touch is not None:
                touches.append((touch[1], touch[0], trade_type, rule_name, scenario_name))
        if not touches:
            return
        touches.sort(key=lambda t: t[0])
        latest_ts = self.feed.last_ts
        for touch_ts, trigger_price, trade_type, rule_name, scenario_name in touches:
            age = (latest_ts - touch_ts).total_seconds()  # feed time, so a lagging quote isn't mistaken for a stale touch
            if age > ENTRY_MAX_TOUCH_AGE_SECONDS:
                self.consumed[rule_name] = latest_ts  # a stale touch: never filled late, the level needs a fresh one
                log.info("%s touch at %s is %.0fs old -- dropped", rule_name, touch_ts, age)
                continue
            if self._try_open(now, touch_ts, trigger_price, trade_type, rule_name, scenario_name, forecast):
                return
            self.consumed[rule_name] = latest_ts

    def _try_open(self, now, touch_ts, trigger_price, trade_type, rule_name, scenario_name, forecast) -> bool:
        side = _entry_side(trade_type)
        # Slow inputs are fetched only now, at a real touch, not every tick (Twelve Data's 15-min candles).
        try:
            candles = fetch_gold_candles()
            rsi_value, adx_value, atr_value = gold_rsi_adx_atr_from_candles(candles, RSI_PERIOD, ADX_PERIOD, ATR_PERIOD)
            adx_rising = gold_adx_rising_from_candles(candles, ADX_PERIOD)
        except Exception:
            rsi_value = adx_value = atr_value = adx_rising = None
        try:
            range_15m = _entry_range(self.client.get_bars(20, side.upper()))
        except Exception:
            range_15m = None
        rules = get_block_rules()
        dxy_readings = self.context.get("dxy_readings")
        ok, _dedup, reasons = evaluate_gates(
            scenario_name, trade_type, dxy_readings, rsi_value, adx_value, adx_rising, atr_value, range_15m, rules
        )
        if not ok:
            send_telegram_message(
                f"{TRADE_PREFIX.rstrip()}{BLOCKED_MARKER}BROKER F ({self.mode}): {rule_name} level ${trigger_price:.2f} "
                f"reached but blocked -- {reasons}."
            )
            log.info("%s blocked: %s", rule_name, reasons)
            return False
        fill_price = float(self.feed.side(side).iloc[-1]["close"])  # a Buy fills on the ask, a Sell on the bid
        order_id = stop_order_id = None
        if self.mode == "live":
            placed = self._place_entry(trade_type, rule_name)
            if placed is None:
                return False
            fill_price, order_id = placed
        slippage = fill_price - trigger_price if trade_type == "Buy" else trigger_price - fill_price  # + = worse than the level
        session = forecast["levels"].get("session", "?")
        trigger_text = f"{session} TA forecast {forecast['forecast_date']}, {scenario_name} @ ${trigger_price:.2f}"
        entry_context = build_entry_context(
            now,
            rsi_value=rsi_value,
            dxy_readings=_trailing_readings(dxy_readings, DXY_CONFIRM_WINDOW_MINUTES),
            forecast=forecast,
            extra={
                "scenario": scenario_name,
                "trigger_price": round(float(trigger_price), 2),
                "slippage": round(slippage, 2),
                "seconds_since_touch": round((self.feed.last_ts - touch_ts).total_seconds(), 1),
                "spread": round(float(self.feed.side("ask").iloc[-1]["close"] - self.feed.side("bid").iloc[-1]["close"]), 2),
                **level_distances(trigger_price, forecast),
            },
        )
        trade_id = insert_forex_b_trade(
            self.mode, rule_name, trade_type, trigger_price, fill_price, touch_ts, now, forecast["id"], trigger_text,
            entry_context, forex_order_id=order_id,
        )
        self.open_trade = get_open_forex_b_trade()
        send_telegram_message(_message(
            trade_id,
            f"opened {trade_type} 1 oz XAU/USD @ ${fill_price:.2f} (rule {rule_name}; level ${trigger_price:.2f}, "
            f"{'slippage' if slippage >= 0 else 'price improvement'} ${abs(slippage):.2f}).\n"
            f"Trigger: {trigger_text}\nFilled: {_format_ts(now)}",
            self.mode,
        ))
        log.info("opened #%s %s @ %.2f (level %.2f)", trade_id, rule_name, fill_price, trigger_price)
        if self.mode == "live":
            self._protect(self.open_trade)
        self._refresh_context(force=True)
        return True

    def _place_entry(self, trade_type: str, rule_name: str) -> tuple[float, object] | None:
        """Sends the market order. Returns (fill price, order id), or None if nothing was opened. An order whose outcome is unknown
        (timeout / 5xx) is never retried: the account's positions are read instead and a single new XAU/USD position is adopted."""
        direction = "buy" if trade_type == "Buy" else "sell"
        try:
            result = self.client.place_market_order(direction)
            return result["fill_price"], result["order_id"]
        except ForexOrderUncertainError as e:
            log.warning("entry order outcome unknown: %s", e)
            time.sleep(2)
            try:
                positions = [p for p in self.client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]
            except Exception:
                positions = None
            if positions is not None and len(positions) == 1:
                send_telegram_message(_message(0, f"{rule_name} order outcome was unknown; adopted the single open position {positions[0]['OrderId']}.", self.mode))
                return float(positions[0]["Price"]), positions[0]["OrderId"]
            send_telegram_message(_message(0, f"{rule_name} order outcome UNKNOWN and positions are unclear -- CHECK THE DEMO ACCOUNT. Not retried.", self.mode))
            return None
        except ForexClientError as e:
            send_telegram_message(_message(0, f"{rule_name} order rejected, nothing opened: {e}", self.mode))
            return None

    def _protect(self, trade: dict) -> None:
        """Attaches the initial platform stop right after the fill: the stop Broker B's exit code gives for a trade with no ticks yet."""
        direction = "buy" if trade["trade_type"] == "Buy" else "sell"
        try:
            _crossing, initial_stop = self._exit_inputs({**trade, "open_ts": datetime.now(timezone.utc)})
            placed = self.client.attach_stop(trade["forex_order_id"], direction, initial_stop)
            set_forex_b_stop_order(trade["id"], placed["stop_order_id"])
            send_telegram_message(_message(trade["id"], f"platform stop set at ${placed['stop_price']:.2f}.", self.mode))
        except ForexClientError as e:
            send_telegram_message(_message(
                trade["id"], f"could NOT attach the platform stop ({e}) -- position is UNPROTECTED on forex.com; the watcher manages it.", self.mode,
            ))

    # --- loop -----------------------------------------------------------------------------------------------------

    def idle(self, now: datetime) -> bool:
        """True when there is nothing to watch: no open trade and no entry possible right now."""
        return self.open_trade is None and not _entry_possible(now) and not _commands_pending()

    def step(self) -> None:
        quote = self.client.get_quote()
        fresh = self.feed.add(quote)
        self.last_quote = quote
        now = datetime.now(timezone.utc)
        self._handle_commands(now)  # Telegram "buy/sell/close broker F", even when the price has not ticked
        if not fresh:
            return  # no new tick since the last look
        if self.open_trade is not None:
            self._check_exit(now)
        elif _entry_possible(now):
            self._check_entry(now)

    # --- manual commands (Telegram "buy broker F" / "sell broker F" / "close broker F") ----------------------------------

    def _handle_commands(self, now: datetime) -> None:
        for cmd in get_pending_forex_b_commands():
            try:
                status, result = self._run_command(cmd["command"], now)
            except Exception as e:
                log.exception("command %s failed", cmd)
                status, result = "failed", repr(e)
            finish_forex_b_command(cmd["id"], status, result)
            if status != "done":
                send_telegram_message(f"{TRADE_PREFIX.rstrip()}{BLOCKED_MARKER}BROKER F ({self.mode}): '{cmd['command']}' not done -- {result}")
            log.info("command %s -> %s (%s)", cmd["command"], status, result)

    def _run_command(self, command: str, now: datetime) -> tuple[str, str]:
        """Executes one queued command; returns (status, result text). The open/close Telegram messages come from the shared helpers."""
        if command in ("buy", "sell"):
            return self._manual_open("Buy" if command == "buy" else "Sell", now)
        if command == "close":
            return self._manual_close(now)
        return "rejected", f"unknown command {command!r}"

    def _manual_open(self, trade_type: str, now: datetime) -> tuple[str, str]:
        if self.open_trade is not None:
            return "rejected", "a Broker F trade is already open. Close it first."
        if trading_pause_reason(now) is not None:
            return "rejected", 'trading is stopped/paused (send "start trading" first).'
        if is_market_closed():
            return "rejected", "the market is closed (Friday 5pm - Sunday 6pm ET)."
        side = _entry_side(trade_type)
        fill_price = float(self.last_quote[side])
        order_id = None
        rule_name = f"Telegram-{trade_type.lower()}"
        if self.mode == "live":
            placed = self._place_entry(trade_type, rule_name)
            if placed is None:
                return "failed", "the order was not placed (see the previous message)."
            fill_price, order_id = placed
        try:
            forecast_id = (get_latest_ta_forecast() or {}).get("id")
        except Exception:
            forecast_id = None
        trade_id = insert_forex_b_trade(
            self.mode, rule_name, trade_type, fill_price, fill_price, now, now, forecast_id, "Manual (Telegram command)",
            {"spread": round(float(self.last_quote["ask"] - self.last_quote["bid"]), 2)}, forex_order_id=order_id,
        )
        self.open_trade = get_open_forex_b_trade()
        send_telegram_message(_message(
            trade_id,
            f"opened {trade_type} 1 oz XAU/USD @ ${fill_price:.2f} (rule {rule_name}).\nTrigger: Manual (Telegram command)\nFilled: {_format_ts(now)}",
            self.mode,
        ))
        if self.mode == "live":
            self._protect(self.open_trade)
        self._refresh_context(force=True)
        return "done", f"opened #{trade_id}"

    def _manual_close(self, now: datetime) -> tuple[str, str]:
        trade = self.open_trade
        if trade is None:
            return "rejected", "no open Broker F trade to close."
        exit_price = float(self.last_quote[_exit_side(trade["trade_type"])])
        close_order_id = None
        if self.mode == "live":
            position = self._live_position(trade)
            if position is None:  # already closed on the platform (its stop fired)
                self._record_platform_close(trade, now)
                return "done", "the position was already closed on forex.com"
            closed = self._close_at_market(trade, position)
            if closed is None:
                return "failed", "the market close was not confirmed (see the previous message)."
            exit_price, close_order_id = closed
        self._finish(trade, exit_price, now, None, "manual close", close_order_id, note="\n(manual close via Telegram)")
        return "done", f"closed #{trade['id']}"


def _live_ready(client: ForexClient) -> bool:
    """Live mode assumes the account holds no XAU/USD position except Broker F's own: refuses to trade (and says so) if there is an
    untracked one, e.g. a manual position, since closing at market could net against it."""
    tracked = get_open_forex_b_trade()
    positions = [p for p in client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]
    extra = [p for p in positions if tracked is None or str(p.get("OrderId")) != str(tracked["forex_order_id"])]
    if extra:
        send_telegram_message(
            f"{TRADE_PREFIX.rstrip()}BROKER F (live): not trading -- the demo account already has {len(extra)} untracked XAU/USD "
            f"position(s) ({', '.join(str(p.get('OrderId')) for p in extra)}). Close them or switch to shadow mode."
        )
        log.error("untracked XAU/USD positions on the account -- refusing to run live")
        return False
    return True


def run(max_minutes: float, mode: str) -> None:
    deadline = time.monotonic() + max_minutes * 60
    init_db()
    if get_open_forex_b_trade() is None and not _entry_possible(datetime.now(timezone.utc)) and not _commands_pending():
        log.info("Nothing to watch (market closed, outside the entry window, or paused) -- exiting")
        return
    client = ForexClient()
    if mode == "live" and not _live_ready(client):
        return
    watcher = Watcher(client, mode)
    log.info("Broker F watcher started (%s mode); open trade: %s", mode, watcher.open_trade and watcher.open_trade["id"])
    while time.monotonic() < deadline:
        started = time.monotonic()
        try:
            watcher.step()
        except Exception:
            log.exception("tick failed -- continuing")
        if watcher.idle(datetime.now(timezone.utc)):
            log.info("Nothing left to watch -- exiting")
            return
        time.sleep(max(0.0, TICK_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--max-minutes", type=float, default=5.0, help="how long to loop before exiting (default 5)")
    parser.add_argument("--mode", choices=("shadow", "live"), default="shadow")
    args = parser.parse_args()
    run(args.max_minutes, args.mode)
