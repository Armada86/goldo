"""The "Forex" broker -- same entry/exit rules as broker.py's Broker (imported directly from there so
the two can never drift out of sync), but instead of only writing an imaginary trade to Postgres, it
places and closes real orders against a FOREX.com DEMO account via forex_client.ForexClient.

Two entry points, with very different reach:
  - check_forex_closes() -- the ONLY part wired into main.poll_once() (every 5-min poll). Read-only
    toward forex.com: it never places, closes or modifies an order. It records closes (a tracked
    position that forex.com's TP/SL closed) and starts tracking any open XAU/USD position placed by hand
    on the platform, sending a Telegram message for each.
  - check_forex_broker_trades() -- the full broker (entries, TP/SL attachment, unprotected-position
    fallback close). NOT wired into the poll, and must stay that way unless the user explicitly asks to
    connect it; it only runs via `python forex_broker.py` (see the __main__ block below), which fetches
    live prices once and checks the rules once -- it does not loop or schedule itself.

Trade state lives in its own `forex_trades` table (see storage.py), separate from broker.py's `trades`
table, so the two engines' open positions/watermarks never interact.

Exits are handled ON THE PLATFORM, not by this module: right after an entry fills, a take-profit and a
stop-loss are attached to the position at fill +/- EXIT_THRESHOLD ($10), so forex.com closes it the
moment either level trades -- no waiting for the next run. Each run then just reconciles: if the
position is gone from forex.com, record the close in forex_trades (exit price from forex.com's trade
history). The old "close it ourselves once |P/L| >= $10" path only survives as a fallback for a position
that has no TP/SL attached (attachment failed); sending our own close alongside live TP/SL orders could
double-close and flip the position.
"""

from datetime import datetime, timezone

from broker import ENTRY_WINDOW_MINUTES, EXIT_THRESHOLD, _match_entry_rule, _pnl, _triggering_text
from forex_client import TRADABLE_MARKET_ID, ForexClient, ForexClientError, ForexOrderUncertainError, _parse_ms_date
from notifier import send_telegram_message
from storage import (
    close_forex_trade_row,
    get_last_forex_trade_open_ts,
    get_open_forex_trade,
    get_recent_alerts,
    insert_forex_trade,
)


def _open_message(
    trade_type: str, rule_name: str, fill_price: float, triggering_text: str, order_id, bracket: dict | None
) -> str:
    if bracket is not None:
        protection = f"TP ${bracket['limit_price']:.2f} / SL ${bracket['stop_price']:.2f} set on forex.com."
    else:
        protection = "WARNING: TP/SL could NOT be attached -- position is UNPROTECTED on forex.com."
    return (
        f"FOREX BROKER (demo account): opened {trade_type} 1 oz XAU/USD @ ${fill_price:.2f} "
        f"(rule {rule_name}, order {order_id}). {protection}\nTrigger: {triggering_text}"
    )


def _close_message(trade: dict, fill_price: float, pnl: float, order_id) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"FOREX BROKER (demo account): closed {trade['trade_type']} 1 oz XAU/USD @ ${fill_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}, order {order_id}) -- "
        f"{result} of ${abs(pnl):.2f}"
    )


def _report_uncertain_order(action: str, error: ForexOrderUncertainError) -> None:
    """An order whose outcome is unknown is not recorded in forex_trades (there may be no real position
    behind it) and not retried (there may be one) -- it's surfaced to a human instead. Until they
    reconcile the demo account against forex_trades, a later run can act on stale state."""
    message = (
        f"FOREX BROKER (demo account): could not confirm the {action} order -- CHECK THE DEMO ACCOUNT "
        f"before running forex_broker.py again. {error}"
    )
    print(f"[forex_broker] {message}")
    send_telegram_message(message)


# rule_name recorded for a position placed by hand on forex.com rather than opened by a broker rule.
MANUAL_RULE_NAME = "Manual"


