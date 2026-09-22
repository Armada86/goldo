# Fundamental analyst — API Weekly Crude Oil Stock description & usage

Description and operational-usage reference for the API Crude Oil Stock Change report, a supporting doc
for the `fundamental-analyst` subagent — the same role `docs/fundamental-analyst-nfp-log.md`/
`docs/fundamental-analyst-adp-log.md` play for the employment reports. Unlike those two, this indicator
is not FRED-sourced and not tracked in `config.FRED_SERIES` at all — see "Data source" below.

## Description

The American Petroleum Institute (API) publishes a **weekly** estimate of US commercial crude oil
inventory levels, released **Tuesday evenings** (observed release times vary within roughly
19:00-22:00 UTC / 3pm-6pm ET, unlike a fixed-minute release like ADP/NFP), one day ahead of the
official, more closely-watched EIA Weekly Petroleum Status Report (Wednesdays ~10:30am ET, not tracked
by this project). Each release covers a specific week ending the preceding Friday — FMP's event name
embeds this as e.g. `API Crude Oil Stock Change (Sep/18)`.

**Relationship to gold is indirect and mixed**, unlike the employment reports' fairly direct
rate-expectations channel: a crude *build* (inventories rising, oversupply) tends to pressure oil
prices down, which can ripple into broader risk-off/inflation-expectations sentiment that's sometimes
gold-supportive and sometimes not, depending on what's driving the supply move (demand weakness reads
differently than a supply glut). A crude *draw* (inventories falling) is the mirror case. Because API's
own numbers are also just a preview of the next day's official EIA figure (and frequently revised away
from it), and because oil/gold co-movement is noisier and less mechanically-linked than the
employment-to-rate-expectations chain, treat this as a lower-conviction, context indicator rather than
a clean directional signal the way GLD/DXY/US10Y's intrahour-swing alerts are.

## Data source

**FMP** (Financial Modeling Prep), not FRED — this is the first indicator in the project sourced this
way. FRED does not carry a matching series (checked; FRED has no API-published weekly crude oil
inventory series at all, only EIA's official one, which this project also doesn't track intraday). FMP's
`/stable/economic-calendar` endpoint has real historical structured data for this event going back at
least a year, with genuine `previous`/`estimate`/`actual` figures — see `docs/data-sources.md` for the
full source comparison and FMP's other quirks (the ~90-day-per-call history cap this indicator's
backfill has to paginate around, confirmed empirically).

## How it's used in this project

**Detection & live alerting**: `oil_weekly_job.py` is a one-shot script, triggered repeatedly by
cron-job.org across each Tuesday's multi-hour release window (see
`.github/workflows/oil_weekly_watch.yml`) rather than on a fixed poll cadence — the release time is too
variable for a tight burst-poll (contrast `release_watch_job.py`'s ADP/NFP approach, which works because
those release at a known, fixed minute). Each invocation checks FMP's calendar once; the moment
`actual` appears for the current week and that week isn't already recorded, it sends a Telegram alert
(🟣 prefix, same category as ADP/NFP release alerts) and records the release directly.

**Recording**: unlike ADP/NFP (where an external Claude Code Routine owns recording, and
`release_watch_job.py` deliberately never writes to Postgres to avoid stepping on that), there's no
other mechanism tracking this indicator, so `oil_weekly_job.py` is the sole writer. Data lives in the
`oil_weekly_reports` table (see `storage.py`): `release_ts`, `week_ending` (a real `DATE`, parsed from
FMP's event-name suffix — the natural per-release key, with a `UNIQUE` constraint so a repeated
invocation after the real release can't double-insert), `previous_value`/`expected_value`/`actual_value`
(freeform text, FMP's raw figures with their unit suffix when present), `gold_at_release` and the
`gold_5min`/`gold_10min`/`gold_30min`/`gold_1h`/`gold_2h` reaction columns (left `NULL` at insert time,
fillable later via `storage.update_oil_weekly_report_reaction()`), and `notes`.

**Backfill**: `backfill_oil_weekly_reports.py` loaded the last 52 weeks, fetched live from FMP (paginated
in ~85-day chunks around FMP's silent per-call history cap) — unlike the original NFP/ADP backfills
(12 hand-researched rows each, since no automated source existed for those at the time), this one is
fully automated, since FMP already has clean structured historical data for this specific event.
**`gold_at_release` and the reaction columns are left `NULL` for every backfilled row** — reconstructing
52 historical intraday gold snapshots is a materially larger, separate research effort (the kind the
original NFP/ADP backfills did by hand for just 12 rows) and wasn't done here; the release figures
themselves (previous/estimate/actual/week_ending) are backfilled in full. Safe to re-run — both the
script's own up-front check and the table's `UNIQUE (week_ending)` constraint make it a no-op once
already populated.

Not currently alerted via `VALUE_CHANGE_ALERT_NAMES` or any mechanism in `rules.py` — it isn't in
`config.FRED_SERIES` or `config.INDICATORS` at all, so the regular poll loop never touches it; all
detection and alerting for this indicator happens in `oil_weekly_job.py` alone, independent of
`main.poll_once()`.
