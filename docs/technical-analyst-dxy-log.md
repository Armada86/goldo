# Technical analyst — DXY findings log

A running record of DXY-specific market-move analyses done for this project. Kept narrow — the
question asked, the method, and the concrete numbers found — not full writeups (those stay in whatever
conversation produced them). Newest entries at the bottom. See also `docs/technical-analyst-gld-log.md`
for GLD-specific analyses.

---

## 2026-09-16 — DXY point-change frequency, poll interval vs. hourly

**Question**: How often does DXY move by a given number of index points, at poll-to-poll (5-min) and
hourly granularity, and how does gold move in the same hours?

**Method**: yfinance OHLC bars (`DX-Y.NYB`, `interval="5m"` and `interval="1h"`), 30-day lookback
(2026-08-16 to 2026-09-16). Note: analysis was run against live yfinance data directly, not the
project's own Postgres readings — see "Data source" note below.

| Granularity | Threshold | Result |
|---|---|---|
| 5-min poll-to-poll (close-to-close) | ≥ 0.3 pts | 2 of 5,884 bars (1 real move, 1 weekend session gap) |
| 1-hour (high-low range) | ≥ 0.3 pts | 6 of 519 bars |
| 1-hour (high-low range) | ≥ 0.25 pts | 8 of 519 bars |
| 1-hour (high-low range) | ≥ 0.2 pts | 13 of 519 bars |
| 1-hour (close-to-close) | ≥ 0.3 pts | 2 of 518 transitions |
| Daily close-to-close | ≥ 0.3 pts | 6 of 21 trading days |

**Notable findings**:
- At 5-minute poll granularity, a 0.3-point DXY move between consecutive polls is rare (~once/30 days
  for a genuine intrabar move) — the old `PCT_CHANGE_ALERT_THRESHOLD["dxy"] = 0.3` (≈0.3 pts at ~99-100)
  was well-calibrated for that granularity, not noisy. At 0.2 pts, poll-to-poll hits are still only 2 of
  30 days (same two events — nothing landed between 0.2 and 0.3) — the 0.2-vs-0.3 gap (13 vs. 6) only
  shows up in the *hourly high-low-range* numbers above, a different, wider-window mechanism.
- At hourly granularity, several of the 13 events (0.2-pt threshold) cluster in the 08:00–10:00 ET
  window (Aug 19, Sep 4), consistent with US data-release/equity-open volatility.
- Gold (`GC=F` futures, used as a proxy — Twelve Data spot wasn't reachable in this session) swung
  meaningfully ($12–$108/hr) in nearly every one of the 13 flagged DXY hours, consistent with the usual
  inverse DXY/gold relationship, though hour-by-hour directional correlation wasn't clean-cut.

**All 13 hourly events, ≥ 0.2-pt threshold, 30-day sample** (ET), with same-hour gold range:

| Hour | DXY range (pts) | Gold range ($) |
|---|---|---|
| 2026-08-19 08:00 | 0.349 | 82.80 |
| 2026-08-19 09:00 | 0.219 | 43.20 |
| 2026-08-19 10:00 | 0.201 | 44.10 |
| 2026-08-28 10:00 | 0.495 | 107.80 |
| 2026-09-02 09:00 | 0.325 | 56.00 |
| 2026-09-03 08:00 | 0.202 | 58.50 |
| 2026-09-04 08:00 | 0.374 | 104.50 |
| 2026-09-04 09:00 | 0.240 | 69.80 |
| 2026-09-08 00:00 | 0.369 | 11.80 |
| 2026-09-09 11:00 | 0.273 | 57.70 |
| 2026-09-10 08:00 | 0.261 | 54.50 |
| 2026-09-11 08:00 | 0.326 | 99.60 |
| 2026-09-14 08:00 | 0.200 | 32.00 |

**Data source**: this analysis (and the resulting config change) was done by pulling directly from
yfinance/live APIs, not the project's Neon Postgres `readings` table — per explicit instruction, the
technical-analyst should never query the DB for this kind of price analysis (see
`.claude/agents/technical-analyst.md`, updated the same day).

**Outcome**: two iterations, same day.
1. First removed `dxy` from `PCT_CHANGE_ALERT_THRESHOLD` (0.3%) and added it to
   `ABS_CHANGE_ALERT_THRESHOLD` (flat 0.2 points) — but that mechanism (`check_abs_change_alerts`)
   only compares consecutive 5-min polls, where 0.2 vs. 0.3 barely differs (~2 hits/30 days either
   way — see table above), not the hourly behavior the request was actually about.
2. Corrected: removed `dxy` from `ABS_CHANGE_ALERT_THRESHOLD` entirely and added it to
   `INTRAHOUR_SWING_ALERT_THRESHOLD` (flat 0.2 points) instead — same trailing-60-minute high-low-range
   mechanism GLD already uses (`check_intrahour_swing_alerts`), which is what actually produces the
   ~13-events/30-days frequency at a 0.2 threshold. Also fixed `check_intrahour_swing_alerts`'s alert
   message, which hardcoded a `$` unit (fine for GLD, wrong for DXY's index points) — unit is now
   picked per-indicator name. Pushed to Telegram via the existing `check_intrahour_swing_alerts` →
   `main.poll_once()` path (no new wiring needed); alerts once per sustained swing via the existing
   rising-edge check, not every 5 minutes for the rest of the hour.

---

## 2026-09-16 — DXY retuned to hit ~30 rising-edge events/30 days (via frequency_test.py)

**Question**: requested directly — retune all three intrahour-swing thresholds (gld, dxy, us10y) so
each produces ~30 rising-edge alert events over the last 30 days, +/-2 tolerance.

**Method**: `frequency_test.py` (new script — replays `check_intrahour_swing_alerts`' exact rising-edge
logic against live yfinance 5-min bars, properly deduped unlike the raw-bar-count method used in the
0.2-point analysis above, which is why this run's counts read lower than that table for the same
thresholds). Threshold-vs-event-count is non-monotonic (rises to a peak around 0.05, then falls); used
the higher-threshold (post-peak) side, consistent with prior "meaningful move" threshold choices.

| Threshold | Events/30 days |
|---|---|
| 0.136 | 34 |
| 0.138 | 32 |
| **0.139** | **29** |
| 0.140 | 27 |

**Outcome**: `INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"]` changed from `0.2` to **`0.139`** — confirmed with
a fresh `frequency_test.py` run: 29 events/30 days, within the +/-2 target band.
