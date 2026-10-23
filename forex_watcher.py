"""Broker F (the Forex broker): Broker B's rules, watched at tick granularity and traded on the FOREX.com demo account.

Why this exists: broker_b.py runs once per 5-minute GitHub poll and reads 1-minute bars, so it notices a level touch
up to several minutes after it happened and fills it at the level price a resting order WOULD have got. On a real
account a market order is sent when the price is there, and fills at the live bid/ask. This loop watches the live
live tick stream (see Watcher v2 below) so the touch is noticed within a second, the trade enters at the actual fill price,
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
"stop trading" needs nothing extra: the loop already closes the position and opens nothing while trading_pause_reason(now, "F") is set (Telegram "stop trading" or "stop trading broker F").

Read-only toward Broker B: this never writes to broker_b_trades / broker_b_blocked, so running it cannot consume or
change a Broker B touch. A touch it blocks is remembered in memory only.

Run: `python forex_watcher.py --max-minutes 25` (what .github/workflows/forex_watch.yml does) or with a large --max-minutes on any
always-on host. It exits at once when there is nothing to watch (market closed, outside the 7am-5pm ET entry window, trading paused)
and nothing open.

Watcher v2 (13 Oct 2026) -- why the loop is event-driven and runs two processes at a handover:
  * Prices come from FOREX.com's STREAMING feed (forex_stream.py, ~4 ticks/s, ~50 ms behind the market). The REST tick history it
    used before is refreshed by the server in ~1-minute batches, so its "latest tick" was 60-130 s old and about two thirds of
    touches were dropped as stale (B #76's 4158.00 breakout was never seen). Every tick is checked as it arrives (entry touches, the
    trailing/lock stop, platform stop moves). If the stream goes silent the process reconnects with a fresh login and meanwhile
    falls back to REST polling; no entry is made on a tick older than ENTRY_MAX_FEED_AGE_SECONDS.
  * One process trades at a time. A Postgres lease (storage.forex_lease_*, table forex_b_lease) names the active holder; it renews
    every few seconds and a crashed holder is replaced after LEASE_TTL_SECONDS. The next run (cron-job.org dispatches every 5 min,
    forex_watch.yml no longer queues them) starts during the last STANDBY_LEAD_SECONDS of the active one, streams and builds the
    same price history, but never trades; when the active run reaches its end it releases the lease (with the used-up touches in
    the lease state) and the standby takes over within ~2 s -- no gap, no cold start. Runs that find a healthy active watcher with
    plenty of time left exit at once. If no standby appeared the active run keeps going up to HARD_EXTRA_SECONDS past its end.
"""

