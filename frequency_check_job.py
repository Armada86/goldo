"""Scheduled job (daily, midnight ET, via cron-job.org -> workflow_dispatch,
same pattern as poll_job.py): run frequency_test.py against the *current*
INTRAHOUR_SWING_ALERT_THRESHOLD values and send a Telegram alert if any
indicator's actual 30-day rising-edge event count has drifted outside the
target band (config.FREQUENCY_TEST_TARGET +/- config.FREQUENCY_TEST_TOLERANCE).

This only alerts -- it never changes a threshold. Per CLAUDE.md's "Standing
frequency test workflow," picking and applying a new threshold is a human
decision made in a Claude Code session, not something this job does
automatically.

Run: python frequency_check_job.py
"""

from config import FREQUENCY_TEST_TARGET, FREQUENCY_TEST_TOLERANCE, INTRAHOUR_SWING_ALERT_THRESHOLD
from frequency_test import run_frequency_test
from notifier import send_telegram_message


def check() -> None:
    results = run_frequency_test()

    lo = FREQUENCY_TEST_TARGET - FREQUENCY_TEST_TOLERANCE
    hi = FREQUENCY_TEST_TARGET + FREQUENCY_TEST_TOLERANCE
    offenders = [
        (name, len(events)) for name, events in results.items()
        if not (lo <= len(events) <= hi)
    ]

    if not offenders:
        print(f"Frequency check: all indicators within {lo}-{hi} events/30 days")
        return

    lines = [
        f"Frequency test: {len(offenders)} indicator(s) outside the "
        f"{FREQUENCY_TEST_TARGET}+/-{FREQUENCY_TEST_TOLERANCE} events/30-day target:"
    ]
    for name, count in offenders:
        threshold = INTRAHOUR_SWING_ALERT_THRESHOLD[name]
        lines.append(f"  {name.upper()}: {count} events (threshold {threshold})")
    lines.append("Run a frequency test in Claude Code to review and approve new thresholds.")

    message = "\n".join(lines)
    print(message)
    send_telegram_message(message)


if __name__ == "__main__":
    check()
