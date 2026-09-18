# Imaginary gold trades

Paper-trading log for the **Broker** — every trade here is imaginary, no real money, no real broker,
no real order ever gets placed. Rules for entry/exit live in `.claude/agents/broker.md`'s own "Rules"
section, not here (this doc is the trade log only).

**This table is auto-generated — don't hand-edit it.** `broker.py`'s `check_broker_trades()` runs
every poll (`main.poll_once()`, both local and cloud), and is the actual implementation of the rules
in `.claude/agents/broker.md`: it watches the `alerts` table in Postgres (the same threshold alerts
`rules.py` generates and sends to Telegram) for a fresh entry signal, checks any open trade's
unrealized P/L against its exit target, and opens/closes imaginary buy/sell positions on gold spot
price (`gold`, Twelve Data `XAU/USD` — the same live price `data_fetcher.fetch_gold_spot_price()`
returns, not yfinance's `GC=F` futures). Every open/close also sends a Telegram message. The `trades`
table in Postgres is the source of truth; this table is wholesale-regenerated from it (not
row-patched) every time `check_broker_trades()` runs, so it always exactly reflects the DB. In the
cloud, `.github/workflows/poll.yml` commits this file (via a PR it squash-merges, same pattern as
`frequency_check.yml`) whenever a poll opens or closes a trade.

The last column, **Rule(s) applied**, names which rule(s) from `.claude/agents/broker.md`'s Rules
section drove each trade (by the rule's short name/id) — kept so trades can later be grouped by rule
and compared for which ones actually perform.

## Trades

| Open date | Open time (UTC) | Type | Entry price | Triggering alert | Close date | Close time (UTC) | Exit price | P/L | Status | Rule(s) applied |
|---|---|---|---|---|---|---|---|---|---|---|
