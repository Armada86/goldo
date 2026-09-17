"""Scheduled job (daily, 8 PM ET, via cron-job.org -> workflow_dispatch,
same pattern as poll_job.py): run frequency_test.py against the *current*
INTRAHOUR_SWING_ALERT_THRESHOLD values (now three windows -- 15/10/5 min --
per indicator) and send a Telegram message either way -- an all-clear summary
if every indicator/window combination's actual
FREQUENCY_TEST_LOOKBACK_DAYS-day rising-edge event count is within target
(config.FREQUENCY_TEST_TARGET +/- config.FREQUENCY_TEST_TOLERANCE), or a
drift alert naming the offenders.

This only alerts -- it never changes a threshold. Per CLAUDE.md's "Standing
frequency test workflow," picking and applying a new threshold is a human
decision made in a Claude Code session, not something this job does
automatically.

Run: python frequency_check_job.py
"""

from config import (
    FREQUENCY_TEST_LOOKBACK_DAYS,
    FREQUENCY_TEST_TARGET,
    FREQUENCY_TEST_TOLERANCE,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
)
from frequency_test import run_frequency_test
from notifier import send_telegram_message


def check() -> None:
    results = run_frequency_test()

    lo = FREQUENCY_TEST_TARGET - FREQUENCY_TEST_TOLERANCE
    hi = FREQUENCY_TEST_TARGET + FREQUENCY_TEST_TOLERANCE
    all_combos = [
        (name, window, len(events))
        for name, by_window in results.items()
        for window, events in by_window.items()
    ]
    offenders = [(name, window, count) for name, window, count in all_combos if not (lo <= count <= hi)]

    if not offenders:
        lines = [f"Frequency check: all indicator/window combos within {lo}-{hi} events/{FREQUENCY_TEST_LOOKBACK_DAYS} days"]
        for name, window, count in all_combos:
            threshold = INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]
            lines.append(f"  {name.upper()} {window}min: {count} events (threshold {threshold})")
        message = "\n".join(lines)
        print(message)
        send_telegram_message(message)
        return

    lines = [
        f"Frequency test: {len(offenders)} indicator/window combo(s) outside the "
        f"{FREQUENCY_TEST_TARGET}+/-{FREQUENCY_TEST_TOLERANCE} events/{FREQUENCY_TEST_LOOKBACK_DAYS}-day target:"
    ]
    for name, window, count in offenders:
        threshold = INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]
        lines.append(f"  {name.upper()} {window}min: {count} events (threshold {threshold})")
    lines.append("Run a frequency test in Claude Code to review and approve new thresholds.")

    message = "\n".join(lines)
    print(message)
    send_telegram_message(message)


if __name__ == "__main__":
    check()
