# Timings reference

Every timing/schedule-bound behavior in this project, in one place. Source of truth for each row is
the code/config cited — this file is descriptive, not authoritative; if it ever disagrees with the
code, the code wins. See `CLAUDE.md`'s "Scheduling" section for the general cron-job.org/GitHub
Actions mechanism each scheduled job uses.

| What | Timing | Days | Source |
|---|---|---|---|
| Poll job trigger | Every 5 minutes | Every day (trigger has no day restriction) | `config.POLL_INTERVAL_MINUTES`, `.github/workflows/poll.yml`, cron-job.org |
| Overnight Neon compute pause (poll skips `init_db()`/price fetch) | Outside 7:00 AM–5:00 PM ET | Weekdays; all day on weekends | `market_hours.is_overnight_polling_pause()` |
| Weekly market close | Friday 5:00 PM ET | Friday | `market_hours.py` (`MARKET_CLOSE_WEEKDAY`/`MARKET_CLOSE_HOUR`) |
| Weekly market reopen | Sunday 6:00 PM ET | Sunday | `market_hours.py` (`MARKET_OPEN_WEEKDAY`/`MARKET_OPEN_HOUR`) |
| Weekend price-fetch skip window | Friday 5:00 PM ET – Sunday 6:00 PM ET | Fri–Sun | `market_hours.is_market_closed()` |
| Market open/close Telegram notification check | Fires once each boundary (`minute < 5` window) | Fri 5pm / Sun 6pm | `market_hours.check_market_hours_alert()`, called every poll |
| Broker A / Broker B trading-hours entry window | 7:00 AM–5:00 PM ET (new entries only; exits unaffected) | Weekdays | `broker._within_entry_window()`, shared with Broker B |
| Broker B entry window (legacy constant) | `ENTRY_WINDOW_START_ET`–`ENTRY_WINDOW_END_ET`, 7am–5pm ET (was 8am–4pm) | Weekdays | `broker_b.py` |
| Broker A/B DXY confirmation window | Trailing 15 minutes | Every poll (continuous) | `broker.DXY_CONFIRM_WINDOW_MINUTES`, `config.INTRAHOUR_SWING_ALERT_THRESHOLD[...][15]` |
| "Trading paused" program window (reference point for `stop trading`/`start trading`) | Opens 7:00 AM ET, closes 5:00 PM ET | Weekdays | `trading_control.py` |
| Scheduled Telegram pause (`<date>. Stop trading from X till Y`) | User-specified start/end, interpreted in America/New_York; no times = whole day | User-specified date | `trading_control.py` (Worker-side parsing) |
| Entry touch max age (Broker B) | A touch older than 7 minutes when noticed is never filled | Continuous | `broker_b.ENTRY_MAX_TOUCH_AGE_MINUTES` |
| Broker A/B trailing stop activation | Starts trailing once trade is $7 in profit | Per-trade | `broker.TRAILING_STOP_ACTIVATION` (overridable via `make trail`) |
| Broker A/B trailing stop distance | Trails $7 behind best price once active | Per-trade | `broker.TRAILING_STOP_DISTANCE` (overridable via `make trail`) |
| Broker A/B initial stop-loss | $10 distance (overridable via `make SL <n>`, 1–100) | Per-trade | `broker.STOP_LOSS_THRESHOLD` / `stop_loss_threshold()` |
| Broker A/B exit-scan candle lookback | Up to 3000 1-minute bars since trade opened | Per-trade | `broker._exit_bar_count()` |
| Intrahour swing alert windows | 15 / 10 / 5 minutes, rising-edge per window | Every poll | `config.INTRAHOUR_SWING_WINDOWS_MINUTES` |
| Broker A Consensus5of7 signal lookback | Trailing 10 minutes of alerts | Every poll | `broker.py` |
| Frequency test — interactive | On-demand, no schedule | Any day, user-triggered in a Claude Code session | `frequency_test.py` / `frequency-test` skill |
| Frequency test — automatic (`frequency_check_job.py`) | 6:00 AM ET | Weekdays (Mon–Fri) | `.github/workflows/frequency_check.yml`, cron-job.org |
| Frequency test lookback window | Rolling 30 days | Continuous | `config.FREQUENCY_TEST_LOOKBACK_DAYS` |
| Common session window (frequency test gold-swing filter) | 9:30 AM–2:55 PM ET | Weekdays | `config.COMMON_SESSION_START_ET` / `COMMON_SESSION_END_ET` |
| XAU/USD technical analysis forecast (`ta_forecast_job.py`) | 7:00 AM and 12:00 PM ET (two runs/day; also on-demand via `run TA` Telegram command) | Weekdays | `.github/workflows/ta_forecast.yml`, cron-job.org (two entries) |
| ADP release watch (`release_watch_job.py`) | Triggered 8:14 AM ET, burst-polls every 15s for up to 6 min | Weekdays | `.github/workflows/release_watch_adp.yml` |
| NFP release watch (`release_watch_job.py`) | Triggered 8:29 AM ET, burst-polls every 15s for up to 6 min | Weekdays | `.github/workflows/release_watch_nfp.yml` |
| Real-world ADP release time | ~8:15 AM ET | Monthly, weekday | `docs/market.md` |
| Real-world NFP release time | ~8:30 AM ET | Monthly, weekday (first Friday typically) | `docs/market.md` |
| API Weekly Crude Oil Stock watch (`oil_weekly_job.py`) | Repeated triggers ~3:00 PM–6:00 PM ET, ~every 10 min | Tuesdays only | `.github/workflows/oil_weekly_watch.yml` |
| Real-world Weekly Crude Oil Stock release | Observed ~7:00 PM–10:00 PM UTC (3pm–6pm ET) | Tuesday evenings | `docs/fundamental-analysts/fundamental-analyst-oil-weekly-log.md` |
| ADP/NFP Routine (live trigger via `routine_trigger.py`) | Fires within same ~5-min poll cycle when a fresh ADP/NFP alert fires | Whenever alert fires | `routine_trigger.py`, called from `main.poll_once()` |
| ADP/NFP Routine (own fallback schedule) | Hourly recheck | Continuous | Claude Code Routine, outside this repo |
| Stop-loss analysis job (`stop_loss_analysis_job.py`) | On-demand via `start SLA` / `start stop loss analysis` Telegram command | Any day, user-triggered | `.github/workflows/stop_loss_analysis.yml` |
| Stop-loss analysis horizon per trade replay | Up to 5:00 PM ET | Per trade day | `stop_loss_analysis.py` |
| Stop-loss analysis bar source cutover | FOREX.com bid/ask bars for last ~2.8 days, Twelve Data mid before that | N/A | `stop_loss_analysis_job.py` |
| Telegram inbound webhook | Event-driven — acts the instant a message arrives, no schedule | Any time | `telegram_webhook/` (Cloudflare Worker) |
| Dashboard price-change windows | 5 / 10 / 15 / 30 / 60 min trailing | Continuous | `config.CHANGE_WINDOWS`, `dashboard.py` |
| Responsive layout breakpoint | N/A (viewport width, not time) — listed for completeness | N/A | `dashboard.py` (`LAPTOP_BREAKPOINT_PX`, 700px) |

## Notes on overlaps

- The **overnight Neon pause** (7am–5pm ET window, all days) is the binding constraint on when the
  poll job actually does real work — it's tighter than the weekend market-closed window on weekdays,
  and covers the full weekend too.
- **Broker A/B's 7am–5pm ET entry window** matches the poll's active hours, so in practice neither
  broker can open a new position outside the hours the poll itself is doing real work anyway — the
  entry-window check is a second, explicit gate on top of that.
- `telegram_webhook/` is the only component in the project with no cron-job.org entry at all — it's
  purely event-driven (Telegram pushes to it whenever a message arrives).
