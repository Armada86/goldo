# Technical analyst — US10Y description & usage

Description and operational-usage reference for us10y (10-year Treasury yield), a supporting doc for
the `technical-analyst` subagent. See also `docs/technical-analyst-gld-log.md`,
`docs/technical-analyst-iau-log.md`, `docs/technical-analyst-gldm-log.md`,
`docs/technical-analyst-gdx-log.md`, `docs/technical-analyst-gdxj-log.md`,
`docs/technical-analyst-ring-log.md`, and `docs/technical-analyst-dxy-log.md` for the other tracked
indicators, and `docs/market.md` for the full indicator reference table.

All figures for this indicator are in **points** (percentage points of yield, e.g. 0.081 = 8.1 basis
points), not bp.

## Description

US10Y (`us10y` in `config.INDICATORS`, ticker `^TNX` via yfinance) tracks the 10-year US Treasury note
yield. Gold pays no yield, so rising Treasury yields raise the opportunity cost of holding gold instead
of interest-bearing bonds — us10y typically moves **opposite** to gold, same reasoning as DXY but via
the rates channel rather than the currency channel. US10Y updates continuously during market hours (see
`docs/market.md`'s Trading times column — `^TNX` is a yield quote, not itself a tradable security, so
it's sparser outside standard Treasury cash-market hours) and is polled every
`config.POLL_INTERVAL_MINUTES` like the other intraday indicators.

## How it's used in this project

US10Y alerts via `rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute high-low swing, each
window independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["us10y"]`). The live threshold
values themselves live in `intrahour_swing_thresholds.json`, not here or in `config.py`, specifically so
`frequency_check_job.py` can recompute it fresh every weekday morning -- it's the average companion swing of
this indicator's own price movement at every moment gold spot itself swings $5/$10/$15
(`config.GOLD_SWING_THRESHOLDS`) over the trailing 30 days, not a fixed or searched-for value (see
`CLAUDE.md`'s Scheduling section and `docs/frequency-test-thresholds.md`) -- treat any specific number
quoted elsewhere as a snapshot, not a fixed setting. Each Telegram alert states the window, direction,
swing size, threshold, and current price.