def _position_for(positions: list[dict], trade: dict) -> dict | None:
    return next((p for p in positions if str(p.get("OrderId")) == str(trade["forex_order_id"])), None)


def _track_manual_position(position: dict, now: datetime) -> None:
    """Starts tracking an XAU/USD position that exists on forex.com but not in forex_trades (placed by
    hand on the platform, or by a one-off script), so its close gets recorded and alerted like a
    broker trade. Its open_ts becomes the entry watermark, same as any other trade."""
    trade_type = "Buy" if position["Direction"].lower() == "buy" else "Sell"
    entry_price = float(position["Price"])
    open_ts = _parse_ms_date(position.get("ExecutedDateTimeUTC")) or now
    insert_forex_trade(
        MANUAL_RULE_NAME, trade_type, entry_price, open_ts,
        "Placed manually on forex.com (not rule-triggered)", position["OrderId"],
    )
    stop, limit = position.get("StopOrder"), position.get("LimitOrder")
    if stop or limit:
        protection = (
            f"TP {'$%.2f' % limit['TriggerPrice'] if limit else 'none'} / "
            f"SL {'$%.2f' % stop['TriggerPrice'] if stop else 'none'} on forex.com."
        )
    else:
        protection = "No TP/SL on forex.com -- it won't close by itself."
    send_telegram_message(
        f"FOREX BROKER (demo account): now tracking manual {trade_type} {position['Quantity']} oz XAU/USD "
        f"@ ${entry_price:.2f} (order {position['OrderId']}). {protection}"
    )


def _reconcile(client: ForexClient, prices: dict[str, float], now: datetime) -> tuple[dict | None, list[dict]]:
    """Brings forex_trades in line with forex.com, without sending any order: records the tracked trade
    as closed if its position is gone, and starts tracking an untracked XAU/USD position if nothing is
    tracked. Returns (the still-open tracked trade or None, XAU/USD positions on the account)."""
    positions = [p for p in client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]
    open_trade = get_open_forex_trade()
    if open_trade is not None and _position_for(positions, open_trade) is None:
        _record_platform_close(client, open_trade, prices.get("gold"), now)
        open_trade = None
    if open_trade is None:
        # forex_trades tracks one open trade at a time; any further untracked positions are picked up
        # one per poll as each closes.
        untracked = sorted(positions, key=lambda p: p.get("ExecutedDateTimeUTC") or "")
        if untracked:
            _track_manual_position(untracked[0], now)
            open_trade = get_open_forex_trade()
    return open_trade, positions


def check_forex_closes(prices: dict[str, float]) -> None:
    """The poll-safe part of the Forex broker (called every poll from main.poll_once()): reconciles
    forex_trades with forex.com -- records closes and starts tracking manual positions, each with a
    Telegram message -- and never places, closes, or modifies an order. No-ops (log line only) if the
    FOREX_* credentials aren't set."""
    try:
        client = ForexClient()
    except ForexClientError as e:
        print(f"[forex_broker] Close check skipped: {e}")
        return
    _reconcile(client, prices, datetime.now(timezone.utc))


def _record_platform_close(client: ForexClient, trade: dict, gold_price: float | None, now: datetime) -> None:
    """The position is gone from forex.com -- its TP or SL (or a manual close on the platform) closed
    it. Record that in forex_trades using forex.com's own closing fill when trade history has it; if
    not, fall back to whichever of the two TP/SL levels is nearer the current price, flagged as an
    estimate in the Telegram message."""
    closing = client.find_closing_trade(int(trade["forex_order_id"]))
    if closing is not None:
        exit_price, close_ts, close_order_id, note = (
            closing["price"], closing["closed_at"] or now, closing["order_id"], ""
        )
    else:
        if gold_price is None:
            gold_price = client.get_price()
        levels = (trade["entry_price"] + EXIT_THRESHOLD, trade["entry_price"] - EXIT_THRESHOLD)
        exit_price = min(levels, key=lambda level: abs(level - gold_price))
        close_ts, close_order_id = now, "unknown"
        note = " (exit price ESTIMATED from the nearer TP/SL level -- not found in forex.com trade history)"
    pnl = _pnl(trade, exit_price)
    close_forex_trade_row(trade["id"], exit_price, close_ts, pnl, close_order_id)
    send_telegram_message(_close_message(trade, exit_price, pnl, close_order_id) + note)


