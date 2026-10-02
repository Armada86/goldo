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
| `run TA` | Starts a new technical forecast, the same job as the 7am and 12pm runs. It becomes the active forecast for Broker B. |
| `rearm levels` | Resets Broker B's trade counts and stop-outs for the current forecast, so each of the four levels can trade again. Broker A is unaffected. |

## Notes

- Phrasing is forgiving about case and spacing. An open or close command needs "broker a" or "broker b"
  plus a buy, sell or close word. Anything else is ignored with no reply.
- Only the chat set as `TELEGRAM_CHAT_ID` can issue commands, and every request must carry the webhook
  secret.
- A trade opened by command is recorded as `Telegram-buy` / `Telegram-sell` and is still closed
  automatically at the $10 take-profit or stop-loss unless you close it first.
- The Forex broker (`forex_broker.py`) cannot be reached from Telegram.
- Open and close messages show the trade number, for example `BROKER B #36`. Numbers are per broker.
