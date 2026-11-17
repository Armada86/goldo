# Broker R (short-range fade paper broker, 5 Nov 2026)

Spec lives in `broker_r.py` / `ta_short.py` and the CLAUDE.md entry. Summary of the user's request: Broker B's behaviour with (a) its own
tighter technical analysis every 30 minutes (levels only $3-$10 from price), (b) fades only, (c) strict +$5 / -$5 exits, no trail and no
filters, (d) its own page with the chart on the Streamlit dashboard.

Everything is a first guess to be judged on the live paper record: the $3 minimum distance (a level closer than that is mostly
spread), the level weights, the 1.5-dollar cluster width, 2 trades per level, and the 40-minute forecast lifetime are not calibrated.

Open points: the entry is checked every 5-minute poll (1-minute bars), not tick by tick like Broker F; a "stop trading" command that
names brokers explicitly (e.g. "stop trading broker B") does not stop R, only a no-name command or a scheduled pause does.
Useful query: `SELECT rule_name, exit_reason, COUNT(*), SUM(pnl) FROM broker_r_trades WHERE status='Closed' GROUP BY 1,2`.

## TP/SL grid replay (6 Nov 2026)

`python broker_r_tp_sl_study.py` (read-only; needs `DATABASE_URL` and `TWELVE_DATA_API_KEY`, or `FOREX_*` with `--bidask`) replays every
closed Broker R trade's real entry against a grid of take-profit x stop-loss pairs (default $2-$10 each) and prints net P&L, expectancy per
trade, P&L per hour in position, trades taken, win rate against break-even win rate, t-stat, drawdown and positive days. Rerun it as
trades accumulate; below 100 trades every cell is a hint only. Entries are fixed (a tighter exit freeing the slot for an earlier
re-entry is not credited), the best cell is optimistic because the grid is searched on the same trades, and it never edits `config.py`.
First run (30 trades, 5-6 Nov, bid/ask bars): live 5/5 = +$30; 7/4 = +$56, 8/5 = +$52 -- t about 1.5-1.8, not significant; both days were wide-range.

## Shadow +$7/-$4 exit (6 Nov 2026, log only, study 41)
Every Broker R trade (open or closed) also gets a hypothetical exit at +$7 / -$4 (`config.R_SHADOW_TAKE_PROFIT` / `R_SHADOW_STOP_LOSS`), stored in `broker_r_trades.shadow_exit` by
`broker_r.resolve_shadow_exits()` on the same side-correct 1-minute bars with the same stop-wins-a-tie rule; a shadow still open at 5 PM ET closes at that bar's close (`EOD`, filled in at the next
poll). It never trades or messages and the live +$5 / -$5 exit ignores it. Compare with `python broker_r_tp_sl_study.py --shadow`; it is judged at the 28 Nov 2026 combined review (study 41).
Query: `SELECT id, pnl, shadow_exit->>'reason', (shadow_exit->>'pnl')::float FROM broker_r_trades WHERE shadow_exit IS NOT NULL`.


## Tick-level trading and the FOREX.com demo account (16 Nov 2026)

Broker R is judged on every live tick, inside `forex_watcher.py` (module `forex_r.py`), not in the 5-minute poll. It needs the watcher workflow
(`forex_watch.yml`, cron-job.org every 5 minutes) to be running, exactly like Broker F; the poll still writes the half-hourly short-range TA.

Set the GitHub repo variable `FOREX_R_MODE` to `live` (Settings > Secrets and variables > Actions > Variables) to make Broker R place real orders on the
FOREX.com **demo** account. Unset or `paper` = paper trades only (filled at the live tick). Rules: only while Broker F has no open trade (any mode) and the
account holds no other XAU/USD position; otherwise R opens a paper trade and Telegram says it is not on Forex and why. TP/SL (+$5/-$5 from the actual
fill) are platform orders. Broker F has priority: when an F entry is applicable, an open R trade (live or paper, in profit or loss) is closed on the spot first (`exit_reason` `Priority`), then F opens; if a live R close cannot be confirmed, F does not open. The live path has not been exercised against forex.com --
watch the first live trade. Query: `SELECT mode, COUNT(*), SUM(pnl) FROM broker_r_trades WHERE status='Closed' GROUP BY 1`.

## Stop rule (17 Nov 2026)

The fixed +$5 take-profit / -$5 stop-loss was replaced by a stop that secures profit, by the user's design: the stop starts at -$5; when the
best profit reaches +$5 the stop moves to +$5 (a trade that got there can no longer close below +$5); from +$10 of best profit upward the stop
trails $5 behind the best price (best $12.50 -> stop +$7.50, best $20 -> stop +$15). There is no take-profit, so winners can run. Constants:
`R_STOP_LOSS`, `R_LOCK_PROFIT`, `R_TRAIL_DISTANCE` in `config.py`; logic in `broker_r.stop_profit()` / `scan_trail()`. Exit reasons stored in
`broker_r_trades.exit_reason`: `SL` (the -$5 stop), `Trail` (the +$5-or-better stop), `Stop`, `Priority`; older rows have `TP`/`SL`.
Live: a stop-only order on the platform that follows the rule (a move needs `ForexClient.move_stop()`, which `forex_live_check.py` has to verify), plus
the watcher's own per-tick check as the backstop. The shadow +$7/-$4 exit is unchanged and still compares against the booked P&L.
