# Market indicators reference

What this project tracks, how often each one actually updates, and how it typically moves relative to
the gold price. See `config.py` for the exact tickers/series and `CLAUDE.md` for the full data-flow
description; see `docs/technical-analyst-*-log.md` for the frequency-tuning analyses referenced below.

Sorted by **Type** (second column), with `Price` kept on top (gold is the tracked price this whole project is
built around).

| Indicator | Type | Update frequency | Relationship to gold price | In `frequency_test.py`? |
|---|---|---|---|---|
| **Gold spot** (`gold`, Twelve Data `XAU/USD`) | Price | Continuous (intraday, polled every 5 min) | — (this *is* the tracked price) | No — uses `ABS_CHANGE_ALERT_THRESHOLD` ($10.00), not the intrahour-swing mechanism the frequency test covers |
| **GLD** (`gld`, SPDR Gold Shares ETF) | Price | Continuous (intraday, market hours) | Same direction — GLD holds physical gold (~1/10 oz/share) and tracks spot closely, minus a small expense-ratio drag over time | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]` = $2.25 |
| **DXY** (`dxy`, US Dollar Index) | Index | Continuous (intraday, market hours) | Opposite direction — gold is dollar-denominated, so a stronger dollar tends to push gold down and vice versa. Real-world correlation is directionally consistent but not clean-cut hour-by-hour (see `docs/technical-analyst-dxy-log.md`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"]` = 0.139 index points |
| **Financial stress index** (`financial_stress`, FRED `STLFSI4`) | Index | Weekly | Same direction, but noisier — rising stress (risk-off, flight to safety) usually supports gold, though acute stress can also spike dollar demand and cause gold to be sold for liquidity, muddying the relationship | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **Industrial Production** (`industrial_production`, FRED `INDPRO`) | Index | Monthly | Opposite direction — strong output signals economic strength, typically gold-negative | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **CPI** (`cpi`, FRED `CPIAUCSL`) | Index | Monthly | Mixed — higher realized inflation supports gold as an inflation hedge, but can also spark rate-hike fears that push yields/the dollar up and gold down | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **PPI** (`ppi`, FRED `PPIFIS`) | Index | Monthly | Mixed — same reasoning as CPI; PPI is a leading indicator for consumer inflation | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **RSI(14) on gold spot** (derived, Twelve Data 15-min candles) | Indicator (oscillator) | Continuous (recomputed every 5-min poll from a fresh candle fetch) | Not a price series — momentum on `gold` itself: high RSI (overbought) suggests gold is due to cool off, low RSI (oversold) suggests it's due to bounce | No — a crossing check (`RSI_OVERBOUGHT_THRESHOLD`/`RSI_OVERSOLD_THRESHOLD`), not the intrahour-swing mechanism the frequency test covers |
| **Inflation expectations** (`inflation`, FRED `T10YIE`, 10Y breakeven) | Indicator (rate) | Daily | Same direction — gold is a traditional inflation hedge, so rising breakeven inflation expectations tend to support gold prices | No — uses `PCT_CHANGE_ALERT_THRESHOLD` (1.0%), not the intrahour-swing mechanism the frequency test covers |
| **Interest rate** (`interest_rate`, FRED `DFF`, Daily Federal Funds Rate) | Indicator (rate) | Daily (but flat between FOMC decisions) | Opposite direction — a higher policy rate raises the opportunity cost of holding non-yielding gold and tends to pressure price down; rate cuts typically support gold | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **NY Fed Empire State Manufacturing Survey** (`empire_state_manufacturing`, FRED `GACDISA066MSFRBNY`) | Indicator (survey index) | Monthly | Mixed — a weak/negative reading signals manufacturing contraction, which can support gold via rate-cut/safe-haven demand, while a strong reading is risk-on and typically pressures gold down | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **US10Y** (`us10y`, 10-Year Treasury yield, `^TNX`) | Indicator (yield) | Continuous (intraday, market hours) | Opposite direction — gold pays no yield, so rising yields raise the opportunity cost of holding it, typically pressuring price down | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]` = 0.021 yield points |
| **Retail Sales** (`retail_sales`, FRED `RSAFS`) | Level ($ millions) | Monthly | Opposite direction — strong consumer spending signals a resilient economy, supporting yields/the dollar and pressuring gold down; weak sales are gold-supportive | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **ADP Employment** (`adp_employment`, FRED `ADPMNUSNERSA`) | Level (private payroll employment) | Monthly | Opposite direction — strong job growth supports "higher for longer" rate expectations, typically pressuring gold down | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **Housing Starts** (`housing_starts`, FRED `HOUST`) | Level (thousands of units, annualized) | Monthly | Opposite direction — strong housing activity signals economic strength (though also rate-sensitive, so noisier than other opposite-direction indicators here) | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **Non-Farm Payrolls** (`nonfarm_payrolls`, FRED `PAYEMS`) | Level (total non-farm employment) | Monthly | Opposite direction — same reasoning as ADP, but the headline market-moving jobs report | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **Initial Jobless Claims (IJC)** (`initial_jobless_claims`, FRED `ICSA`) | Level (weekly claims) | Weekly | Same direction — rising claims signal labor-market weakness, same reasoning as unemployment rate | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **Capacity Utilization** (`capacity_utilization`, FRED `TCU`) | Percent | Monthly | Opposite direction — released alongside industrial production, same reasoning | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **Unemployment Rate** (`unemployment_rate`, FRED `UNRATE`) | Percent | Monthly | Same direction — rising unemployment signals a weakening labor market, supporting gold via rate-cut expectations and safe-haven demand | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |

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

- `gld`, `dxy`, `us10y` — trailing 60-minute high/low swing (`INTRAHOUR_SWING_ALERT_THRESHOLD`)
- `gold` — absolute $ move since the previous poll (`ABS_CHANGE_ALERT_THRESHOLD`)
- `inflation` — % move since the previous poll (`PCT_CHANGE_ALERT_THRESHOLD`)
- `financial_stress`, `interest_rate`, and the ten scheduled macro reports above — any change at all
  since the previous poll (`VALUE_CHANGE_ALERT_NAMES`), since each one is flat between releases and any
  change means a new report just printed
- The ten scheduled macro reports are polled, logged, and alerted like everything else, but excluded
  from the dashboard for now (`config.DASHBOARD_INDICATOR_NAMES` vs. `ALL_INDICATOR_NAMES`) — eleven
  more series at wildly different scales/frequencies would clutter the one shared price chart
- `gold` also has a separate 20/50-day SMA crossover check on daily closes, independent of any threshold
- `gold`'s RSI(14) (15-min candles) alerts once when it crosses into overbought (`RSI_OVERBOUGHT_THRESHOLD`,
  70) or oversold (`RSI_OVERSOLD_THRESHOLD`, 30) territory — a crossing check like the SMA crossover, not a
  poll-to-poll comparison, so it doesn't repeat every 5 minutes while RSI stays past the threshold
