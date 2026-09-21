"""One-shot detector for the weekly API Crude Oil Stock Change release, via FMP's economic-calendar
endpoint (docs/data-sources.md) -- the same source and event-matching approach as
release_watch_job.py, but a different job because this one also records the release directly (see
below) and because its release time is far less precise than ADP/NFP's fixed 8:15am/8:30am ET.

Real-world API Crude Oil Stock Change releases Tuesday evenings, but not at a fixed minute --
observed release times in FMP's own calendar range roughly 19:30-22:00 UTC (~3:30pm-6:00pm ET), a
multi-hour window. A single short burst-poll (release_watch_job.py's approach) can't cover that
cheaply, so this job is a pure one-shot instead: cron-job.org triggers it repeatedly (e.g. every 10
minutes) across the whole Tuesday-evening window, and each invocation just checks once and exits --
see .github/workflows/oil_weekly_watch.yml. `storage.oil_weekly_report_exists()` makes repeated
invocations after the real release a safe no-op rather than a duplicate alert.

Unlike release_watch_job.py (which deliberately never writes to Postgres, since an existing Routine
owns recording ADP/NFP releases), this job DOES write directly to oil_weekly_reports -- there's no
other mechanism recording this indicator, so this job is the sole source of truth for it.

Run: python oil_weekly_job.py
"""

import os
import re
from datetime import date, datetime, timezone

import requests
from dotenv import load_dotenv

from data_fetcher import fetch_gold_spot_price
from notifier import send_telegram_message
from storage import init_db, insert_oil_weekly_report, oil_weekly_report_exists

load_dotenv()

FMP_API_KEY = os.environ.get("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com/stable"

# Same purple-circle prefix as release_watch_job.py's ADP/NFP alerts -- this is the same category of
# "a scheduled macro release just printed" alert.
RELEASE_ALERT_PREFIX = "\U0001f7e3 "

EVENT_PREFIX = "API Crude Oil Stock Change ("
WEEK_ENDING_RE = re.compile(r"\((\w{3})/(\d{2})\)")
MONTH_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_week_ending(event: str, release_date: date) -> date:
    """FMP's event name embeds the week-ending date as e.g. "(Sep/18)" with no year -- the week
    ending is always a few days before the release, so infer the year from release_date, rolling
    back one year in the one case that matters (a December week-ending label on a January
    release)."""
    match = WEEK_ENDING_RE.search(event)
    if not match:
        raise ValueError(f"Could not parse week-ending date from event name: {event!r}")
    month = MONTH_ABBR[match.group(1)]
    day = int(match.group(2))
    year = release_date.year
    candidate = date(year, month, day)
    if candidate > release_date:
        candidate = date(year - 1, month, day)
    return candidate


def _fetch_todays_event(today: str) -> dict | None:
    response = requests.get(
        f"{FMP_BASE}/economic-calendar",
        params={"from": today, "to": today, "apikey": FMP_API_KEY},
        timeout=20,
    )
    response.raise_for_status()
    for row in response.json():
        if row.get("country") == "US" and row.get("event", "").startswith(EVENT_PREFIX):
            return row
    return None


def check() -> None:
    if not FMP_API_KEY:
        print("[oil_weekly_job] FMP_API_KEY not set, skipping")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = _fetch_todays_event(today)
    if row is None:
        print(f"[oil_weekly_job] No API Crude Oil Stock Change event scheduled for {today}")
        return

    if row.get("actual") is None:
        print("[oil_weekly_job] Event scheduled today but actual not yet released")
        return

    release_ts = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    week_ending = _parse_week_ending(row["event"], release_ts.date())

    init_db()
    if oil_weekly_report_exists(week_ending):
        print(f"[oil_weekly_job] Week ending {week_ending} already recorded, nothing to do")
        return

    unit = row.get("unit") or ""
    previous, estimate, actual = row.get("previous"), row.get("estimate"), row.get("actual")
    gold_price = None
    try:
        gold_price = fetch_gold_spot_price()
    except Exception:
        pass  # gold_at_release is a nice-to-have, not worth failing the whole job over

    message = (
        f"{RELEASE_ALERT_PREFIX}API Crude Oil Stock Change (week ending {week_ending}) just "
        f"released: actual {actual}{unit} vs estimate {estimate}{unit} (previous {previous}{unit})"
    )
    send_telegram_message(message)

    insert_oil_weekly_report(
        release_ts=release_ts,
        week_ending=week_ending,
        previous_value=str(previous) if previous is not None else None,
        expected_value=str(estimate) if estimate is not None else None,
        actual_value=str(actual) if actual is not None else None,
        gold_at_release=gold_price,
        gold_5min=None,
        gold_10min=None,
        gold_30min=None,
        gold_1h=None,
        gold_2h=None,
        notes="Recorded live by oil_weekly_job.py",
    )
    print(f"[oil_weekly_job] Recorded and alerted week ending {week_ending}")


if __name__ == "__main__":
    check()
