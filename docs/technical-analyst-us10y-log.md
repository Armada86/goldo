# Technical analyst — US10Y description & analysis

Description and running analysis log for us10y (10-year Treasury yield), a supporting doc for the
`technical-analyst` subagent. Kept narrow — the question asked, the method, and the findings that
matter — not full writeups (those stay in whatever conversation produced them) and not raw data tables
(superseded by weekday auto-tuning, see below). Newest entries at the bottom. See also
`docs/technical-analyst-gld-log.md` and `docs/technical-analyst-dxy-log.md` for GLD/DXY-specific
analyses, and `docs/market.md` for the full indicator reference table.

All figures are in **points** (percentage points of yield, e.g. 0.081 = 8.1 basis points), not bp.

## Description

US10Y (`us10y` in `config.INDICATORS`, ticker `^TNX` via yfinance) tracks the 10-year US Treasury note
yield. Gold pays no yield, so rising Treasury yields raise the opportunity cost of holding gold instead
of interest-bearing bonds — us10y typically moves **opposite** to gold, same reasoning as DXY but via
the rates channel rather than the currency channel. US10Y updates continuously during market hours and
is polled every 5 minutes like the other intraday indicators. It alerts via
`rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute high-low swing, each window
independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]`, stored in
`intrahour_swing_thresholds.json`, re-tuned automatically every weekday morning by `frequency_check_job.py` — see
`CLAUDE.md`'s Scheduling section for exactly how).

---

## 2026-09-16 — US10Y intrahour (1-hour) fluctuation: max and average

**Question**: Over the last 30 days, what's the largest 1-hour high-low fluctuation in us10y, and what's
the average 1-hour fluctuation, so both can be compared?

**Method**: `us10y` maps to yfinance ticker `^TNX` (see `config.py` `INDICATORS`). Neon Postgres and
Twelve Data credentials were unavailable in-session, so data was pulled live via
`yf.Ticker("^TNX").history(period="30d", interval="15m")` — 810 bars, 2026-08-04 through 2026-09-15,
regular US trading hours only. For every bar's timestamp, built the trailing 60-minute window (same
shape as `rules.check_intrahour_swing_alerts`'s windowing, minus the alert/threshold/rising-edge parts)
and took high−low across bars in that window — 783 overlapping windows total. Computed two ways:
**Bar High/Low** (true intrahour extremes, primary) and **Close-only** (single price per bar, comparable
to how this project's DB stores one reading per poll).

**Maximum 1-hour fluctuation**: the largest Bar High/Low swing was **0.081 points** on 2026-09-11,
08:05–09:05 ET (high 4.985 at the 08:20 bar, low 4.904 at the 08:50 bar); the same window measured
close-only was a smaller 0.047 points. The runner-up (Bar High/Low) was 0.069 points on 2026-08-07 at
the 08:20 bar. Context: a fast spike-and-reversal, not a grind — yield jumped to the 08:20 high then
reversed hard to the 08:50 low, landing squarely in the 8:30am ET scheduled macro-data release window.
Both the max and runner-up cluster at the same clock slot on different Fridays.

**Average 1-hour fluctuation** (783 windows, 30-day sample): Bar High/Low (primary) averaged **0.0167**
points (median 0.0140, std dev 0.0106, range 0.002–0.081); Close-only (secondary) averaged 0.0086
points (median 0.0080, range 0.000–0.047). The Bar High/Low distribution is tightly clustered — 75.2% of
all trailing-60-min windows sit at or under 0.02 points (24.8% under 0.01, 50.4% between 0.01–0.02),
with a long right-skewed tail: 16.3% land 0.02–0.03, 4.7% land 0.03–0.04, and only 0.9%/0.3% exceed
0.06/0.08 respectively. The 90th/95th/99th percentiles are 0.028/0.036/0.064 points — the max (0.081)
sits above the 99th percentile and is ~4.8x the mean (0.0167), confirming that event was a genuine tail
move, not typical intrahour noise.

**Cross-check against gold** (GC=F futures — Twelve Data spot unavailable in-session, so this is COMEX
futures, not true XAU/USD spot) over the same max-swing window, 2026-09-11 08:05–09:05 ET: gold opened
at $4,371.10, dropped to a low of $4,333.00 at 08:30, then rallied to a high of $4,436.40 by 09:05
(close $4,424.80) — a net move of +$53.70 (+1.23%) but a full range of $103.40 (2.37%). A single
1-minute bar (08:30–08:31 ET) carried a ~$66 plunge-and-recovery (verified on 1-min data, not a bad
tick), then gold climbed steadily to the 09:05 high. The inverse relationship with us10y held tightly
through both legs: yields spiking up (~08:20–08:30) lined up with gold's plunge, and yields reversing
down (~08:50–09:05) lined up with gold's rally — both instruments show the same spike-then-reversal
shape at the same clock-minute, pointing to one shared macro data release rather than coincidence.

**Outcome**: changed the us10y alert to a fixed amount threshold of **0.02 points**. `us10y` moved from
`PCT_CHANGE_ALERT_THRESHOLD` to `ABS_CHANGE_ALERT_THRESHOLD` in `config.py` (`check_abs_change_alerts`
in `rules.py`), so it now alerts on a flat 0.02-point move since the previous poll instead of a
percentage. (Separately lowered from an initial 0.05 points to 0.02 after checking how often each
threshold would actually fire over the 30-day sample above.)

---

## 2026-09-16 — US10Y hourly swing threshold, to match GLD/DXY's intrahour-swing mechanism

**Question**: US10Y was alerting on the 0.02-yield-point poll-to-poll (5-min) move from the previous
entry (`ABS_CHANGE_ALERT_THRESHOLD`), the only one of the three indicators (gld, dxy, us10y) not using
the trailing-60-minute `check_intrahour_swing_alerts` mechanism. What hourly-swing threshold would give
a comparable alert frequency to gld ($3, ~21 events/30 days) and dxy (0.2 pts, ~13 events/30 days)?

**Method**: yfinance 5-min bars (`^TNX`, `interval="5m"`), 30-day lookback (2026-08-17 to 2026-09-16,
1,735 bars). Rolling 60-minute high-low range (12 bars), rising-edge event count at various thresholds
— same method as the DXY analysis.

**Results**: scanning thresholds from 0.015 to 0.05 points, rising-edge event count fell from 60/30 days
at 0.015, to 41 at 0.02 (the previous poll-to-poll threshold), 24 at 0.025, 16 at 0.03, 13 at 0.035, 10
at 0.04, and 8 at 0.05.

**0.035 chosen** — matches DXY's 13-events/30-days frequency exactly, landing in the same "frequent
enough to matter, not noise" band as gld/dxy. Several of the 13 events at this threshold showed
identical High/Low readings within their hour — an artifact of `^TNX`'s intraday data being sparse
right around the early-morning window (only one real print inside the trailing-60-min lookback at that
point), not a data error; worth re-checking once the project's own 5-min polled readings (denser, from
a live poll loop) have accumulated 30+ days, rather than relying on yfinance's intraday bars for this
indicator.

