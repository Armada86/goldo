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
