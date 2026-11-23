"""Broker R at tick granularity (16 Nov 2026) -- runs inside forex_watcher.py's loop, on the same live bid/ask stream and the same Postgres lease
as Broker F (so exactly one process trades both, and "does Broker F have a trade" is answered from memory, not racing a second process).

Rules are Broker R's own (broker_r.py): fades only at the half-hourly short-range TA's nearest resistance/support, strict +$5/-$5, no entry
filters beyond the 7am-5pm ET window and `stop trading` / pauses. What changed from the 5-minute poll: a touch is noticed within a second (the same
`broker_b._scan_zone_entry()` on tick frames, a touch older than ENTRY_MAX_TOUCH_AGE_SECONDS is dropped), the entry fills at the live tick (paper)
or at the real order's fill (live), and a paper trade's +/-$5 is judged on every tick.

Venue (FOREX_R_MODE, a repo variable passed by forex_watch.yml; default paper): `live` sends a real market order on the FOREX.com demo account with a
platform TP/SL -- but only while Broker F has no open trade and the account has no other XAU/USD position; otherwise the trade is a paper trade and
Telegram says "not on Forex because ...". See broker_r.choose_venue()/open_trade_r()/manage_live_trade().
"""

import logging
import time
from datetime import datetime, timezone

import pandas as pd

from broker_b import _entry_side, _scan_zone_entry
from broker_r import (
    TRADE_ALERT_PREFIX,
    _close_message,
    _find_exit,
    _pnl,
    armed_scenarios,
    choose_venue,
    close_live_now,
    forex_mode,
    manage_live_trade,
    open_trade_r,
    reconcile_if_gone,
    scan_trail,
)
from market_hours import is_market_closed
from notifier import send_telegram_message
from storage import close_trade_row_r, get_last_close_ts_r, get_latest_ta_short_forecast, get_open_trade_r, get_open_trades_r
from ta_short import DISPLAY_TZ, in_window
from trading_control import trading_pause_reason

log = logging.getLogger("forex-watcher")

ENTRY_MAX_TOUCH_AGE_SECONDS = 15
ENTRY_MAX_FEED_AGE_SECONDS = 20
CONTEXT_REFRESH_SECONDS = 60
GATE_CACHE_SECONDS = 5
SWEEP_SECONDS = 60  # how often every Open R row is compared with what this trader tracks (orphan rows, 23 Nov 2026)
POSITION_CHECK_SECONDS = 3  # live: how often the open position is read from forex.com


def entry_possible(now: datetime) -> bool:
    """A fresh Broker R entry is allowed right now: market open, 7am-5pm ET weekday, not paused for R."""
    return not is_market_closed() and in_window(now) and trading_pause_reason(now, "R") is None


