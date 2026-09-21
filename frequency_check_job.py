"""Scheduled job (weekdays, 6 AM ET, via cron-job.org -> workflow_dispatch, same pattern as
poll_job.py): recompute frequency_test.py's companion-swing average for all twenty-four
indicator/window combinations (15/10/5 min for each of gld/dxy/us10y/iau/gldm/gdx/gdxj/ring)
over the trailing FREQUENCY_TEST_LOOKBACK_DAYS days, and overwrite
intrahour_swing_thresholds.json with the fresh values.

Unlike the target-event-rate search this replaced, there's no off-target check -- every
threshold is a plain rolling average of what each indicator does when gold itself swings
GOLD_SWING_THRESHOLDS[window], so every weekday run recomputes and potentially rewrites all
twenty-four values, keeping them current as market volatility drifts. The GitHub Actions
workflow (.github/workflows/frequency_check.yml) commits intrahour_swing_thresholds.json,
opens a PR, and merges it whenever the recomputed values actually differ from what's on disk.
A Telegram message is always sent, listing every one of the twenty-four combinations and
whether it changed (old value -> new value) or stayed the same, plus the directional
co-flagging distribution (frequency_test.py's co_flagging_distribution()) for each of the three
windows -- how many of the eight indicators, at each gold event, crossed their own threshold
AND moved in the direction broker.py's Consensus6of8 rule requires. This is reporting only --
it doesn't feed back into the twenty-four thresholds themselves or into broker.py.

Run: python frequency_check_job.py
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from config import DOLLAR_UNIT_NAMES, INTRAHOUR_SWING_ALERT_THRESHOLD, INTRAHOUR_SWING_THRESHOLDS_PATH
from frequency_test import run_frequency_test
from notifier import send_telegram_message
from storage import init_db, insert_threshold_history_row


def append_history_row(thresholds: dict[str, dict[int, float]]) -> None:
    """Logs one row -- today's date (America/New_York) plus the final value of all twenty-four
    indicator/window combinations -- to the `threshold_history` table in Postgres (see
    storage.py). Runs every weekday regardless of whether check() changed anything, so the
    table is a complete weekday log."""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    insert_threshold_history_row(today, thresholds)


def _co_flag_lines(co_flags: dict[int, dict]) -> list[str]:
    lines = ["Co-flagging (magnitude AND direction coherent with gold, per Consensus6of8):"]
    for window in sorted(co_flags, reverse=True):
        data = co_flags[window]
        dist = data["distribution"]
        counts = ", ".join(f">={n}: {dist[n]}" for n in range(1, 9))
        lines.append(f"  {window}min ({data['n_events']} events): {counts}")
    return lines


def check() -> None:
    init_db()
    results, co_flags = run_frequency_test()

    updated_thresholds = {
        name: dict(by_window) for name, by_window in INTRAHOUR_SWING_ALERT_THRESHOLD.items()
    }
    lines = []
    any_changed = False

    for name, by_window in results.items():
        unit = "$" if name in DOLLAR_UNIT_NAMES else ""
        for window, data in sorted(by_window.items(), reverse=True):
            old_threshold = INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]

            if data["avg"] is None:
                lines.append(
                    f"  {name.upper()} {window}min: no usable data this run, kept "
                    f"{unit}{old_threshold:.4f}"
                )
                continue

            new_threshold = round(data["avg"], 4)
            updated_thresholds[name][window] = new_threshold

            if new_threshold == old_threshold:
                lines.append(
                    f"  {name.upper()} {window}min: unchanged, {unit}{old_threshold:.4f} "
                    f"(n={data['n_used']}/{data['n_total']})"
                )
            else:
                any_changed = True
                lines.append(
                    f"  {name.upper()} {window}min: {unit}{old_threshold:.4f} -> "
                    f"{unit}{new_threshold:.4f} (n={data['n_used']}/{data['n_total']})"
                )

    if any_changed:
        with open(INTRAHOUR_SWING_THRESHOLDS_PATH, "w") as f:
            json.dump(
                {name: {str(w): v for w, v in by_window.items()} for name, by_window in updated_thresholds.items()},
                f,
                indent=2,
            )
            f.write("\n")
        header = "Frequency check: companion-swing thresholds recomputed (rolling 30-day average)"
    else:
        header = "Frequency check: all 24 indicator/window combos unchanged this run"

    message = "\n".join([header] + lines + [""] + _co_flag_lines(co_flags))
    print(message)
    send_telegram_message(message)

    append_history_row(updated_thresholds)


if __name__ == "__main__":
    check()
