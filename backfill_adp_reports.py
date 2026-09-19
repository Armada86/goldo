"""One-time migration/seed: loads the last 12 ADP National Employment Change (ADP NEC) releases into
the `adp_reports` table in Postgres -- the same shape as `backfill_nfp_reports.py`/`nfp_reports`, see
`docs/fundamental-analyst-adp-log.md` for sourcing/method. Run once (`python backfill_adp_reports.py`,
with DATABASE_URL set) -- init_db() below creates the table if needed. Safe to re-run: it checks for
existing rows first and skips instead of duplicating, since nothing else in this project needs a unique
constraint on release_ts.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from storage import get_adp_reports, init_db, insert_adp_report

ET = ZoneInfo("America/New_York")


def _release(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 8, 15, tzinfo=ET)  # ADP releases are always 8:15am ET


REPORTS = [
    dict(
        release_ts=_release(2025, 10, 1), data_month="Sep 2025",
        previous_value="-3K (Aug, revised down from +54K originally reported, due to ADP's "
        "benchmark rebasing against BLS's Sep 2025 revisions)",
        expected_value="45K", actual_value="-32K", gold_at_release=3894.73, gold_5min=3899.01,
        gold_10min=3892.36, gold_30min=3886.42, gold_1h=3878.59, gold_2h=3881.38,
        notes="September's -32K was itself later revised to -29K by the time the October report "
        "printed (see next row's previous_value).",
    ),
    dict(
        release_ts=_release(2025, 11, 5), data_month="Oct 2025",
        previous_value="-29K (Sep, revised from -32K originally reported)",
        expected_value="22K (Dow Jones consensus; a second source cited ~28K)",
        actual_value="42K", gold_at_release=3968.19, gold_5min=3962.50, gold_10min=3969.15,
        gold_30min=3969.90, gold_1h=3974.52, gold_2h=3966.47,
        notes="Released during the Oct 1 - Nov 12, 2025 federal shutdown, while BLS NFP data was "
        "blacked out -- ADP is privately compiled and unaffected by the shutdown, so its schedule "
        "didn't slip the way BLS NFP's did that period. October's 42K was itself later revised to "
        "47K (see next row's previous_value).",
    ),
    dict(
        release_ts=_release(2025, 12, 3), data_month="Nov 2025",
        previous_value="47K (Oct, revised up from +42K originally reported)",
        expected_value="40K", actual_value="-32K", gold_at_release=4212.61, gold_5min=4218.23,
        gold_10min=4221.48, gold_30min=4225.41, gold_1h=4225.07, gold_2h=4216.57,
        notes="Biggest monthly drop since March 2023. November's -32K was itself later revised to "
        "-29K by the time the December report printed (see next row's previous_value).",
    ),
    dict(
        release_ts=_release(2026, 1, 7), data_month="Dec 2025",
        previous_value="-29K (Nov, revised from -32K originally reported)",
        expected_value="48K (Dow Jones consensus per CNBC; investing.com's economic calendar shows 49K)",
        actual_value="41K", gold_at_release=4433.66, gold_5min=4434.92, gold_10min=4442.76,
        gold_30min=4448.31, gold_1h=4449.39, gold_2h=4431.95, notes=None,
    ),
    dict(
        release_ts=_release(2026, 2, 4), data_month="Jan 2026", previous_value="37K",
        expected_value="46K", actual_value="22K", gold_at_release=5031.08, gold_5min=5036.99,
        gold_10min=5047.29, gold_30min=5035.55, gold_1h=5047.25, gold_2h=4990.66, notes=None,
    ),
    dict(
        release_ts=_release(2026, 3, 4), data_month="Feb 2026", previous_value="11K",
        expected_value="50K", actual_value="63K", gold_at_release=5194.46, gold_5min=5190.19,
        gold_10min=5182.55, gold_30min=5188.16, gold_1h=5167.34, gold_2h=5140.71, notes=None,
    ),
    dict(
        release_ts=_release(2026, 4, 1), data_month="Mar 2026", previous_value="66K",
        expected_value="41K", actual_value="62K", gold_at_release=4743.85, gold_5min=4738.07,
        gold_10min=4734.16, gold_30min=4737.05, gold_1h=4731.98, gold_2h=4745.56, notes=None,
    ),
    dict(
        release_ts=_release(2026, 5, 6), data_month="Apr 2026", previous_value="61K",
        expected_value="118K", actual_value="109K", gold_at_release=4676.39, gold_5min=4669.52,
        gold_10min=4676.72, gold_30min=4681.49, gold_1h=4679.42, gold_2h=4710.88, notes=None,
    ),
    dict(
        release_ts=_release(2026, 6, 3), data_month="May 2026", previous_value="105K",
        expected_value="118K", actual_value="122K", gold_at_release=4459.46, gold_5min=4457.52,
        gold_10min=4459.36, gold_30min=4454.35, gold_1h=4463.20, gold_2h=4455.24, notes=None,
    ),
    dict(
        release_ts=_release(2026, 7, 1), data_month="Jun 2026", previous_value="122K",
        expected_value="118K", actual_value="98K", gold_at_release=4023.00, gold_5min=4025.84,
        gold_10min=4026.07, gold_30min=4024.82, gold_1h=4015.76, gold_2h=4098.01, notes=None,
    ),
    dict(
        release_ts=_release(2026, 8, 5), data_month="Jul 2026", previous_value="95K",
        expected_value="68K", actual_value="44K", gold_at_release=4199.13, gold_5min=4207.17,
        gold_10min=4211.81, gold_30min=4194.92, gold_1h=4188.53, gold_2h=4226.95, notes=None,
    ),
    dict(
        release_ts=_release(2026, 9, 2), data_month="Aug 2026", previous_value="46K",
        expected_value="47K", actual_value="38K", gold_at_release=4331.81, gold_5min=4332.86,
        gold_10min=4336.34, gold_30min=4333.63, gold_1h=4339.49, gold_2h=4388.40, notes=None,
    ),
]


def main() -> None:
    init_db()
    if get_adp_reports():
        print("adp_reports already has rows -- skipping to avoid duplicating the backfill.")
        return
    for report in REPORTS:
        insert_adp_report(**report)
    print(f"Inserted {len(REPORTS)} historical ADP NEC reports.")


if __name__ == "__main__":
    main()
