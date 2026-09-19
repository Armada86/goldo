# Fundamental analyst — ADP National Employment Change description & analysis

Description and running analysis log for the ADP National Employment Change report (ADP NEC), a
supporting doc for the `fundamental-analyst` subagent — the same role `docs/fundamental-analyst-nfp-log.md`
plays for BLS Non-Farm Payrolls. Kept narrow — the question asked, the method, and descriptive/
methodology notes — not full writeups (those stay in whatever conversation produced them) and not the
raw per-release data (see below — that lives in Postgres, not markdown tables). `adp_employment` (FRED
series `ADPMNUSNERSA`) is tracked in `config.FRED_SERIES` and alerts via `VALUE_CHANGE_ALERT_NAMES` (any
change from the previous poll) — see `docs/market.md` and `CLAUDE.md`'s architecture section.

**ADP NEC vs. BLS NFP**: these are two separate employment reports, easy to conflate (see
`docs/market.md`'s intro note). ADP NEC is privately compiled by ADP (a payroll-processing company,
using its own payroll data, not a government survey) and released a couple of days *before* BLS NFP each
month — typically the first Wednesday of the month, at **8:15am ET** (BLS NFP releases at 8:30am ET, on
the first Friday). Because ADP isn't a government agency, its release schedule is unaffected by federal
government shutdowns — see the Oct 2025 entry below, released on schedule during the Oct 1 – Nov 12,
2025 shutdown that delayed BLS NFP by ~6 weeks.

**Report-by-report data lives in Postgres, not in this file** — the `adp_reports` table (see
`storage.py`), same column shape as `nfp_reports`: `release_ts` (the exact 8:15am ET release moment),
`data_month`, `previous_value`/`expected_value`/`actual_value` (freeform text — ADP revises the prior
month's figure almost every release, so several rows' `previous_value` carries a provenance note rather
than a bare number, the same convention `nfp_reports` uses), `gold_at_release` and the gold spot price
`gold_5min`/`gold_10min`/`gold_30min`/`gold_1h`/`gold_2h` after it, and `notes` for anything
release-specific. Dollar/percentage deltas aren't stored — derive them from the raw prices when reading.
Query it read-only via `DATABASE_URL` (same connection `storage.get_connection()` uses);
`storage.insert_adp_report()`/`storage.update_adp_report_reaction()` are the write paths. The 12 releases
below were loaded by `backfill_adp_reports.py` (a one-time script, safe to re-run — it skips if the table
already has rows), the same pattern `backfill_nfp_reports.py` used for `nfp_reports`.

**Live recommendation on a fresh release**: `.claude/agents/fundamental-analyst.md`'s "New-release
recommendation workflow" is currently written against `nfp_reports` by name but is explicitly generic
("or the equivalent table for a non-NFP release, if one exists") — it applies the same way to a new ADP
NEC print using `adp_reports`/`insert_adp_report()`/`update_adp_report_reaction()` in place of the NFP
equivalents. Everything below this point is retrospective research, not a live workflow.

---

## 2026-09-19 — Last 12 ADP NEC reports vs. gold spot's reaction

**Question**: requested directly — for each of the last 12 ADP NEC reports, record the release
date/time, previous number, expected (consensus) number, actual number, and how gold spot reacted in
the following 5 minutes, 10 minutes, 30 minutes, 1 hour, and 2 hours. (The resulting data lives in the
`adp_reports` table now — see above — not inline here.)

**Method** (mirrors `docs/fundamental-analyst-nfp-log.md`'s method exactly, substituting ADP's own
release time):
- Release dates/times and expected/actual/previous figures: ADP's own press releases
  (`mediacenter.adp.com`/`adp-ri-nrip-static.adp.com`), economic-calendar consensus data
  (investing.com and mql5.com), and contemporaneous news coverage (CNBC, NBC, Bloomberg) for the
  Dow-Jones-survey consensus figure and each release's stated revision to the prior month.
