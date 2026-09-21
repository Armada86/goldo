# Market indicators reference

What this project tracks, how often each one actually updates, and how it typically moves relative to
the gold price. See `config.py` for the exact tickers/series and `CLAUDE.md` for the full data-flow
description; see `docs/technical-analyst-*-log.md` for a description and operational-usage reference on
each of GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y.

Sorted by **Type** (second column), with `Price` kept on top (gold is the tracked price this whole project is
built around).

The **Agent** column (right after **Type**) says which subagent (`.claude/agents/technical-analyst.md`
or `.claude/agents/fundamental-analyst.md`) treats this row as its territory for
analysis/recommendations — `Technical` for gold's own price action and the market-based indicators
traded continuously alongside it (`gld`, `iau`, `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y`, gold's
RSI, `inflation` breakevens, `interest_rate`), `Fundamental` for the eleven scheduled macro releases
(the BLS NFP report, the ADP National Employment Change report, and the nine other FRED reports below)
plus the financial stress index (also a FRED macro-risk series, not a traded price), and `NA` for the
one row neither subagent claims (`gold` itself) — it doesn't mean unmonitored, just that no subagent
currently specializes in it.

Note `iau`, `gldm`, `gdx`, `gdxj`, and `ring` are alerted and frequency-tested exactly like `gld` (same
`check_intrahour_swing_alerts` mechanism, same auto-tuning), and — unlike before — are now also part of
the Broker's automated paper-trading rules: see `.claude/agents/broker.md`'s "Rules" section, whose
`Consensus6of8-buy`/`-sell` rules require at least 6 of all eight intrahour-swing indicators
(`gld`/`iau`/`gldm`/`gdx`/`gdxj`/`ring`/`dxy`/`us10y`) to flag together, not just `gld`/`dxy`/`us10y`.
Also note `iau`/`gldm` are physically-backed gold ETFs like `gld`
(same-direction, near-1:1 tracking), while `gdx`/`gdxj`/`ring` hold gold-**mining company** shares
instead — still same-direction with gold on average, but leveraged/noisier, since mining margins amplify
gold-price moves and add company-specific/equity-market risk on top (see each one's
`docs/technical-analyst-*-log.md` for the distinction).

