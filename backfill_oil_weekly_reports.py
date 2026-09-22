"""One-time migration/seed: loads the last 52 weeks of API Crude Oil Stock Change releases into the
`oil_weekly_reports` table in Postgres, fetched live from FMP's economic-calendar endpoint (see
docs/fundamental-analyst-oil-weekly-log.md for sourcing/method). Unlike backfill_nfp_reports.py/
backfill_adp_reports.py (which seeded 12 hand-researched rows, since no automated source existed for
NFP/ADP release-by-release data at the time), this backfill is fully automated -- FMP's calendar
already has clean, structured historical data for this indicator, so there's no manual research step.

gold_at_release and the gold_5min/10min/30min/1h/2h reaction columns are deliberately left NULL for
every backfilled row -- reconstructing 52 historical intraday gold snapshots/reactions is a much
larger, separate effort (the kind of one-off research the original NFP/ADP backfills did for just 12
rows each) and isn't needed for the release figures themselves to be useful. Fill them in later via
storage.update_oil_weekly_report_reaction() if wanted.

FMP's economic-calendar endpoint silently caps how much history a single call returns (confirmed:
requesting a full year in one call fails outright; each call effectively returns ~90 days of data
regardless of how far back `from` is set), so this paginates in ~85-day chunks, same lesson as
frequency_test.py's Twelve Data pagination.

Run once (`python backfill_oil_weekly_reports.py`, with DATABASE_URL and FMP_API_KEY set) -- init_db()
creates the table if needed. Safe to re-run: insert_oil_weekly_report() has an ON CONFLICT (week_ending)
DO NOTHING clause, so a duplicate week is skipped rather than erroring, and this script also skips
entirely up front once oil_weekly_reports already has a full 52+ weeks.
"""

import os
import re
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from storage import get_oil_weekly_reports, init_db, insert_oil_weekly_report

load_dotenv()

FMP_API_KEY = os.environ.get("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com/stable"

BACKFILL_WEEKS = 52
CHUNK_DAYS = 85  # stays comfortably under FMP's silent ~90-day-per-call cap

EVENT_PREFIX = "API Crude Oil Stock Change ("
WEEK_ENDING_RE = re.compile(r"\((\w{3})/(\d{2})\)")
MONTH_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_week_ending(event: str, release_date: date) -> date:
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


def fetch_historical_events() -> list[dict]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(weeks=BACKFILL_WEEKS)

    events_by_date: dict[str, dict] = {}
    chunk_start = start
    while chunk_start < today:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), today)
        response = requests.get(
            f"{FMP_BASE}/economic-calendar",
            params={"from": str(chunk_start), "to": str(chunk_end), "apikey": FMP_API_KEY},
            timeout=20,
        )
        response.raise_for_status()
        for row in response.json():
            if row.get("country") == "US" and row.get("event", "").startswith(EVENT_PREFIX):
                events_by_date[row["date"]] = row
        chunk_start = chunk_end + timedelta(days=1)

    return [events_by_date[d] for d in sorted(events_by_date)]


def main() -> None:
    if not FMP_API_KEY:
        print("FMP_API_KEY not set -- required to fetch historical releases, aborting.")
        return

    init_db()
    if len(get_oil_weekly_reports()) >= BACKFILL_WEEKS:
        print(f"oil_weekly_reports already has {BACKFILL_WEEKS}+ rows -- skipping to avoid re-fetching.")
        return

    events = fetch_historical_events()
    inserted = 0
    for row in events:
        if row.get("actual") is None:
            continue  # the current, still-pending week
        release_ts = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        week_ending = _parse_week_ending(row["event"], release_ts.date())
        unit = row.get("unit") or ""
        previous, estimate, actual = row.get("previous"), row.get("estimate"), row.get("actual")
        insert_oil_weekly_report(
            release_ts=release_ts,
            week_ending=week_ending,
            previous_value=f"{previous}{unit}" if previous is not None else None,
            expected_value=f"{estimate}{unit}" if estimate is not None else None,
            actual_value=f"{actual}{unit}" if actual is not None else None,
            gold_at_release=None,
            gold_5min=None,
            gold_10min=None,
            gold_30min=None,
            gold_1h=None,
            gold_2h=None,
            notes="Backfilled from FMP economic-calendar; gold reaction columns not populated.",
        )
        inserted += 1

    print(f"Inserted {inserted} historical API Crude Oil Stock Change reports "
          f"(ON CONFLICT DO NOTHING means already-present weeks were skipped, not duplicated).")


if __name__ == "__main__":
    main()
