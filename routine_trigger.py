"""Fires the "ADP/NFP release watcher" Claude Code Routine's API trigger the moment poll_once()
sees a fresh ADP_EMPLOYMENT/NONFARM_PAYROLLS value-change alert, instead of waiting for that
Routine's own hourly schedule -- see CLAUDE.md's "ADP/NFP release trigger" entry. Best-effort: the
caller wraps this in try/except so a Routines-API hiccup never blocks the rest of the poll cycle."""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

ROUTINE_FIRE_URL = os.environ.get("ROUTINE_FIRE_URL")
ROUTINE_FIRE_TOKEN = os.environ.get("ROUTINE_FIRE_TOKEN")

# Indicators whose VALUE_CHANGE_ALERT_NAMES alert should also fire the release-analysis Routine.
RELEASE_TRIGGER_NAMES = ["adp_employment", "nonfarm_payrolls"]


def trigger_release_analysis(alert: str) -> None:
    """POSTs to the Routine's API-trigger endpoint so it re-checks FRED and sends its Telegram
    analysis within minutes instead of on its own hourly schedule. `alert` is passed through as
    the routine-fire-payload's `text` field for context; the Routine's own saved prompt re-derives
    everything it needs from FRED/Neon regardless, so this is best-effort context, not a required
    input. No-ops (with a log line) if the two env vars below aren't configured."""
    if not ROUTINE_FIRE_URL or not ROUTINE_FIRE_TOKEN:
        print(f"[routine_trigger] Not configured, would have fired for: {alert}")
        return

    response = requests.post(
        ROUTINE_FIRE_URL,
        headers={
            "Authorization": f"Bearer {ROUTINE_FIRE_TOKEN}",
            "anthropic-beta": "experimental-cc-routine-2026-04-01",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"text": alert},
        timeout=10,
    )
    response.raise_for_status()
