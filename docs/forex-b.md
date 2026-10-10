# Forex B -- Broker B's rules on the FOREX.com demo account

Status (10 Oct 2026): **shadow mode only.** `forex_watcher.py` watches the live bid/ask every 10 s and records what Forex B
*would* do in `forex_b_trades` (`mode = 'shadow'`). It sends no order to forex.com. Live orders are blocked until the
"Open questions" below are verified on the demo account.

## Problem 1 -- tracking at a finer time scale

Broker B runs inside the 5-minute poll: it reads 1-minute bars, notices a level touch up to ~7 minutes late and records the
fill at the level price. A real account has to act when the price is there and gets the live bid/ask instead.

`forex_watcher.py` polls `ForexClient.get_quote()` (BID and ASK `tickhistory`, ~0.5 s each, confirmed live) every
`TICK_SECONDS` = 10 and appends each new tick to the seeded 1-minute bars as a bar-shaped row (open = high = low = close). That lets
Broker B's own code run unchanged on the faster feed -- nothing is re-implemented:

| Decision | Code reused |
|---|---|
| which levels are armed, re-arm budget | `broker_b.armed_candidates()` (counting `forex_b_trades`, not `broker_b_trades`) |
| a level was touched (approach side first, fade invalidation) | `broker_b._scan_zone_entry()` |
| the five entry filters (DXY, RSI, ADX, ATR, spike) | `broker_b.evaluate_gates()` |
| stop, trailing stop, fade cap, fade lock | `broker._scan_exit_crossing()`, `_fade_lock_level()`, `stop_loss_threshold()`, `trailing_stop_params()` |
| trading hours, pause, weekend | `_within_entry_window()`, `trading_pause_reason()`, `is_market_closed()` |

A touch is acted on only if it is at most `ENTRY_MAX_TOUCH_AGE_SECONDS` (40 s) old in feed time; older ones are dropped, never
filled late. A blocked touch is remembered in memory only -- the watcher never writes to Broker B's tables, so it cannot consume
a Broker B touch. `make SL` / `make trail` apply to it automatically.

Where it runs: it is host-agnostic. `.github/workflows/forex_watch.yml` runs a ~5-minute burst (queued back to back by its
concurrency group, triggered by cron-job.org every 5 minutes, weekdays 6:59am-5pm ET), or run `python forex_watcher.py
--max-minutes 600` on any always-on host. It exits immediately when there is nothing to watch.
GitHub-hosted bursts cost roughly 600 runner-minutes per trading day (free on a public repo; check the quota on a private one),
and between two bursts there can be a gap of seconds to a minute -- an always-on host removes both.

## Problem 2 -- stop-loss rules on Forex (design; needs the demo checks below)

Broker B's exit is a trailing stop evaluated on bars (initial stop 10, trail 7/7, fade cap 15, fade lock). On forex.com a stop is
an order resting on the platform, and a 10-second watcher can overshoot it in a fast move (the offline simulation closed a
lock-level stop $2.20 beyond the level). So the plan uses two layers:

1. **Hard stop on the platform, from the first second.** Right after the entry fills, a stop-only order (no take-profit -- Broker B
   has none) is attached at entry -/+ the initial stop (`stop_loss_threshold()`, capped at `FADE_STOP_LOSS_CAP` for fades). The existing
   `attach_take_profit_and_stop_loss()` call is confirmed live; it needs a stop-only variant. This protects the position if the
   watcher is down, queued or slow.
2. **Trailing / lock stop moved by the watcher.** Each tick the watcher computes the stop with `_scan_exit_crossing()` and, when it
   moves in the trade's favour, amends the platform stop to it (so the fill happens at the platform, not at our 10 s sample). If
   amending is not possible, the fallback is that the watcher closes at market when its stop is crossed (what shadow mode records).

Differences from Broker B worth measuring in shadow mode (all stored per trade): entry slippage (`entry_price` vs `trigger_price`), the
spread (Buy enters on the ask, exits on the bid; confirmed live: bid 4192.56 / ask 4196.13 at the Friday close), exit slippage
(`stop_level` vs `exit_price`), and how many touches/trades the faster feed finds or loses versus Broker B on the same day.

## Open questions -- verify on the demo account before enabling live mode

forex.com's API reference is login-gated, so each of these needs a 0.1 oz demo test (minimum size 0.1; `MinDistance` for XAU/USD is 0):

1. Can an attached stop be **amended** (`/order/updatetradeorder` with `IfDone` carrying the existing stop's `OrderId`)? Is there a
   native trailing-stop order type?
2. Can a stop be **cancelled** (`/order/cancel`), and does the position then stay open?
3. What happens when we send an **opposite market order while a stop is attached** -- does it net/close the position and cancel the stop,
   or double up?
4. What does `/order/tradehistory` return for a **stop-triggered close** (price, time), so the real exit can be recorded?
5. Does the stop fill **at its level or worse** when price gaps through it (slippage on the platform)?
6. Entry as a **resting order** (limit at the fade level / stop-entry at the breakout) instead of a market order on touch -- gives the
   exact level price but cannot run the filters at the touch; probably keep market-on-touch.

Not yet decided: whether Forex B should also be controllable by the Telegram `stop trading` / `make` commands for the real position
(`stop trading` is already honoured: the shadow watcher closes and opens nothing while paused).
