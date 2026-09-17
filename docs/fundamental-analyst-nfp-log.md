# Fundamental analyst — Non-Farm Payrolls findings log

A running record of Non-Farm Payrolls (NFP)-specific analyses done for this project. Kept narrow — the
question asked, the method, and the concrete numbers found — not full writeups (those stay in whatever
conversation produced them). Newest entries at the bottom. `nonfarm_payrolls` (FRED series `PAYEMS`) is
tracked in `config.FRED_SERIES` and alerts via `VALUE_CHANGE_ALERT_NAMES` (any change from the previous
poll) — see `docs/market.md` and `CLAUDE.md`'s architecture section. See also
`docs/technical-analyst-gld-log.md`/`-dxy-log.md`/`-us10y-log.md` for the equivalent technical-analysis
logs.

---

## 2026-09-17 — Last 12 NFP reports vs. gold spot's reaction

**Question**: requested directly — for each of the last 12 NFP reports, record the release date/time,
expected (consensus) number, actual number, previous number, and how gold spot reacted in the following
5 minutes, 30 minutes, 1 hour, and 2 hours.

**Method**:
- Release dates and expected/actual/previous figures: BLS Employment Situation releases (always 8:30am
  ET) and economic-calendar consensus data (investing.com for the 9 most recent, ordinary releases;
  BLS/news sourcing for the 3 releases disrupted by the Oct 1 – Nov 12, 2025 federal government
  shutdown, which investing.com's scrape didn't cover — see note below).
- Gold reaction: real XAU/USD 5-minute candles pulled from Twelve Data (`GOLD_SPOT_SYMBOL`, same source
  `data_fetcher.fetch_gold_spot_price()` uses live), `timezone=America/New_York`. Baseline = open of the
  08:30 candle (the release moment); each reaction column = open of the candle exactly N minutes later
  (08:35 / 09:00 / 09:30 / 10:30), so the columns line up on the same 5-minute grid the rest of this
  project uses (`POLL_INTERVAL_MINUTES`).

**Shutdown disruption (Oct 1 – Nov 12, 2025)**: the September 2025 report was delayed ~6 weeks (from
Oct 3 to Nov 20); the October 2025 report was **never separately published** — the establishment survey
folded October's payroll change into the November report, released together on Dec 16, 2025, as a
combined release. Both are marked below.

Each reaction column shows: price, then the change vs. the @release price as both a dollar amount and
a percentage.

