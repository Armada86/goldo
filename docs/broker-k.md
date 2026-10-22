# Broker K -- RSI / ADX / ATR trend follower (paper only)

Built 22 Oct 2026. Code: `broker_k.py`, constants `K_*` in `config.py`, table `broker_k_trades`, backtest `broker_k_backtest.py`.
Independent of every other broker: no alerts, TA levels, `block_rules`, stop-loss / trailing settings, DXY, and it ignores
the Telegram `stop trading` / pause commands (user request). Paper only -- nothing is ever sent to FOREX.com except the read-only
bid/ask bar requests it shares with the other brokers.

## Rules

| | |
|---|---|
| Entry check | once per poll, 7:00am-5:00pm ET weekdays, no trade open, using the poll's own 15-min gold candles (forming candle included, no extra call) |
| Trend | ADX(14) >= 35 (`K_ADX_MIN`); ADX does not need to be rising |
| Volatility | ATR(14) between $3 and $12 (`K_ATR_MIN`, `K_ATR_MAX`) |
| Direction | RSI(14) in [50, 70] -> `Trend-buy`; in [30, 50] -> `Trend-sell` (`K_RSI_BUY_MIN/MAX`, mirrored) |
| Fill | live ask (Buy) / bid (Sell) from the FOREX.com demo feed, else the poll's Twelve Data spot |
| Exit | take-profit $12, stop-loss $8, or 60 minutes after the open, whichever first (`K_TAKE_PROFIT`, `K_STOP_LOSS`, `K_TIME_STOP_MINUTES`) |
| Exit price | 1-minute bar scan, a Buy exits on the bid, a Sell on the ask; the stop wins if one bar touches both; a time stop closes at the last in-window bar's close. A trade open when the 5pm ET poll cutoff hits is closed retroactively the next morning at the level it really touched |
| Size / limits | 1 oz, one trade at a time, no cooldown, no daily cap |

Telegram: `🟫 BROKER K #n` opens, `🟫🟩` / `🟫🟥` closes, with ADX/RSI/ATR on the open message and the close reason (take-profit /
stop-loss / time stop). The `broker_k_trades` row has `exit_reason` (`TP`/`SL`/`Time`), `entry_context` (hour, RSI, ADX, ATR, ADX
rising, spot, spread) and `exit_context` (RSI/ADX/ATR at the close, minutes held). The dashboard's Recent Trades table and P&L line
include Broker K.

## Where the numbers come from

`python broker_k_backtest.py --fetch` downloads ~44 days of Twelve Data 1-minute bars (cached, gitignored);
`python broker_k_backtest.py [--study wide] [--spread 0.4 --delay 1]` replays them: a simulated poll every 5 minutes in the trading
window, indicators from the last 96 15-minute candles including the forming one, fill at the next minute's open on the ask/bid with
an assumed spread, side-correct bar scan, one trade at a time, train/test split.

Findings (9 Sep - 22 Oct 2026, ~2,450 polls):

- Spread alone costs about $0.30-0.45 a trade; no entry filter at $3-5 limits or $8-15 limits loses $350-675 over the period.
- "Very small" $2-6 limits with the RSI/ADX/ATR momentum entry lost after spread, and the best training cells lost in the untouched
  test third (260 of 1,581 profitable-in-train cells stayed profitable in test; median -$11.6).
- Pullback entries (RSI dip, ATR retracement, direction from +DI/-DI) failed: positive only in the one exit setting they were tuned on.
- Strong-trend momentum (ADX >= 35-40) with $12/$8 or $15/$10 limits and a 60-minute stop was positive in 6 of 7 weeks:
  ADX 35 / $12-$8: +$68 on 66 trades; ADX 40: +$101 on 41; $15/$10: +$99 on 52 (ADX 35). With a $0.80 spread and a 2-minute fill
  delay it stays positive for $12/$8 (+$43 / +$39) but nearly vanishes for $15/$10 at ADX 35 (+$7). ADX 30 is much weaker.

Limits: ~65 trades over six weeks, one regime, parameters chosen after seeing the data (the "test" third was looked at in the first
sweep too), mid prices plus an assumed spread rather than recorded bid/ask. These are starting values to judge on the live paper
record -- `entry_context` / `exit_context` are there to calibrate them (e.g. `SELECT rule_name, (entry_context->>'adx14')::float, pnl
FROM broker_k_trades`).

## Not done / ideas

- Honouring `stop trading` / `broker status` for K (needs a `K` branch in `trading_control.trading_pause_reason()` and the Worker).
- Per-day trade cap or cooldown (the backtest's cooldown 0 was best, but a cap would bound a bad day).
- No Telegram command to open/close K by hand.
