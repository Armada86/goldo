"""One-time migration: loads the 12 historical NFP releases that used to live in
docs/fundamental-analyst-nfp-log.md's table into the `nfp_reports` table in Postgres. Run once
(`python backfill_nfp_reports.py`, with DATABASE_URL set) after that table exists -- init_db() below
creates it if needed. Safe to re-run: it checks for existing rows first and skips instead of
duplicating, since nothing else in this project needs a unique constraint on release_ts.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from storage import get_nfp_reports, init_db, insert_nfp_report

ET = ZoneInfo("America/New_York")


def _release(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 8, 30, tzinfo=ET)  # BLS releases are always 8:30am ET


REPORTS = [
    dict(
        release_ts=_release(2026, 9, 4), data_month="Aug 2026", previous_value="21K",
        expected_value="55K", actual_value="162K", gold_at_release=4470.95, gold_5min=4398.92,
        gold_10min=4394.20, gold_30min=4403.66, gold_1h=4406.73, gold_2h=4431.85, notes=None,
    ),
    dict(
        release_ts=_release(2026, 8, 7), data_month="Jul 2026", previous_value="20K",
        expected_value="85K", actual_value="-23K", gold_at_release=4312.86, gold_5min=4353.31,
        gold_10min=4352.74, gold_30min=4357.77, gold_1h=4355.37, gold_2h=4351.02, notes=None,
    ),
    dict(
        release_ts=_release(2026, 7, 2), data_month="Jun 2026", previous_value="129K",
        expected_value="114K", actual_value="57K", gold_at_release=4068.63, gold_5min=4113.60,
        gold_10min=4131.38, gold_30min=4118.86, gold_1h=4112.38, gold_2h=4121.71, notes=None,
    ),
    dict(
        release_ts=_release(2026, 6, 5), data_month="May 2026", previous_value="179K",
        expected_value="85K", actual_value="172K", gold_at_release=4463.14, gold_5min=4452.85,
        gold_10min=4442.42, gold_30min=4411.89, gold_1h=4410.87, gold_2h=4358.31, notes=None,
    ),
    dict(
        release_ts=_release(2026, 5, 8), data_month="Apr 2026", previous_value="185K",
        expected_value="65K", actual_value="115K", gold_at_release=4709.60, gold_5min=4717.18,
        gold_10min=4725.34, gold_30min=4730.17, gold_1h=4726.51, gold_2h=4721.41, notes=None,
    ),
    dict(
        release_ts=_release(2026, 4, 3), data_month="Mar 2026", previous_value="-133K",
        expected_value="65K", actual_value="178K", gold_at_release=4676.42, gold_5min=4676.34,
        gold_10min=4676.41, gold_30min=4676.53, gold_1h=4676.41, gold_2h=4676.39,
        notes="Essentially flat (within $0.20) across all windows despite a large beat -- a far "
        "smaller reaction than every other release in the sample; possibly a thin-liquidity/"
        "data-feed quirk for that session rather than a real 'no reaction'.",
    ),
    dict(
        release_ts=_release(2026, 3, 6), data_month="Feb 2026", previous_value="126K",
        expected_value="58K", actual_value="-92K", gold_at_release=5078.43, gold_5min=5121.41,
        gold_10min=5125.11, gold_30min=5086.23, gold_1h=5103.70, gold_2h=5165.62, notes=None,
    ),
    dict(
        release_ts=_release(2026, 2, 11), data_month="Jan 2026", previous_value="48K",
        expected_value="66K", actual_value="130K", gold_at_release=5079.70, gold_5min=5047.97,
        gold_10min=5055.84, gold_30min=5069.14, gold_1h=5069.64, gold_2h=5049.43,
        notes="Consensus varied by provider: 66K (investing.com, used here) vs. 40K (a second "
        "source checked) -- normal, since providers poll forecasters at different times pre-release.",
    ),
    dict(
        release_ts=_release(2026, 1, 9), data_month="Dec 2025", previous_value="56K",
        expected_value="66K", actual_value="50K", gold_at_release=4471.70, gold_5min=4484.86,
        gold_10min=4487.39, gold_30min=4483.84, gold_1h=4494.62, gold_2h=4509.64, notes=None,
    ),
    dict(
        release_ts=_release(2025, 12, 16), data_month="Nov 2025",
        previous_value="-105K (Oct, first published same day)", expected_value="~45K",
        actual_value="64K", gold_at_release=4300.78, gold_5min=4307.78, gold_10min=4308.02,
        gold_30min=4311.06, gold_1h=4328.20, gold_2h=4319.13,
        notes="Shutdown-disrupted: October 2025's payroll change was never separately published -- "
        "the establishment survey folded it into this combined Nov release. 'Previous' here is "
        "October, published for the first time this same day, not a revision of a known number.",
    ),
    dict(
        release_ts=_release(2025, 11, 20), data_month="Sep 2025", previous_value="-4K",
        expected_value="50K", actual_value="119K", gold_at_release=4088.47, gold_5min=4083.64,
        gold_10min=4086.98, gold_30min=4074.21, gold_1h=4084.80, gold_2h=4097.14,
        notes="Shutdown-disrupted: delayed ~6 weeks (from Oct 3, 2025 to Nov 20, 2025) by the "
        "Oct 1 - Nov 12, 2025 federal government shutdown.",
    ),
    dict(
        release_ts=_release(2025, 9, 5), data_month="Aug 2025", previous_value="79K",
        expected_value="75K", actual_value="22K", gold_at_release=3559.60, gold_5min=3577.49,
        gold_10min=3583.50, gold_30min=3581.94, gold_1h=3579.21, gold_2h=3579.54, notes=None,
    ),
]


def main() -> None:
    init_db()
    if get_nfp_reports():
        print("nfp_reports already has rows -- skipping to avoid duplicating the backfill.")
        return
    for report in REPORTS:
        insert_nfp_report(**report)
    print(f"Inserted {len(REPORTS)} historical NFP reports.")


if __name__ == "__main__":
    main()
