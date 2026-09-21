# Frequency test thresholds (15/10/5-min windows)

`config.INTRAHOUR_SWING_ALERT_THRESHOLD` holds three independent thresholds per indicator — 15, 10,
and 5 minutes (`config.INTRAHOUR_SWING_WINDOWS_MINUTES`) — so `rules.check_intrahour_swing_alerts` can
fire up to three alerts per indicator per poll (one per window), each naming the indicator, the window,
the threshold, the direction (up/down), and the swing amount.

## Methodology (companion-swing average, not a target event rate)

This replaced an earlier methodology that searched for a threshold landing each indicator/window
combination at ~60 rising-edge events per 60 days. The current methodology (`frequency_test.py`) is
simpler and has no search step at all:

1. **Define a "gold event."** Gold spot itself must swing a fixed dollar amount, within a matching
   window, over the trailing `FREQUENCY_TEST_LOOKBACK_DAYS` (30) days: $5 in a 5-minute window, $10 in
   10 minutes, $15 in 15 minutes (`config.GOLD_SWING_THRESHOLDS`). Rising-edge deduped (current window
   over the threshold, the window one poll interval — 5 min — earlier wasn't), so a sustained swing
   counts once, not repeatedly.

2. **Restrict to the common trading session.** Only gold events whose *entire* window falls within
   `COMMON_SESSION_START_ET`-`COMMON_SESSION_END_ET` (9:30am-2:55pm ET, weekdays) count. This is the
   trading-hours intersection of all eight intrahour-swing indicators — the six equity ETFs (GLD/IAU/
   GLDM/GDX/GDXJ/RING) trade 9:30am-4pm ET, DXY is near-24hr, and US10Y (`^TNX`) only quotes roughly
   8:20am-2:55pm ET, the tightest constraint. A gold move outside this window can't be fairly compared
   against all eight, since some of them simply weren't trading.

3. **Measure each indicator's companion swing at every gold event.** For each of the eight indicators,
   at every one of gold's event timestamps, measure that indicator's own high-low range over the
   *identical* window (same start, same end — not the window before or after). Average that swing
   across every event. **That average is the indicator's threshold** for that window — there is no
   target/tolerance band and nothing to search for.

4. **Recompute fresh every time.** `FREQUENCY_TEST_LOOKBACK_DAYS` is a rolling 30-day window, not a
   fixed historical range, so rerunning this study on a different day produces (slightly) different
   numbers as the trailing window shifts.

Data sources: gold spot and the six gold ETFs come from **Twelve Data** at true 1-minute resolution,
paginated back the full lookback (yfinance's 1-minute bars are capped at ~7-8 days by Yahoo itself, far
short of 30 days). DXY and US10Y stay on **yfinance's 5-minute bars** — neither Twelve Data nor FMP has
a genuine Dollar Index or intraday Treasury-yield instrument at any plan tier evaluated (Twelve Data's
`USDX`/`DX` symbols *look* plausible but resolve to unrelated tickers, confirmed via its own
`symbol_search`; FMP has no Dollar Index at all and only daily treasury rates). See
`docs/data-sources.md` for the full data-source comparison.

## Thresholds (as of this backtest)

| Indicator | 15-min threshold | 10-min threshold | 5-min threshold |
|---|---|---|---|
| GLD | $1.77 | $1.18 | $0.60 |
| IAU | $0.37 | $0.24 | $0.12 |
| GLDM | $0.39 | $0.25 | $0.13 |
| GDX | $0.83 | $0.56 | $0.28 |
| GDXJ | $1.16 | $0.77 | $0.40 |
| RING | $0.69 | $0.46 | $0.22 |
| DXY | 0.0445 | 0.0223 | 0.0147 |
| US10Y | 0.0071 | 0.0035 | 0.0025 |

**These twenty-four thresholds are re-tuned automatically every weekday morning.** The values above are
what this backtest produced at the time this doc was written; the live values always live in
`intrahour_swing_thresholds.json`, not here.

## Sample sizes behind these numbers

Over the 30-day lookback this doc's numbers came from: 224 gold events at the 15-min window, 452 at
10-min, 1,002 at 5-min (all restricted to the common trading session). Not every indicator had usable
data (≥2 price points) at every one of those events — the 15-min and 10-min windows had near-total
coverage (224/224 and ~447-452/452 across all eight indicators), but the 5-min window is
resolution-limited even with 1-minute source data: DXY and US10Y, still sourced from yfinance's 5-minute
bars, only had usable data at ~191/1,002 of the 5-min gold events (a 5-minute window against
5-minute-spaced bars usually only catches one bar, too few to measure a swing). Treat the 5-min column
for DXY/US10Y specifically as the least reliable of the twenty-four; the ETF group's 5-min column (now
sourced from genuine 1-minute Twelve Data bars) had ~987-989/1,002 coverage and is far more trustworthy
than it was under the previous all-5-minute-bar methodology.

## Alerting

Every one of these twenty-four threshold values is wired into `rules.check_intrahour_swing_alerts` and
alerts to Telegram — no window or indicator here is backtest-only. A poll can produce anywhere from
zero to twenty-four of these alerts (three windows x eight indicators) in a single cycle, each
rising-edge deduped per window so a sustained swing alerts once, not repeatedly for the rest of the
window. IAU, GLDM, GDX, GDXJ, and RING are alerted and re-tuned exactly like GLD, but — unlike GLD —
are **not** referenced by the Broker's paper-trading rules (`.claude/agents/broker.md`), which still
only watch GLD/DXY/US10Y.

## Weekday auto-tuning

`frequency_check_job.py` reruns this same companion-swing study every weekday morning (6 AM ET,
Monday-Friday — cron-job.org is configured to skip Saturday/Sunday, see `CLAUDE.md`'s "Scheduling")
and overwrites `intrahour_swing_thresholds.json` with whichever of the twenty-four combinations'
freshly computed averages actually differ from what's currently on disk — combinations that land on
the same value (rounded to 4 decimal places) are left untouched.
`.github/workflows/frequency_check.yml` then commits the file, opens a PR, and merges it (`gh pr merge
--squash`, no branch-protection bypass — a protected `main` requiring review will leave the PR open for
a human instead of forcing it through).

A Telegram message is sent every weekday run either way, listing all twenty-four combinations and
marking each one `unchanged` (with its current value) or showing the change (old value -> new value,
with the sample size behind the new one). Unlike the interactive workflow above, this path never asks
for approval first — that trade-off (routine drift correction with no human gate, vs. a threshold that
can go stale between manual runs) was a deliberate choice; see `CLAUDE.md`'s "Automatic (weekday
mornings, unattended)" workflow section for the reasoning.

Every weekday run also logs one row — the date and that run's final value for all twenty-four
combinations, whether or not any of them changed — to the `threshold_history` table in Postgres (see
`storage.py`'s `insert_threshold_history_row()`/`get_threshold_history()`), **not** to a table in this
file. It's the audit trail for "what was the threshold on day X", independent of the Telegram message
history — unaffected by the methodology change above, since it just logs whatever the final values are
each run. Columns: `date` (America/New_York, the job's own schedule) and
`gld_15min`/`gld_10min`/`gld_5min`/`dxy_15min`/`dxy_10min`/`dxy_5min`/`us10y_15min`/`us10y_10min`/
`us10y_5min`, plus `iau_*`/`gldm_*`/`gdx_*`/`gdxj_*`/`ring_*` (added later, nullable, so pre-existing
rows logged before a given indicator was tracked simply have `NULL` there).
