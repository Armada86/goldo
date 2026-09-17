"""Scheduled job (daily, 8 PM ET, via cron-job.org -> workflow_dispatch,
same pattern as poll_job.py): run frequency_test.py against the *current*
INTRAHOUR_SWING_ALERT_THRESHOLD values (nine indicator/window combinations --
15/10/5 min for each of gld/dxy/us10y) and, for any combination whose
FREQUENCY_TEST_LOOKBACK_DAYS-day rising-edge event count has drifted outside
config.FREQUENCY_TEST_TARGET +/- config.FREQUENCY_TEST_TOLERANCE, search a
new threshold (threshold_search.search_threshold) and write it to
intrahour_swing_thresholds.json.

Unlike the interactive "Standing frequency test workflow" in CLAUDE.md (which
reports and waits for a human to approve a new threshold), this job applies
the change itself: it's meant to run fully unattended every night, with the
GitHub Actions workflow (.github/workflows/frequency_check.yml) committing
the updated JSON file, opening a PR, and merging it automatically when this
script changes anything. A Telegram message is always sent, listing every
one of the nine combinations and whether it changed or stayed the same.

Run: python frequency_check_job.py
"""

import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from config import (
    FREQUENCY_TEST_LOOKBACK_DAYS,
    FREQUENCY_TEST_TARGET,
    FREQUENCY_TEST_TOLERANCE,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    INTRAHOUR_SWING_THRESHOLDS_PATH,
)
from frequency_test import run_frequency_test
from notifier import send_telegram_message
from threshold_search import search_threshold

# "Threshold history" table in this doc -- one row appended per night's run,
# every night, whether or not any threshold changed.
HISTORY_DOC_PATH = Path(__file__).parent / "docs" / "frequency-test-thresholds.md"
HISTORY_HEADER = (
    "| Date | GLD 15min | GLD 10min | GLD 5min | DXY 15min | DXY 10min | DXY 5min "
    "| US10Y 15min | US10Y 10min | US10Y 5min |"
)
HISTORY_ROW_ORDER = [("gld", 15), ("gld", 10), ("gld", 5), ("dxy", 15), ("dxy", 10),
                     ("dxy", 5), ("us10y", 15), ("us10y", 10), ("us10y", 5)]


def _format_threshold(name: str, value: float) -> str:
    unit = "$" if name == "gld" else ""
    decimals = 2 if name == "gld" else 4
    return f"{unit}{value:.{decimals}f}"


def append_history_row(thresholds: dict[str, dict[int, float]]) -> None:
    """Append one row -- today's date (America/New_York) plus the final value
    of all nine indicator/window combinations -- to the "Threshold history"
    table in docs/frequency-test-thresholds.md. Runs every night regardless
    of whether check() changed anything, so the table is a complete daily log."""
    date_str = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    cells = [date_str] + [_format_threshold(name, thresholds[name][window]) for name, window in HISTORY_ROW_ORDER]
    row = "| " + " | ".join(cells) + " |"

    lines = HISTORY_DOC_PATH.read_text().splitlines()
    header_idx = lines.index(HISTORY_HEADER)
    insert_idx = header_idx + 2  # header line, then the "|---|...|" separator line
    while insert_idx < len(lines) and lines[insert_idx].startswith("|"):
        insert_idx += 1
    lines.insert(insert_idx, row)
    HISTORY_DOC_PATH.write_text("\n".join(lines) + "\n")


def check() -> None:
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
        unit = "$" if name == "gld" else ""
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