| Release (8:30am ET) | Data month | Expected | Actual | Previous | Gold @release | +5min | +30min | +1h | +2h |
|---|---|---|---|---|---|---|---|---|---|
| 2026-09-04 | Aug 2026 | 55K | 162K | 21K | $4,470.95 | $4,398.92 (−$72.03, −1.61%) | $4,403.66 (−$67.29, −1.51%) | $4,406.73 (−$64.22, −1.44%) | $4,431.85 (−$39.10, −0.87%) |
| 2026-08-07 | Jul 2026 | 85K | −23K | 20K | $4,312.86 | $4,353.31 (+$40.45, +0.94%) | $4,357.77 (+$44.91, +1.04%) | $4,355.37 (+$42.51, +0.99%) | $4,351.02 (+$38.16, +0.88%) |
| 2026-07-02 | Jun 2026 | 114K | 57K | 129K | $4,068.63 | $4,113.60 (+$44.97, +1.11%) | $4,118.86 (+$50.23, +1.23%) | $4,112.38 (+$43.75, +1.08%) | $4,121.71 (+$53.07, +1.30%) |
| 2026-06-05 | May 2026 | 85K | 172K | 179K | $4,463.14 | $4,452.85 (−$10.28, −0.23%) | $4,411.89 (−$51.24, −1.15%) | $4,410.87 (−$52.27, −1.17%) | $4,358.31 (−$104.83, −2.35%) |
| 2026-05-08 | Apr 2026 | 65K | 115K | 185K | $4,709.60 | $4,717.18 (+$7.58, +0.16%) | $4,730.17 (+$20.56, +0.44%) | $4,726.51 (+$16.91, +0.36%) | $4,721.41 (+$11.80, +0.25%) |
| 2026-04-03 | Mar 2026 | 65K | 178K | −133K | $4,676.42 | $4,676.34 (−$0.08, −0.00%) | $4,676.53 (+$0.12, +0.00%) | $4,676.41 (−$0.00, −0.00%) | $4,676.39 (−$0.03, −0.00%) |
| 2026-03-06 | Feb 2026 | 58K | −92K | 126K | $5,078.43 | $5,121.41 (+$42.98, +0.85%) | $5,086.23 (+$7.80, +0.15%) | $5,103.70 (+$25.27, +0.50%) | $5,165.62 (+$87.19, +1.72%) |
| 2026-02-11 | Jan 2026 | 66K | 130K | 48K | $5,079.70 | $5,047.97 (−$31.73, −0.62%) | $5,069.14 (−$10.56, −0.21%) | $5,069.64 (−$10.06, −0.20%) | $5,049.43 (−$30.26, −0.60%) |
| 2026-01-09 | Dec 2025 | 66K | 50K | 56K | $4,471.70 | $4,484.86 (+$13.16, +0.29%) | $4,483.84 (+$12.14, +0.27%) | $4,494.62 (+$22.92, +0.51%) | $4,509.64 (+$37.94, +0.85%) |
| 2025-12-16 † | Nov 2025 | ~45K | 64K | −105K (Oct, first published same day) | $4,300.78 | $4,307.78 (+$6.99, +0.16%) | $4,311.06 (+$10.27, +0.24%) | $4,328.20 (+$27.42, +0.64%) | $4,319.13 (+$18.35, +0.43%) |
| 2025-11-20 † | Sep 2025 | 50K | 119K | −4K | $4,088.47 | $4,083.64 (−$4.83, −0.12%) | $4,074.21 (−$14.26, −0.35%) | $4,084.80 (−$3.67, −0.09%) | $4,097.14 (+$8.67, +0.21%) |
| 2025-09-05 | Aug 2025 | 75K | 22K | 79K | $3,559.60 | $3,577.49 (+$17.89, +0.50%) | $3,581.94 (+$22.34, +0.63%) | $3,579.21 (+$19.61, +0.55%) | $3,579.54 (+$19.94, +0.56%) |

† Shutdown-disrupted release — see note above. The Dec 16, 2025 release's "previous" figure is October
2025, published for the first time that same day (no separate October report ever came out), not a
revision of a previously-known number the way every other row's "previous" is.

**Notable findings**:
- No consistent directional relationship between the beat/miss size and gold's move: e.g. 2026-09-04
  was a huge beat (162K vs. 55K expected) and gold **dropped** ~1.6% in the first 5 minutes and stayed
  down through +2h — the opposite of the "strong jobs = risk-on = gold down... or up on inflation fears"
  intuition either direction would predict cleanly. 2026-08-07 (a miss, −23K vs. 85K expected) also saw
  gold **rise**. Whatever else is driving gold day-to-day (rate-cut expectations, dollar moves, etc.)
  appears to swamp the immediate NFP surprise more often than not in this sample.
- 2026-04-03 is an outlier worth flagging rather than smoothing over: despite a large beat (178K vs. 65K
  expected), gold's price is essentially flat (within $0.20) across all four windows — a far smaller
  reaction than every other release in the sample, beat or miss. Possibly a thin-liquidity/data-feed
  quirk for that specific session rather than a real "no reaction," but reported as-is rather than
  discarded.
- The two shutdown-affected releases (delayed Sep 2025 report, combined Oct/Nov 2025 report) both show
  *smaller* gold reactions (all four windows under ±0.65%) than most ordinary releases — consistent with
  those numbers being partially anticipated/stale by the time they finally printed, though the sample
  here is too small (2 of 12) to call it a pattern.
- Consensus/"expected" figures for Feb 11, 2026 (Jan 2026 data) vary a few thousand jobs by data
  provider (investing.com: 66K; a second source checked: 40K) — normal, since different providers poll
  forecasters at different times before the release. investing.com's figure is used above for
  consistency across the table; flagging so the number isn't mistaken for a typo against other sources.

**Outcome**: research/reference only — no code or config changed. This is a fundamental-analysis
reference table, not an alert-threshold tuning exercise like the technical-analyst logs; nothing here
feeds `INTRAHOUR_SWING_ALERT_THRESHOLD` or any other `config.py` value.
