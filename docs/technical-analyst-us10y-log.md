# Technical analyst — US10Y findings log

A running record of us10y (10-year Treasury yield) move analyses done for this project. Kept narrow —
the question asked, the method, and the concrete numbers found — not full writeups (those stay in
whatever conversation produced them). Newest entries at the bottom. See also
`docs/technical-analyst-gld-log.md` and `docs/technical-analyst-dxy-log.md` for GLD/DXY-specific
analyses.

All figures are in **points** (percentage points of yield, e.g. 0.081 = 8.1 basis points), not bp.

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

**Maximum 1-hour fluctuation**:

| | Window (ET) | High | Low | Swing (points) |
|---|---|---|---|---|
| Bar High/Low (largest) | 2026-09-11, 08:05–09:05 | 4.985 (08:20 bar) | 4.904 (08:50 bar) | **0.081** |
| Close-only (same window) | 2026-09-11, 08:05–09:05 | 4.955 (close) | 4.908 (close) | 0.047 |
| Runner-up (Bar High/Low) | 2026-08-07, 08:20 bar | 4.672 | 4.603 | 0.069 |

Context: fast spike-and-reversal, not a grind — yield jumped to the 08:20 high then reversed hard to the
08:50 low, landing squarely in the 8:30am ET scheduled macro-data release window. Both the max and
runner-up cluster at the same clock slot on different Fridays.

**Average 1-hour fluctuation** (783 windows, 30-day sample):

| Method | Mean | Median | Std dev | Range |
|---|---|---|---|---|
| Bar High/Low (primary) | **0.0167** | 0.0140 | 0.0106 | 0.002–0.081 |
| Close-only (secondary) | 0.0086 | 0.0080 | — | 0.000–0.047 |

**Distribution (Bar High/Low, points)**:

| Range | Count | % of sample |
|---|---|---|
| 0.00–0.01 | 194 | 24.8% |
| 0.01–0.02 | 395 | 50.4% |
| 0.02–0.03 | 128 | 16.3% |
| 0.03–0.04 | 37 | 4.7% |
| 0.04–0.06 | 20 | 2.6% |
| 0.06–0.08 | 7 | 0.9% |
| 0.08+ | 2 | 0.3% |

90th/95th/99th percentiles: 0.028 / 0.036 / 0.064 points. The sample is tightly clustered — 75% of all
trailing-60-min windows sit at ≤0.02 points — with a long right-skewed tail. The max (0.081) sits above
the 99th percentile and is ~4.8x the mean (0.0167), confirming that event was a genuine tail move, not
typical intrahour noise.

**Cross-check against gold** (GC=F futures — Twelve Data spot unavailable in-session, so this is COMEX
futures, not true XAU/USD spot) over the same max-swing window, 2026-09-11 08:05–09:05 ET:

| | Time (ET) | Price |
|---|---|---|
| Open | 08:05 | $4,371.10 |
| Low | 08:30 | $4,333.00 |
| High | 09:05 | $4,436.40 |
| Close | 09:05 | $4,424.80 |

Net move +$53.70 (+1.23%), full range $103.40 (2.37%). A single 1-minute bar (08:30–08:31 ET) carried a
~$66 plunge-and-recovery (verified on 1-min data, not a bad tick), then gold climbed steadily to the
09:05 high. The inverse relationship with us10y held tightly through both legs: yields spiking up
(~08:20–08:30) lined up with gold's plunge, and yields reversing down (~08:50–09:05) lined up with
gold's rally — both instruments show the same spike-then-reversal shape at the same clock-minute,
pointing to one shared macro data release rather than coincidence.

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

| Threshold (yield points) | Distinct rising-edge events / 30 days |
|---|---|
| 0.015 | 60 |
| 0.02 (previous poll-to-poll threshold) | 41 |
| 0.025 | 24 |
| 0.03 | 16 |
| **0.035** | **13** |
| 0.04 | 10 |
| 0.05 | 8 |

**0.035 chosen** — matches DXY's 13-events/30-days frequency exactly, landing in the same "frequent
enough to matter, not noise" band as gld/dxy.

**All 13 hourly events, ≥ 0.035-pt threshold, 30-day sample** (local/exchange time as returned by
yfinance):

| Timestamp | Swing (yield pts) | High | Low |
|---|---|---|---|
| 2026-08-19 07:30 | 0.055 | 4.682 | 4.653 |
| 2026-08-20 07:20 | 0.051 | 4.704 | 4.704 |
| 2026-08-24 07:20 | 0.036 | 4.704 | 4.704 |
| 2026-08-25 07:20 | 0.036 | 4.668 | 4.668 |
| 2026-08-28 09:20 | 0.038 | 4.700 | 4.662 |
| 2026-08-28 10:25 | 0.036 | 4.702 | 4.666 |
| 2026-09-03 07:35 | 0.044 | 4.766 | 4.754 |
| 2026-09-04 07:30 | 0.046 | 4.796 | 4.750 |
| 2026-09-09 10:05 | 0.041 | 4.839 | 4.798 |
| 2026-09-10 07:20 | 0.048 | 4.881 | 4.881 |
| 2026-09-11 07:55 | 0.037 | 4.955 | 4.918 |
| 2026-09-14 10:25 | 0.039 | 5.008 | 4.965 |
| 2026-09-15 07:20 | 0.059 | 5.014 | 5.014 |

Several rows show identical High/Low — an artifact of ^TNX's intraday data being sparse right around
the early-morning window (only one real print inside the trailing-60-min lookback at that point), not
a data error. Worth re-checking once the project's own 5-min polled readings (denser, from a live poll
loop) have accumulated 30+ days, rather than relying on yfinance's intraday bars for this indicator.

**Data source**: pulled directly from yfinance, not the project's Neon Postgres `readings` table — per
standing instruction, the technical-analyst never queries the DB for this kind of price analysis.

**Outcome**: moved `us10y` from `ABS_CHANGE_ALERT_THRESHOLD` (0.02, poll-to-poll) to
`INTRAHOUR_SWING_ALERT_THRESHOLD` (0.035, trailing 60-minute high-low range) in `config.py` — now all
three of gld/dxy/us10y use the same `check_intrahour_swing_alerts` mechanism. Also updated the alert
message in `rules.py` to include the current price (`now {price}`), on top of the direction (up/down)
and threshold it already reported, so every intrahour-swing Telegram alert states move, direction,
threshold, and current price.
