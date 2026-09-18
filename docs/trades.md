# Imaginary gold trades

Paper-trading log for the **Broker** agent (`.claude/agents/broker.md`). Every trade here is
imaginary — no real money, no real broker, no real order ever gets placed. The agent watches the
`alerts` table in Postgres (the same threshold alerts `rules.py` generates and `main.poll_once()`/
`poll_job.py` send to Telegram, saved via `storage.save_alert()`) and, according to the rules below,
opens and closes simulated buy/sell positions on gold spot price (`gold`, Twelve Data `XAU/USD` — the
same live price `data_fetcher.fetch_gold_spot_price()` returns, not yfinance's `GC=F` futures).

## Rules

*Pending.* No entry/exit/position-sizing/hold-time rules have been defined yet. The Broker agent must
not open or close any imaginary trade until this section is filled in with, at minimum:

- Which alert(s) (from `rules.py` — pct/abs/value change, intrahour swing, SMA crossover, RSI) trigger
  a buy vs. a sell.
- How long a trade stays open before the agent closes it.
- Position size (or whether P/L is just tracked per $1 of gold move).
- Whether multiple trades can be open at once, or only one at a time.

## Trades

| Open date | Open time (UTC) | Type | Entry price | Triggering alert | Close date | Close time (UTC) | Exit price | P/L | Status |
|---|---|---|---|---|---|---|---|---|---|
