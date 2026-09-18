# Imaginary gold trades

Paper-trading log for the **Broker** agent (`.claude/agents/broker.md`). Every trade here is
imaginary — no real money, no real broker, no real order ever gets placed. The agent watches the
`alerts` table in Postgres (the same threshold alerts `rules.py` generates and `main.poll_once()`/
`poll_job.py` send to Telegram, saved via `storage.save_alert()`) and, according to the trading rules
defined in `.claude/agents/broker.md`'s own "Rules" section (not here — this doc is the trade log
only), opens and closes simulated buy/sell positions on gold spot price (`gold`, Twelve Data
`XAU/USD` — the same live price `data_fetcher.fetch_gold_spot_price()` returns, not yfinance's `GC=F`
futures).

The last column, **Rule(s) applied**, names which rule(s) from the agent's Rules section drove each
trade (by the rule's short name/id) — kept so trades can later be grouped by rule and compared for
which ones actually perform.

## Trades

| Open date | Open time (UTC) | Type | Entry price | Triggering alert | Close date | Close time (UTC) | Exit price | P/L | Status | Rule(s) applied |
|---|---|---|---|---|---|---|---|---|---|---|