Note the two separate employment reports below are easy to conflate: **BLS NFP** (`nonfarm_payrolls`)
is the official government Non-Farm Payrolls report from the Bureau of Labor Statistics — this is the
one whose release-by-release data (previous/expected/actual, gold's reaction) lives in the Neon
Postgres `nfp_reports` table and `docs/fundamental-analyst-nfp-log.md` (see CLAUDE.md's "NFP
fundamental-analysis data"). **ADP NEC** (`adp_employment`) is the separate, privately-compiled ADP
National Employment Change report, released a couple of days before BLS NFP each month at 8:15am ET
(15 minutes before BLS NFP's 8:30am ET) — it is tracked as a FRED indicator here and, like BLS NFP, now
also has its own Neon Postgres release-data table (`adp_reports`, same column shape as `nfp_reports`)
and `docs/fundamental-analyst-adp-log.md` log — see CLAUDE.md's "ADP NEC fundamental-analysis data".

**Not every row trades on the same schedule.** The nine continuously-traded price/index/yield rows each
track a *different* underlying market with its own hours — gold spot, GLD, IAU, GLDM, GDX, GDXJ, RING,
DXY, and US10Y are nine separate instruments, not nine views of one continuous tape (GLD/IAU/GLDM/
GDX/GDXJ/RING do share the same NYSE Arca session, since they're all US-listed gold-related equity
ETFs, but gold spot/DXY/US10Y each run on their own separate schedule) — so a swing measured at 2am ET
might be real price
action on one and simply stale/flat on another. See the **Trading times** column (all times ET, matching
`DISPLAY_TZ`) for each one's actual session; this project polls every `config.POLL_INTERVAL_MINUTES`
(5) regardless, so a poll outside an instrument's own trading hours just re-reads its last traded price
(or a `None`/failed fetch, depending on the source) rather than a fresh move. The eleven FRED scheduled
macro reports and rate/index series below aren't traded instruments at all — they're published economic
data, so "Trading times" doesn't apply to them the way it does to a market price; see their `Update
frequency` cell instead for how often each one actually changes.

| Indicator | Type | Agent | Data source | Update frequency | Relationship to gold price | Telegram alert? | In `frequency_test.py`? | Trading times |
|---|---|---|---|---|---|---|---|---|
| **Gold spot** (`gold`, Twelve Data `XAU/USD`) | Price | NA | Twelve Data (`XAU/USD`) — the separate SMA crossover check below instead uses yfinance `GC=F` daily closes | Continuous (intraday, polled every 5 min) | — (this *is* the tracked price) | Yes — absolute $ move since the previous poll (`ABS_CHANGE_ALERT_THRESHOLD`); also triggers the separate 20/50-day SMA crossover alert | No — uses `ABS_CHANGE_ALERT_THRESHOLD` ($5.00), not the intrahour-swing mechanism the frequency test covers | Sun 6:00 PM – Fri 5:00 PM ET, with a daily settlement break ~5:00–6:00 PM ET (standard OTC FX-style gold market hours — see `market_hours.py`) |
| **GLD** (`gld`, SPDR Gold Shares ETF) | Price | Technical | yfinance (`GLD`) | Continuous (intraday, market hours) | Same direction — GLD holds physical gold (~1/10 oz/share) and tracks spot closely, minus a small expense-ratio drag over time | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]` = {15: $1.77, 10: $1.18, 5: $0.60} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **IAU** (`iau`, iShares Gold Trust) | Price | Technical | yfinance (`IAU`) | Continuous (intraday, market hours) | Same direction — IAU holds physical gold (~1/100 oz/share, a smaller/cheaper share size than GLD) and tracks spot closely, minus a small expense-ratio drag | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["iau"]` = {15: $0.37, 10: $0.24, 5: $0.12} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **GLDM** (`gldm`, SPDR Gold MiniShares Trust) | Price | Technical | yfinance (`GLDM`) | Continuous (intraday, market hours) | Same direction — GLDM holds physical gold (~1/100 oz/share, SPDR's lower-cost/lower-share-price sibling to GLD) and tracks spot closely, minus a small expense-ratio drag | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gldm"]` = {15: $0.39, 10: $0.25, 5: $0.13} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **GDX** (`gdx`, VanEck Gold Miners ETF) | Price | Technical | yfinance (`GDX`) | Continuous (intraday, market hours) | Same direction, but leveraged/noisier than the physical ETFs — GDX holds large/mid-cap gold **mining company** shares, not gold itself, so mining-margin leverage plus company/equity-market risk amplify the correlation to gold | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gdx"]` = {15: $0.83, 10: $0.56, 5: $0.28} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **GDXJ** (`gdxj`, VanEck Junior Gold Miners ETF) | Price | Technical | yfinance (`GDXJ`) | Continuous (intraday, market hours) | Same direction, but more leveraged/noisier than GDX — GDXJ holds smaller/earlier-stage ("junior") mining and exploration companies, whose economics are even more sensitive to the gold price than established producers' | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gdxj"]` = {15: $1.16, 10: $0.77, 5: $0.40} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **RING** (`ring`, iShares MSCI Global Gold Miners ETF) | Price | Technical | yfinance (`RING`) | Continuous (intraday, market hours) | Same direction, but leveraged/noisier than the physical ETFs — RING holds global gold **mining company** shares (same space as GDX, different index/methodology), so mining-margin leverage plus company/equity-market risk amplify the correlation to gold | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["ring"]` = {15: $0.69, 10: $0.46, 5: $0.22} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **DXY** (`dxy`, US Dollar Index) | Index | Technical | yfinance (`DX-Y.NYB`) | Continuous (intraday, market hours) | Opposite direction — gold is dollar-denominated, so a stronger dollar tends to push gold down and vice versa. Real-world correlation is directionally consistent but not clean-cut hour-by-hour (see `docs/technical-analyst-dxy-log.md`) | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"]` = {15: 0.0445, 10: 0.0223, 5: 0.0147} index points | Sun 8:00 PM – Fri 6:00 PM ET, with a brief daily pause (~6:00–8:00 PM ET) — standard ICE US Dollar Index futures session |
| **Financial stress index** (`financial_stress`, FRED `STLFSI4`) | Index | Fundamental | FRED (`STLFSI4`) | Weekly | Same direction, but noisier — rising stress (risk-off, flight to safety) usually supports gold, though acute stress can also spike dollar demand and cause gold to be sold for liquidity, muddying the relationship | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic index, not a traded instrument |
| **Industrial Production** (`industrial_production`, FRED `INDPRO`) | Index | Fundamental | FRED (`INDPRO`) | Monthly | Opposite direction — strong output signals economic strength, typically gold-negative | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **CPI** (`cpi`, FRED `CPIAUCSL`) | Index | Fundamental | FRED (`CPIAUCSL`) | Monthly | Mixed — higher realized inflation supports gold as an inflation hedge, but can also spark rate-hike fears that push yields/the dollar up and gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **PPI** (`ppi`, FRED `PPIFIS`) | Index | Fundamental | FRED (`PPIFIS`) | Monthly | Mixed — same reasoning as CPI; PPI is a leading indicator for consumer inflation | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **RSI(14) on gold spot** (derived, Twelve Data 15-min candles) | Indicator (oscillator) | Technical | Twelve Data (derived from `XAU/USD` 15-min candles via `fetch_gold_candles`) | Continuous (recomputed every 5-min poll from a fresh candle fetch) | Not a price series — momentum on `gold` itself: high RSI (overbought) suggests gold is due to cool off, low RSI (oversold) suggests it's due to bounce | Yes — crossing into overbought/oversold territory (`RSI_OVERBOUGHT_THRESHOLD`/`RSI_OVERSOLD_THRESHOLD`) | No — a crossing check (`RSI_OVERBOUGHT_THRESHOLD`/`RSI_OVERSOLD_THRESHOLD`), not the intrahour-swing mechanism the frequency test covers | Same as gold spot above (derived from the same `XAU/USD` candles) |
| **Inflation expectations** (`inflation`, FRED `T10YIE`, 10Y breakeven) | Indicator (rate) | Technical | FRED (`T10YIE`) | Daily | Same direction — gold is a traditional inflation hedge, so rising breakeven inflation expectations tend to support gold prices | Yes — % move since the previous poll (`PCT_CHANGE_ALERT_THRESHOLD`) | No — uses `PCT_CHANGE_ALERT_THRESHOLD` (1.0%), not the intrahour-swing mechanism the frequency test covers | N/A — a once-daily FRED value derived from Treasury market pricing, not itself directly tradable via this feed |
| **Interest rate** (`interest_rate`, FRED `DFF`, Daily Federal Funds Rate) | Indicator (rate) | Technical | FRED (`DFF`) | Daily (but flat between FOMC decisions) | Opposite direction — a higher policy rate raises the opportunity cost of holding non-yielding gold and tends to pressure price down; rate cuts typically support gold | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — a policy rate, not a traded instrument; changes only around FOMC decisions |
| **NY Fed Empire State Manufacturing Survey** (`empire_state_manufacturing`, FRED `GACDISA066MSFRBNY`) | Indicator (survey index) | Fundamental | FRED (`GACDISA066MSFRBNY`) | Monthly | Mixed — a weak/negative reading signals manufacturing contraction, which can support gold via rate-cut/safe-haven demand, while a strong reading is risk-on and typically pressures gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published survey, not a traded instrument |
| **US10Y** (`us10y`, 10-Year Treasury yield, `^TNX`) | Indicator (yield) | Technical | yfinance (`^TNX`) | Continuous (intraday, market hours) | Opposite direction — gold pays no yield, so rising yields raise the opportunity cost of holding it, typically pressuring price down | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]` = {15: 0.0071, 10: 0.0035, 5: 0.0025} yield points | Mon–Fri 8:00 AM – 5:00 PM ET (SIFMA-recommended U.S. Treasury cash-market hours; `^TNX` is a yield quote, not itself a tradable security, so intraday updates are sparser outside this window) |
| **Retail Sales** (`retail_sales`, FRED `RSAFS`) | Level ($ millions) | Fundamental | FRED (`RSAFS`) | Monthly | Opposite direction — strong consumer spending signals a resilient economy, supporting yields/the dollar and pressuring gold down; weak sales are gold-supportive | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **ADP National Employment Change** (ADP NEC; `adp_employment`, FRED `ADPMNUSNERSA`) | Level (private payroll employment) | Fundamental | FRED (`ADPMNUSNERSA`) | Monthly | Opposite direction — strong job growth supports "higher for longer" rate expectations, typically pressuring gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`); release-by-release detail (previous/expected/actual, gold's reaction) is also recorded separately in the Neon Postgres `adp_reports` table — see CLAUDE.md's "ADP NEC fundamental-analysis data" | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Housing Starts** (`housing_starts`, FRED `HOUST`) | Level (thousands of units, annualized) | Fundamental | FRED (`HOUST`) | Monthly | Opposite direction — strong housing activity signals economic strength (though also rate-sensitive, so noisier than other opposite-direction indicators here) | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Non-Farm Payrolls** (BLS NFP; `nonfarm_payrolls`, FRED `PAYEMS`) | Level (total non-farm employment) | Fundamental | FRED (`PAYEMS`) | Monthly | Opposite direction — same reasoning as ADP NEC, but the headline market-moving jobs report | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`); release-by-release detail (previous/expected/actual, gold's reaction) is also recorded separately in the Neon Postgres `nfp_reports` table — see CLAUDE.md's "NFP fundamental-analysis data" | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Initial Jobless Claims (IJC)** (`initial_jobless_claims`, FRED `ICSA`) | Level (weekly claims) | Fundamental | FRED (`ICSA`) | Weekly | Same direction — rising claims signal labor-market weakness, same reasoning as unemployment rate | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Capacity Utilization** (`capacity_utilization`, FRED `TCU`) | Percent | Fundamental | FRED (`TCU`) | Monthly | Opposite direction — released alongside industrial production, same reasoning | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Unemployment Rate** (`unemployment_rate`, FRED `UNRATE`) | Percent | Fundamental | FRED (`UNRATE`) | Monthly | Same direction — rising unemployment signals a weakening labor market, supporting gold via rate-cut expectations and safe-haven demand | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |

Every row above alerts on Telegram — see `notifier.send_telegram_message`, called from `main.poll_once`
for every alert string `rules.py` returns, regardless of which mechanism produced it.

Three data sources are in play, per `CLAUDE.md`: **yfinance** (`config.INDICATORS`, generic path in
`data_fetcher._fetch_yfinance_price`) for `gld`, `iau`, `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y`,
and (only for the SMA crossover's daily closes) `gold`; **Twelve Data** (`config.GOLD_SPOT_SYMBOL`) for
`gold`'s live spot
price and the RSI(14) candles derived from it, since yfinance no longer serves a working spot-gold
quote; and **FRED** (`config.FRED_SERIES`) for every daily/weekly/monthly macro series, including all
eleven scheduled reports.

## Notes on frequency

- "Continuous" indicators are only as fresh as the poll loop (`config.POLL_INTERVAL_MINUTES = 5`) and
  only move during their underlying market's trading hours — they don't update overnight/weekends. See
  each row's **Trading times** column in the table above; the nine "Continuous" rows (`gold`, `gld`,
  `iau`, `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y`) don't share one schedule — each is a different
  instrument, on one of two different exchanges/markets.
- `inflation`, `financial_stress`, and `interest_rate` come from FRED and update on their own
  daily/weekly schedule regardless of how often this project polls; polling more frequently than the
  source updates doesn't add signal for those three.
- The ten scheduled macro reports (`empire_state_manufacturing`, `retail_sales`,
  `industrial_production`, `capacity_utilization`, `housing_starts`, `adp_employment`,
  `nonfarm_payrolls`, `unemployment_rate`, `initial_jobless_claims`, `cpi`, `ppi`) are the same story:
  each is flat on FRED between its own monthly/weekly release day, so polling every 5 minutes just
  means the alert fires within one poll cycle of the report actually printing, not that the number
  itself is any fresher.

## Alert mechanisms in play

Not every indicator uses the same alert logic — see `rules.py` / `CLAUDE.md` for details:

- `gld`, `iau`, `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y` — trailing high/low swing over three
  independent windows (15/10/5 min, `INTRAHOUR_SWING_WINDOWS_MINUTES`), each with its own threshold
  (`INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]`, stored in `intrahour_swing_thresholds.json`) — up to
  one alert per window per poll, naming the window, direction, swing size, threshold, and current price.
  Each threshold is the **average companion swing** of that indicator, in that window, at every moment
  gold spot itself swung $5/$10/$15 (`GOLD_SWING_THRESHOLDS`) over the trailing 30 days
  (`FREQUENCY_TEST_LOOKBACK_DAYS`) — not a fixed value or a tuned-to-a-target-rate one. These twenty-four
  averages are recomputed fresh every weekday morning by `frequency_check_job.py` (see
  `docs/frequency-test-thresholds.md` and CLAUDE.md's "Scheduling"), so the exact numbers quoted in the
  table above reflect the last successful weekday run, not a value fixed at design time.
- `gold` — absolute $ move since the previous poll (`ABS_CHANGE_ALERT_THRESHOLD`)
- `inflation` — % move since the previous poll (`PCT_CHANGE_ALERT_THRESHOLD`)
- `financial_stress`, `interest_rate`, and the ten scheduled macro reports above — any change at all
  since the previous poll (`VALUE_CHANGE_ALERT_NAMES`), since each one is flat between releases and any
  change means a new report just printed
- The ten scheduled macro reports are polled, logged, and alerted like everything else, but excluded
  from the dashboard for now (`config.DASHBOARD_INDICATOR_NAMES` vs. `ALL_INDICATOR_NAMES`) — eleven
  more rows at wildly different scales/frequencies would clutter the one compact symbols table
- `gold` also has a separate 20/50-day SMA crossover check on daily closes, independent of any threshold
- `gold`'s RSI(14) (15-min candles) alerts once when it crosses into overbought (`RSI_OVERBOUGHT_THRESHOLD`,
  70) or oversold (`RSI_OVERSOLD_THRESHOLD`, 30) territory — a crossing check like the SMA crossover, not a
  poll-to-poll comparison, so it doesn't repeat every 5 minutes while RSI stays past the threshold