- Gold reaction: real XAU/USD 5-minute candles pulled from Twelve Data (`GOLD_SPOT_SYMBOL`, same
  source `data_fetcher.fetch_gold_spot_price()` uses live), `timezone=America/New_York`. Baseline =
  open of the 08:15 candle (the release moment, 15 minutes earlier than BLS NFP's 08:30); each reaction
  column = open of the candle exactly N minutes later (08:20 / 08:25 / 08:45 / 09:15 / 10:15).

**Revision churn**: ADP revises the prior month's headline figure almost every release, sometimes by a
large margin — e.g. September 2025's -32K was revised to -29K by the November print, and October 2025's
+42K was revised up to +47K by the December print. Per the existing NFP-log convention, `previous_value`
records whatever figure was being cited as "previous" as of that release, with a note when it differs
from what was originally reported the month before — it is not a claim that the number is final. Two
releases also had a cross-provider forecast/consensus discrepancy of a few thousand jobs (Oct 2025:
Dow Jones 22K vs. a second source's ~28K; Dec 2025: Dow Jones 48K vs. investing.com's 49K) — the same
kind of provider variance flagged in the NFP log's Feb 2026 entry; both sources are noted in that row's
`expected_value` rather than picking one as ground truth.

**Notable findings**:
- **Unlike BLS NFP, the immediate reaction has a strong, consistent directional relationship with the
  beat/miss.** Classifying each release as a "beat" (actual > expected, gold predicted to fall) or "miss"
  (actual < expected, gold predicted to rise) per `docs/market.md`'s opposite-direction relationship, and
  checking gold's actual direction at each window:

  | Window | Consistent with beat-down/miss-up | Hit rate |
  |---|---|---|
  | +5 min | 11 of 12 | 92% |
  | +10 min | 10 of 12 | 83% |
  | +30 min | 9 of 12 | 75% |
  | +1 hour | 7 of 12 | 58% |
  | +2 hours | 8 of 12 | 67% |

  The only outright reversal at +5min was the May 6, 2026 release (Apr 2026 data): a miss (109K vs.
  118K expected, which should push gold up) where gold instead fell -0.15% in the first five minutes.
  Every other release in the sample moved the "textbook" way in that first candle. This is a materially
  cleaner signal than BLS NFP showed in the equivalent analysis (`docs/fundamental-analyst-nfp-log.md`:
  "no consistent directional relationship between the beat/miss size and gold's move" even at the same
  short-horizon windows).
- **The edge decays fast and doesn't reliably survive to +1h.** The hit rate falls from 92% at +5min to
  a near-coin-flip 58% at +1h before partially recovering to 67% at +2h. Several releases show a clean
  short-term "textbook" move that fully reverses within the hour (e.g. Jan 2026: a big miss, 22K vs. 46K
  expected, sent gold up through +1h [+0.32%] before a sharp -0.80% reversal by +2h; Jun 2026: a miss
  reversed direction at +1h [-0.18%] before swinging to a large +1.86% by +2h). This is consistent with
  ADP being a genuine leading indicator for the *initial* algorithmic/headline reaction, with broader
  session flow (DXY, yields, unrelated news) reasserting itself within the hour — the same dynamic that
  swamps BLS NFP's reaction entirely, just delayed here by roughly 30-60 minutes instead of acting from
  minute one.
- **Magnitude of the surprise doesn't obviously scale the size of the reaction.** The two largest
  surprises in the sample — Nov 2025 (-32K vs. 40K expected, a 72K miss) and Feb 2026 (63K vs. 50K
  expected, a comparatively modest 13K beat) — produced similar-sized +5min moves (+0.13% and -0.08%
  respectively), while a small miss like Dec 2025 (41K vs. 48K, a 7K miss) barely moved gold at +5min
  (+0.03%) but built to a much larger +0.35% by +1h. Direction is the reliable signal here, not size.
- **The Oct 2025 release (during the federal shutdown) behaved like an ordinary release.** Gold's
  reaction (-0.14% at +5min on a beat, in the predicted direction) doesn't show the damping the two
  shutdown-affected BLS NFP releases showed in the NFP log — consistent with ADP's schedule and
  compilation being unaffected by the shutdown, unlike BLS's.

**Conclusion**: ADP NEC gives a genuinely tradeable short-horizon signal that BLS NFP does not, in this
sample — the first 5-10 minutes after release move opposite to the beat/miss direction (strong job
growth → gold down, weak/miss → gold up) 83-92% of the time, a far cleaner hit rate than NFP's roughly
coin-flip record over the same windows. But the signal has a short shelf life: by an hour after release
it degrades to little better than chance, so a reaction-based read is only useful as a same-window (sub-
15-minute) call, not a directional bias to hold into the next hour. Practically, this makes ADP NEC a
better candidate than NFP for a fast, short-window fundamental signal (e.g. feeding the New-release
recommendation workflow's 5/10/15-minute read) — but any recommendation should say plainly that the
edge is a short-horizon one and stop short of projecting it out to +1h/+2h, where this sample shows it's
no longer reliable.

**Outcome**: research/reference only — no code or config changed beyond adding `adp_reports` (a new
Postgres table plus its `storage.py` read/write functions and `backfill_adp_reports.py` seed script,
mirroring `nfp_reports`/`backfill_nfp_reports.py`) so this data has somewhere to live; this is a
fundamental-analysis reference dataset, not an alert-threshold tuning exercise, and nothing here feeds
`INTRAHOUR_SWING_ALERT_THRESHOLD` or any other `config.py` value.
