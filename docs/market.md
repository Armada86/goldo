# Market indicators reference

What this project tracks, how often each one actually updates, and how it typically moves relative to
the gold price. See `config.py` for the exact tickers/series and `CLAUDE.md` for the full data-flow
description; see `docs/technical-analyst-*-log.md` for the frequency-tuning analyses referenced below.

| Indicator | Type | Update frequency | Relationship to gold price | In `frequency_test.py`? |
|---|---|---|---|---|
| **Gold spot** (`gold`, Twelve Data `XAU/USD`) | Price | Continuous (intraday, polled every 5 min) | — (this *is* the tracked price) | No — uses `ABS_CHANGE_ALERT_THRESHOLD` ($10.00), not the intrahour-swing mechanism the frequency test covers |
| **GLD** (`gld`, SPDR Gold Shares ETF) | Price | Continuous (intraday, market hours) | Same direction — GLD holds physical gold (~1/10 oz/share) and tracks spot closely, minus a small expense-ratio drag over time | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]` = $2.25 |
| **DXY** (`dxy`, US Dollar Index) | Index | Continuous (intraday, market hours) | Opposite direction — gold is dollar-denominated, so a stronger dollar tends to push gold down and vice versa. Real-world correlation is directionally consistent but not clean-cut hour-by-hour (see `docs/technical-analyst-dxy-log.md`) | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"]` = 0.139 index points |
| **US10Y** (`us10y`, 10-Year Treasury yield, `^TNX`) | Indicator (yield) | Continuous (intraday, market hours) | Opposite direction — gold pays no yield, so rising yields raise the opportunity cost of holding it, typically pressuring price down | Yes — `INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]` = 0.021 yield points |
| **Inflation expectations** (`inflation`, FRED `T10YIE`, 10Y breakeven) | Indicator (rate) | Daily | Same direction — gold is a traditional inflation hedge, so rising breakeven inflation expectations tend to support gold prices | No — uses `PCT_CHANGE_ALERT_THRESHOLD` (1.0%), not the intrahour-swing mechanism the frequency test covers |
| **Financial stress index** (`financial_stress`, FRED `STLFSI4`) | Index | Weekly | Same direction, but noisier — rising stress (risk-off, flight to safety) usually supports gold, though acute stress can also spike dollar demand and cause gold to be sold for liquidity, muddying the relationship | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **Interest rate** (`interest_rate`, FRED `DFF`, Daily Federal Funds Rate) | Indicator (rate) | Daily (but flat between FOMC decisions) | Opposite direction — a higher policy rate raises the opportunity cost of holding non-yielding gold and tends to pressure price down; rate cuts typically support gold | No — uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change), not the intrahour-swing mechanism the frequency test covers |
| **RSI(14) on gold spot** (derived, Twelve Data 15-min candles) | Indicator (oscillator) | Continuous (recomputed every 5-min poll from a fresh candle fetch) | Not a price series — momentum on `gold` itself: high RSI (overbought) suggests gold is due to cool off, low RSI (oversold) suggests it's due to bounce | No — a crossing check (`RSI_OVERBOUGHT_THRESHOLD`/`RSI_OVERSOLD_THRESHOLD`), not the intrahour-swing mechanism the frequency test covers |

## Notes on frequency

- "Continuous" indicators are only as fresh as the poll loop (`config.POLL_INTERVAL_MINUTES = 5`) and
  only move during their underlying market's trading hours — they don't update overnight/weekends.
- `inflation`, `financial_stress`, and `interest_rate` come from FRED and update on their own
  daily/weekly schedule regardless of how often this project polls; polling more frequently than the
  source updates doesn't add signal for those three.

## Alert mechanisms in play

Not every indicator uses the same alert logic — see `rules.py` / `CLAUDE.md` for details:

- `gld`, `dxy`, `us10y` — trailing 60-minute high/low swing (`INTRAHOUR_SWING_ALERT_THRESHOLD`)
- `gold` — absolute $ move since the previous poll (`ABS_CHANGE_ALERT_THRESHOLD`)
- `inflation` — % move since the previous poll (`PCT_CHANGE_ALERT_THRESHOLD`)
- `financial_stress`, `interest_rate` — any change at all since the previous poll (`VALUE_CHANGE_ALERT_NAMES`)
- `gold` also has a separate 20/50-day SMA crossover check on daily closes, independent of any threshold
- `gold`'s RSI(14) (15-min candles) alerts once when it crosses into overbought (`RSI_OVERBOUGHT_THRESHOLD`,
  70) or oversold (`RSI_OVERSOLD_THRESHOLD`, 30) territory — a crossing check like the SMA crossover, not a
  poll-to-poll comparison, so it doesn't repeat every 5 minutes while RSI stays past the threshold
