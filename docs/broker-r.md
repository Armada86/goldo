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

