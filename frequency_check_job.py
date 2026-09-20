"""Scheduled job (weekdays, 6 AM ET, via cron-job.org -> workflow_dispatch,
same pattern as poll_job.py): run frequency_test.py against the *current*
INTRAHOUR_SWING_ALERT_THRESHOLD values (eighteen indicator/window combinations
-- 15/10/5 min for each of gld/dxy/us10y/iau/gldm/sgol) and, for any
combination whose FREQUENCY_TEST_LOOKBACK_DAYS-day rising-edge event count has
drifted outside config.FREQUENCY_TEST_TARGET +/- config.FREQUENCY_TEST_TOLERANCE,
search a new threshold (threshold_search.search_threshold) and write it to
intrahour_swing_thresholds.json.

Unlike the interactive "Standing frequency test workflow" in CLAUDE.md (which
reports and waits for a human to approve a new threshold), this job applies
the change itself: it's meant to run fully unattended every weekday, with the
GitHub Actions workflow (.github/workflows/frequency_check.yml) committing
the updated JSON file, opening a PR, and merging it automatically when this
script changes anything. A Telegram message is always sent, listing every
one of the eighteen combinations and whether it changed or stayed the same.

Run: python frequency_check_job.py
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    DOLLAR_UNIT_NAMES,
    FREQUENCY_TEST_LOOKBACK_DAYS,
    FREQUENCY_TEST_TARGET,
    FREQUENCY_TEST_TOLERANCE,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    INTRAHOUR_SWING_THRESHOLDS_PATH,
)
from frequency_test import run_frequency_test
from notifier import send_telegram_message
from storage import init_db, insert_threshold_history_row
from threshold_search import search_threshold


def append_history_row(thresholds: dict[str, dict[int, float]]) -> None:
    """Logs one row -- today's date (America/New_York) plus the final value of all eighteen
    indicator/window combinations -- to the `threshold_history` table in Postgres (see storage.py).
    Runs every weekday regardless of whether check() changed anything, so the table is a complete
    weekday log. Replaces the old "Threshold history" table that used to live in
    docs/frequency-test-thresholds.md -- a routine log entry shouldn't need a repo commit."""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    insert_threshold_history_row(today, thresholds)


def check() -> None:
    init_db()
    results = run_frequency_test()

    lo = FREQUENCY_TEST_TARGET - FREQUENCY_TEST_TOLERANCE
    hi = FREQUENCY_TEST_TARGET + FREQUENCY_TEST_TOLERANCE

    updated_thresholds = {
        name: dict(by_window) for name, by_window in INTRAHOUR_SWING_ALERT_THRESHOLD.items()
    }
    lines = []
    any_changed = False

    for name, data in results.items():
        timestamps, prices = data["series"]
        unit = "$" if name in DOLLAR_UNIT_NAMES else ""
        for window, events in sorted(data["windows"].items(), reverse=True):
            count = len(events)
            old_threshold = INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]

            if lo <= count <= hi:
                lines.append(
                    f"  {name.upper()} {window}min: unchanged, {unit}{old_threshold:.4f} "
                    f"({count} events)"
                )
                continue

            any_changed = True
            new_threshold, new_count = search_threshold(
                timestamps, prices, window, lo, hi, seed=old_threshold
            )
            updated_thresholds[name][window] = round(new_threshold, 4)
            lines.append(
                f"  {name.upper()} {window}min: {unit}{old_threshold:.4f} ({count} events) -> "
                f"{unit}{new_threshold:.4f} ({new_count} events)"
            )

    if any_changed:
        with open(INTRAHOUR_SWING_THRESHOLDS_PATH, "w") as f:
            json.dump(
                {name: {str(w): v for w, v in by_window.items()} for name, by_window in updated_thresholds.items()},
                f,
                indent=2,
            )
            f.write("\n")
        header = (
            f"Frequency check: thresholds updated (target {FREQUENCY_TEST_TARGET}+/-"
            f"{FREQUENCY_TEST_TOLERANCE} events/{FREQUENCY_TEST_LOOKBACK_DAYS} days)"
        )
    else:
        header = (
            f"Frequency check: all 9 indicator/window combos within {lo}-{hi} "
            f"events/{FREQUENCY_TEST_LOOKBACK_DAYS} days, no changes"
        )

    message = "\n".join([header] + lines)
    print(message)
    send_telegram_message(message)

    append_history_row(updated_thresholds)


if __name__ == "__main__":
    check()