def check_forex_broker_trades(prices: dict[str, float]) -> None:
    """Same Consensus5of7-buy/-sell entry trigger logic as broker.check_broker_trades() (imported from
    broker.py, not re-implemented), executed against the real FOREX.com demo account instead of just
    writing a row to Postgres. Every call that finds an entry/exit condition places or closes a live
    order on that demo account -- never call this from an automated path."""
    gold_price = prices.get("gold")
    if gold_price is None:
        return

    try:
        client = ForexClient()
    except ForexClientError as e:
        print(f"[forex_broker] Not connected: {e}")
        return

    now = datetime.now(timezone.utc)
    open_trade, positions = _reconcile(client, prices, now)

    if open_trade is not None:
        position = _position_for(positions, open_trade)
        if position.get("StopOrder") or position.get("LimitOrder"):
            pass  # TP/SL live on forex.com -- it will close the position itself.
        elif open_trade["rule_name"] != MANUAL_RULE_NAME and abs(_pnl(open_trade, gold_price)) >= EXIT_THRESHOLD:
            # Fallback only for a broker-opened position with no TP/SL attached (see module docstring);
            # a manual position is the user's to manage.
            try:
                result = client.close_position(open_trade["trade_type"])
            except ForexOrderUncertainError as e:
                _report_uncertain_order("close", e)
                return
            actual_pnl = _pnl(open_trade, result["fill_price"])
            close_forex_trade_row(
                open_trade["id"], result["fill_price"], now, actual_pnl, result["order_id"]
            )
            send_telegram_message(_close_message(open_trade, result["fill_price"], actual_pnl, result["order_id"]))
            open_trade = None

    if open_trade is None:
        watermark = get_last_forex_trade_open_ts()
        alerts = get_recent_alerts(minutes=ENTRY_WINDOW_MINUTES)
        if watermark is not None:
            alerts = [(ts, message) for ts, message in alerts if ts > watermark]

        trade_type, rule_name = _match_entry_rule(alerts)
        if trade_type is not None:
            triggering_text = _triggering_text(alerts, trade_type)
            direction = "buy" if trade_type == "Buy" else "sell"
            try:
                result = client.place_market_order(direction)
            except ForexOrderUncertainError as e:
                _report_uncertain_order("open", e)
                return
            insert_forex_trade(
                rule_name, trade_type, result["fill_price"], now, triggering_text, result["order_id"]
            )
            try:
                bracket = client.attach_take_profit_and_stop_loss(
                    result["order_id"], direction, result["fill_price"], EXIT_THRESHOLD
                )
            except ForexClientError as e:
                # Covers ForexOrderUncertainError too. The trade is real and already recorded; without
                # TP/SL the next run's fallback closes it at +/- EXIT_THRESHOLD instead.
                print(f"[forex_broker] Could not attach TP/SL to order {result['order_id']}: {e}")
                bracket = None
            send_telegram_message(
                _open_message(
                    trade_type, rule_name, result["fill_price"], triggering_text, result["order_id"], bracket
                )
            )


if __name__ == "__main__":
    # Manual, one-shot entry point -- the only way this module ever runs. Fetches live prices once and
    # checks the rules once against the real demo account; does not loop or schedule itself.
    from data_fetcher import fetch_latest_prices
    from storage import init_db

    init_db()
    prices = fetch_latest_prices()
    if prices:
        check_forex_broker_trades(prices)
    else:
        print("[forex_broker] No prices fetched -- nothing to check.")
