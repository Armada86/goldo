"""Forex B: Broker B's rules, watched at tick granularity against the FOREX.com demo account.

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
      entry-/exit-side prices, and sends Telegram messages, but sends NO order to forex.com. This is what answers "how
      do live fills differ from Broker B's candle-based ones?" with zero order risk.
  live -- real demo-account orders. NOT implemented yet: the order plumbing (resting stop at the platform, amending
      or cancelling it, closing a position that has one) has to be verified on the demo account first; see
      docs/forex-b.md "Open questions". Refuses to start.

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
    _scan_exit_crossing,
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
from forex_client import ForexClient
from market_hours import is_market_closed
from notifier import send_telegram_message
from storage import (
    close_forex_b_trade,
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
    return f"{TRADE_PREFIX.rstrip()}{marker}FOREX B ({mode}) #{trade_id}: {text}"


def _entry_possible(now: datetime) -> bool:
    """A fresh entry is allowed right now: market open, inside the 7am-5pm ET weekday window, trading not paused."""
    return not is_market_closed() and _within_entry_window(now) and trading_pause_reason(now) is None


class Watcher:
    def __init__(self, client: ForexClient, mode: str):
        self.client = client
        self.mode = mode
        self.feed = Feed(client)
        self.open_trade = get_open_forex_b_trade()
        self.consumed: dict[str, datetime] = {}  # rule_name -> newest tick time a blocked / stale touch used up
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

    def _check_exit(self, now: datetime) -> None:
        trade = self.open_trade
        exit_frame = self.feed.side(_exit_side(trade["trade_type"]))
        latest = exit_frame.iloc[-1]
        price_col = "low" if trade["trade_type"] == "Buy" else "high"
        paused = trading_pause_reason(now) is not None
        crossing = None
        if not paused:
            ticks = exit_frame[exit_frame["datetime"] > pd.Timestamp(trade["open_ts"])]
            fade = trade["rule_name"] in FADE_OPPOSITE_SCENARIO
            activation, distance = trailing_stop_params()
            crossing = _scan_exit_crossing(
                trade, ticks, stop_loss_threshold(), activation, distance, self._refresh_context().get("lock_level"), fade
            )
        if crossing is None and not paused:
            return
        if paused:
            stop_level, exit_price, exit_ts, reason = None, float(latest["close"]), now, "trading paused"
        else:
            stop_level, crossed_ts = crossing
            hit = exit_frame[exit_frame["datetime"] == pd.Timestamp(crossed_ts)].iloc[0]
            # The market close happens when the tick is seen: at that tick's price, which is at or beyond the stop.
            exit_price, exit_ts, reason = float(hit[price_col]), crossed_ts, "stop"
        pnl = _pnl(trade, exit_price)
        close_forex_b_trade(trade["id"], exit_price, exit_ts, pnl, stop_level, reason)
        stop_text = f" (stop level ${stop_level:.2f}, slipped ${abs(exit_price - stop_level):.2f})" if stop_level is not None else ""
        send_telegram_message(_message(
            trade["id"],
            f"closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f}{stop_text} (opened @ ${trade['entry_price']:.2f}, "
            f"rule {trade['rule_name']}) -- {'profit' if pnl >= 0 else 'loss'} of ${abs(pnl):.2f}\nFilled: {_format_ts(exit_ts)}",
            self.mode, _result_marker(pnl, PROFIT_MARKER, LOSS_MARKER),
        ))
        log.info("closed #%s %s pnl %+.2f (%s)", trade["id"], trade["rule_name"], pnl, reason)
        self.open_trade = None
        self._refresh_context(force=True)

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
                f"{TRADE_PREFIX.rstrip()}{BLOCKED_MARKER}FOREX B ({self.mode}): {rule_name} level ${trigger_price:.2f} "
                f"reached but blocked -- {reasons}."
            )
            log.info("%s blocked: %s", rule_name, reasons)
            return False
        fill_price = float(self.feed.side(side).iloc[-1]["close"])  # a Buy fills on the ask, a Sell on the bid
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
            entry_context,
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
        self._refresh_context(force=True)
        return True

    # --- loop -----------------------------------------------------------------------------------------------------

    def idle(self, now: datetime) -> bool:
        """True when there is nothing to watch: no open trade and no entry possible right now."""
        return self.open_trade is None and not _entry_possible(now)

    def step(self) -> None:
        if not self.feed.add(self.client.get_quote()):
            return  # no new tick since the last look
        now = datetime.now(timezone.utc)
        if self.open_trade is not None:
            self._check_exit(now)
        elif _entry_possible(now):
            self._check_entry(now)


def run(max_minutes: float, mode: str) -> None:
    if mode != "shadow":
        raise SystemExit(
            "live mode is not implemented: the order plumbing must be verified on the demo account first "
            "(see docs/forex-b.md, 'Open questions'). Use --mode shadow."
        )
    deadline = time.monotonic() + max_minutes * 60
    init_db()
    if get_open_forex_b_trade() is None and not _entry_possible(datetime.now(timezone.utc)):
        log.info("Nothing to watch (market closed, outside the entry window, or paused) -- exiting")
        return
    watcher = Watcher(ForexClient(), mode)
    log.info("Forex B watcher started (%s mode); open trade: %s", mode, watcher.open_trade and watcher.open_trade["id"])
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
