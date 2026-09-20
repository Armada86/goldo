# Technical analyst — SGOL description & usage

Description and operational-usage reference for SGOL (abrdn Physical Gold Shares ETF), a supporting doc
for the `technical-analyst` subagent. See also `docs/technical-analyst-gld-log.md`,
`docs/technical-analyst-iau-log.md`, `docs/technical-analyst-gldm-log.md`,
`docs/technical-analyst-dxy-log.md`, and `docs/technical-analyst-us10y-log.md` for the other tracked
indicators, and `docs/market.md` for the full indicator reference table.

## Description

SGOL (`sgol` in `config.INDICATORS`, ticker `SGOL` via yfinance) is the abrdn Physical Gold Shares ETF —
a physically backed gold ETF (roughly 1/100 oz per share) that vaults its bullion in Switzerland. Because
it's backed by physical gold, SGOL moves in the **same** direction as gold spot — it's tracked here as a
second liquid, exchange-traded proxy alongside GLD/IAU/GLDM. SGOL updates continuously during market
hours (see `docs/market.md`'s Trading times column — same NYSE Arca session as GLD/IAU/GLDM) and is
polled every `config.POLL_INTERVAL_MINUTES` like the other intraday indicators.

## How it's used in this project

SGOL alerts via `rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute high-low swing, each
window independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["sgol"]`). The live threshold values
themselves live in `intrahour_swing_thresholds.json`, not here or in `config.py`, specifically so
`frequency_check_job.py` can re-tune any off-target value automatically every weekday morning (see
`CLAUDE.md`'s Scheduling section and `docs/frequency-test-thresholds.md`) — treat any specific number
quoted elsewhere as a snapshot, not a fixed setting. Each Telegram alert states the window, direction,
swing size, threshold, and current price. Unlike GLD, SGOL is **not** wired into the Broker's automated
paper-trading rules (`.claude/agents/broker.md`) — it's alerted and frequency-tested, but never used as
a trade-entry signal.