class RTickTrader:
    """Owned by a Watcher; `w` supplies the client, the live feed, Broker F's open trade, the role and the shared used-up-touch dict."""

    def __init__(self, watcher):
        self.w = watcher
        self.open_trade: dict | None = None
        self.context: dict = {}
        self.context_at = -1e9
        self.position_checked_at = 0.0
        self.swept_at = -1e9
        self.notified: dict[str, float] = {}
        self._gate_at = -1e9
        self._paused = False
        self._entry_ok_at = -1e9
        self._entry_ok_value = False

    # --- helpers ---------------------------------------------------------------------------------------------------

    def _note(self, key: str, text: str, every_minutes: float = 15.0) -> None:
        last = self.notified.get(key)
        if last is not None and time.monotonic() - last < every_minutes * 60:
            return
        self.notified[key] = time.monotonic()
        try:
            send_telegram_message(f"{TRADE_ALERT_PREFIX.rstrip()}⚠️ BROKER R: {text}")
        except Exception:
            log.exception("could not send a Broker R note")

    def _is_paused(self, now: datetime) -> bool:
        if time.monotonic() - self._gate_at >= GATE_CACHE_SECONDS:
            self._paused = trading_pause_reason(now, "R") is not None
            self._gate_at = time.monotonic()
        return self._paused

    def entry_ok(self, now: datetime) -> bool:
        if time.monotonic() - self._entry_ok_at >= GATE_CACHE_SECONDS:
            self._entry_ok_value = not is_market_closed() and in_window(now) and not self._is_paused(now)
            self._entry_ok_at = time.monotonic()
        return self._entry_ok_value

    def idle(self, now: datetime) -> bool:
        return self.open_trade is None and not self.entry_ok(now)

    def _price(self, side: str) -> float:
        return float(self.w.feed.side(side).iloc[-1]["close"])

    # --- lifecycle -------------------------------------------------------------------------------------------------

    def activate(self) -> None:
        """This process now trades: pick up an open Broker R trade. A paper trade older than the feed's history is first judged on 1-minute
        bars (it may have been left open overnight or across a handover), exactly as the old poll did."""
        self.open_trade = get_open_trade_r()
        self.context_at = -1e9
        self.position_checked_at = 0.0
        trade = self.open_trade
        if trade is not None and trade.get("mode") != "live":
            now = datetime.now(timezone.utc)
            spot = self._price(_exit_side(trade["trade_type"])) if self.w.feed.ticks else trade["entry_price"]
            result = _find_exit(trade, spot, now)
            if result is not None:
                exit_price, exit_ts, reason = result
                self._close_paper(trade, exit_price, exit_ts, reason)

    def demote(self) -> None:
        self.open_trade = None

    def close_for_broker_f(self, now: datetime) -> bool:
        """Broker F has priority (16 Nov 2026): when an F entry is about to open, any open R trade -- live or paper, in profit or loss -- is closed
        on the spot. True when R holds nothing afterwards; False if a live close was not confirmed (F must then not open: the two would share the account)."""
        trade = self.open_trade
        if trade is None:
            return True
        if not self.w.feed.ticks:
            return False
        spot = self._price(_exit_side(trade["trade_type"]))
        if trade.get("mode") == "live":
            if not close_live_now(trade, spot, now, self.w.client):
                return False
        else:
            self._close_paper(trade, spot, now, "Priority")
        self.open_trade = None
        self.context_at = -1e9
        return True

    # --- per tick --------------------------------------------------------------------------------------------------

    def _sweep_orphans(self, now: datetime) -> None:
        """Every Open R row other than the one tracked here is an orphan (e.g. two processes both opened a trade in a handover overlap, 23 Nov 2026:
        the live #203 was never managed or closed and blocked Broker F). A live orphan whose position is gone is closed from the trade history;
        a paper one is judged on 1-minute bars; with nothing tracked, a still-live orphan is adopted and managed normally."""
        if time.monotonic() - self.swept_at < SWEEP_SECONDS:
            return
        self.swept_at = time.monotonic()
        try:
            rows = get_open_trades_r()
        except Exception:
            return
        tracked = self.open_trade["id"] if self.open_trade is not None else None
        for trade in rows:
            if trade["id"] == tracked:
                continue
            if trade.get("mode") == "live":
                if reconcile_if_gone(trade, self.w.client, now):
                    log.warning("Broker R: orphaned live row #%s was already closed on forex.com -- recorded", trade["id"])
                elif self.open_trade is None:
                    self.open_trade = trade
                    tracked = trade["id"]
                    log.warning("Broker R: adopted orphaned live row #%s", trade["id"])
            else:
                spot = self._price(_exit_side(trade["trade_type"])) if self.w.feed.ticks else trade["entry_price"]
                result = _find_exit(trade, spot, now)
                if result is not None:
                    exit_price, exit_ts, reason = result
                    pnl = _pnl(trade["trade_type"], trade["entry_price"], exit_price)
                    close_trade_row_r(trade["id"], exit_price, exit_ts, pnl, reason, {"reason": reason, "orphan": True})
                    log.warning("Broker R: orphaned paper row #%s closed (%s)", trade["id"], reason)

    def step(self, now: datetime) -> None:
        if self.w.role != "active":
            return
        self._sweep_orphans(now)
        if self.open_trade is not None:
            self._check_exit(now)
        elif self.entry_ok(now):
            self._check_entry(now)

    def _close_paper(self, trade: dict, exit_price: float, exit_ts, reason: str) -> None:
        pnl = _pnl(trade["trade_type"], trade["entry_price"], exit_price)
        held = round((pd.Timestamp(exit_ts) - pd.Timestamp(trade["open_ts"])).total_seconds() / 60, 1)
        close_trade_row_r(trade["id"], exit_price, exit_ts, pnl, reason, {"reason": reason, "held_minutes": held})
        send_telegram_message(_close_message(trade, exit_price, exit_ts, pnl, reason))
        self.open_trade = None
        self.context_at = -1e9

    def _check_exit(self, now: datetime) -> None:
        trade = self.open_trade
        paused = self._is_paused(now)
        spot = self._price(_exit_side(trade["trade_type"]))
        if paused and trade.get("mode") != "live":
            self._close_paper(trade, spot, now, "Stop")
            return
        # The stop (-$5, then locked at +$5, then trailing $5 behind the best price) is judged on every tick, here, for both modes.
        frame = self.w.feed.side(_exit_side(trade["trade_type"]))
        hit, stop_now = scan_trail(trade["trade_type"], trade["entry_price"], pd.Timestamp(trade["open_ts"]), frame)
        if trade.get("mode") != "live":
            if hit is not None:
                exit_price, exit_ts, reason = hit
                self._close_paper(trade, exit_price, exit_ts, reason)
            return
        # Live: the stop also sits on the platform (kept at stop_now every POSITION_CHECK_SECONDS); if our own scan says it was crossed
        # first (a quick spike and reversal between two checks) the position is closed at market at once.
        if hit is not None and not paused:
            if close_live_now(trade, spot, now, self.w.client, reason=hit[2], desired_stop=stop_now):
                self.open_trade = None
                self.context_at = -1e9
            return
        if paused or time.monotonic() - self.position_checked_at >= POSITION_CHECK_SECONDS:
            self.position_checked_at = time.monotonic()
            if manage_live_trade(trade, spot, now, paused, self.w.client, desired_stop=stop_now):
                self.open_trade = None
                self.context_at = -1e9

    def _refresh_context(self) -> dict:
        if time.monotonic() - self.context_at < CONTEXT_REFRESH_SECONDS:
            return self.context
        now = datetime.now(timezone.utc)
        context: dict = {}
        try:
            forecast = get_latest_ta_short_forecast()
            context["forecast"] = forecast
            context["armed"] = armed_scenarios(forecast, now) if forecast and forecast.get("levels") else []
            context["last_close_ts"] = get_last_close_ts_r()
        except Exception:
            log.exception("Broker R: could not refresh the short-range forecast -- keeping the previous one")
            self._note("refresh", "could not read the short-range TA from the database -- no entries until it recovers.")
            context = self.context
        self.context, self.context_at = context, time.monotonic()
        return context

    def _check_entry(self, now: datetime) -> None:
        context = self._refresh_context()
        forecast, armed = context.get("forecast"), context.get("armed") or []
        feed = self.w.feed
        if not armed or feed.last_ts is None:
            return
        if (now - feed.last_ts).total_seconds() > ENTRY_MAX_FEED_AGE_SECONDS:
            return  # stale feed: judge nothing, consume nothing
        floor = pd.Timestamp(forecast["ts"])
        if context.get("last_close_ts") is not None:
            floor = max(floor, pd.Timestamp(context["last_close_ts"]))
        touches = []
        for name, trade_type, rule, scenario in armed:
            frame = feed.side(_entry_side(trade_type))
            rule_floor = max(floor, pd.Timestamp(self.w.consumed[rule])) if rule in self.w.consumed else floor
            touch = _scan_zone_entry(name, scenario, frame[frame["datetime"] > rule_floor])
            if touch is not None:
                touches.append((touch[1], touch[0], trade_type, rule, name))
        if not touches:
            return
        touches.sort(key=lambda t: t[0])
        latest_ts = feed.last_ts
        touch_ts, level, trade_type, rule, name = touches[0]
        age = (latest_ts - touch_ts).total_seconds()
        self.w.consumed[rule] = latest_ts  # whatever happens next, this touch is used up
        if age > ENTRY_MAX_TOUCH_AGE_SECONDS:
            log.info("Broker R %s touch at %s is %.0fs old -- dropped", rule, touch_ts, age)
            return
        client, paper_reason = None, None
        if forex_mode() == "live":
            paper_reason = choose_venue(self.w.client, self.w.open_trade is not None)
            client = self.w.client if paper_reason is None else None
        spot = self._price(_entry_side(trade_type))
        ctx = {
            "scenario": name, "trigger_price": round(float(level), 2), "spot_at_poll": round(spot, 2),
            "seconds_since_touch": round(age, 1),
            "hour_et": now.astimezone(DISPLAY_TZ).hour, "minute_et": now.astimezone(DISPLAY_TZ).minute,
        }
        existing = get_open_trade_r()
        if existing is not None:  # another process opened one in the meantime: never a second open R row
            self.open_trade = existing
            return
        trade_id = open_trade_r(client, paper_reason, trade_type, rule, name, forecast, level, touch_ts, spot, now, ctx)
        self.open_trade = get_open_trade_r()
        self.context_at = -1e9
        log.info("Broker R opened #%s %s (level %.2f)", trade_id, rule, level)


def _exit_side(trade_type: str) -> str:
    return "bid" if trade_type == "Buy" else "ask"

