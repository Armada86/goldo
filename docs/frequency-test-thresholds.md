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
| GLD | $1.77 | $1.17 | $0.60 |
| IAU | $0.37 | $0.24 | $0.12 |
| GLDM | $0.39 | $0.25 | $0.13 |
| GDX | $0.84 | $0.56 | $0.28 |
| GDXJ | $1.18 | $0.78 | $0.40 |
| RING | $0.68 | $0.45 | $0.22 |
| DXY | 0.0439 | 0.0223 | 0.0145 |
| US10Y | 0.0070 | 0.0035 | 0.0025 |

**These twenty-four thresholds are re-tuned automatically every weekday morning.** The values above are
what this backtest produced at the time this doc was written; the live values always live in
`intrahour_swing_thresholds.json`, not here.

## Sample sizes behind these numbers

Over the 30-day lookback this doc's numbers came from: 232 gold events at the 15-min window, 484 at
10-min, 1,060 at 5-min (all restricted to the common trading session — exact counts drift slightly run
to run since this is a rolling window, not a fixed date range). Not every indicator had usable data (≥2
price points) at every one of those events — the 15-min and 10-min windows had near-total coverage
(232/232 and ~479-484/484 across all eight indicators), but the 5-min window is resolution-limited even
with 1-minute source data: DXY and US10Y, still sourced from yfinance's 5-minute bars, only had usable
data at ~204/1,060 of the 5-min gold events (a 5-minute window against 5-minute-spaced bars usually only
catches one bar, too few to measure a swing). Treat the 5-min column for DXY/US10Y specifically as the
least reliable of the twenty-four; the ETF group's 5-min column (now sourced from genuine 1-minute
Twelve Data bars) had ~986-1,047/1,060 coverage and is far more trustworthy than it was under the
previous all-5-minute-bar methodology.

## Co-flagging: how many indicators move together (direction-coherent)

At each moment gold itself crossed $5/$10/$15, how many indicators *also* crossed their own
companion-swing threshold in that identical window, moving the direction that actually matters?
"Flagging" here requires **both** conditions: the indicator's own swing, in that window, reached its
threshold from the table above, **and** its direction was coherent with gold's — GLD/IAU/GLDM/GDX/
GDXJ/RING moving the *same* direction gold moved, DXY/US10Y moving the *opposite* direction. An
indicator that merely crossed its threshold in the wrong direction does not count as a flag.

This replaces an earlier version of this table that counted a flag on magnitude alone, regardless of
direction — that version overstated real co-flagging (crediting e.g. DXY for a same-magnitude move in
the *same* direction as gold, which the Broker would never treat as a signal) and is no longer valid;
see `frequency_test.py`'s `co_flagging_distribution()` for the implementation, which both this doc and
`frequency_check_job.py`'s weekday Telegram report now draw from.

Two distributions are computed from the same gold events, since they answer different questions —
**both are real, freshly computed numbers**, not proxies or approximations of each other:

### Consensus5of7 (the seven indicators broker.py actually trades on)

This is the one that directly answers "how often would `broker.py`'s live rule actually see enough
flags to fire" — computed with `co_flagging_distribution()` restricted to `BROKER_TRADED_NAMES`
(GLD/IAU/GLDM/GDX/GDXJ/RING/DXY; US10Y excluded, matching the Broker's own indicator set exactly, not
approximated). Using the same 1,060/484/232 gold events (5/10/15-min) as the thresholds table above:

| At least N of 7 flagging together (direction-coherent) | 5-min (of 1,060) | 10-min (of 484) | 15-min (of 232) |
|---|---|---|---|
| ≥1 | 617 (58.2%) | 323 (66.7%) | 165 (71.1%) |
| ≥2 | 510 (48.1%) | 275 (56.8%) | 125 (53.9%) |
| ≥3 | 440 (41.5%) | 234 (48.3%) | 99 (42.7%) |
| ≥4 | 275 (25.9%) | 148 (30.6%) | 57 (24.6%) |
| **≥5** | **221 (20.8%)** | **111 (22.9%)** | **46 (19.8%)** |
| ≥6 | 167 (15.8%) | 84 (17.4%) | 40 (17.2%) |
| ≥7 (all seven, coherent) | 10 (0.9%) | 38 (7.9%) | 22 (9.5%) |

The **≥5 row is the real, directly-computed `Consensus5of7` firing-condition rate** — no proxy, no
caveat needed. At the 5-minute window (the one `broker.py`'s trailing `ENTRY_WINDOW_MINUTES` check
draws alerts from most often), a coherent 5-of-7 shows up at **20.8% of gold's own qualifying events**
— call it roughly 20 times per 30-day rolling window at this window alone, before accounting for the
10/15-min windows also feeding the same alert stream, or for the mechanical differences noted below.

Per-indicator directional-flag counts, this seven-indicator set:

