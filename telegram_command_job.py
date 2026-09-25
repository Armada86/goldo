"""Inbound Telegram command handler -- lets the user manually open a Broker A or Broker B position
by sending a plain-text message to the bot, e.g. "buy broker a" or "sell broker B". This is the
project's first (and, for now, only) inbound path: every other Telegram interaction in this repo is
one-way, notifier.send_telegram_message() only ever sending, never reading.

Scope, deliberately narrow: OPEN commands only (buy/sell + broker A/B), no close command, no
settings control, and the Forex broker (forex_broker.py -- real orders on a FOREX.com demo account)
is NOT reachable from here. forex_broker.py's own module docstring says its full trading logic
"must stay disconnected [from automation] unless the user explicitly asks to connect it" -- wiring
a real-money-adjacent broker up to an inbound command path is exactly that kind of decision, so it's
excluded until asked for separately. A manually-opened trade still closes the normal way: the
existing check_broker_trades()/check_broker_b_trades() (already running every poll) scan for the
$10 take-profit/stop-loss crossing the same as any rule-triggered trade -- this job only ever
inserts the open row, nothing about the exit path changes.

Mechanism: Telegram's Bot API getUpdates, polled by a scheduled job (cron-job.org -> workflow_dispatch,
same pattern as every other job in this repo) rather than a webhook, since this project has no
persistent server to receive one. Each run is stateless (a fresh GitHub Actions container), so the
highest update_id already processed is persisted in Postgres (telegram_command_state,
storage.get_last_telegram_update_id()/set_last_telegram_update_id()) and passed back as getUpdates'
own `offset` next run, so a message already acted on is never processed twice. `timeout=0` (a short
poll, not Telegram's long-polling mode) since this job is meant to check once and exit, not block
waiting for a new message -- the scheduled cadence (see .github/workflows/telegram_command.yml) is
what provides responsiveness.

Security: every update's message.chat.id is checked against TELEGRAM_CHAT_ID before anything is
acted on. A message from any other chat is skipped (but still advances the offset, so it's never
retried) -- without this check, anyone who discovered the bot could open trades.

Command parsing is intentionally forgiving: any message containing both a buy/sell word and "broker
a"/"broker b" (case-insensitive, in either order) matches -- "sell broker A", "Broker B buy", "BUY
BROKER A" all work. Anything that doesn't match both parts is silently ignored, not replied to, so
this doesn't turn the chat into a bot that talks back to every unrelated message.

Run: python telegram_command_job.py
"""

import os
import re
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

import broker
import broker_b
from broker import _format_ts
from data_fetcher import fetch_gold_spot_price
from notifier import send_telegram_message
from storage import (
    get_last_telegram_update_id,
    get_latest_ta_forecast,
    get_open_trade,
    get_open_trade_b,
    init_db,
    insert_trade,
    insert_trade_b,
    set_last_telegram_update_id,
)

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

DIRECTION_RE = re.compile(r"\b(buy|sell)\b", re.IGNORECASE)
BROKER_RE = re.compile(r"\bbroker\s+([ab])\b", re.IGNORECASE)

# Reuse each broker's own "signal reached but not acted on" marker for a rejected command (already
# open / no forecast yet), so a Telegram-command rejection looks visually consistent with the
# existing blocked-entry notices rather than inventing a third convention.
REJECT_MARKER = "⛔ "  # no-entry sign, same as broker.BLOCKED_MARKER/broker_b.BLOCKED_MARKER


def parse_command(text: str) -> tuple[str, str] | None:
    """Returns (trade_type, broker_letter) -- e.g. ("Buy", "A") -- if `text` names both a
    buy/sell direction and a broker A/B, else None. Both must be present; a message with only one
    (e.g. just "buy", or just "broker a") is not a command."""
    direction_match = DIRECTION_RE.search(text)
    broker_match = BROKER_RE.search(text)
    if direction_match is None or broker_match is None:
        return None
    trade_type = direction_match.group(1).capitalize()  # "Buy" / "Sell"
    broker_letter = broker_match.group(1).upper()  # "A" / "B"
    return trade_type, broker_letter


def _open_broker_a(trade_type: str) -> str:
    if get_open_trade() is not None:
        return (
            f"{broker.TRADE_ALERT_PREFIX.rstrip()}{REJECT_MARKER}BROKER A: cannot open {trade_type} -- "
            f"a trade is already open. Close it first."
        )
    price = fetch_gold_spot_price()
    if price is None:
        return f"{broker.TRADE_ALERT_PREFIX.rstrip()}{REJECT_MARKER}BROKER A: cannot open {trade_type} -- gold spot price unavailable right now."

    now = datetime.now(timezone.utc)
    rule_name = f"Telegram-{trade_type.lower()}"
    insert_trade(rule_name, trade_type, price, now, "Manual (Telegram command)")
    return broker._open_message(trade_type, rule_name, price, "Manual (Telegram command)", now)


def _open_broker_b(trade_type: str) -> str:
    if get_open_trade_b() is not None:
        return (
            f"{broker_b.TRADE_ALERT_PREFIX.rstrip()}{REJECT_MARKER}BROKER B: cannot open {trade_type} -- "
            f"a trade is already open. Close it first."
        )
    forecast = get_latest_ta_forecast()
    if forecast is None:
        return (
            f"{broker_b.TRADE_ALERT_PREFIX.rstrip()}{REJECT_MARKER}BROKER B: cannot open {trade_type} -- "
            f"no TA forecast exists yet to attribute the trade to."
        )
    price = fetch_gold_spot_price()
    if price is None:
        return f"{broker_b.TRADE_ALERT_PREFIX.rstrip()}{REJECT_MARKER}BROKER B: cannot open {trade_type} -- gold spot price unavailable right now."

    now = datetime.now(timezone.utc)
    rule_name = f"Telegram-{trade_type.lower()}"
    insert_trade_b(rule_name, trade_type, price, now, "Manual (Telegram command)", forecast["id"])
    return (
        f"{broker_b.TRADE_ALERT_PREFIX}BROKER B: opened {trade_type} 1 oz XAU/USD @ ${price:.2f} (rule {rule_name}).\n"
        f"Trigger: Manual (Telegram command)\n"
        f"Filled: {_format_ts(now)}"
    )


def _handle_message(text: str) -> None:
    parsed = parse_command(text)
    if parsed is None:
        return
    trade_type, broker_letter = parsed
    print(f"[telegram_command_job] Command recognized: {trade_type} broker {broker_letter}")
    message = _open_broker_a(trade_type) if broker_letter == "A" else _open_broker_b(trade_type)
    send_telegram_message(message)


def check() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram_command_job] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set, skipping")
        return

    init_db()
    offset = get_last_telegram_update_id() + 1
    response = requests.get(
        f"{TELEGRAM_API_BASE}/getUpdates",
        params={"offset": offset, "timeout": 0},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        print(f"[telegram_command_job] Telegram API error: {payload}")
        return

    updates = payload["result"]
    if not updates:
        print("[telegram_command_job] No new messages")
        return

    highest_update_id = offset - 1
    for update in updates:
        highest_update_id = max(highest_update_id, update["update_id"])
        message = update.get("message")
        if message is None or "text" not in message:
            continue  # not a plain text message (edited_message, a sticker, etc.) -- ignore
        if str(message.get("chat", {}).get("id")) != str(CHAT_ID):
            print(f"[telegram_command_job] Ignoring message from unauthorized chat {message.get('chat', {}).get('id')}")
            continue
        _handle_message(message["text"])

    set_last_telegram_update_id(highest_update_id)


if __name__ == "__main__":
    check()
