"""The "Forex" broker -- same entry/exit rules as broker.py's Broker (imported directly from there so
the two can never drift out of sync), but instead of only writing an imaginary trade to Postgres, it
places and closes real orders against a FOREX.com DEMO account via forex_client.ForexClient.

NOT wired into main.poll_once()/poll_job.py -- nothing in the automatic poll cycle imports this module,
and it must stay that way unless the user explicitly asks to connect it. It only runs when something
calls check_forex_broker_trades() directly, e.g. by running `python forex_broker.py` by hand (see the
__main__ block below), which fetches live prices once and checks the rules once -- it does not loop or
schedule itself.

Trade state lives in its own `forex_trades` table (see storage.py), separate from broker.py's `trades`
table, so the two engines' open positions/watermarks never interact.
"""

from datetime import datetime, timezone

from broker import ENTRY_WINDOW_MINUTES, EXIT_THRESHOLD, _match_entry_rule, _pnl, _triggering_text
from forex_client import ForexClient, ForexClientError, ForexOrderUncertainError
from notifier import send_telegram_message
from storage import (
    close_forex_trade_row,
    get_last_forex_trade_open_ts,
    get_open_forex_trade,
    get_recent_alerts,
    insert_forex_trade,
)


def _open_message(trade_type: str, rule_name: str, fill_price: float, triggering_text: str, order_id) -> str:
    return (
        f"FOREX BROKER (demo account): opened {trade_type} 1 oz XAU/USD @ ${fill_price:.2f} "
        f"(rule {rule_name}, order {order_id}).\nTrigger: {triggering_text}"
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
    open_trade = get_open_forex_trade()

    if open_trade is not None:
        pnl = _pnl(open_trade, gold_price)
        if abs(pnl) >= EXIT_THRESHOLD:
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
            send_telegram_message(
                _open_message(trade_type, rule_name, result["fill_price"], triggering_text, result["order_id"])
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
