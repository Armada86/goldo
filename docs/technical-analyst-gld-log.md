# Technical analyst — findings log

A running record of market-move analyses done for this project. Kept narrow — the question asked, the
method, and the concrete numbers found — not full writeups (those stay in whatever conversation
produced them). Newest entries at the bottom.

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

**Outcome**: moved `dxy` from `PCT_CHANGE_ALERT_THRESHOLD` (0.3%) to `ABS_CHANGE_ALERT_THRESHOLD` in
`config.py`, with a flat `0.2`-point threshold — same previous-poll comparison mechanism as gold/us10y
(not an hourly-window check), just a fixed-point threshold instead of the old percentage one. Pushed to
Telegram via the existing `check_abs_change_alerts` → `main.poll_once()` path (no new wiring needed).
