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
traded continuously alongside it (`gld`, `iau`, `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y`,
`inflation` breakevens, `interest_rate` — the derived technical indicators (RSI, EMA, SMA, MACD, ATR)
are also Technical-agent territory, in their own table below), `Fundamental` for the twelve scheduled
macro releases
(the BLS NFP report, the ADP National Employment Change report, the nine other FRED reports below, and
the FMP-sourced weekly API Crude Oil Stock Change report — see
`docs/fundamental-analyst-oil-weekly-log.md`, the first indicator in this project not sourced from
FRED/yfinance/Twelve Data) plus the financial stress index (also a FRED macro-risk series, not a
traded price), and `NA` for the one row neither subagent claims (`gold` itself) — it doesn't mean
unmonitored, just that no subagent currently specializes in it.

Note `iau`, `gldm`, `gdx`, `gdxj`, and `ring` are alerted and frequency-tested exactly like `gld` (same
`check_intrahour_swing_alerts` mechanism, same auto-tuning), and are also part of the Broker's automated
paper-trading rules: see `.claude/agents/broker.md`'s "Rules" section, whose `Consensus5of7-buy`/`-sell`
rules require at least 5 of seven intrahour-swing indicators
(`gld`/`iau`/`gldm`/`gdx`/`gdxj`/`ring`/`dxy`) to flag together, not just `gld`/`dxy`. `us10y` is
alerted/frequency-tested identically to the other seven but is deliberately **not** part of this
seven — it was dropped from the Broker's indicator set entirely (the rule was `Consensus6of8`,
including `us10y`, before this change).
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
| **GLD** (`gld`, SPDR Gold Shares ETF) | Price | Technical | yfinance (`GLD`) | Continuous (intraday, market hours) | Same direction — GLD holds physical gold (~1/10 oz/share) and tracks spot closely, minus a small expense-ratio drag over time | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]` = {15: $1.77, 10: $1.18, 5: $0.60} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **IAU** (`iau`, iShares Gold Trust) | Price | Technical | yfinance (`IAU`) | Continuous (intraday, market hours) | Same direction — IAU holds physical gold (~1/100 oz/share, a smaller/cheaper share size than GLD) and tracks spot closely, minus a small expense-ratio drag | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["iau"]` = {15: $0.37, 10: $0.24, 5: $0.12} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **GLDM** (`gldm`, SPDR Gold MiniShares Trust) | Price | Technical | yfinance (`GLDM`) | Continuous (intraday, market hours) | Same direction — GLDM holds physical gold (~1/100 oz/share, SPDR's lower-cost/lower-share-price sibling to GLD) and tracks spot closely, minus a small expense-ratio drag | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gldm"]` = {15: $0.39, 10: $0.25, 5: $0.13} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **GDX** (`gdx`, VanEck Gold Miners ETF) | Price | Technical | yfinance (`GDX`) | Continuous (intraday, market hours) | Same direction, but leveraged/noisier than the physical ETFs — GDX holds large/mid-cap gold **mining company** shares, not gold itself, so mining-margin leverage plus company/equity-market risk amplify the correlation to gold | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gdx"]` = {15: $0.83, 10: $0.56, 5: $0.28} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **GDXJ** (`gdxj`, VanEck Junior Gold Miners ETF) | Price | Technical | yfinance (`GDXJ`) | Continuous (intraday, market hours) | Same direction, but more leveraged/noisier than GDX — GDXJ holds smaller/earlier-stage ("junior") mining and exploration companies, whose economics are even more sensitive to the gold price than established producers' | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gdxj"]` = {15: $1.16, 10: $0.77, 5: $0.40} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **RING** (`ring`, iShares MSCI Global Gold Miners ETF) | Price | Technical | yfinance (`RING`) | Continuous (intraday, market hours) | Same direction, but leveraged/noisier than the physical ETFs — RING holds global gold **mining company** shares (same space as GDX, different index/methodology), so mining-margin leverage plus company/equity-market risk amplify the correlation to gold | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["ring"]` = {15: $0.69, 10: $0.46, 5: $0.22} | Mon–Fri 9:30 AM – 4:00 PM ET (regular NYSE Arca session; no overnight/pre-post-market data via this feed) |
| **DXY** (`dxy`, US Dollar Index) | Index | Technical | yfinance (`DX-Y.NYB`) | Continuous (intraday, market hours) | Opposite direction — gold is dollar-denominated, so a stronger dollar tends to push gold down and vice versa. Real-world correlation is directionally consistent but not clean-cut hour-by-hour (see `docs/technical-analyst-dxy-log.md`) | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"]` = {15: 0.0445, 10: 0.0223, 5: 0.0147} index points | Sun 8:00 PM – Fri 6:00 PM ET, with a brief daily pause (~6:00–8:00 PM ET) — standard ICE US Dollar Index futures session |
| **Financial stress index** (`financial_stress`, FRED `STLFSI4`) | Index | Fundamental | FRED (`STLFSI4`) | Weekly | Same direction, but noisier — rising stress (risk-off, flight to safety) usually supports gold, though acute stress can also spike dollar demand and cause gold to be sold for liquidity, muddying the relationship | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic index, not a traded instrument |
| **Industrial Production** (`industrial_production`, FRED `INDPRO`) | Index | Fundamental | FRED (`INDPRO`) | Monthly | Opposite direction — strong output signals economic strength, typically gold-negative | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **CPI** (`cpi`, FRED `CPIAUCSL`) | Index | Fundamental | FRED (`CPIAUCSL`) | Monthly | Mixed — higher realized inflation supports gold as an inflation hedge, but can also spark rate-hike fears that push yields/the dollar up and gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **PPI** (`ppi`, FRED `PPIFIS`) | Index | Fundamental | FRED (`PPIFIS`) | Monthly | Mixed — same reasoning as CPI; PPI is a leading indicator for consumer inflation | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Inflation expectations** (`inflation`, FRED `T10YIE`, 10Y breakeven) | Indicator (rate) | Technical | FRED (`T10YIE`) | Daily | Same direction — gold is a traditional inflation hedge, so rising breakeven inflation expectations tend to support gold prices | Yes — % move since the previous poll (`PCT_CHANGE_ALERT_THRESHOLD`) | No — uses `PCT_CHANGE_ALERT_THRESHOLD` (1.0%), not the intrahour-swing mechanism the frequency test covers | N/A — a once-daily FRED value derived from Treasury market pricing, not itself directly tradable via this feed |
| **Interest rate** (`interest_rate`, FRED `DFF`, Daily Federal Funds Rate) | Indicator (rate) | Technical | FRED (`DFF`) | Daily (but flat between FOMC decisions) | Opposite direction — a higher policy rate raises the opportunity cost of holding non-yielding gold and tends to pressure price down; rate cuts typically support gold | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — a policy rate, not a traded instrument; changes only around FOMC decisions |
| **NY Fed Empire State Manufacturing Survey** (`empire_state_manufacturing`, FRED `GACDISA066MSFRBNY`) | Indicator (survey index) | Fundamental | FRED (`GACDISA066MSFRBNY`) | Monthly | Mixed — a weak/negative reading signals manufacturing contraction, which can support gold via rate-cut/safe-haven demand, while a strong reading is risk-on and typically pressures gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published survey, not a traded instrument |
| **US10Y** (`us10y`, 10-Year Treasury yield, `^TNX`) | Indicator (yield) | Technical | yfinance (`^TNX`) | Continuous (intraday, market hours) | Opposite direction — gold pays no yield, so rising yields raise the opportunity cost of holding it, typically pressuring price down | No — trailing 15/10/5-min high/low swing alerts (`INTRAHOUR_SWING_ALERT_THRESHOLD`) are still computed and saved to the `alerts` table (the Broker reads them), but not sent to Telegram (`INTRAHOUR_SWING_SEND_TELEGRAM = False`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]` = {15: 0.0071, 10: 0.0035, 5: 0.0025} yield points | Mon–Fri 8:00 AM – 5:00 PM ET (SIFMA-recommended U.S. Treasury cash-market hours; `^TNX` is a yield quote, not itself a tradable security, so intraday updates are sparser outside this window) |
| **Retail Sales** (`retail_sales`, FRED `RSAFS`) | Level ($ millions) | Fundamental | FRED (`RSAFS`) | Monthly | Opposite direction — strong consumer spending signals a resilient economy, supporting yields/the dollar and pressuring gold down; weak sales are gold-supportive | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **ADP National Employment Change** (ADP NEC; `adp_employment`, FRED `ADPMNUSNERSA`) | Level (private payroll employment) | Fundamental | FRED (`ADPMNUSNERSA`) | Monthly | Opposite direction — strong job growth supports "higher for longer" rate expectations, typically pressuring gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`), plus a separate same-minute 🟣 alert from `release_watch_job.py` (FMP economic-calendar, burst-polled around the 8:15am ET scheduled release — see CLAUDE.md's "Same-minute release detection"), decoupled from FRED's own ingestion lag; release-by-release detail (previous/expected/actual, gold's reaction) is also recorded separately in the Neon Postgres `adp_reports` table — see CLAUDE.md's "ADP NEC fundamental-analysis data" | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Housing Starts** (`housing_starts`, FRED `HOUST`) | Level (thousands of units, annualized) | Fundamental | FRED (`HOUST`) | Monthly | Opposite direction — strong housing activity signals economic strength (though also rate-sensitive, so noisier than other opposite-direction indicators here) | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Non-Farm Payrolls** (BLS NFP; `nonfarm_payrolls`, FRED `PAYEMS`) | Level (total non-farm employment) | Fundamental | FRED (`PAYEMS`) | Monthly | Opposite direction — same reasoning as ADP NEC, but the headline market-moving jobs report | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`), plus a separate same-minute 🟣 alert from `release_watch_job.py` (FMP economic-calendar, burst-polled around the 8:30am ET scheduled release — see CLAUDE.md's "Same-minute release detection"), decoupled from FRED's own ingestion lag; release-by-release detail (previous/expected/actual, gold's reaction) is also recorded separately in the Neon Postgres `nfp_reports` table — see CLAUDE.md's "NFP fundamental-analysis data" | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Initial Jobless Claims (IJC)** (`initial_jobless_claims`, FRED `ICSA`) | Level (weekly claims) | Fundamental | FRED (`ICSA`) | Weekly | Same direction — rising claims signal labor-market weakness, same reasoning as unemployment rate | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Capacity Utilization** (`capacity_utilization`, FRED `TCU`) | Percent | Fundamental | FRED (`TCU`) | Monthly | Opposite direction — released alongside industrial production, same reasoning | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **Unemployment Rate** (`unemployment_rate`, FRED `UNRATE`) | Percent | Fundamental | FRED (`UNRATE`) | Monthly | Same direction — rising unemployment signals a weakening labor market, supporting gold via rate-cut expectations and safe-haven demand | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | N/A — published economic report, not a traded instrument |
| **API Weekly Crude Oil Stock** (API Crude Oil Stock Change; not in `config.FRED_SERIES`/`config.INDICATORS`) | Level (millions of barrels, change) | Fundamental | **FMP** (`/stable/economic-calendar`) — not FRED/yfinance/Twelve Data, see `docs/data-sources.md` | Weekly (Tuesday evenings, ~3pm-6pm ET — the exact minute varies, unlike the fixed-time ADP/NFP releases) | Mixed/indirect — a crude build (oversupply) pressures oil down, which ripples into risk/inflation sentiment that's only sometimes gold-relevant; see `docs/fundamental-analyst-oil-weekly-log.md` for the full reasoning | Yes — 🟣 same-minute-ish alert from `oil_weekly_job.py` the moment FMP's `actual` field populates (triggered repeatedly across the release window by `.github/workflows/oil_weekly_watch.yml`, not on the regular 5-min poll cycle); release-by-release detail is recorded in the Neon Postgres `oil_weekly_reports` table — see CLAUDE.md's "API Weekly Crude Oil Stock data" | No — not in `INTRAHOUR_SWING_ALERT_THRESHOLD`, and not polled by `main.poll_once()`/`data_fetcher.py` at all — entirely outside the regular poll loop | N/A — published economic report, not a traded instrument |

Every row above alerts on Telegram — see `notifier.send_telegram_message`, called from `main.poll_once`
for every alert string `rules.py` returns, regardless of which mechanism produced it.

Four data sources are in play, per `CLAUDE.md`: **yfinance** (`config.INDICATORS`, generic path in
`data_fetcher._fetch_yfinance_price`) for `gld`, `iau`, `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y`,
and (only for the SMA crossover's daily closes) `gold`; **Twelve Data** (`config.GOLD_SPOT_SYMBOL`) for
`gold`'s live spot price and every derived technical indicator below (RSI, EMA, MACD, ATR, and most of
SMA) built from `XAU/USD` candles at various intervals, since yfinance no longer serves a working
spot-gold quote; **FRED** (`config.FRED_SERIES`) for every daily/weekly/monthly macro series, including
all eleven scheduled reports in that dict; and **FMP** (`/stable/economic-calendar`), used only for the
API Weekly Crude Oil Stock row below and for `release_watch_job.py`'s ADP/NFP same-minute detection —
not part of `config.FRED_SERIES`/`config.INDICATORS`/the regular poll loop at all, see
`docs/data-sources.md`.

## Technical indicators

Derived indicators computed from gold candles, not tracked instruments in their own right — so they
don't get a row in the table above. All five are Technical-agent territory (`.claude/agents/
technical-analyst.md`).

| Indicator | Data source | How it works |
|---|---|---|
| **RSI(14)** (Relative Strength Index) | Twelve Data `XAU/USD` candles — 15-min for the alert (`rules.check_rsi_alerts`, via `data_fetcher.fetch_gold_candles()`), 1h/4h/daily for the forecast job (`ta_forecast_job.compute_snapshot()`) | One shared helper, `data_fetcher.compute_rsi()` — Wilder's smoothing (`ewm(alpha=1/14, min_periods=14, adjust=False)`), the standard formula most platforms show. `rules.py` fires a Telegram alert (🟠) the moment it *crosses* into overbought (`RSI_OVERBOUGHT_THRESHOLD`, 70) or oversold (`RSI_OVERSOLD_THRESHOLD`, 30) territory — not a repeat-every-poll check. `ta_forecast_job.py` instead uses 1h/4h/daily RSI(14) as inputs to the forecast's bias score and its "BIG PICTURE" (daily RSI vs 50) block; no alert of its own there. |
| **EMA** (Exponential Moving Average) | Twelve Data `XAU/USD` 1h candles (also 4h for EMA100) | `ta_forecast_job._ema()` — `series.ewm(span=N, adjust=False).mean()`. Computed as 1h EMA20/50/100/200 plus 4h EMA100, used only by `ta_forecast_job.py`: as resistance/support zone candidates in the price ladder, and as inputs to the bias score (price vs 1h EMA200, price vs 4h EMA100, 1h EMA20 vs EMA50). No Telegram alert of its own, and not used anywhere in `rules.py`. |
| **SMA** (Simple Moving Average) | Two separate computations/sources: yfinance `GC=F` daily closes for the 20/50-day crossover alert; Twelve Data `XAU/USD` daily and 4h candles for the forecast job | No shared helper — each is an inline `.rolling(N).mean()`. `rules.check_sma_crossover()` computes 20/50-day SMA on daily closes and fires a Telegram alert (🟡) on a bullish/bearish crossover. `ta_forecast_job.py` separately computes daily SMA20/50/100/200 and 4h SMA100 as zone candidates, bias-score inputs (price vs daily SMA50), and its "BIG PICTURE" block (price vs 200-day SMA) — these two SMA computations use different windows and different underlying data, and never share a value. |
| **MACD(12,26,9)** | Twelve Data `XAU/USD` 1h candles | Computed inline in `ta_forecast_job.compute_snapshot()` from the same `_ema()` helper as EMA above: `macd = ema(close,12) - ema(close,26)`, `signal = ema(macd,9)`, `histogram = macd - signal`. Only the 1h MACD histogram's sign feeds the bias score; the full line/signal/histogram values are shown in the forecast's rendered indicator snapshot. Not used in `rules.py` or any Telegram alert. |
| **ATR(14)** (Average True Range) | Twelve Data `XAU/USD` daily candles | `ta_forecast_job._atr()` — Wilder-smoothed true range (`max(high-low, \|high-prev_close\|, \|low-prev_close\|)`, then `ewm(alpha=1/14, adjust=False)`). Used only by `ta_forecast_job.py`, for two things: the stop buffer beyond entry zones (`max($5.00, 0.1 × ATR)`), and the minimum required gap between listed resistance/support zones (`0.15 × ATR`, `MIN_LEVEL_GAP_ATR`), so zones don't get listed closer together than the market's own recent daily range would justify. Not used in `rules.py` or any Telegram alert. |

Full methodology and reference-analysis notes for the technical forecast that consumes all five are in
`docs/technical-analyst-forecast-log.md`.

## Notes on frequency

- "Continuous" indicators are only as fresh as the poll loop (`config.POLL_INTERVAL_MINUTES = 5`) and
  only move during their underlying market's trading hours — they don't update overnight/weekends. See
  each row's **Trading times** column in the table above; the nine "Continuous" rows (`gold`, `gld`,
  `iau`, `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y`) don't share one schedule — each is a different
  instrument, on one of two different exchanges/markets.
- `inflation`, `financial_stress`, and `interest_rate` come from FRED and update on their own
  daily/weekly schedule regardless of how often this project polls; polling more frequently than the
  source updates doesn't add signal for those three.
- The eleven scheduled macro reports (`empire_state_manufacturing`, `retail_sales`,
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
  These alerts are saved to the `alerts` table (the Broker's entry rules read them there) but **not sent
  to Telegram** while `config.INTRAHOUR_SWING_SEND_TELEGRAM` is `False`.
  Each threshold is the **average companion swing** of that indicator, in that window, at every moment
  gold spot itself swung $5/$10/$15 (`GOLD_SWING_THRESHOLDS`) over the trailing 30 days
  (`FREQUENCY_TEST_LOOKBACK_DAYS`) — not a fixed value or a tuned-to-a-target-rate one. These twenty-four
  averages are recomputed fresh every weekday morning by `frequency_check_job.py` (see
  `docs/frequency-test-thresholds.md` and CLAUDE.md's "Scheduling"), so the exact numbers quoted in the
  table above reflect the last successful weekday run, not a value fixed at design time.
- `gold` — absolute $ move since the previous poll (`ABS_CHANGE_ALERT_THRESHOLD`)
- `inflation` — % move since the previous poll (`PCT_CHANGE_ALERT_THRESHOLD`)
- `financial_stress`, `interest_rate`, and the eleven scheduled macro reports above — any change at all
  since the previous poll (`VALUE_CHANGE_ALERT_NAMES`), since each one is flat between releases and any
  change means a new report just printed
- The eleven scheduled macro reports, plus `inflation`, `financial_stress`, and `interest_rate`, are
  polled, logged, and alerted like everything else, but excluded from the dashboard
  (`config.DASHBOARD_EXCLUDED_NAMES`/`config.MACRO_REPORT_NAMES`, both subtracted from
  `ALL_INDICATOR_NAMES` to get `config.DASHBOARD_INDICATOR_NAMES`) — the macro reports at the user's
  original request (fourteen more rows at wildly different scales/frequencies would clutter the one
  compact symbols table), and the three FRED rate/index series at the user's later request, leaving the
  dashboard showing only the nine continuously-traded market prices/yields (`gold`, `gld`, `iau`,
  `gldm`, `gdx`, `gdxj`, `ring`, `dxy`, `us10y`)
- `gold` also has a separate 20/50-day SMA crossover check on daily closes, independent of any threshold
- The API Weekly Crude Oil Stock report has its own dedicated mechanism entirely outside `rules.py`/
  `main.poll_once()`: `oil_weekly_job.py`, triggered repeatedly by cron-job.org across each Tuesday's
  multi-hour release window (not the regular 5-min poll), alerts and records the release directly the
  moment FMP's economic-calendar `actual` field appears — see `docs/fundamental-analyst-oil-weekly-log.md`.
- `gold`'s RSI(14) alerts once when it crosses into overbought/oversold territory — a crossing check
  like the SMA crossover, not a poll-to-poll comparison, so it doesn't repeat every 5 minutes while RSI
  stays past the threshold; see the "Technical indicators" table above for how it's computed
