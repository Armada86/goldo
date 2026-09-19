# Fundamental analyst — Non-Farm Payrolls description & analysis

Description and running analysis log for Non-Farm Payrolls (NFP), a supporting doc for the
`fundamental-analyst` subagent. Kept narrow — the question asked, the method, and descriptive/
methodology notes — not full writeups (those stay in whatever conversation produced them) and not the
raw per-release data (see below — that lives in Postgres, not markdown tables). Files starting with
`fundamental-analyst-` (this one, and others to come for other scheduled releases) are what the
`fundamental-analyst` subagent reads for context; see also `docs/technical-analyst-gld-log.md`/
`-dxy-log.md`/`-us10y-log.md`, the equivalent supporting docs for the `technical-analyst` subagent.
`nonfarm_payrolls` (FRED series `PAYEMS`) is tracked in `config.FRED_SERIES` and alerts via
`VALUE_CHANGE_ALERT_NAMES` (any change from the previous poll) — see `docs/market.md` and `CLAUDE.md`'s
architecture section.

**Report-by-report data now lives in Postgres, not in this file.** The `nfp_reports` table (see
`storage.py`) holds one row per NFP release: `release_ts` (the exact 8:30am ET release moment),
`data_month`, `previous_value`/`expected_value`/`actual_value` (freeform text, since these sometimes
carry a provenance note rather than a bare number — see the Dec 2025 example below), `gold_at_release`
and the gold spot price `gold_5min`/`gold_10min`/`gold_30min`/`gold_1h`/`gold_2h` after it, and `notes`
for anything release-specific (a shutdown disruption, a data-feed quirk, etc.). Dollar/percentage deltas
aren't stored — derive them from the raw prices when reading. Query it read-only via `DATABASE_URL`
(same connection `storage.get_connection()` uses); `storage.insert_nfp_report()` is the write path, used
whenever a new release's figures are compiled. Recording a new release this way needs no code change and
no repo commit — that's deliberate, the same reasoning as the Broker's `trades` table (see
`.claude/agents/broker.md`/`CLAUDE.md`'s Broker entry): a routine data update shouldn't require touching
this repository at all. The 12 releases originally logged in this file's table were migrated into the
table by `backfill_nfp_reports.py` (a one-time script, safe to re-run — it skips if the table already
has rows).

**Live recommendation on a fresh release**: when the `fundamental-analyst` subagent is asked to react to
an NFP release right as it prints, its "New-release recommendation workflow" (see
`.claude/agents/fundamental-analyst.md`) is a pre-approved exception to its usual read-only/plan-then-ask
rule — it sends one Telegram message with a short 5/10/15-minute directional read and records the
release's figures in `nfp_reports` itself (`gold_at_release` only at first; the later
`gold_5min`/`gold_10min`/`gold_30min`/`gold_1h`/`gold_2h` columns get filled in afterward via
`storage.update_nfp_report_reaction()` once those windows have actually happened). Nothing else about
how releases get analyzed changes — everything below this point is retrospective research, not the live
workflow.

---

## 2026-09-17 — Last 12 NFP reports vs. gold spot's reaction

**Question**: requested directly — for each of the last 12 NFP reports, record the release date/time,
previous number, expected (consensus) number, actual number, and how gold spot reacted in the following
5 minutes, 10 minutes, 30 minutes, 1 hour, and 2 hours. (The resulting data lives in the `nfp_reports`
table now — see above — not inline here.)

**Method**:
- Release dates and expected/actual/previous figures: BLS Employment Situation releases (always 8:30am
  ET) and economic-calendar consensus data (investing.com for the 9 most recent, ordinary releases;
  BLS/news sourcing for the 3 releases disrupted by the Oct 1 – Nov 12, 2025 federal government
  shutdown, which investing.com's scrape didn't cover — see note below).
- Gold reaction: real XAU/USD 5-minute candles pulled from Twelve Data (`GOLD_SPOT_SYMBOL`, same source
  `data_fetcher.fetch_gold_spot_price()` uses live), `timezone=America/New_York`. Baseline = open of the
  08:30 candle (the release moment); each reaction column = open of the candle exactly N minutes later
  (08:35 / 08:40 / 09:00 / 09:30 / 10:30), so the columns line up on the same 5-minute grid the rest of
  this project uses (`POLL_INTERVAL_MINUTES`).

**Shutdown disruption (Oct 1 – Nov 12, 2025)**: the September 2025 report was delayed ~6 weeks (from
Oct 3 to Nov 20); the October 2025 report was **never separately published** — the establishment survey
folded October's payroll change into the November report, released together on Dec 16, 2025, as a
combined release. Both are flagged in the corresponding `nfp_reports` rows' `notes` column.

**Notable findings**:
- No consistent directional relationship between the beat/miss size and gold's move: e.g. the Sep 2026
  release (data month Aug 2026) was a huge beat (162K vs. 55K expected) and gold **dropped** ~1.6% in
  the first 5 minutes and stayed down through +2h — the opposite of the "strong jobs = risk-on = gold
  down... or up on inflation fears" intuition either direction would predict cleanly. The Aug 2026
  release (a miss, −23K vs. 85K expected) also saw gold **rise**. Whatever else is driving gold day-to-day
  (rate-cut expectations, dollar moves, etc.) appears to swamp the immediate NFP surprise more often
  than not in this sample.
- The Apr 2026 release is an outlier worth flagging rather than smoothing over: despite a large beat
  (178K vs. 65K expected), gold's price is essentially flat (within $0.20) across all five windows — a
  far smaller reaction than every other release in the sample, beat or miss. Possibly a thin-liquidity/
  data-feed quirk for that specific session rather than a real "no reaction," but reported as-is rather
  than discarded (see that row's `notes`).
- The two shutdown-affected releases (delayed Sep 2025 report, combined Oct/Nov 2025 report) both show
  *smaller* gold reactions (all four windows under ±0.65%) than most ordinary releases — consistent with
  those numbers being partially anticipated/stale by the time they finally printed, though the sample
  here is too small (2 of 12) to call it a pattern.
- Consensus/"expected" figures for the Feb 2026 release (Jan 2026 data) vary a few thousand jobs by data
  provider (investing.com: 66K; a second source checked: 40K) — normal, since different providers poll
  forecasters at different times before the release. investing.com's figure is used for consistency
  across the dataset; flagging so the number isn't mistaken for a typo against other sources (see that
  row's `notes`).

**Outcome**: research/reference only — no code or config changed. This is a fundamental-analysis
reference dataset, not an alert-threshold tuning exercise like the technical-analyst logs; nothing here
feeds `INTRAHOUR_SWING_ALERT_THRESHOLD` or any other `config.py` value.