**Data source**: pulled directly from yfinance, not the project's Neon Postgres `readings` table — per
standing instruction, the technical-analyst never queries the DB for this kind of price analysis.

**Outcome**: moved `us10y` from `ABS_CHANGE_ALERT_THRESHOLD` (0.02, poll-to-poll) to
`INTRAHOUR_SWING_ALERT_THRESHOLD` (0.035, trailing 60-minute high-low range) in `config.py` — now all
three of gld/dxy/us10y use the same `check_intrahour_swing_alerts` mechanism. Also updated the alert
message in `rules.py` to include the current price (`now {price}`), on top of the direction (up/down)
and threshold it already reported, so every intrahour-swing Telegram alert states move, direction,
threshold, and current price.

---

## 2026-09-16 — US10Y hourly threshold lowered from 0.035 to 0.025

**Question**: at discrete 1-hour bars (not the rolling-window method above), how many times did US10Y's
high-low range exceed a few candidate thresholds over the last 30 days?

**Method**: yfinance hourly bars (`^TNX`, `interval="1h"`), 153 bars, 2026-08-17 to 2026-09-16.

**Results**: a threshold of 0.02 points was exceeded in 57 of 153 hourly bars (~37%); 0.025 points was
exceeded in 33 bars (~22%) — a looser/more frequent threshold than the previous 0.035 value, which had
produced ~13 events/30 days under the rolling-window method above (not directly comparable, since it's
a different counting method).

**Outcome**: `us10y`'s `INTRAHOUR_SWING_ALERT_THRESHOLD` changed from `0.035` to **`0.025`**, requested
directly (not re-derived from a target event-rate the way 0.035 was) — roughly 33 events/30 days at
discrete hourly-bar granularity, a looser/more frequent threshold than the previous value.

---

## 2026-09-16 — US10Y retuned to hit ~30 rising-edge events/30 days (via frequency_test.py)

**Question**: requested directly — retune all three intrahour-swing thresholds (gld, dxy, us10y) so
each produces ~30 rising-edge alert events over the last 30 days, +/-2 tolerance.

**Method**: `frequency_test.py` (new script — replays `check_intrahour_swing_alerts`' exact rising-edge
logic against live yfinance 5-min bars, properly deduped unlike the raw-hourly-bar-count method used in
the 0.025 analysis above, which is why this run's counts read lower for the same thresholds — e.g. 0.025
gave 20 rising-edge events here vs. 33 raw hourly-bar exceedances above). Threshold-vs-event-count is
non-monotonic (rises to a peak around 0.012, then falls); used the higher-threshold (post-peak) side,
consistent with prior "meaningful move" threshold choices.

**Results**: scanning the post-peak side, 0.020 produced 32 events/30 days, 0.021 produced 31, 0.0215
produced 31, and 0.022 produced 28.

**Outcome**: `INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]` changed from `0.025` to **`0.021`** — confirmed
with a fresh `frequency_test.py` run: 31 events/30 days, within the +/-2 target band.
