# Telegram commands

Messages you can send to the bot to control trading. They are handled by the Cloudflare Worker in
`telegram_webhook/` (see `telegram_webhook/src/index.js` for the exact parsing) and take effect the moment
the message is sent, not on the next poll. All times are Eastern (`America/New_York`).

| Message you send | What it does |
|---|---|
| `buy broker A` / `sell broker A` | Opens a Buy or Sell of 1 oz in Broker A at the current spot price. Replies with a reason instead if a trade is already open. |
| `buy broker B` / `sell broker B` | The same for Broker B. It also needs a TA forecast to exist. |
| `close broker A` / `close broker B` | Closes that broker's open trade at the current spot price, whether it is winning or losing. |
| `stop trading` | Closes both brokers' open positions at spot and pauses trading until the program's own window next opens (7am ET on a weekday). |
| `start trading` | Resumes trading until the program's own window closes (5pm ET). It also lifts any scheduled pause that has already begun. |
| `<date>. Stop trading from 7 till 10` | Schedules a pause. For example, "Thursday, 1st of October 2026. Stop trading from 7 till 10 o'clock". Leave out the times for the whole day. Leave out the date for an immediate stop. Times from 1 to 6 without am/pm are read as pm. A date that does not match its weekday gets an error reply and nothing is scheduled. |
| `make SL 15` / `set stop loss 12.5` | Sets the stop-loss, in dollars per oz (1 to 100), for both Broker A and Broker B. It stays at that value until you change it, and it also applies to a trade that is already open. It is the starting stop: once a trade is $7 in profit, the trailing stop takes over. With no setting, the default is $10. |
| `make trail 3 10` / `make trail 7` | Sets the trailing stop for both brokers. The **first** number is how much profit switches the trailing on (activation), the **second** is how far behind the best price the stop then follows (distance). One number sets both. `make trail 10 activate 3` also works, because the word names the role. Values are $1 to $100, apply to open trades too, and stay until you change them. The default is 7 and 7. Example: `make trail 3 10` starts trailing at +$3 and keeps the stop $10 behind the best price. |
| `start stop loss analysis` / `start SLA` | Replays every closed trade against many stop-loss and trailing-stop settings and sends back advice (for example a -$10 or -$15 stop and a trailing amount). It arrives in a few minutes. **If it advises a change, it applies it automatically** (same settings as `make SL` and `make trail`); it also runs by itself every weekday at 6:15 AM ET. See `docs/stop-loss-analysis.md`. |
| `start block rules analysis` / `start BRA` | Replays every Broker B touch (the trades that opened, plus touches blocked by the DXY / ADX / RSI / ATR filters) against different filter values, **writes the resulting rules to the `block_rules` table** (Broker B reads them on its next poll) and sends back a report listing what changed. A row is written on every run, even when nothing changes. It arrives in a few minutes, and it also runs by itself every weekday at 6:30 AM ET. See `docs/block-rules-analysis.md`. |
| `run TA` | Starts a new technical forecast, the same job as the 7am and 12pm runs. It becomes the active forecast for Broker B. |
| `rearm levels` | Resets Broker B's trade counts and stop-outs for the current forecast, so each of the four levels can trade again. Broker A is unaffected. |

## Notes

- Phrasing is forgiving about case and spacing. An open or close command needs "broker a" or "broker b"
  plus a buy, sell or close word. Anything else is ignored with no reply.
- Only the chat set as `TELEGRAM_CHAT_ID` can issue commands, and every request must carry the webhook
  secret.
- A trade opened by command is recorded as `Telegram-buy` / `Telegram-sell` and is still closed
  automatically by the trailing stop unless you close it first.
- The Forex broker (`forex_broker.py`) cannot be reached from Telegram.
- Open and close messages show the trade number, for example `BROKER B #36`. Numbers are per broker.
