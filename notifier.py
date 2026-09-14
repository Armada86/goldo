"""Sends alert text to a Telegram chat via the Bot API."""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[notifier] Telegram not configured, would have sent: {text}")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    response.raise_for_status()
