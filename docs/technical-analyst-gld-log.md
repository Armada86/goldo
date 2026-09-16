# Technical analyst — GLD findings log

A running record of GLD-specific market-move analyses done for this project. Kept narrow — the
question asked, the method, and the concrete numbers found — not full writeups (those stay in whatever
conversation produced them). Newest entries at the bottom. See also `docs/technical-analyst-dxy-log.md`
for DXY-specific analyses.

---

## 2026-09-15 — GLD intrahour/multi-hour swing frequency

**Question**: How often does GLD (SPDR Gold Shares ETF) move by more than a given dollar amount within
a given time window?

**Method**: yfinance hourly OHLC bars (`GLD`, `interval="1h"`), rolling high-low range per window.

| Window | Threshold | Period | Result |
|---|---|---|---|
| 1 hour (single bar) | > $5 | 30 days | 3 times (out of 210 bars) |
| 1 hour (single bar) | > $3 | 30 days | 21 times (out of 210 bars) |

**Notable findings**:
- Two of the three >$5 hourly events happened on the same day (2026-08-28, at 09:30 and 11:30 ET) — an
  unusually volatile session.
- 17 of the 21 >$3 hourly events land at 09:30 or 10:30 ET, i.e. right at/near market open — the first
  hour of trading is where most of GLD's sharp hourly moves happen.

**All 21 hourly events, >$3 threshold, 30-day sample** (ET):

| Date/time | Swing | High | Low |
|---|---|---|---|
| 2026-08-28 09:30 | $9.55 | 424.79 | 415.24 |
| 2026-08-28 11:30 | $9.47 | 418.58 | 409.11 |
| 2026-09-09 10:30 | $5.26 | 406.44 | 401.18 |
| 2026-08-05 09:30 | $4.73 | 389.05 | 384.32 |
| 2026-08-20 10:30 | $4.62 | 416.45 | 411.83 |
| 2026-08-19 09:30 | $4.20 | 411.67 | 407.47 |
| 2026-08-18 10:30 | $4.17 | 403.24 | 399.07 |
| 2026-09-03 10:30 | $4.07 | 413.54 | 409.47 |
| 2026-08-17 09:30 | $3.98 | 406.16 | 402.18 |
| 2026-09-02 09:30 | $3.75 | 403.33 | 399.58 |
| 2026-08-07 09:30 | $3.71 | 400.66 | 396.95 |
| 2026-08-26 10:30 | $3.67 | 423.98 | 420.31 |
| 2026-08-24 11:30 | $3.50 | 429.04 | 425.54 |
| 2026-08-05 10:30 | $3.34 | 391.24 | 387.90 |
| 2026-08-19 14:30 | $3.31 | 413.47 | 410.16 |
| 2026-08-05 11:30 | $3.21 | 390.78 | 387.57 |
| 2026-09-01 09:30 | $3.17 | 401.25 | 398.08 |
| 2026-08-13 10:30 | $3.15 | 402.31 | 399.16 |
| 2026-08-25 10:30 | $3.03 | 426.03 | 423.00 |
| 2026-08-06 11:30 | $3.01 | 390.28 | 387.27 |
| 2026-08-31 09:30 | $3.01 | 407.91 | 404.90 |

**Cross-check against gold spot (XAU/USD)**: for the three >$5 hourly GLD events, gold spot's swing
over the exact same 60-minute window (Twelve Data 5-min bars, aggregated):

| GLD event hour | GLD swing | Gold spot swing | Gold spot net change |
|---|---|---|---|
| 2026-08-28 09:30 | $9.55 | $108.59 | −$35.27 |
| 2026-08-28 11:30 | $9.47 | $106.05 | −$86.95 |
| 2026-09-09 10:30 | $5.26 | $53.02 | −$23.56 |

Consistent with GLD's known ~1/11.1 ratio to spot at current prices: dividing each spot swing by ~11.1
gives $9.78 / $9.55 / $4.78 — matching GLD's actual swings closely. Also notable: net change is much
smaller than swing in every case (e.g. spot swung $106 but only netted −$87 in the Aug 28 11:30 hour) —
a sharp move with a partial bounce mid-hour, not a clean one-directional move.

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

| Threshold | Events/30 days |
|---|---|
| $2.20 | 35 |
| $2.24 | 31 |
| **$2.25** | **30** |
| $2.28 | 27 |
| $2.30 | 28 |

**Outcome**: `INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]` changed from `3.0` to **`2.25`** — confirmed with
a fresh `frequency_test.py` run: 30 events/30 days, exact target.