| Indicator | 5-min | 10-min | 15-min |
|---|---|---|---|
| GLD | 368 | 175 | 72 |
| IAU | 389 | 172 | 73 |
| GLDM | 397 | 164 | 74 |
| GDX | 348 | 213 | 93 |
| GDXJ | 366 | 193 | 92 |
| RING | 338 | 185 | 88 |
| DXY | 34 | 111 | 62 |

DXY is the clear outlier here — at 5-min it flags only 34 times versus 300+ for every ETF, the same
pattern noted below: DXY's inverse correlation with gold is real but far weaker and noisier than the
ETFs' near-1:1 tracking, especially at the shortest window.

**This still isn't the literal Broker trade-open rate**, for two mechanical reasons unrelated to which
indicator set is used: this table anchors each check to a moment gold itself crossed the study's own
$5/$10/$15 bar, while the Broker's actual rule scans a rolling `alerts` table over its own trailing
`ENTRY_WINDOW_MINUTES` (10) window independent of whether gold's own move happened to also qualify as
one of this table's events; and a genuine buy/sell tie (both directions independently reaching 5) opens
no trade either way (`_match_entry_rule()`'s tie-break), which this table doesn't separately track.
Treat ≥5 as a ceiling on how often the condition is met, not a measured trade count.

### All eight indicators (general research view, not Broker-specific)

The same study, but every indicator including US10Y — a general research artifact of
`frequency_test.py`'s methodology, independent of which ones `broker.py` actually trades on. Useful for
understanding co-flagging behavior broadly (e.g. comparing US10Y against DXY as the "moves opposite
gold" indicator), not as a stand-in for the Consensus5of7 table above.

| At least N of 8 flagging together (direction-coherent) | 5-min (of 1,060) | 10-min (of 484) | 15-min (of 232) |
|---|---|---|---|
| ≥1 | 626 (59.1%) | 340 (70.2%) | 171 (73.7%) |
| ≥2 | 517 (48.8%) | 283 (58.5%) | 140 (60.3%) |
| ≥3 | 441 (41.6%) | 238 (49.2%) | 101 (43.5%) |
| ≥4 | 279 (26.3%) | 170 (35.1%) | 69 (29.7%) |
| ≥5 | 223 (21.0%) | 127 (26.2%) | 56 (24.1%) |
| ≥6 | 168 (15.8%) | 94 (19.4%) | 44 (19.0%) |
| ≥7 | 15 (1.4%) | 53 (11.0%) | 27 (11.6%) |
| ≥8 (all together, coherent) | 7 (0.7%) | 29 (6.0%) | 14 (6.0%) |

Per-indicator directional-flag counts, all eight:

| Indicator | 5-min | 10-min | 15-min |
|---|---|---|---|
| GLD | 368 | 175 | 72 |
| IAU | 389 | 172 | 73 |
| GLDM | 397 | 164 | 74 |
| GDX | 348 | 213 | 93 |
| GDXJ | 366 | 193 | 92 |
| RING | 338 | 185 | 88 |
| DXY | 34 | 111 | 62 |
| US10Y | 36 | 121 | 68 |

**Reading this table**: US10Y behaves almost identically to DXY here (36 vs. 34 flags at 5-min, 121 vs.
111 at 10-min) — both are noisy, weakly-coherent "moves opposite gold" signals at short windows, which
is part of why dropping US10Y from the Broker's rule cost little in practical consensus-reaching power
(the ETFs are doing nearly all the work in both tables — GLD alone flags 368 times at 5-min, more than
ten times DXY's 34). The direction filter matters far less for the six ETFs, which move with gold the
overwhelming majority of the time they cross their threshold at all.

## Alerting

Every one of these twenty-four threshold values is wired into `rules.check_intrahour_swing_alerts` and
alerts to Telegram — no window or indicator here is backtest-only. A poll can produce anywhere from
zero to twenty-four of these alerts (three windows x eight indicators) in a single cycle, each
rising-edge deduped per window so a sustained swing alerts once, not repeatedly for the rest of the
window. IAU, GLDM, GDX, GDXJ, and RING are alerted and re-tuned exactly like GLD, and are also
referenced by the Broker's paper-trading rules (`.claude/agents/broker.md`) — the `Consensus5of7-buy`/
`-sell` rules require at least 5 of seven of these indicators (GLD/IAU/GLDM/GDX/GDXJ/RING/DXY) to flag
together (in the correct direction) within a trailing 10-minute window, not just GLD/DXY. US10Y is
alerted/re-tuned the same as the rest but is deliberately excluded from the Broker's indicator set
(dropped when the rule changed from `Consensus6of8` to `Consensus5of7`).

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
with the sample size behind the new one), followed by **two** direction-coherent co-flagging
distributions for each of the three windows (the "Co-flagging" section above): the `Consensus5of7`
one, restricted to the seven indicators `broker.py`'s live rule actually trades on, and the general
eight-indicator research one — reporting only, neither feeds back into the twenty-four thresholds
themselves. Unlike the interactive workflow above, this path never asks
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
