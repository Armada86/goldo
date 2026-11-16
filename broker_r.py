"""Broker R -- a short-range fade paper broker (5 Nov 2026).

Broker B's fade logic, shrunk. Differences from Broker B, all by the user's design:
  - Its own, much tighter technical analysis (ta_short.py, a new row every 30 minutes in `ta_short_forecasts`): the resistance and
    support it trades are only $3-$10 from price.
  - Fades only (`R-Fade-sell` at the resistance, `R-Fade-buy` at the support). No breakouts.
  - Strict fixed exits on a 1-oz position: take-profit +$5 or stop-loss -$5 (config R_TAKE_PROFIT / R_STOP_LOSS). No trailing
    stop, no profit lock, no time stop, no fade cap, and `make SL` / `make trail` / SLA do not touch it.
  - No entry filters: no DXY / RSI / ADX / ATR / spike gates, no block rules (BRA), no TA-bias gate, no "bias mode". The only
    conditions are the 7am-5pm ET weekday trading window and Telegram `stop trading` / scheduled pauses (which reach it through the
    global state, since the Worker does not name R).
It borrows Broker B's touch scan unchanged (`broker_b._scan_zone_entry()`): a level counts only once price was first seen on its
approach side; it is fed live ticks, and the fill is the live entry-side tick (ask for a Buy, bid for a Sell) or the real order fill. A touch
older than forex_r.ENTRY_MAX_TOUCH_AGE_SECONDS when noticed is never filled. A level is traded at most R_MAX_TRADES_PER_LEVEL times per
forecast row and is retired by its first stop-out (re-arm only after a win), then a new half-hourly analysis supplies new levels.
Tick-level (16 Nov 2026): Broker R no longer runs in the 5-minute poll. forex_r.py runs inside forex_watcher.py's loop (same live bid/ask stream, same
lease as Broker F), judging every tick. The poll only produces the half-hourly short-range TA and scores the shadow exit.
FOREX.com demo: with the repo variable FOREX_R_MODE=live (default paper) R's trades are real market orders on the FOREX.com demo account, with a
take-profit and stop-loss attached on the platform at fill +/- R_TAKE_PROFIT / R_STOP_LOSS, but ONLY while Broker F has no open trade (any mode)
and the account shows no other XAU/USD position; otherwise the trade is opened as paper at the live tick price and Telegram says it is not on
Forex and why. Broker F in turn refuses to place an order while an R live trade is open (forex_watcher._place_entry), because both share one
account and an opposite order could net against the other's position.
One position at a time. Paper by default; own table `broker_r_trades`, never read by SLA / BRA / the reviews. Telegram: orange square.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from broker import _format_ts
from config import (
    R_MAX_TRADES_PER_LEVEL, R_SHADOW_STOP_LOSS, R_SHADOW_TAKE_PROFIT, R_STOP_LOSS, R_TA_FORECAST_MAX_AGE_MINUTES, R_TAKE_PROFIT,
)
from forex_client import TRADABLE_MARKET_ID, ForexClient, ForexClientError, ForexOrderUncertainError
from notifier import send_telegram_message
from price_bars import fetch_gold_bars
from storage import (
    close_trade_row_r,
    get_trades_r_without_shadow,
    insert_trade_r,
    set_shadow_exit_r,
    trade_r_level_history,
)
from ta_short import DISPLAY_TZ, WINDOW_END_HOUR_ET

log = logging.getLogger("gold-monitor")

TRADE_ALERT_PREFIX = "\U0001f7e7 "  # orange square (A blue circle, B blue square, F purple, K brown)
PROFIT_MARKER = "\U0001f7e9 "
LOSS_MARKER = "\U0001f7e5 "
EXIT_BARS_MAX = 3000
SHADOW_MAX_AGE_HOURS = 48  # older unresolved trades are left alone (the bars may not reach back to their entry)

SCENARIOS = {"sell_resistance": ("Sell", "R-Fade-sell"), "buy_support": ("Buy", "R-Fade-buy")}

NOT_ON_FOREX_F = "Broker F already has a trade"


def forex_mode() -> str:
    """'live' only when the FOREX_R_MODE environment variable (the repo variable of the same name, passed by poll.yml) is 'live'."""
    return "live" if os.environ.get("FOREX_R_MODE", "paper").strip().lower() == "live" else "paper"


def _label(mode: str | None, paper_reason: str | None = None) -> str:
    if mode == "live":
        return "BROKER R (Forex demo)"
    return "BROKER R (paper)" if paper_reason else "BROKER R"


def _xau_positions(client: ForexClient) -> list[dict]:
    return [p for p in client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]


def choose_venue(client: ForexClient, broker_f_has_trade: bool) -> str | None:
    """None when this entry may be a real FOREX.com demo order, else the reason it is paper, worded to follow "not on Forex because ".
    FOREX_R_MODE not 'live' is plain paper trading and also returns None here -- the caller checks forex_mode() first. Every doubt means paper."""
    if broker_f_has_trade:
        return NOT_ON_FOREX_F
    try:
        if _xau_positions(client):
            return "forex.com already shows an open XAU/USD position"
    except Exception as e:
        log.exception("Broker R: forex.com unavailable")
        return f"forex.com could not be reached ({e})"
    return None


def _place_live(client: ForexClient, trade_type: str) -> tuple[dict | None, str | None]:
    """(order result, None) or (None, reason). A failed or uncertain order is never retried."""
    try:
        return client.place_market_order("buy" if trade_type == "Buy" else "sell"), None
    except ForexOrderUncertainError as e:
        log.exception("Broker R: order outcome unknown")
        return None, f"the order outcome was unknown -- CHECK THE DEMO ACCOUNT ({e})"
    except ForexClientError as e:
        log.exception("Broker R: order rejected")
        return None, f"the order was rejected ({e})"


def _attach_exits(client: ForexClient, order_id, trade_type: str, fill_price: float) -> str | None:
    """Attaches the platform TP/SL; returns an error text if that failed (the poll then watches the price itself)."""
    try:
        client.attach_take_profit_and_stop_loss(
            order_id, "buy" if trade_type == "Buy" else "sell", fill_price, R_STOP_LOSS, limit_distance=R_TAKE_PROFIT
        )
        return None
    except ForexClientError as e:
        log.exception("Broker R: could not attach TP/SL")
        return str(e)


def _close_live_at_market(client: ForexClient, trade: dict, position: dict) -> tuple[float, object] | None:
    """Cancels the platform TP/SL (so neither fires against the close) and closes at market; None if not confirmed."""
    for key in ("StopOrder", "LimitOrder"):
        order = position.get(key)
        if order:
            try:
                client.cancel_order(order["OrderId"])
            except ForexClientError as e:
                log.warning("Broker R: could not cancel %s %s: %s", key, order.get("OrderId"), e)
    try:
        result = client.close_position(trade["trade_type"])
    except ForexClientError as e:
        send_telegram_message(f"{TRADE_ALERT_PREFIX}\u26a0\ufe0f BROKER R (Forex demo) #{trade['id']}: market close NOT confirmed ({e}) -- CHECK THE DEMO ACCOUNT.")
        return None
    return result["fill_price"], result["order_id"]


def _finish_live(trade: dict, exit_price: float, exit_ts: datetime, reason: str, close_order_id, note: str = "") -> None:
    pnl = _pnl(trade["trade_type"], trade["entry_price"], exit_price)
    close_trade_row_r(
        trade["id"], exit_price, exit_ts, pnl, reason,
        {"reason": reason, "held_minutes": round((exit_ts - trade["open_ts"]).total_seconds() / 60, 1)}, close_order_id,
    )
    send_telegram_message(_close_message(trade, exit_price, exit_ts, pnl, reason) + note)


def manage_live_trade(trade: dict, spot: float, now: datetime, paused: bool, client: ForexClient | None = None) -> bool:
    """One poll's look at an open live trade. Its TP/SL sit on forex.com, so normally this only notices that the position is gone and records
    the close from the trade history. Returns True once the trade is closed. Any forex.com problem leaves it open for the next poll."""
    try:
        client = client or ForexClient()
        position = next((p for p in _xau_positions(client) if str(p.get("OrderId")) == str(trade["forex_order_id"])), None)
    except Exception:
        log.exception("Broker R: could not read the live position -- trying again next poll")
        return False
    if position is None:
        closing = None
        try:
            closing = client.find_closing_trade(int(trade["forex_order_id"]))
        except Exception:
            log.exception("Broker R: could not read the closing trade")
        if closing is not None:
            price, ts, order_id, note = closing["price"], closing["closed_at"] or now, closing["order_id"], ""
        else:
            sign = 1.0 if trade["trade_type"] == "Buy" else -1.0
            levels = (trade["entry_price"] + sign * R_TAKE_PROFIT, trade["entry_price"] - sign * R_STOP_LOSS)
            price, ts, order_id = min(levels, key=lambda lv: abs(lv - spot)), now, None
            note = "\n(exit price ESTIMATED from the nearer TP/SL level -- not found in forex.com trade history)"
        pnl = _pnl(trade["trade_type"], trade["entry_price"], price)
        _finish_live(trade, price, ts, "TP" if pnl > 0 else "SL", order_id, note)
        return True
    if paused:
        closed = _close_live_at_market(client, trade, position)
        if closed is None:
            return False
        _finish_live(trade, closed[0], datetime.now(timezone.utc), "Stop", closed[1])
        return True
    if not (position.get("StopOrder") and position.get("LimitOrder")):
        # TP/SL missing (the attach failed, or one leg is gone): try again; if that fails the poll judges +/-$ on the spot price itself.
        for key in ("StopOrder", "LimitOrder"):
            if position.get(key):
                try:
                    client.cancel_order(position[key]["OrderId"])
                except ForexClientError:
                    pass
        if _attach_exits(client, trade["forex_order_id"], trade["trade_type"], trade["entry_price"]) is not None:
            pnl = _pnl(trade["trade_type"], trade["entry_price"], spot)
            if pnl <= -R_STOP_LOSS or pnl >= R_TAKE_PROFIT:
                closed = _close_live_at_market(client, trade, position)
                if closed is not None:
                    _finish_live(trade, closed[0], datetime.now(timezone.utc), "TP" if pnl > 0 else "SL", closed[1])
                    return True
    return False


def _pnl(trade_type: str, entry_price: float, price: float) -> float:
    return price - entry_price if trade_type == "Buy" else entry_price - price


def scan_exit(
    trade_type: str, entry_price: float, open_ts: datetime, bars: pd.DataFrame,
    take_profit: float = R_TAKE_PROFIT, stop_loss: float = R_STOP_LOSS,
) -> tuple[float, datetime, str] | None:
    """(exit_price, exit_ts, 'TP'|'SL') for the first 1-minute bar after `open_ts` that reaches +R_TAKE_PROFIT or -R_STOP_LOSS.
    `bars` are priced on the exit side (bid for a Buy, ask for a Sell). Exit is at the limit price exactly; a bar touching both
    counts as the stop. None if neither was reached."""
    sign = 1.0 if trade_type == "Buy" else -1.0
    for row in bars[bars["datetime"] > open_ts].itertuples():
        best = (row.high - entry_price) if sign > 0 else (entry_price - row.low)
        worst = (entry_price - row.low) if sign > 0 else (row.high - entry_price)
        if worst >= stop_loss:
            return entry_price - sign * stop_loss, row.datetime.to_pydatetime(), "SL"
        if best >= take_profit:
            return entry_price + sign * take_profit, row.datetime.to_pydatetime(), "TP"
    return None


def _find_exit(trade: dict, spot: float, now: datetime) -> tuple[float, datetime, str] | None:
    try:
        side = "bid" if trade["trade_type"] == "Buy" else "ask"
        minutes = int((now - trade["open_ts"]).total_seconds() // 60) + 5
        bars = fetch_gold_bars(max(10, min(minutes, EXIT_BARS_MAX)), side)
        return scan_exit(trade["trade_type"], trade["entry_price"], trade["open_ts"], bars)
    except Exception:
        log.exception("Broker R: 1-minute bars unavailable -- point check on spot")
    pnl = _pnl(trade["trade_type"], trade["entry_price"], spot)
    if pnl <= -R_STOP_LOSS:
        return spot, now, "SL"
    if pnl >= R_TAKE_PROFIT:
        return spot, now, "TP"
    return None


def resolve_shadow_exits(now: datetime | None = None) -> int:
    """LOG ONLY (6 Nov 2026, study 41): score the hypothetical R_SHADOW_TAKE_PROFIT / R_SHADOW_STOP_LOSS exit on every Broker R trade
    (open or closed) that has none yet, and store it in `broker_r_trades.shadow_exit`. Same entry, same side-correct 1-minute bars,
    same tie rule as the live exit; a shadow still unresolved at 5 PM ET closes at the last bar's close ("EOD"). It never trades,
    sends nothing and changes no live decision (the live exit ignores it). Bars are only fetched when something is unresolved. Returns
    the number of shadows resolved. The shadow ignores the one-position-at-a-time rule and `stop trading` closes -- the study handles those."""
    now = now or datetime.now(timezone.utc)
    pending = get_trades_r_without_shadow(now - timedelta(hours=SHADOW_MAX_AGE_HOURS))
    if not pending:
        return 0
    minutes = min(int((now - min(t["open_ts"] for t in pending)).total_seconds() // 60) + 5, EXIT_BARS_MAX)
    bars_by_side: dict[str, pd.DataFrame] = {}
    resolved = 0
    for t in pending:
        side = _entry_side_exit(t["trade_type"])
        if side not in bars_by_side:
            bars_by_side[side] = fetch_gold_bars(max(10, minutes), side)
        bars = bars_by_side[side]
        if bars.empty or bars["datetime"].iloc[0] > t["open_ts"] + timedelta(minutes=1):
            continue  # the fetched bars do not reach back to this entry
        end = t["open_ts"].astimezone(DISPLAY_TZ).replace(hour=WINDOW_END_HOUR_ET, minute=0, second=0, microsecond=0)
        end = end.astimezone(timezone.utc)
        bars = bars[bars["datetime"] < end]
        res = scan_exit(t["trade_type"], t["entry_price"], t["open_ts"], bars, R_SHADOW_TAKE_PROFIT, R_SHADOW_STOP_LOSS)
        if res is None:
            after = bars[bars["datetime"] > t["open_ts"]]
            if now < end + timedelta(minutes=2) or after.empty:
                continue  # session not over yet (or no bar after the entry): still undecided
            last = after.iloc[-1]
            res = (float(last["close"]), last["datetime"].to_pydatetime(), "EOD")
        exit_price, exit_ts, reason = res
        set_shadow_exit_r(t["id"], {
            "take_profit": R_SHADOW_TAKE_PROFIT, "stop_loss": R_SHADOW_STOP_LOSS, "reason": reason,
            "exit_price": round(float(exit_price), 2), "exit_ts": exit_ts.isoformat(),
            "pnl": round(_pnl(t["trade_type"], t["entry_price"], exit_price), 2),
            "held_minutes": round((exit_ts - t["open_ts"]).total_seconds() / 60, 1),
        })
        resolved += 1
    return resolved


def _entry_side_exit(trade_type: str) -> str:
    """Bar side an exit is priced on: a Buy exits on the bid, a Sell on the ask."""
    return "bid" if trade_type == "Buy" else "ask"


def _open_message(
    trade_id: int, trade_type: str, rule: str, price: float, run: str, open_ts: datetime, mode: str = "paper",
    paper_reason: str | None = None, level: float | None = None, note: str = "",
) -> str:
    where = f" (level ${level:.2f})" if level is not None and mode == "live" else ""
    text = (
        f"{TRADE_ALERT_PREFIX}{_label(mode, paper_reason)} #{trade_id}: opened {trade_type} 1 oz XAU/USD @ ${price:.2f}{where} (rule {rule}).\n"
        f"Exits: take-profit +${R_TAKE_PROFIT:g} / stop-loss -${R_STOP_LOSS:g}, nothing else"
        f"{' (set on the forex.com platform)' if mode == 'live' else ''}. Level from short-range TA {run}.\n"
    )
    if paper_reason:
        text += f"Paper only -- not on Forex because {paper_reason}.\n"
    return text + note + f"Filled: {_format_ts(open_ts)}"


def _close_message(trade: dict, exit_price: float, exit_ts: datetime, pnl: float, reason: str) -> str:
    marker = PROFIT_MARKER if pnl >= 0 else LOSS_MARKER
    why = {"TP": "take-profit", "SL": "stop-loss", "Stop": "closed by stop trading / pause"}[reason]
    return (
        f"{TRADE_ALERT_PREFIX.rstrip()}{marker}{'P' if pnl >= 0 else 'L'} ${abs(pnl):.2f} {_label(trade.get('mode'))} #{trade['id']}: closed "
        f"{trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} (opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}, {why})\n"
        f"Filled: {_format_ts(exit_ts)}"
    )


def armed_scenarios(forecast: dict, now: datetime, history_fn=trade_r_level_history) -> list[tuple[str, str, str, dict]]:
    """(scenario_name, trade_type, rule_name, scenario) for each level of a young, same-day forecast still within its trade budget."""
    if forecast.get("forecast_date") != now.astimezone(DISPLAY_TZ).date():
        return []
    if now - forecast["ts"] > timedelta(minutes=R_TA_FORECAST_MAX_AGE_MINUTES):
        return []
    scenarios = {s["name"]: s for s in (forecast.get("levels") or {}).get("scenarios", [])}
    out = []
    for name, (trade_type, rule) in SCENARIOS.items():
        sc = scenarios.get(name)
        if sc is None:
            continue
        history = history_fn(forecast["id"], rule, 0.0)  # a win is any close at +R_TAKE_PROFIT; a stop-out retires the level
        if history["stopped_out"] or history["count"] >= R_MAX_TRADES_PER_LEVEL:
            continue
        out.append((name, trade_type, rule, sc))
    return out


def open_trade_r(
    client: ForexClient | None, paper_reason: str | None, trade_type: str, rule: str, scenario_name: str, forecast: dict,
    level: float, touch_ts: datetime, paper_price: float, now: datetime, context: dict,
) -> int:
    """Records (and announces) a new Broker R trade filled at `now`. With a `client` it is a real FOREX.com demo market order plus platform TP/SL;
    if that order fails, or without a client, it is a paper trade at `paper_price` (the live entry-side tick) -- with `paper_reason`
    in the Telegram message when R was meant to be live. Called by forex_r.py on the tick that touched the level."""
    mode, order_id, note, entry_price = "paper", None, "", paper_price
    if client is not None:
        result, failure = _place_live(client, trade_type)
        if result is not None:
            mode, order_id, entry_price = "live", result["order_id"], float(result["fill_price"])
            failed = _attach_exits(client, order_id, trade_type, entry_price)
            if failed:
                note = (f"\u26a0\ufe0f The platform TP/SL could NOT be attached ({failed}) -- it is retried every few seconds and the "
                        f"price is watched meanwhile.\n")
        else:
            paper_reason = failure  # still recorded as a paper trade, so the touch can never be sent as an order twice
    context = dict(context)
    context["slippage"] = round(entry_price - level if trade_type == "Buy" else level - entry_price, 2)
    if paper_reason:
        context["not_on_forex"] = paper_reason
    run = (forecast["levels"] or {}).get("session", "?")
    trade_id = insert_trade_r(
        rule, trade_type, entry_price, now, f"short-range TA {run}, {scenario_name} @ ${level:.2f}", forecast["id"], context, mode, order_id,
    )
    send_telegram_message(_open_message(trade_id, trade_type, rule, entry_price, run, now, mode, paper_reason, level, note))
    return trade_id