import argparse
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

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
from config import ADX_PERIOD, ATR_PERIOD, INTRAHOUR_SWING_ALERT_THRESHOLD, RSI_PERIOD
from data_fetcher import fetch_gold_candles, gold_adx_rising_from_candles, gold_rsi_adx_atr_from_candles
from entry_context import DISPLAY_TZ as ENTRY_TZ
from entry_context import _num, build_exit_context, level_distances, shadow_bias_adx
from forex_client import TRADABLE_MARKET_ID, ForexClient, ForexClientError, ForexOrderUncertainError
from forex_stream import PriceStream
from market_hours import is_market_closed
from notifier import send_telegram_message
from storage import (
    LEASE_TTL_SECONDS,
    close_forex_b_trade,
    finish_forex_b_command,
    forex_lease_acquire,
    forex_legacy_watcher_alive,
    forex_lease_register_standby,
    forex_lease_release,
    forex_lease_status,
    get_forex_b_heartbeat,
    get_pending_forex_b_commands,
    set_forex_b_stop_order,
    touch_forex_b_heartbeat,
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

LOOP_SECONDS = 0.25  # the main loop waits this long for a tick; a tick that arrives is handled at once
SEED_MINUTES = 30  # 1-minute bars loaded at start so a fresh process already knows which side of a level price was on
SEED_TICKS = 300  # REST ticks loaded at start (~2 min, the freshest the REST history has)
CONTEXT_REFRESH_SECONDS = 60  # forecast / armed levels / DXY readings / lock level are re-read from Postgres this often
# A touch is acted on only if it is at most this old (feed time) when seen. With the stream a touch is seen within a second; older ones
# (seeded history, a reconnect gap) are dropped, never filled late -- the same "no retroactive fills" rule as Broker B.
ENTRY_MAX_TOUCH_AGE_SECONDS = 15
ENTRY_MAX_FEED_AGE_SECONDS = 20  # no entry is judged while the newest tick is older than this on the wall clock (stale feed)
FEED_LAG_WARN_SECONDS = 30  # newest tick older than this = Telegram note
STREAM_SILENT_RECONNECT_SECONDS = 20  # no tick for this long: log in again and reconnect the stream
STREAM_RESTART_MIN_SECONDS = 30  # but not more often than this
REST_FALLBACK_AFTER_SECONDS = 30  # stream silent this long: also poll the (laggy) REST tick history so exits are still judged
REST_FALLBACK_EVERY_SECONDS = 10
POSITION_CHECK_SECONDS = 3  # how often the open position / platform stop is read from forex.com while no stop crossing is pending
COMMAND_CHECK_SECONDS = 2  # how often queued Telegram commands are read
GATE_CACHE_SECONDS = 5  # trading pause / entry-window checks (Postgres) are cached this long
LEASE_RENEW_SECONDS = 5
STANDBY_LEAD_SECONDS = 420  # a new run becomes the standby only when the active one ends within this many seconds
STANDBY_MAX_WAIT_SECONDS = 780  # a standby gives up after this long without taking over
STANDBY_REGISTER_SECONDS = 8
GATE_INPUT_FRESH_SECONDS = 90  # RSI/ADX/ATR (Twelve Data 15-min candles) older than this are fetched again at a touch
GATE_INPUT_PREFETCH_SECONDS = 45  # ... and refreshed this often while price is within NEAR_LEVEL_DOLLARS of an armed level
NEAR_LEVEL_DOLLARS = 4.0
HARD_EXTRA_SECONDS = 360  # an active run with no standby waiting keeps going this long past its planned end
TRADE_PREFIX = "\U0001f7ea "  # purple square -- Broker B is blue, A is a blue circle
BLOCKED_MARKER = "⛔ "
BLOCKED_NOTICE_MIN_SECONDS = 60  # per rule/level: one blocked-entry Telegram notice per minute
PROFIT_MARKER = "\U0001f7e9 "
LOSS_MARKER = "\U0001f7e5 "

MIN_STOP_STEP = 0.5  # the platform stop is only moved when the new level is at least this much better ($ per oz)

COLUMNS = ["datetime", "open", "high", "low", "close"]


class Feed:
    """Entry/exit-side price history as bar-shaped frames (columns of get_bars()). Older history is 1-minute bars (the REST seed, then
    ticks older than RECENT_SECONDS rolled up into minute bars); the last RECENT_SECONDS are kept as one row per tick with
    open = high = low = close = that tick's price. The existing bar scanners work on this unchanged, so a tick that is already through
    a level counts exactly like a bar that reached it. side() returns both parts as one time-sorted frame."""

    RECENT_SECONDS = 300

    def __init__(self, client: ForexClient | None):
        if client is not None:
            self.history = {side: client.get_bars(SEED_MINUTES, side.upper())[COLUMNS] for side in ("bid", "ask")}
        else:
            self.history = {side: pd.DataFrame(columns=COLUMNS) for side in ("bid", "ask")}
        self.ticks: list[tuple] = []  # (ts, bid, ask), oldest first
        self.last_ts = None
        self._frames: dict = {}
        self._rolled_at = time.monotonic()

    def add(self, quote: dict) -> bool:
        return self.add_ticks([quote])

    def add_ticks(self, ticks: list[dict]) -> bool:
        """Appends every tick newer than the last one seen (oldest first); False if there was nothing new."""
        new = sorted((t for t in ticks if self.last_ts is None or t["ts"] > self.last_ts), key=lambda t: t["ts"])
        if not new:
            return False
        self.ticks.extend((t["ts"], t["bid"], t["ask"]) for t in new)
        self.last_ts = new[-1]["ts"]
        self._frames = {}
        if time.monotonic() - self._rolled_at >= 60:
            self._roll()
        return True

    def _roll(self) -> None:
        """Turns ticks older than RECENT_SECONDS (to the minute) into 1-minute bars, so the frames the scanners walk stay small."""
        self._rolled_at = time.monotonic()
        if not self.ticks:
            return
        cutoff = (pd.Timestamp(self.last_ts) - pd.Timedelta(seconds=self.RECENT_SECONDS)).floor("min")
        n_old = sum(1 for t in self.ticks if pd.Timestamp(t[0]) < cutoff)
        if n_old == 0:
            return
        old = pd.DataFrame(self.ticks[:n_old], columns=["ts", "bid", "ask"])
        old["minute"] = pd.to_datetime(old["ts"], utc=True).dt.floor("min")
        for side in ("bid", "ask"):
            g = old.groupby("minute")[side].agg(open="first", high="max", low="min", close="last").reset_index()
            g = g.rename(columns={"minute": "datetime"})[COLUMNS]
            self.history[side] = pd.concat([self.history[side], g], ignore_index=True)
        self.ticks = self.ticks[n_old:]
        self._frames = {}

    def side(self, side: str) -> pd.DataFrame:
        frame = self._frames.get(side)
        if frame is None:
            idx = 1 if side == "bid" else 2
            rows = pd.DataFrame(
                {
                    "datetime": [pd.Timestamp(t[0]) for t in self.ticks],
                    "open": [t[idx] for t in self.ticks], "high": [t[idx] for t in self.ticks],
                    "low": [t[idx] for t in self.ticks], "close": [t[idx] for t in self.ticks],
                }
            )[COLUMNS]
            parts = [p for p in (self.history[side], rows) if len(p)]
            frame = pd.concat(parts, ignore_index=True) if parts else rows
            frame = frame.sort_values("datetime", kind="mergesort").reset_index(drop=True)
            self._frames[side] = frame
        return frame


def _exit_side(trade_type: str) -> str:
    """A Buy closes by selling on the bid, a Sell by buying back on the ask (the reverse of _entry_side())."""
    return "bid" if trade_type == "Buy" else "ask"


def _send(text: str) -> None:
    """Telegram is a notification, never a precondition: a failed send (retried once) is logged and swallowed, so a Telegram
    outage right after a trade is recorded can't skip the platform stop, the consumed-touch bookkeeping or the context refresh."""
    for attempt in (1, 2):
        try:
            send_telegram_message(text)
            return
        except Exception:
            log.exception("Telegram send failed (attempt %d)", attempt)
            if attempt == 1:
                time.sleep(1)


def _message(trade_id: int, text: str, mode: str, marker: str = "") -> str:
    return f"{TRADE_PREFIX.rstrip()}{marker}BROKER F ({mode}) #{trade_id}: {text}"


def _entry_possible(now: datetime) -> bool:
    """A fresh entry is allowed right now: market open, inside the 7am-5pm ET weekday window, trading not paused."""
    return not is_market_closed() and _within_entry_window(now) and trading_pause_reason(now, "F") is None


def _commands_pending() -> bool:
    try:
        return bool(get_pending_forex_b_commands())
    except Exception:
        return False


class Watcher:
    def __init__(self, client: ForexClient, mode: str, feed: Feed | None = None):
        self.client = client
        self.mode = mode
        self.feed = feed if feed is not None else Feed(client)
        self.role = "standby"  # "active" trades; "standby" only keeps the feed warm (see activate())
        self.open_trade = None
        self.consumed: dict[str, datetime] = {}  # rule_name -> newest tick time a blocked / stale touch used up
        self.last_quote: dict | None = None
        self.heartbeat_at = 0.0
        self.context: dict = {}
        self.context_at = 0.0
        self.started_at = datetime.now(timezone.utc)
        self.previous_tick_ts = self._previous_tick_ts()  # read before our own first heartbeat overwrites it
        self.notified: dict[str, float] = {}  # throttle key -> monotonic time of the last Telegram note
        self.position_checked_at = 0.0
        self.commands_at = 0.0
        self._paused_at = -1e9
        self._paused_value = False
        self._gate_at = -1e9
        self._gate_value = False
        self.stream_restart_at = 0.0
        self._gate_inputs = None  # (rsi, adx, atr, adx_rising) of gold's 15-min candles
        self._gate_inputs_at = -1e9

    # --- role ------------------------------------------------------------------------------------------------------

    def activate(self, state: dict | None = None) -> None:
        """This process now holds the lease: load the used-up touches the previous holder left, and its open trade from the database."""
        self.role = "active"
        self.consumed = {}
        for rule_name, ts in ((state or {}).get("consumed") or {}).items():
            try:
                self.consumed[rule_name] = pd.Timestamp(ts)
            except Exception:
                log.warning("ignoring unreadable consumed touch %s=%s", rule_name, ts)
        self.open_trade = get_open_forex_b_trade()
        self._refresh_context(force=True)
        self.position_checked_at = 0.0
        log.info("this watcher is now ACTIVE (open trade: %s, used-up touches: %s)", self.open_trade and self.open_trade["id"], list(self.consumed))

    def demote(self) -> None:
        """The lease was lost to another process: stop acting (that process now trades), keep the feed warm."""
        self.role = "standby"
        self.open_trade = None
        log.warning("this watcher lost the lease and is now STANDBY")

    def lease_state(self) -> dict:
        return {
            "consumed": {k: pd.Timestamp(v).isoformat() for k, v in self.consumed.items()},
            "tick_ts": self.feed.last_ts.isoformat() if self.feed.last_ts is not None else None,
        }

    # --- cached gates ----------------------------------------------------------------------------------------------

    def _paused(self, now: datetime) -> bool:
        if time.monotonic() - self._paused_at >= GATE_CACHE_SECONDS:
            self._paused_value = trading_pause_reason(now, "F") is not None
            self._paused_at = time.monotonic()
        return self._paused_value

    def _entry_ok(self, now: datetime) -> bool:
        """_entry_possible() with the Postgres-backed pause check cached for GATE_CACHE_SECONDS (called on every tick)."""
        if time.monotonic() - self._gate_at >= GATE_CACHE_SECONDS:
            self._gate_value = not is_market_closed() and _within_entry_window(now) and not self._paused(now)
            self._gate_at = time.monotonic()
        return self._gate_value

    def _previous_tick_ts(self):
        """Newest tick the previous watcher process had evaluated (its last heartbeat), or None. Touches up to then were already judged
        by that process, so this one need not report them as missed."""
        try:
            beat = get_forex_b_heartbeat()
        except Exception:
            log.exception("could not read the previous watcher heartbeat")
            return None
        if not beat or beat.get("mode") != self.mode or beat.get("tick_ts") is None:
            return None
        return pd.Timestamp(beat["tick_ts"])

    def _already_judged(self, touch_ts) -> bool:
        """True if the previous process had watched ticks past the end of this 1-minute bar (bars carry their start time)."""
        return self.previous_tick_ts is not None and pd.Timestamp(touch_ts) + pd.Timedelta(minutes=1) <= self.previous_tick_ts

    def _note(self, key: str, text: str, every_minutes: float = 15.0) -> None:
        """A diagnostic Telegram note (a dropped touch, an error) -- at most one per `key` every `every_minutes`, so a repeating fault
        cannot flood the chat. These exist because the log of a running GitHub job cannot be read until the run ends."""
        last = self.notified.get(key)
        if last is not None and time.monotonic() - last < every_minutes * 60:
            return
        self.notified[key] = time.monotonic()
        try:
            send_telegram_message(f"{TRADE_PREFIX.rstrip()}\u26A0\uFE0F BROKER F ({self.mode}): {text}")
        except Exception:
            log.exception("could not send a diagnostic note")

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
            self._note("refresh", "could not read the TA forecast / armed levels from the database -- no entries until it recovers "
                                  "(an open trade is still managed).")
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
        paused = self._paused(now)
        crossing, next_stop = (None, None) if paused else self._exit_inputs(trade)
        position = None
        # The stop is judged locally on every tick. The position / platform stop on forex.com is read every POSITION_CHECK_SECONDS,
        # and at once when our own stop is crossed or trading is paused (so a close is never sent for a position that is already gone).
        due = time.monotonic() - self.position_checked_at >= POSITION_CHECK_SECONDS
        if self.mode == "live" and (crossing is not None or paused or due):
            position = self._live_position(trade)
            self.position_checked_at = time.monotonic()
            if position is None:
                self._record_platform_close(trade, now)  # forex.com's own stop closed it
                return
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
                self.position_checked_at = 0.0  # not confirmed closed: re-read the position on the next tick and decide again
                return
            exit_price, close_order_id = closed
            exit_ts = datetime.now(timezone.utc)
        self._finish(trade, exit_price, exit_ts, stop_level, reason, close_order_id)

    def _finish(self, trade, exit_price, exit_ts, stop_level, reason, close_order_id=None, note="") -> None:
        pnl = _pnl(trade, exit_price)
        close_forex_b_trade(
            trade["id"], exit_price, exit_ts, pnl, stop_level, reason, close_order_id,
            build_exit_context(datetime.now(timezone.utc), opened_at=trade["open_ts"]),
        )
        stop_text = f" (stop level ${stop_level:.2f}, slipped ${abs(exit_price - stop_level):.2f})" if stop_level is not None else ""
        _send(_message(
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
                placed = self.client.attach_stop(int(trade["forex_order_id"]), direction, desired_stop)
                set_forex_b_stop_order(trade["id"], placed["stop_order_id"])
                _send(_message(trade["id"], f"stop was missing -- re-attached at ${placed['stop_price']:.2f}.", self.mode))
                return
            current = float(stop["TriggerPrice"])
            better = desired_stop - current if is_buy else current - desired_stop
            if better >= MIN_STOP_STEP:
                moved = self.client.move_stop(int(trade["forex_order_id"]), stop["OrderId"], direction, desired_stop)
                log.info("#%s platform stop %.2f -> %.2f", trade["id"], current, moved["stop_price"])
        except ForexClientError as e:  # incl. ForexOrderUncertainError: the next tick re-reads the position's real stop
            log.warning("could not set the platform stop for #%s: %s", trade["id"], e)
            action = "attach" if not stop else "move"
            state = "missing" if not stop else "still at $%.2f" % float(stop["TriggerPrice"])
            self._note(
                f"stop-sync-{trade['id']}",
                f"#{trade['id']}: could NOT {action} the platform stop to ${desired_stop:.2f} ({e}) -- the stop on forex.com is {state}; "
                f"the watcher retries every tick and closes at market if its own stop is crossed.",
                every_minutes=5.0,
            )

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
            _send(_message(trade["id"], f"market close NOT confirmed ({e}) -- CHECK THE DEMO ACCOUNT.", self.mode))
            return None
        leftovers = [p for p in self.client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]
        if leftovers:
            _send(_message(
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
        if not candidates or self.feed.last_ts is None:
            return
        if (now - self.feed.last_ts).total_seconds() > ENTRY_MAX_FEED_AGE_SECONDS:
            return  # the newest tick is too old on the wall clock: judge nothing (and consume nothing) until the feed is live again
        self._maybe_prefetch(candidates)
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
                if self._already_judged(touch_ts):
                    log.info("%s touch at %s was already judged by the previous watcher process -- no notice", rule_name, touch_ts)
                    continue
                before_start = touch_ts < self.started_at
                self._note(
                    f"stale:{rule_name}:{touch_ts:%H%M}",
                    f"{rule_name} level ${trigger_price:.2f} was touched at {_format_ts(touch_ts)}, {age:.0f}s before it was noticed "
                    + (f"(the watcher only started at {_format_ts(self.started_at)}) " if before_start else "")
                    + "-- not filled late; the level needs a fresh touch.",
                    every_minutes=1,
                )
                continue
            if self._try_open(now, touch_ts, trigger_price, trade_type, rule_name, scenario_name, forecast):
                return
            self.consumed[rule_name] = latest_ts

    def _gate_input_values(self, max_age: float) -> tuple:
        """(rsi, adx, atr, adx_rising) from Twelve Data's 15-min candles, reused while younger than `max_age` s. The fetch takes ~1 s and the
        daily Twelve Data allowance is small, so it is done only near an armed level (_maybe_prefetch) or at a touch, never per tick."""
        if self._gate_inputs is not None and time.monotonic() - self._gate_inputs_at < max_age:
            return self._gate_inputs
        try:
            candles = fetch_gold_candles()
            rsi_value, adx_value, atr_value = gold_rsi_adx_atr_from_candles(candles, RSI_PERIOD, ADX_PERIOD, ATR_PERIOD)
            values = (rsi_value, adx_value, atr_value, gold_adx_rising_from_candles(candles, ADX_PERIOD))
        except Exception:
            values = (None, None, None, None)
        self._gate_inputs, self._gate_inputs_at = values, time.monotonic()
        return values

    def _maybe_prefetch(self, candidates: list) -> None:
        """Keeps the gate inputs fresh while price is near an armed level, so a touch can be judged and ordered without a ~1 s fetch."""
        if self._gate_inputs is not None and time.monotonic() - self._gate_inputs_at < GATE_INPUT_PREFETCH_SECONDS:
            return
        for _name, trade_type, _rule, scenario_item in candidates:
            try:
                trigger = _entry_price_and_invalidation(_name, scenario_item)[0]
                last = float(self.feed.side(_entry_side(trade_type)).iloc[-1]["close"])
            except Exception:
                continue
            if abs(last - trigger) <= NEAR_LEVEL_DOLLARS:
                self._gate_input_values(0)
                return

    def _range_15m(self, side: str):
        """High-low range of the last 15 one-minute entry-side bars, from the live feed (the REST bars lag by 1-2 minutes, which is
        exactly when a spike would be missed); None when fewer than 10 minutes of history exist (fail open)."""
        frame = self.feed.side(side)
        if frame.empty:
            return None
        minute = pd.to_datetime(frame["datetime"], utc=True).dt.floor("min")
        bars = frame.groupby(minute).agg(high=("high", "max"), low=("low", "min"))
        return _entry_range(bars)

    def _entry_context(self, now, rsi_value, adx_value, atr_value, dxy_readings, forecast, extra: dict) -> dict:
        """The same snapshot entry_context.build_entry_context() makes, built from values this process already has (cached gate inputs,
        the live feed) instead of fetching candles and bars again: that fetch took 2-5 s while the new position sat on the platform
        without its stop. Never raises."""
        ctx: dict = {}
        try:
            local = now.astimezone(ENTRY_TZ)
            ctx.update(hour_et=local.hour, minute_et=local.minute, weekday=local.strftime("%a"))
            for key, value in (("rsi14", rsi_value), ("adx14", adx_value), ("atr14", atr_value)):
                if value is not None:
                    ctx[key] = _num(value, 2)
            if dxy_readings and len(dxy_readings) >= 2:
                ctx["dxy_change_15m"] = _num(dxy_readings[-1][1] - dxy_readings[0][1])
            ctx["dxy_threshold_15m"] = _num(INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][DXY_CONFIRM_WINDOW_MINUTES])
            levels = (forecast or {}).get("levels") or {}
            ctx.update(bias_score=_num(levels.get("bias_score"), 1), bias=levels.get("bias"), session=levels.get("session"),
                       forecast_id=(forecast or {}).get("id"))
            for side in ("bid", "ask"):
                value = self._range_15m(side)
                if value is not None:
                    ctx[f"range_15m_{side}"] = _num(value, 2)
        except Exception:
            log.exception("entry context incomplete")
        ctx.update({k: v for k, v in (extra or {}).items() if v is not None})
        return ctx

    def _try_open(self, now, touch_ts, trigger_price, trade_type, rule_name, scenario_name, forecast) -> bool:
        side = _entry_side(trade_type)
        rsi_value, adx_value, atr_value, adx_rising = self._gate_input_values(GATE_INPUT_FRESH_SECONDS)
        range_15m = self._range_15m(side)
        rules = get_block_rules()
        dxy_readings = self.context.get("dxy_readings")
        ok, _dedup, reasons = evaluate_gates(
            scenario_name, trade_type, dxy_readings, rsi_value, adx_value, adx_rising, atr_value, range_15m, rules
        )
        if not ok:
            # A level chopping around its trigger is touched many times a minute: at most one notice per level per minute.
            last = self.notified.get(f"blocked:{rule_name}")
            if last is None or time.monotonic() - last >= BLOCKED_NOTICE_MIN_SECONDS:
                self.notified[f"blocked:{rule_name}"] = time.monotonic()
                _send(
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
        entry_context = self._entry_context(
            now, rsi_value, adx_value, atr_value, _trailing_readings(dxy_readings, DXY_CONFIRM_WINDOW_MINUTES), forecast,
            {
                "scenario": scenario_name,
                "trigger_price": round(float(trigger_price), 2),
                "slippage": round(slippage, 2),
                "seconds_since_touch": round((self.feed.last_ts - touch_ts).total_seconds(), 1),
                "spread": round(float(self.feed.side("ask").iloc[-1]["close"] - self.feed.side("bid").iloc[-1]["close"]), 2),
                **level_distances(trigger_price, forecast),
            },
        )
        entry_context.update(shadow_bias_adx(rule_name, trade_type, entry_context))  # log only, blocks nothing (study 32)
        trade_id = insert_forex_b_trade(
            self.mode, rule_name, trade_type, trigger_price, fill_price, touch_ts, now, forecast["id"], trigger_text,
            entry_context, forex_order_id=order_id,
        )
        self.open_trade = get_open_forex_b_trade()
        _send(_message(
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
                _send(_message(0, f"{rule_name} order outcome was unknown; adopted the single open position {positions[0]['OrderId']}.", self.mode))
                return float(positions[0]["Price"]), positions[0]["OrderId"]
            _send(_message(0, f"{rule_name} order outcome UNKNOWN and positions are unclear -- CHECK THE DEMO ACCOUNT. Not retried.", self.mode))
            return None
        except ForexClientError as e:
            _send(_message(0, f"{rule_name} order rejected, nothing opened: {e}", self.mode))
            return None

    def _protect(self, trade: dict) -> None:
        """Attaches the initial platform stop right after the fill: the stop Broker B's exit code gives for a trade with no ticks yet."""
        direction = "buy" if trade["trade_type"] == "Buy" else "sell"
        try:
            _crossing, initial_stop = self._exit_inputs({**trade, "open_ts": datetime.now(timezone.utc)})
            placed = self.client.attach_stop(int(trade["forex_order_id"]), direction, initial_stop)
            set_forex_b_stop_order(trade["id"], placed["stop_order_id"])
            _send(_message(trade["id"], f"platform stop set at ${placed['stop_price']:.2f}.", self.mode))
        except ForexClientError as e:
            _send(_message(
                trade["id"], f"could NOT attach the platform stop ({e}) -- position is UNPROTECTED on forex.com; the watcher manages it.", self.mode,
            ))

    # --- loop -----------------------------------------------------------------------------------------------------

    def idle(self, now: datetime) -> bool:
        """True when there is nothing to watch: no open trade and no entry possible right now."""
        return self.open_trade is None and not self._entry_ok(now) and not _commands_pending()

    def _check_feed_lag(self, newest_tick_ts, now: datetime) -> None:
        """Warns (Telegram, at most one per 15 min) when the newest tick is more than FEED_LAG_WARN_SECONDS old."""
        lag = (now - newest_tick_ts).total_seconds()
        if lag <= FEED_LAG_WARN_SECONDS:
            return
        log.warning("price feed is %.0f s behind (newest tick %s)", lag, newest_tick_ts)
        if self.role == "active":
            self._note(
                "feed-lag",
                f"the price feed is {lag:.0f} s behind (newest tick {_format_ts(newest_tick_ts)}). Touches and stops are judged on "
                f"that older price until it catches up.",
            )

    def watch_stream(self, stream: PriceStream, now: datetime) -> None:
        """Watchdog: reconnects (with a fresh login) when the stream has been silent too long, and reports a lagging feed."""
        silent = stream.seconds_since_tick()
        if silent > STREAM_SILENT_RECONNECT_SECONDS and time.monotonic() - self.stream_restart_at >= STREAM_RESTART_MIN_SECONDS:
            self.stream_restart_at = time.monotonic()
            log.warning("stream silent for %.0f s (status %s, last error %s) -- logging in again and reconnecting", silent, stream.status, stream.last_error)
            if self.role == "active":
                self._note("stream-silent", f"the live price stream was silent for {silent:.0f} s ({stream.status}) -- reconnecting; "
                                            f"REST prices (delayed ~1-2 min) are used meanwhile and no new entry is made on stale prices.")
            try:
                self.client.refresh_session()
            except Exception:
                log.exception("could not log in again")
            try:
                stream.restart()
            except Exception:
                log.exception("could not restart the stream")
        if self.feed.last_ts is not None:
            self._check_feed_lag(self.feed.last_ts, now)

    def _heartbeat(self, force: bool = False) -> None:
        if self.role != "active":
            return
        if not force and time.monotonic() - self.heartbeat_at < 60:
            return
        self.heartbeat_at = time.monotonic()
        try:
            touch_forex_b_heartbeat(self.mode, self.feed.last_ts)
        except Exception:
            log.exception("heartbeat failed")

    def step(self, now: datetime, fresh: bool) -> None:
        """One pass of the active watcher: queued Telegram commands (every COMMAND_CHECK_SECONDS), then -- when new ticks arrived --
        the exit check for an open trade or the entry check for the armed levels."""
        self._heartbeat()
        if time.monotonic() - self.commands_at >= COMMAND_CHECK_SECONDS:
            self.commands_at = time.monotonic()
            self._handle_commands(now)
        if not fresh or self.last_quote is None:
            return
        if self.open_trade is not None:
            self._check_exit(now)
        elif self._entry_ok(now):
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
                _send(f"{TRADE_PREFIX.rstrip()}{BLOCKED_MARKER}BROKER F ({self.mode}): '{cmd['command']}' not done -- {result}")
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
        if trading_pause_reason(now, "F") is not None:
            return "rejected", 'trading is stopped/paused for Broker F (send "start trading broker F" or "start trading" first).'
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
        _send(_message(
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
        _send(
            f"{TRADE_PREFIX.rstrip()}BROKER F (live): not trading -- the demo account already has {len(extra)} untracked XAU/USD "
            f"position(s) ({', '.join(str(p.get('OrderId')) for p in extra)}). Close them or switch to shadow mode."
        )
        log.error("untracked XAU/USD positions on the account -- refusing to run live")
        return False
    return True


def _decide_role(holder: str, ends_at: datetime) -> tuple[str, dict] | None:
    """("active", lease state) if this process may trade now, ("standby", {}) if it should wait warm for the active one to finish,
    None if it is not needed (a healthy watcher has plenty of time left, or a standby is already waiting)."""
    try:
        if forex_legacy_watcher_alive():
            log.info("a watcher without the lease (the previous version) is still running -- not trading alongside it, exiting")
            return None
    except Exception:
        log.exception("could not check for a previous-version watcher")
    acquired = forex_lease_acquire(holder, ends_at)
    if acquired is not None:
        return "active", acquired["state"]
    status = forex_lease_status()
    if status is None or not status["alive"]:
        acquired = forex_lease_acquire(holder, ends_at)  # the holder released between our two reads
        if acquired is not None:
            return "active", acquired["state"]
        status = forex_lease_status()
    if status and status["alive"] and status["ends_in"] is not None and status["ends_in"] > STANDBY_LEAD_SECONDS:
        log.info("another watcher is active and ends in %.0f s -- not needed yet, exiting", status["ends_in"])
        return None
    if forex_lease_register_standby(holder):
        return "standby", {}
    log.info("a standby watcher is already waiting -- exiting")
    return None


def run(max_minutes: float, mode: str) -> None:
    started = time.monotonic()
    init_db()
    if get_open_forex_b_trade() is None and not _entry_possible(datetime.now(timezone.utc)) and not _commands_pending():
        log.info("Nothing to watch (market closed, outside the entry window, or paused) -- exiting")
        return
    holder = f"{os.environ.get('GITHUB_RUN_ID', 'local')}-{uuid.uuid4().hex[:6]}"
    soft = started + max_minutes * 60  # planned end; an active run with no standby waiting goes on until `hard`
    hard = soft + HARD_EXTRA_SECONDS

    def ends_at_now() -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=max(0.0, soft - time.monotonic()))

    decided = _decide_role(holder, ends_at_now())
    if decided is None:
        return
    role, lease_state = decided
    client = ForexClient()
    if role == "active" and mode == "live" and not _live_ready(client):
        forex_lease_release(holder)
        return
    feed = Feed(client)
    try:
        feed.add_ticks(client.get_ticks(SEED_TICKS))  # the freshest REST history (~2 min old); the stream continues from there
    except Exception:
        log.exception("could not seed REST ticks")
    stream = PriceStream(client.username, lambda: client.session_token, client.market_id())
    stream.start()
    watcher = Watcher(client, mode, feed)
    if feed.ticks:
        watcher.last_quote = {"ts": feed.ticks[-1][0], "bid": feed.ticks[-1][1], "ask": feed.ticks[-1][2]}
    if role == "active":
        watcher.activate(lease_state)
    log.info("Broker F watcher started (%s mode, holder %s, role %s)", mode, holder, role)
    registered_at = last_renew = last_renew_ok = last_register = last_acquire = last_rest = last_idle = started
    try:
        while True:
            batch = stream.drain(LOOP_SECONDS)
            nowm = time.monotonic()
            if stream.seconds_since_tick() > REST_FALLBACK_AFTER_SECONDS and nowm - last_rest >= REST_FALLBACK_EVERY_SECONDS:
                last_rest = nowm  # the stream is down: the laggy REST history still lets an open trade's stop be judged
                try:
                    batch = batch + client.get_ticks(SEED_TICKS)
                except Exception:
                    log.exception("REST fallback failed")
            fresh = feed.add_ticks(batch) if batch else False
            if fresh:
                watcher.last_quote = {"ts": feed.ticks[-1][0], "bid": feed.ticks[-1][1], "ask": feed.ticks[-1][2]}
            now = datetime.now(timezone.utc)
            watcher.watch_stream(stream, now)

            if watcher.role == "active":
                if nowm - last_renew >= LEASE_RENEW_SECONDS:
                    last_renew = nowm
                    try:
                        renewed = forex_lease_acquire(holder, ends_at_now(), watcher.lease_state())
                    except Exception:
                        log.exception("lease renewal failed")
                        renewed = "error"
                    if renewed is None or (renewed == "error" and nowm - last_renew_ok > LEASE_TTL_SECONDS - 6):
                        watcher.demote()  # another process holds it, or we cannot prove we still do: stop acting
                        registered_at = nowm
                        continue
                    if renewed != "error":
                        last_renew_ok = nowm
                try:
                    watcher.step(now, fresh)
                except Exception as e:
                    log.exception("step failed -- continuing")
                    watcher._note("tick", f"a check failed ({type(e).__name__}: {str(e)[:200]}) -- it keeps running.")
                if nowm - last_idle >= 2:
                    last_idle = nowm
                    if watcher.idle(now):
                        log.info("Nothing left to watch -- exiting")
                        return
                if nowm >= soft:
                    try:
                        status = forex_lease_status()
                    except Exception:
                        status = None
                    if nowm >= hard or (status and status["standby_alive"]):
                        log.info("planned end reached -- handing over (%s)", "standby waiting" if status and status["standby_alive"] else "hard limit")
                        return
            else:
                if nowm - last_register >= STANDBY_REGISTER_SECONDS:
                    last_register = nowm
                    try:
                        forex_lease_register_standby(holder)
                    except Exception:
                        log.exception("standby registration failed")
                if nowm - last_acquire >= 2:
                    last_acquire = nowm
                    try:
                        got = forex_lease_acquire(holder, datetime.now(timezone.utc) + timedelta(seconds=max_minutes * 60))
                    except Exception:
                        log.exception("lease acquire failed")
                        got = None
                    if got is not None:
                        soft = nowm + max_minutes * 60
                        hard = soft + HARD_EXTRA_SECONDS
                        last_renew = last_renew_ok = nowm
                        if mode == "live" and not _live_ready(client):
                            forex_lease_release(holder)
                            return
                        watcher.activate(got["state"])
                        continue
                if nowm - registered_at > STANDBY_MAX_WAIT_SECONDS:
                    log.info("standby waited too long without taking over -- exiting")
                    return
    finally:
        try:
            watcher._heartbeat(force=True)  # records the last evaluated tick for the next process
            if watcher.role == "active":
                forex_lease_release(holder, watcher.lease_state())
        except Exception:
            log.exception("could not hand the lease over cleanly")
        stream.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--max-minutes", type=float, default=5.0, help="how long to loop before exiting (default 5)")
    parser.add_argument("--mode", choices=("shadow", "live"), default="shadow")
    args = parser.parse_args()
    run(args.max_minutes, args.mode)
