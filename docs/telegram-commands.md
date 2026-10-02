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
| `make trail 7` / `make trail 6 activate 8` | Sets the trailing stop for both brokers. The first number is how far behind the best price the stop follows. The optional second number is how much profit switches the trailing on (it defaults to the first number). Values are $1 to $100, apply to open trades too, and stay until you change them. The default is 7 and 7. |
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
