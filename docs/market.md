# Market indicators reference

What this project tracks, how often each one actually updates, and how it typically moves relative to
the gold price. See `config.py` for the exact tickers/series and `CLAUDE.md` for the full data-flow
description; see `docs/technical-analyst-*-log.md` for the frequency-tuning analyses referenced below.

Sorted by **Type** (second column), with `Price` kept on top (gold is the tracked price this whole project is
built around).

The **Agent** column says which subagent (`.claude/agents/technical-analyst.md` or
`.claude/agents/fundamental-analyst.md`) treats this row as its territory for analysis/recommendations —
`Technical` for gold's own price action and the market-based indicators traded continuously alongside it
(`gld`, `dxy`, `us10y`, gold's RSI, `inflation` breakevens, `interest_rate`), `Fundamental` for the
eleven scheduled macro releases (NFP and the ten other FRED reports below), and `NA` for rows neither
subagent claims (`gold` itself, `financial_stress`) — it doesn't mean unmonitored, just that no subagent
currently specializes in it.

| Indicator | Type | Data source | Update frequency | Relationship to gold price | Telegram alert? | In `frequency_test.py`? | Agent |
|---|---|---|---|---|---|---|---|
| **Gold spot** (`gold`, Twelve Data `XAU/USD`) | Price | Twelve Data (`XAU/USD`) — the separate SMA crossover check below instead uses yfinance `GC=F` daily closes | Continuous (intraday, polled every 5 min) | — (this *is* the tracked price) | Yes — absolute $ move since the previous poll (`ABS_CHANGE_ALERT_THRESHOLD`); also triggers the separate 20/50-day SMA crossover alert | No — uses `ABS_CHANGE_ALERT_THRESHOLD` ($10.00), not the intrahour-swing mechanism the frequency test covers | NA |
| **GLD** (`gld`, SPDR Gold Shares ETF) | Price | yfinance (`GLD`) | Continuous (intraday, market hours) | Same direction — GLD holds physical gold (~1/10 oz/share) and tracks spot closely, minus a small expense-ratio drag over time | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]` = {15: $1.65, 10: $1.42, 5: $1.08} | Technical |
| **DXY** (`dxy`, US Dollar Index) | Index | yfinance (`DX-Y.NYB`) | Continuous (intraday, market hours) | Opposite direction — gold is dollar-denominated, so a stronger dollar tends to push gold down and vice versa. Real-world correlation is directionally consistent but not clean-cut hour-by-hour (see `docs/technical-analyst-dxy-log.md`) | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"]` = {15: 0.102, 10: 0.084, 5: 0.064} index points | Technical |
| **Financial stress index** (`financial_stress`, FRED `STLFSI4`) | Index | FRED (`STLFSI4`) | Weekly | Same direction, but noisier — rising stress (risk-off, flight to safety) usually supports gold, though acute stress can also spike dollar demand and cause gold to be sold for liquidity, muddying the relationship | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | NA |
| **Industrial Production** (`industrial_production`, FRED `INDPRO`) | Index | FRED (`INDPRO`) | Monthly | Opposite direction — strong output signals economic strength, typically gold-negative | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **CPI** (`cpi`, FRED `CPIAUCSL`) | Index | FRED (`CPIAUCSL`) | Monthly | Mixed — higher realized inflation supports gold as an inflation hedge, but can also spark rate-hike fears that push yields/the dollar up and gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **PPI** (`ppi`, FRED `PPIFIS`) | Index | FRED (`PPIFIS`) | Monthly | Mixed — same reasoning as CPI; PPI is a leading indicator for consumer inflation | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **RSI(14) on gold spot** (derived, Twelve Data 15-min candles) | Indicator (oscillator) | Twelve Data (derived from `XAU/USD` 15-min candles via `fetch_gold_candles`) | Continuous (recomputed every 5-min poll from a fresh candle fetch) | Not a price series — momentum on `gold` itself: high RSI (overbought) suggests gold is due to cool off, low RSI (oversold) suggests it's due to bounce | Yes — crossing into overbought/oversold territory (`RSI_OVERBOUGHT_THRESHOLD`/`RSI_OVERSOLD_THRESHOLD`) | No — a crossing check (`RSI_OVERBOUGHT_THRESHOLD`/`RSI_OVERSOLD_THRESHOLD`), not the intrahour-swing mechanism the frequency test covers | Technical |
| **Inflation expectations** (`inflation`, FRED `T10YIE`, 10Y breakeven) | Indicator (rate) | FRED (`T10YIE`) | Daily | Same direction — gold is a traditional inflation hedge, so rising breakeven inflation expectations tend to support gold prices | Yes — % move since the previous poll (`PCT_CHANGE_ALERT_THRESHOLD`) | No — uses `PCT_CHANGE_ALERT_THRESHOLD` (1.0%), not the intrahour-swing mechanism the frequency test covers | Technical |
| **Interest rate** (`interest_rate`, FRED `DFF`, Daily Federal Funds Rate) | Indicator (rate) | FRED (`DFF`) | Daily (but flat between FOMC decisions) | Opposite direction — a higher policy rate raises the opportunity cost of holding non-yielding gold and tends to pressure price down; rate cuts typically support gold | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Technical |
| **NY Fed Empire State Manufacturing Survey** (`empire_state_manufacturing`, FRED `GACDISA066MSFRBNY`) | Indicator (survey index) | FRED (`GACDISA066MSFRBNY`) | Monthly | Mixed — a weak/negative reading signals manufacturing contraction, which can support gold via rate-cut/safe-haven demand, while a strong reading is risk-on and typically pressures gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **US10Y** (`us10y`, 10-Year Treasury yield, `^TNX`) | Indicator (yield) | yfinance (`^TNX`) | Continuous (intraday, market hours) | Opposite direction — gold pays no yield, so rising yields raise the opportunity cost of holding it, typically pressuring price down | Yes — trailing 15/10/5-min high/low swing, independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]` = {15: 0.0140, 10: 0.0123, 5: 0.0100} yield points | Technical |
| **Retail Sales** (`retail_sales`, FRED `RSAFS`) | Level ($ millions) | FRED (`RSAFS`) | Monthly | Opposite direction — strong consumer spending signals a resilient economy, supporting yields/the dollar and pressuring gold down; weak sales are gold-supportive | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **ADP Employment** (`adp_employment`, FRED `ADPMNUSNERSA`) | Level (private payroll employment) | FRED (`ADPMNUSNERSA`) | Monthly | Opposite direction — strong job growth supports "higher for longer" rate expectations, typically pressuring gold down | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **Housing Starts** (`housing_starts`, FRED `HOUST`) | Level (thousands of units, annualized) | FRED (`HOUST`) | Monthly | Opposite direction — strong housing activity signals economic strength (though also rate-sensitive, so noisier than other opposite-direction indicators here) | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **Non-Farm Payrolls** (`nonfarm_payrolls`, FRED `PAYEMS`) | Level (total non-farm employment) | FRED (`PAYEMS`) | Monthly | Opposite direction — same reasoning as ADP, but the headline market-moving jobs report | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **Initial Jobless Claims (IJC)** (`initial_jobless_claims`, FRED `ICSA`) | Level (weekly claims) | FRED (`ICSA`) | Weekly | Same direction — rising claims signal labor-market weakness, same reasoning as unemployment rate | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **Capacity Utilization** (`capacity_utilization`, FRED `TCU`) | Percent | FRED (`TCU`) | Monthly | Opposite direction — released alongside industrial production, same reasoning | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |
| **Unemployment Rate** (`unemployment_rate`, FRED `UNRATE`) | Percent | FRED (`UNRATE`) | Monthly | Same direction — rising unemployment signals a weakening labor market, supporting gold via rate-cut expectations and safe-haven demand | Yes — any change since the previous poll (`VALUE_CHANGE_ALERT_NAMES`) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers | Fundamental |

Every row above alerts on Telegram — see `notifier.send_telegram_message`, called from `main.poll_once`
for every alert string `rules.py` returns, regardless of which mechanism produced it.

Three data sources are in play, per `CLAUDE.md`: **yfinance** (`config.INDICATORS`, generic path in
`data_fetcher._fetch_yfinance_price`) for `gld`, `dxy`, `us10y`, and (only for the SMA crossover's daily
closes) `gold`; **Twelve Data** (`config.GOLD_SPOT_SYMBOL`) for `gold`'s live spot price and the RSI(14)
candles derived from it, since yfinance no longer serves a working spot-gold quote; and **FRED**
(`config.FRED_SERIES`) for every daily/weekly/monthly macro series, including all eleven scheduled
reports.

## Notes on frequency

- "Continuous" indicators are only as fresh as the poll loop (`config.POLL_INTERVAL_MINUTES = 5`) and
  only move during their underlying market's trading hours — they don't update overnight/weekends.
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

- `gld`, `dxy`, `us10y` — trailing high/low swing over three independent windows (15/10/5 min,
  `INTRAHOUR_SWING_WINDOWS_MINUTES`), each with its own threshold
  (`INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]`, stored in `intrahour_swing_thresholds.json`) — up to
  one alert per window per poll, naming the window, direction, swing size, threshold, and current price.
  These nine thresholds are re-tuned automatically every night by `frequency_check_job.py` when one
  drifts off target (see `docs/frequency-test-thresholds.md` and CLAUDE.md's "Scheduling"), so the exact
  numbers quoted in the table above and in `docs/technical-analyst-*-log.md` reflect the last successful
  nightly run, not a value fixed at design time.
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
