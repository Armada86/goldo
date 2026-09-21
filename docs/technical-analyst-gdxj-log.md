# Technical analyst — GDXJ description & usage

Description and operational-usage reference for GDXJ (VanEck Junior Gold Miners ETF), a supporting doc
for the `technical-analyst` subagent. See also `docs/technical-analyst-gld-log.md`,
`docs/technical-analyst-iau-log.md`, `docs/technical-analyst-gldm-log.md`,
`docs/technical-analyst-gdx-log.md`, `docs/technical-analyst-ring-log.md`,
`docs/technical-analyst-dxy-log.md`, and `docs/technical-analyst-us10y-log.md` for the other tracked
indicators, and `docs/market.md` for the full indicator reference table.

## Description

GDXJ (`gdxj` in `config.INDICATORS`, ticker `GDXJ` via yfinance) is the VanEck Junior Gold Miners ETF,
which tracks an index of smaller/earlier-stage ("junior") gold mining and exploration companies — the
riskier, more speculative end of the mining sector, as opposed to GDX's larger/more established miners.
Like GDX, GDXJ holds equity shares, not physical gold, so it typically moves in the **same** direction
as gold but leveraged and noisier than GLD/IAU/GLDM — and more leveraged/volatile than GDX itself, since
junior miners' economics (often pre-production or single-mine operations) are even more sensitive to the
gold price than established producers', on top of the same company-specific and equity-market noise GDX
has. GDXJ updates continuously during market hours (see `docs/market.md`'s Trading times column — same
NYSE Arca session as the other gold ETFs) and is polled every `config.POLL_INTERVAL_MINUTES` like the
other intraday indicators.

## How it's used in this project

GDXJ alerts via `rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute high-low swing, each
window independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["gdxj"]`). The live threshold values
themselves live in `intrahour_swing_thresholds.json`, not here or in `config.py`, specifically so
`frequency_check_job.py` can re-tune any off-target value automatically every weekday morning (see
`CLAUDE.md`'s Scheduling section and `docs/frequency-test-thresholds.md`) — treat any specific number
quoted elsewhere as a snapshot, not a fixed setting. Each Telegram alert states the window, direction,
swing size, threshold, and current price. Like the other gold ETFs beyond GLD, GDXJ is **not** wired
into the Broker's automated paper-trading rules (`.claude/agents/broker.md`) — it's alerted and
frequency-tested, but never used as a trade-entry signal.
