# Technical analyst — GLD description & usage

Description and operational-usage reference for GLD (SPDR Gold Shares ETF), a supporting doc for the
`technical-analyst` subagent. See also `docs/technical-analyst-iau-log.md`,
`docs/technical-analyst-gldm-log.md`, `docs/technical-analyst-gdx-log.md`,
`docs/technical-analyst-gdxj-log.md`, and `docs/technical-analyst-ring-log.md` for the other tracked
gold ETFs, `docs/technical-analyst-dxy-log.md` and `docs/technical-analyst-us10y-log.md` for DXY/US10Y,
and `docs/market.md` for the full indicator reference table.

## Description

GLD (`gld` in `config.INDICATORS`, ticker `GLD` via yfinance) is the SPDR Gold Shares ETF, which holds
physical gold bullion (roughly 1/10 oz per share) and tracks spot gold closely, minus a small
expense-ratio drag over time. Because it's backed by physical gold, GLD moves in the **same** direction
as gold spot — it's used here as a liquid, exchange-traded proxy that's easy to pull historical intraday
OHLC bars for. GLD updates continuously during market hours (see `docs/market.md`'s Trading times
column) and is polled every `config.POLL_INTERVAL_MINUTES` like the other intraday indicators.

## How it's used in this project

GLD alerts via `rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute high-low swing, each
window independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["gld"]`). The live threshold values
themselves live in `intrahour_swing_thresholds.json`, not here or in `config.py`, specifically so
`frequency_check_job.py` can recompute it fresh every weekday morning -- it's the average companion swing of
this indicator's own price movement at every moment gold spot itself swings $5/$10/$15
(`config.GOLD_SWING_THRESHOLDS`) over the trailing 30 days, not a fixed or searched-for value (see
`CLAUDE.md`'s Scheduling section and `docs/frequency-test-thresholds.md`) -- treat any specific number
quoted elsewhere as a snapshot, not a fixed setting. Each Telegram alert states the window, direction,
swing size, threshold, and current price.
