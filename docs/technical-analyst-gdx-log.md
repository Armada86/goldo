# Technical analyst — GDX description & usage

Description and operational-usage reference for GDX (VanEck Gold Miners ETF), a supporting doc for the
`technical-analyst` subagent. See also `docs/technical-analyst-gld-log.md`,
`docs/technical-analyst-iau-log.md`, `docs/technical-analyst-gldm-log.md`,
`docs/technical-analyst-gdxj-log.md`, `docs/technical-analyst-ring-log.md`,
`docs/technical-analyst-dxy-log.md`, and `docs/technical-analyst-us10y-log.md` for the other tracked
indicators, and `docs/market.md` for the full indicator reference table.

## Description

GDX (`gdx` in `config.INDICATORS`, ticker `GDX` via yfinance) is the VanEck Gold Miners ETF, which
tracks an index of large/mid-cap **gold mining companies**, not physical gold bullion — unlike
GLD/IAU/GLDM, GDX holds equity shares, not gold itself. Because miners' profit margins are leveraged to
the gold price (their costs are mostly fixed, so a given % move in gold translates to a larger % move
in mining profitability), GDX typically moves in the **same** direction as gold but amplified, and adds
noise GLD/IAU/GLDM don't have: company-specific risk (management, production costs, individual mine
issues) and broader equity-market sentiment (GDX can sell off on a bad stock-market day even if gold
itself is flat). GDX updates continuously during market hours (see `docs/market.md`'s Trading times
column — same NYSE Arca session as GLD/IAU/GLDM) and is polled every `config.POLL_INTERVAL_MINUTES` like
the other intraday indicators.

## How it's used in this project

GDX alerts via `rules.check_intrahour_swing_alerts` — trailing 15/10/5-minute high-low swing, each
window independently thresholded (`INTRAHOUR_SWING_ALERT_THRESHOLD["gdx"]`). The live threshold values
themselves live in `intrahour_swing_thresholds.json`, not here or in `config.py`, specifically so
`frequency_check_job.py` can recompute it fresh every weekday morning -- it's the average companion swing of
this indicator's own price movement at every moment gold spot itself swings $5/$10/$15
(`config.GOLD_SWING_THRESHOLDS`) over the trailing 30 days, not a fixed or searched-for value (see
`CLAUDE.md`'s Scheduling section and `docs/frequency-test-thresholds.md`) -- treat any specific number
quoted elsewhere as a snapshot, not a fixed setting. Each Telegram alert states the window, direction,
swing size, threshold, and current price. Like the other gold ETFs beyond GLD, GDX is **not** wired into
the Broker's automated paper-trading rules (`.claude/agents/broker.md`) — it's alerted and
frequency-tested, but never used as a trade-entry signal.
