# Technical analyst — GLD description & analysis

Description and running analysis log for GLD (SPDR Gold Shares ETF), a supporting doc for the
`technical-analyst` subagent. Kept narrow — the question asked, the method, and the findings that
matter — not full writeups (those stay in whatever conversation produced them) and not raw data tables
(superseded by nightly auto-tuning, see below). Newest entries at the bottom. See also
`docs/technical-analyst-dxy-log.md` and `docs/technical-analyst-us10y-log.md` for DXY/US10Y-specific
analyses, and `docs/market.md` for the full indicator reference table.

## Description

GLD (`gld` in `config.INDICATORS`, ticker `GLD` via yfinance) is the SPDR Gold Shares ETF, which holds
physical gold bullion (roughly 1/10 oz per share) and tracks spot gold closely, minus a small
expense-ratio drag over time. Because it's backed by physical gold, GLD moves in the **same** direction
as gold spot — it's used here as a liquid, exchange-traded proxy that's easy to pull historical intraday
OHLC bars for. GLD updates continuously during market hours and is polled every 5 minutes like the other
intraday indicators. It alerts via `rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute
high-low swing, each window independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]`, stored
in `intrahour_swing_thresholds.json`, re-tuned automatically every night by `frequency_check_job.py` —
see `CLAUDE.md`'s Scheduling section for exactly how).

---

## 2026-09-15 — GLD intrahour/multi-hour swing frequency

**Question**: How often does GLD (SPDR Gold Shares ETF) move by more than a given dollar amount within
a given time window?

**Method**: yfinance hourly OHLC bars (`GLD`, `interval="1h"`), rolling high-low range per window, 30-day
period.

**Results**: a single-hour-bar swing > $5 happened 3 times out of 210 bars; > $3 happened 21 times out
of 210 bars.

**Notable findings**:
- Two of the three >$5 hourly events happened on the same day (2026-08-28, at 09:30 and 11:30 ET) — an
  unusually volatile session.
- 17 of the 21 >$3 hourly events land at 09:30 or 10:30 ET, i.e. right at/near market open — the first
  hour of trading is where most of GLD's sharp hourly moves happen.

**Cross-check against gold spot (XAU/USD)**: for the three >$5 hourly GLD events, gold spot's swing
over the exact same 60-minute window (Twelve Data 5-min bars, aggregated) was consistent with GLD's
known ~1/11.1 ratio to spot at current prices — e.g. the largest event (2026-08-28 09:30, GLD swing
$9.55) corresponded to a $108.59 spot swing, and dividing each of the three spot swings by ~11.1 matched
GLD's actual swings closely ($9.78 / $9.55 / $4.78 predicted vs. $9.55 / $9.47 / $5.26 actual). Also
notable: net change was much smaller than swing in every case (e.g. spot swung $106 but only netted
−$87 in the Aug 28 11:30 hour) — a sharp move with a partial bounce mid-hour, not a clean
one-directional move.

**Outcome**: added `check_intrahour_swing_alerts` to `rules.py`, with `INTRAHOUR_SWING_ALERT_THRESHOLD
= {"gld": 3.0}` in `config.py`. The $3 threshold came directly from this analysis — frequent enough to
be meaningful (~21 times/30 days) without being noise-level, and clustered enough at market open to be
actionable. Uses a rising-edge check so a sustained swing alerts once, not every 5 minutes.

---

## 2026-09-16 — GLD retuned to hit ~30 rising-edge events/30 days (via frequency_test.py)

**Question**: requested directly — retune all three intrahour-swing thresholds (gld, dxy, us10y) so
each produces ~30 rising-edge alert events over the last 30 days, +/-2 tolerance.

**Method**: `frequency_test.py` (new script, see repo root — replays `check_intrahour_swing_alerts`'
exact rising-edge logic against live yfinance 5-min bars). Scanned a range of GLD thresholds; the
threshold-vs-event-count relationship is **not monotonic** — event count rises from a low threshold,
peaks (~$1.2, 67 events), then falls as threshold increases further (since very low thresholds mean
the swing rarely drops back below threshold to reset, collapsing what would be many events into one
long sustained one). Two threshold values can therefore produce the same event count, one on each side
of the peak; picked the **higher-threshold (post-peak) side** for all three indicators, consistent with
the existing "meaningful move, not noise" philosophy from prior threshold choices, rather than the much
smaller/noisier low-threshold alternative.

**Results**: scanning the post-peak side, $2.20 produced 35 events/30 days, $2.24 produced 31, $2.25
produced 30, $2.28 produced 27, and $2.30 produced 28.

**Outcome**: `INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]` changed from `3.0` to **`2.25`** — confirmed with
a fresh `frequency_test.py` run: 30 events/30 days, exact target.
