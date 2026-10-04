# Stop loss analysis (SLA)

Send `start stop loss analysis` or `start SLA` to the Telegram bot. It replays every closed Broker A and
Broker B trade against a grid of stop-loss and trailing-stop settings and sends back advice: a stop loss
(for example -$10 or -$15) and a trailing stop (how much profit switches it on, and how far behind the best
price it follows). **When it advises a change it applies it automatically** (from 4 Oct 2026): the new values go
into the same tables `make SL <n>` and `make trail <activation> <distance>` write, so both brokers use them from their
next check, open trades included. If the advice is to keep, nothing changes. It also runs by itself every weekday at
6:15 AM ET (cron-job.org), as well as on demand. Each applied change is also logged in the `stop_settings_history` table (as are `make SL` / `make trail`), which the dashboard
uses to show the stop loss and trailing stop that were in force on each forecast page.

Code: `stop_loss_analysis.py` (the analysis), `stop_loss_analysis_job.py` (reads trades and prices, sends the
message), `.github/workflows/stop_loss_analysis.yml` (the job, started by the Worker the same way as `run TA`).

## Method

1. **Trades.** Every closed trade of the last 45 days from `trades` and `broker_b_trades`. Only each trade's
   entry (price, time, direction) is used. How the trade really closed is ignored, so manual closes and
   "stop trading" closes count like any other.
2. **Prices.** 1-minute gold bars. Trades inside the last ~2.8 days use FOREX.com bid/ask bars (a Buy exits on
   the bid, a Sell on the ask). Older trades use Twelve Data mid prices, which can differ by a few dollars. A
   trade is skipped if its price data has a hole or does not match its entry.
3. **Exit rule.** The same rule the brokers run live (`broker._scan_exit_crossing()`; the replay is a
   vectorised copy that was checked against it on 875 cases with identical results): the stop starts at the
   stop loss and, once the trade has been `activation` in profit, follows `distance` behind the best price. No
   fixed take-profit. Each bar is checked against the stop as it stood before that bar can raise the peak, and the exit fills at the stop level.
4. **Horizon.** A trade is followed until its stop is hit or 5 PM ET that day, then counted at that price.
   Without the cap a wide stop lets one trade run for days.
5. **One position at a time.** Broker A and Broker B each hold one trade at a time. The replay is sequential per
   broker, so a trade is skipped if an earlier simulated trade of that broker would still be open at its entry.
6. **Grid.** Stop loss $5, 6, 8, 10, 12, 15, 20. Activation $3, 4, 5, 6, 7, 8, 10. Trail distance $3, 4, 5, 6, 7, 8, 10.
7. **Choosing.** Each setting is scored by the average total of itself and its neighbours one step away in
   every direction, so a plateau beats a lucky spike. The advice is **change** only if the pick beats your
   current live setting by at least $10 (or 10% of the current total) **and** still beats it with the single
   most-improved trade removed. Otherwise it says keep.

## Reading the message

The message is deliberately short (since 4 Oct 2026):

- The title, how many closed trades it used and over which dates (and how many were priced on FOREX.com bid/ask
  versus Twelve Data mid prices).
- The advice (change, or keep) with the advised stop loss and trailing stop.
- The replay total for the advised setting, for your current setting, and for the old fixed +$10 / -$10 rule, and by
  how much it beats the current one (also without its single best trade).
- When the advice is to change, a line saying the new settings were **applied automatically**. (If the database write
  fails, the message says so and gives the two commands, `make SL ...` and `make trail <distance> activate
  <activation>`, to apply them by hand.)

The per-stop-loss table, the runner-up settings, the confidence note and the "edge of the tested range" warning
used to be in the message; they are no longer sent (the analysis still computes them).

## Limits

- With dozens of trades the advice is a lean, not proof. A different market (gold has been trending down
  through this period) can reverse it.
- Trades that were blocked at the time cannot be replayed; only the trades that really happened are known.
- Mid prices for older trades are less exact than bid/ask.
