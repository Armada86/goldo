"""Same-minute ADP/NFP release detector -- a faster, independent alternative to the existing
FRED-value-change path (rules.check_value_change_alerts -> routine_trigger.py), which can lag the
real release by anywhere from minutes to hours depending on when FRED itself ingests the fresh
BLS/ADP number, on top of poll.yml's own 5-minute cadence.

This job is triggered by cron-job.org (same workflow_dispatch pattern as poll.yml/
frequency_check_job.py) at a fixed time just before ADP's 8:15am ET or NFP's 8:30am ET release --
which invocation this is comes from the RELEASE_WATCH_TARGET env var ("adp" or "nfp"), set by
.github/workflows/release_watch_adp.yml / release_watch_nfp.yml respectively, not auto-detected.

Unlike every other scheduled job in this repo, this one is not a pure one-shot: once invoked, it
burst-polls FMP's economic-calendar endpoint (docs/data-sources.md) every
BURST_POLL_INTERVAL_SECONDS for up to BURST_POLL_MAX_MINUTES, since cron-job.org itself can't
reliably schedule sub-minute triggers. FMP showed no rate-limit friction in testing (10+ rapid
calls with no throttling), so a burst of ~24 calls is cheap. The moment FMP's `actual` field for
today's matching release goes from null to a real number, this job sends a Telegram alert
immediately and exits -- it does not wait out the rest of the burst window.

Deliberately does NOT write to nfp_reports/adp_reports or fire routine_trigger.trigger_release_analysis():
the existing Routine still owns that (it re-checks FRED itself when nudged, and its own no-op
logic keys off whether the release is already recorded there) -- see CLAUDE.md's "ADP/NFP release
trigger" entry. Writing the row here first would make the Routine think its job was already done
and skip its own recommendation message. This job is purely an additive, faster "the number just
printed" alert layered on top of that unchanged pipeline, not a replacement for it.

Run: RELEASE_WATCH_TARGET=adp python release_watch_job.py
     RELEASE_WATCH_TARGET=nfp python release_watch_job.py
"""

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from notifier import send_telegram_message

load_dotenv()

FMP_API_KEY = os.environ.get("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com/stable"

ET = ZoneInfo("America/New_York")

BURST_POLL_INTERVAL_SECONDS = 15
BURST_POLL_MAX_MINUTES = 6

# Prefix for this job's Telegram alerts -- distinct from XAU/USD price alerts' 🟡
# (rules.XAUUSD_ALERT_PREFIX), RSI alerts' 🟠 (rules.RSI_ALERT_PREFIX), and the Broker's trade
# alerts' 🔵 (broker.TRADE_ALERT_PREFIX).
RELEASE_ALERT_PREFIX = "\U0001f7e3 "  # purple circle

# FMP economic-calendar event-name prefixes that uniquely identify each release (country=US).
# "Non Farm Payrolls (" (with the space) is the BLS headline print -- distinct from "Nonfarm
# Payrolls Private (" (no space), "Government Payrolls (", and "Manufacturing Payrolls (", which
# are separate sub-releases bundled into the same NFP report but not the number this job watches.
TARGET_EVENT_PREFIXES = {
    "adp": "ADP Employment Change (",
    "nfp": "Non Farm Payrolls (",
}


def _fetch_todays_release(target: str, today: str) -> dict | None:
    """Returns today's matching FMP economic-calendar row for `target` ("adp" or "nfp"), or None
    if nothing of that kind is scheduled today."""
    prefix = TARGET_EVENT_PREFIXES[target]
    response = requests.get(
        f"{FMP_BASE}/economic-calendar",
        params={"from": today, "to": today, "apikey": FMP_API_KEY},
        timeout=20,
    )
    response.raise_for_status()
    for row in response.json():
        if row.get("country") == "US" and row.get("event", "").startswith(prefix):
            return row
    return None


def _format_alert(target: str, row: dict) -> str:
    label = "ADP Employment Change" if target == "adp" else "Non Farm Payrolls"
    unit = row.get("unit") or ""
    previous = row.get("previous")
    estimate = row.get("estimate")
    actual = row.get("actual")
    return (
        f"{RELEASE_ALERT_PREFIX}{label} {row['event'][row['event'].index('('):]} just released: "
        f"actual {actual}{unit} vs estimate {estimate}{unit} (previous {previous}{unit})"
    )


def watch() -> None:
    target = os.environ.get("RELEASE_WATCH_TARGET")
    if target not in TARGET_EVENT_PREFIXES:
        raise SystemExit(
            f"RELEASE_WATCH_TARGET must be 'adp' or 'nfp', got {target!r}"
        )
    if not FMP_API_KEY:
        print("[release_watch_job] FMP_API_KEY not set, skipping")
        return

    today = datetime.now(ET).strftime("%Y-%m-%d")
    row = _fetch_todays_release(target, today)
    if row is None:
        print(f"[release_watch_job] No {target.upper()} release scheduled for {today}, nothing to watch")
        return

    if row.get("actual") is not None:
        print(f"[release_watch_job] {target.upper()} already released by the time this job started")
        send_telegram_message(_format_alert(target, row))
        return

    deadline = time.monotonic() + BURST_POLL_MAX_MINUTES * 60
    print(f"[release_watch_job] Watching for {target.upper()} release, up to {BURST_POLL_MAX_MINUTES} min")
    while time.monotonic() < deadline:
        time.sleep(BURST_POLL_INTERVAL_SECONDS)
        row = _fetch_todays_release(target, today)
        if row is not None and row.get("actual") is not None:
            send_telegram_message(_format_alert(target, row))
            print(f"[release_watch_job] {target.upper()} release detected and alerted")
            return

    print(f"[release_watch_job] {target.upper()} release did not appear within the burst window")


if __name__ == "__main__":
    watch()
