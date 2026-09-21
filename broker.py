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

# Consensus6of8-buy / Consensus6of8-sell entry window and exit target -- see
# .claude/agents/broker.md.
ENTRY_WINDOW_MINUTES = 10
EXIT_THRESHOLD = 10.0  # take-profit and stop-loss, symmetric, $ per troy ounce

# The six physically/mining-correlated gold ETFs must flag the same direction gold itself is
# presumed to be moving; dxy/us10y (inversely correlated with gold) must flag the opposite
# direction. A trade only needs MIN_FLAGGING_COUNT of these eight to actually flag, not all of
# them, and each can flag from any of its own 15/10/5-min windows -- see
# .claude/agents/broker.md's Consensus6of8 rules.
GOLD_DIRECTION_NAMES = ["gld", "iau", "gldm", "gdx", "gdxj", "ring"]
INVERSE_DIRECTION_NAMES = ["dxy", "us10y"]
MIN_FLAGGING_COUNT = 6


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
    MIN_FLAGGING_COUNT of the eight indicators flagging in the required direction. If both
    directions independently reach the threshold at once (a genuine conflict in the alert
    stream), no trade opens either way -- an incoherent signal is not acted on."""
    buy_count = _count_flagging(alerts, _entry_direction_map("Buy"))
    sell_count = _count_flagging(alerts, _entry_direction_map("Sell"))
    buy_ok = buy_count >= MIN_FLAGGING_COUNT
    sell_ok = sell_count >= MIN_FLAGGING_COUNT
    if buy_ok and not sell_ok:
        return "Buy", "Consensus6of8-buy"
    if sell_ok and not buy_ok:
        return "Sell", "Consensus6of8-sell"
    return None, None


def _triggering_text(alerts: list[tuple[datetime, str]], trade_type: str) -> str:
    direction_map = _entry_direction_map(trade_type)
    parts = [_first_alert(alerts, name, direction) for name, direction in direction_map.items()]
    return "; ".join(part for part in parts if part)


def _pnl(trade: dict, current_price: float) -> float:
    if trade["trade_type"] == "Buy":
        return current_price - trade["entry_price"]
    return trade["entry_price"] - current_price


def _open_message(trade_type: str, rule_name: str, price: float, triggering_text: str) -> str:
    return (
        f"BROKER: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: {triggering_text}"
    )


def _close_message(trade: dict, exit_price: float, pnl: float) -> str:
    result = "profit" if pnl >= 0 else "loss"
    return (
        f"BROKER: closed {trade['trade_type']} 1 oz XAU/USD @ ${exit_price:.2f} "
        f"(opened @ ${trade['entry_price']:.2f}, rule {trade['rule_name']}) -- "
        f"{result} of ${abs(pnl):.2f}"
    )


def check_broker_trades(prices: dict[str, float]) -> None:
    """Runs once per poll, after this cycle's alerts are saved. Closes the open trade (if any) the
    moment its unrealized P/L reaches the $10 take-profit/stop-loss, then looks for a fresh
    Consensus6of8-buy/-sell entry signal -- at least MIN_FLAGGING_COUNT (6) of the eight
    intrahour-swing indicators, in the required directions, landing in the alerts table within the
    trailing ENTRY_WINDOW_MINUTES (10) minutes. See .claude/agents/broker.md for the rules
    themselves."""
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
        alerts = get_recent_alerts(minutes=ENTRY_WINDOW_MINUTES)
        if watermark is not None:
            alerts = [(ts, message) for ts, message in alerts if ts > watermark]

        trade_type, rule_name = _match_entry_rule(alerts)
        if trade_type is not None:
            triggering_text = _triggering_text(alerts, trade_type)
            insert_trade(rule_name, trade_type, gold_price, now, triggering_text)
            send_telegram_message(_open_message(trade_type, rule_name, gold_price, triggering_text))
