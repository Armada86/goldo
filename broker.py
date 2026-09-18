"""Automated paper-trading engine for the Broker's rules.

This is the code implementation of the rules documented in `.claude/agents/broker.md`'s "Rules"
section -- that file is the human-readable spec, this module is what actually executes it every poll.
The two must be kept in sync by hand when the rules change (same convention as `docs/market.md` vs.
`config.py`): a rule change here without the matching prose update there is an incomplete change.

Every trade lives only in the `trades` table in Postgres (see storage.py) -- there is deliberately no
markdown/doc mirror to keep in sync, so a trade never requires a repo commit.
"""

from datetime import datetime, timezone

from notifier import send_telegram_message
from storage import (
    close_trade_row,
    get_last_trade_open_ts,
    get_open_trade,
    get_recent_alerts,
    insert_trade,
)

# GLD-DXY-US10Y-buy / GLD-DXY-US10Y-sell correlation window and exit target -- see
# .claude/agents/broker.md.
CORRELATION_WINDOW_MINUTES = 15
EXIT_THRESHOLD = 10.0  # take-profit and stop-loss, symmetric, $ per troy ounce


def _has_alert(alerts: list[tuple[datetime, str]], name: str, direction: str) -> bool:
    prefix = f"{name.upper()} moved {direction} "
    return any(message.startswith(prefix) for _, message in alerts)


def _first_alert(alerts: list[tuple[datetime, str]], name: str, direction: str) -> str | None:
    prefix = f"{name.upper()} moved {direction} "
    for _, message in alerts:
        if message.startswith(prefix):
            return message
    return None


def _match_entry_rule(alerts: list[tuple[datetime, str]]):
    """Returns (trade_type, rule_name), or (None, None) if no rule's entry condition is met."""
    if _has_alert(alerts, "gld", "up") and _has_alert(alerts, "dxy", "down") and _has_alert(alerts, "us10y", "down"):
        return "Buy", "GLD-DXY-US10Y-buy"
    if _has_alert(alerts, "gld", "down") and _has_alert(alerts, "dxy", "up") and _has_alert(alerts, "us10y", "up"):
        return "Sell", "GLD-DXY-US10Y-sell"
    return None, None


def _triggering_text(alerts: list[tuple[datetime, str]], trade_type: str) -> str:
    if trade_type == "Buy":
        directions = [("gld", "up"), ("dxy", "down"), ("us10y", "down")]
    else:
        directions = [("gld", "down"), ("dxy", "up"), ("us10y", "up")]
    parts = [_first_alert(alerts, name, direction) for name, direction in directions]
    return "; ".join(part for part in parts if part)


def _pnl(trade: dict, current_price: float) -> float:
    if trade["trade_type"] == "Buy":
        return current_price - trade["entry_price"]
    return trade["entry_price"] - current_price


def _open_message(trade_type: str, rule_name: str, price: float, triggering_text: str) -> str:
    return (
        f"BROKER: opened {trade_type} 1 oz gold spot @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: {triggering_text}"
    )


def _close_message(trade: dict, exit_price: float, pnl: float) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"BROKER: closed {trade['trade_type']} 1 oz gold spot @ ${exit_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}) -- "
        f"{result} of ${abs(pnl):.2f}"
    )


def check_broker_trades(prices: dict[str, float]) -> None:
    """Runs once per poll, after this cycle's alerts are saved. Closes the open trade (if any) the
    moment its unrealized P/L reaches the $10 take-profit/stop-loss, then looks for a fresh
    GLD-DXY-US10Y-buy/-sell entry signal -- all three intrahour-swing alerts, in the required
    directions, landing in the alerts table within the trailing 15 minutes. See
    .claude/agents/broker.md for the rules themselves."""
    gold_price = prices.get("gold")
    if gold_price is None:
        return

    now = datetime.now(timezone.utc)
    open_trade = get_open_trade()

    if open_trade is not None:
        pnl = _pnl(open_trade, gold_price)
        if abs(pnl) >= EXIT_THRESHOLD:
            close_trade_row(open_trade["id"], gold_price, now, pnl)
            send_telegram_message(_close_message(open_trade, gold_price, pnl))
            open_trade = None

    if open_trade is None:
        watermark = get_last_trade_open_ts()
        alerts = get_recent_alerts(minutes=CORRELATION_WINDOW_MINUTES)
        if watermark is not None:
            alerts = [(ts, message) for ts, message in alerts if ts > watermark]

        trade_type, rule_name = _match_entry_rule(alerts)
        if trade_type is not None:
            triggering_text = _triggering_text(alerts, trade_type)
            insert_trade(rule_name, trade_type, gold_price, now, triggering_text)
            send_telegram_message(_open_message(trade_type, rule_name, gold_price, triggering_text))
