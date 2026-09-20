# Technical analyst — DXY description & usage

Description and operational-usage reference for DXY (US Dollar Index), a supporting doc for the
`technical-analyst` subagent. See also `docs/technical-analyst-gld-log.md`,
`docs/technical-analyst-iau-log.md`, `docs/technical-analyst-gldm-log.md`,
`docs/technical-analyst-sgol-log.md`, and `docs/technical-analyst-us10y-log.md` for the other tracked
indicators, and `docs/market.md` for the full indicator reference table.

## Description

DXY (`dxy` in `config.INDICATORS`, ticker `DX-Y.NYB` via yfinance) tracks the US Dollar Index — the US
dollar's value against a basket of six major foreign currencies. Gold is dollar-denominated, so DXY
typically moves **opposite** to gold: a stronger dollar makes gold more expensive in other currencies
and tends to pressure the price down, and a weaker dollar tends to support it. Real-world correlation is
directionally consistent but not clean-cut hour-by-hour. DXY updates continuously during market hours
(see `docs/market.md`'s Trading times column) and is polled every `config.POLL_INTERVAL_MINUTES` like
the other intraday indicators.

## How it's used in this project

DXY alerts via `rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute high-low swing, each
window independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"]`). The live threshold values
themselves live in `intrahour_swing_thresholds.json`, not here or in `config.py`, specifically so
`frequency_check_job.py` can re-tune any off-target value automatically every weekday morning (see
`CLAUDE.md`'s Scheduling section and `docs/frequency-test-thresholds.md`) — treat any specific number
quoted elsewhere as a snapshot, not a fixed setting. Each Telegram alert states the window, direction,
swing size, threshold, and current price.
