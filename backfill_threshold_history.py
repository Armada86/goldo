"""One-time migration: loads the single historical row that used to be in
docs/frequency-test-thresholds.md's "Threshold history" table into the `threshold_history` table in
Postgres. Run once (`python backfill_threshold_history.py`, with DATABASE_URL set) after that table
exists -- init_db() below creates it if needed. Safe to re-run: it skips instead of duplicating if the
table already has rows.
"""

from datetime import date

from storage import get_threshold_history, init_db, insert_threshold_history_row

ROWS = [
    (
        date(2026, 9, 17),
        {"gld": {15: 1.65, 10: 1.42, 5: 1.08}, "dxy": {15: 0.1020, 10: 0.0840, 5: 0.0640},
         "us10y": {15: 0.0140, 10: 0.0123, 5: 0.0100}},
    ),
]


def main() -> None:
    init_db()
    if get_threshold_history():
        print("threshold_history already has rows -- skipping to avoid duplicating the backfill.")
        return
    for row_date, thresholds in ROWS:
        insert_threshold_history_row(row_date, thresholds)
    print(f"Inserted {len(ROWS)} historical threshold_history row(s).")


if __name__ == "__main__":
    main()
