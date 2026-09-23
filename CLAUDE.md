# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A market-indicator monitor: polls gold price and related macro indicators on a schedule, checks alert
rules, sends Telegram messages, and shows a live dashboard. It runs entirely in the cloud (GitHub
Actions + Streamlit Community Cloud + Neon Postgres) — no server to manage, no local process needs to
stay running. Local runs are just for development/testing.

## Commands

Windows venv already exists at `./venv`. Activate or call binaries directly:

```powershell
.\venv\Scripts\Activate.ps1
python main.py                    # continuous local poller (BlockingScheduler loop)
streamlit run dashboard.py        # local dashboard
python poll_job.py                # one-shot poll (what the cloud job actually runs)
python frequency_test.py          # backtest: how often would each intrahour-swing threshold have fired?
python frequency_check_job.py     # one-shot: frequency_test.py + auto-tune off-target thresholds + Telegram report
```

There is no test suite or linter configured in this repo. `frequency_test.py` is the closest thing to
one — not a correctness test, but a historical backtest (see its docstring) against live Twelve Data
1-minute bars (gold spot and the six gold ETFs) and yfinance 5-minute bars (dxy/us10y — see
`docs/data-sources.md` for why those two stay coarser) for computing `INTRAHOUR_SWING_ALERT_THRESHOLD`
(see `docs/technical-analyst-*-log.md` for what each indicator is and how it's used).
`INTRAHOUR_SWING_ALERT_THRESHOLD` itself lives in `intrahour_swing_thresholds.json`, not inline in
`config.py`, specifically so `frequency_check_job.py` can rewrite it programmatically (see below)
without touching hand-maintained source.

**Two separate frequency-test workflows exist** — an interactive, human-approved one for ad hoc
requests in a Claude Code session, and a fully automatic one that runs every weekday morning. Both run
the same underlying study, defined in `frequency_test.py`: find every moment gold spot itself swung
`GOLD_SWING_THRESHOLDS[window]` (a fixed $5/$10/$15 for the 5/10/15-min windows) within that trailing
window, rising-edge deduped, and restricted to `COMMON_SESSION_START_ET`-`COMMON_SESSION_END_ET`
(9:30am-2:55pm ET, weekdays) — the trading hours shared by all eight intrahour-swing indicators, so a
gold move outside that window can't be compared against all eight. At each of those moments, measure
each indicator's own high-low swing over that identical window and average it across every such moment
— **that average is the indicator's threshold itself**, not a target to search toward. There is no
event-count target/tolerance and no search step (unlike the project's original methodology, which this
replaced): `FREQUENCY_TEST_LOOKBACK_DAYS` (30, a rolling window, not a fixed historical range) is the
only knob. Don't conflate the two workflows:

- **Interactive ("Standing frequency test workflow")**: when a user asks *you* (in a Claude Code
  session) to run a frequency test, (1) run `frequency_test.py` and report each of the twenty-four
  indicator/window combinations' freshly computed average (companion swing, in dollars/index-points/
  yield-points as appropriate) alongside its sample size (`n_used`/`n_total` — how many of gold's
  events actually fell in the common session with enough data to measure); (2) **do not edit
  `intrahour_swing_thresholds.json` or commit anything until the user approves the new values** — report
  and wait. This is for a human explicitly asking in a session; it's the only path that touches
  `config.py` itself (e.g. changing `INTRAHOUR_SWING_WINDOWS_MINUTES`, `GOLD_SWING_THRESHOLDS`, or the
  common-session window), since those aren't things the automatic job below ever rewrites.
- **Automatic (weekday mornings, unattended)**: `frequency_check_job.py` reruns the same study each
  weekday (see Scheduling below) but does *not* wait for approval — it recomputes all twenty-four
  averages fresh every run and rewrites `intrahour_swing_thresholds.json` with whichever ones actually
  changed. The GitHub Actions workflow then commits that file, opens a PR, and merges it — see
  Scheduling below for exactly how and its limits. A Telegram report is sent every run either way,
  naming every one of the twenty-four indicator/window combinations and whether it was left unchanged
  or updated (old threshold -> new threshold, with the sample size behind the new value).

Installing/updating deps: `pip install -r requirements.txt` (into `./venv`).

## Architecture

**Data flow**: `data_fetcher.fetch_latest_prices()` → `main.poll_once()` → `storage.save_readings()`
→ `rules.check_*_alerts()` → `notifier.send_telegram_message()`. Both `main.py` (local, continuous)
and `poll_job.py` (cloud, one-shot) call the same `poll_once()`.

**Three different data sources, per indicator** (all defined in `config.py`):
- `dxy`, `us10y`, `gld`, `iau`, `gldm`, `gdx`, `gdxj`, `ring` — yfinance (`INDICATORS` dict, generic
  path in `data_fetcher._fetch_yfinance_price`)
- `gold` — **Twelve Data**, not yfinance. Yahoo's spot-gold symbols (`XAUUSD=X`, `XAU=X`) no longer
  resolve; `GC=F` (COMEX futures) is the only gold quote yfinance still serves, and it trades at a
  premium/discount to spot. So `gold`'s live price is special-cased in `fetch_latest_prices()` to call
  `fetch_gold_spot_price()` instead. `GC=F` is still used, but only for `rules.check_sma_crossover()`'s
  daily-close history (trend shape doesn't need spot-exact values).
- `inflation`, `financial_stress` — **FRED** (`FRED_SERIES` dict). `inflation` (T10YIE) updates daily;
  `financial_stress` (STLFSI4) updates weekly and oscillates around zero, which is why it's deliberately
  left out of `PCT_CHANGE_ALERT_THRESHOLD` (a % change near zero is meaningless/explosive) and instead
  uses `VALUE_CHANGE_ALERT_NAMES` (alerts on any change at all, via `rules.check_value_change_alerts`).

`config.ALL_INDICATOR_NAMES` (= `INDICATORS` keys + `FRED_SERIES` keys) is what the poll loop and the
dashboard actually iterate over — adding a new indicator to one of those two dicts is enough to make it
show up everywhere (storage, alerts loop, dashboard tiles/charts) without touching other files, unless
it needs its own alert threshold.

**Standing "new indicator" workflow**: whenever a new indicator/price is added to the project (a new
entry in `INDICATORS` or `FRED_SERIES`, or any other tracked price), also add a row for it to the table
in `docs/market.md` — its type (price / index / indicator), data source, update frequency, its typical
relationship (same direction / opposite / mixed) to the gold price, which mechanism (if any) sends it to
Telegram, and its `Agent` cell (`Technical`, `Fundamental`, or `NA` — see the table's intro prose for
which subagent, if any, treats it as its territory).

**Standing "indicator change" workflow**: this cuts the other way too — whenever an *existing*
indicator's config changes (its alert threshold, which alert mechanism it's wired into, whether/how it
alerts to Telegram at all, its data source, its FRED series ID, or its name/key), update that
indicator's row in the `docs/market.md` table in the same change, not just `config.py`. Concretely: a
threshold edit updates the `Telegram alert?`/`In frequency_test.py?` cell text that quotes the old
number; moving an indicator between `PCT_CHANGE_ALERT_THRESHOLD`/`ABS_CHANGE_ALERT_THRESHOLD`/
`VALUE_CHANGE_ALERT_NAMES`/`INTRAHOUR_SWING_ALERT_THRESHOLD` updates both the `Telegram alert?` cell and
the "Alert mechanisms in play" bullets below the table; a data-source change (e.g. switching a series
off FRED) updates the `Data source` cell; removing an indicator removes its row. If an indicator is
excluded from the dashboard (`config.DASHBOARD_INDICATOR_NAMES`), reflect that in the row/prose too. The
goal is that `docs/market.md` never lags `config.py` — treat a config change without the matching doc
update as an incomplete change, the same way a code change without its test would be.

**Every external call is wrapped in `retry.with_retries()`** (Twelve Data, FRED, yfinance, and the
Postgres connection in `storage.get_connection()`) — exponential backoff, re-raises after exhausting
attempts. `fetch_latest_prices()` then catches that final exception per-indicator so one permanently
failing API skips just that indicator for the cycle instead of crashing the whole poll (this was an
observed real failure mode: a single Neon/Twelve Data hiccup used to take down the entire cycle).

**Ordering gotcha in `main.poll_once()`**: `save_readings()` runs *before* the alert checks, not after.
`storage.get_previous_reading()` returns the *second*-most-recent row for a given indicator, assuming
the current price has already been saved as the most recent one. Reordering this was a deliberate bug
fix — doing it the other way around caused every real change to be alerted on twice (compared against
the value from two polls back instead of one).

**Six alert mechanisms in `rules.py`**, each suited to a different kind of signal:
- `check_pct_change_alerts` — % move since the *previous poll only* (`PCT_CHANGE_ALERT_THRESHOLD`) —
  inflation
- `check_abs_change_alerts` — same previous-poll comparison, but a fixed move
  (`ABS_CHANGE_ALERT_THRESHOLD`) instead of a %. Used for gold only now (flat $5.00 threshold matters
  more than a % of a ~$4,300 price). `gold` and `inflation` are mutually exclusive between this and
  `check_pct_change_alerts` — an indicator should only be in one of the two threshold dicts. This
  alert's message is prefixed with a 🟡 (`rules.XAUUSD_ALERT_PREFIX`), same as `check_sma_crossover`'s
  below — every Telegram alert about spot gold (XAU/USD) price itself gets this prefix, distinct from
  `check_rsi_alerts`' 🟠 (`rules.RSI_ALERT_PREFIX`) and the Broker's 🔵 trade alerts (Telegram has no
  real text-color support, so a colored-circle emoji is the practical substitute).
- `check_value_change_alerts` — any change at all (`VALUE_CHANGE_ALERT_NAMES`), for indicators like
  `financial_stress` where a % threshold breaks down near zero
- `check_intrahour_swing_alerts` — absolute high-low range over three independent trailing windows,
  15/10/5 minutes (`INTRAHOUR_SWING_WINDOWS_MINUTES`), each with its own threshold
  (`INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]`), computed from our own 5-min polled readings via
  `storage.get_recent_readings()`. Catches a slow climb/drop that never trips the poll-to-poll %
  check. Uses a rising-edge comparison per window (current window over threshold, the window as of one
  poll ago wasn't) so a sustained swing alerts once per window, not every 5 minutes for the rest of the
  window — so a single poll can produce up to one alert per window (up to 3 per indicator, 24 total
  across all eight indicators this mechanism covers). This is the mechanism for gld ($1.77/$1.18/$0.60
  for 15/10/5 min, dollars, see `docs/technical-analyst-gld-log.md`), the two other physically-backed
  gold ETFs iau (see `docs/technical-analyst-iau-log.md`) and gldm
  (`docs/technical-analyst-gldm-log.md`), and the three gold-**mining** ETFs gdx
  (`docs/technical-analyst-gdx-log.md`), gdxj (`docs/technical-analyst-gdxj-log.md`), and ring
  (`docs/technical-analyst-ring-log.md`, holding mining-company shares rather than gold itself, so
  leveraged/noisier than the physical ETFs) — all five added the same way, alerted/frequency-tested
  identically to gld, and — like gld — now also referenced by the Broker's paper-trading rules below
  (`Consensus5of7-buy`/`-sell`, requiring at least 5 of these seven indicators: the five just listed
  plus gld and dxy) — dxy (0.0445/0.0223/0.0147 index points, see
  `docs/technical-analyst-dxy-log.md`), also part of the Broker's seven, and us10y
  (0.0071/0.0035/0.0025 yield points, see `docs/technical-analyst-us10y-log.md`) — alerted and
  frequency-tested identically to the other seven, but deliberately **excluded** from the Broker's
  paper-trading rules (dropped from `Consensus6of8` when it became `Consensus5of7`) — there is no
  single 60-min window anymore; it was replaced by these three shorter windows so each of these
  price/rate indicators alerts at multiple timescales.
  Current values are each the **average companion swing** of that indicator, in that window, at every
  moment over the trailing `FREQUENCY_TEST_LOOKBACK_DAYS` (30) days gold spot itself swung
  `GOLD_SWING_THRESHOLDS[window]` ($5/$10/$15 for 5/10/15 min) — see `frequency_test.py` and
  `docs/frequency-test-thresholds.md` for the full methodology. There is no target event rate to hit;
  `frequency_check_job.py` simply recomputes this average fresh every weekday morning and
  `.github/workflows/frequency_check.yml` merges any change (see Scheduling below), so the numbers above
  are current as of the last successful weekday run, not necessarily what's in this file's git history.
  Each Telegram message states the window, direction
  (up/down), the swing size, the threshold, and the current price; the `$` vs. no-unit formatting is
  picked per-name via `config.DOLLAR_UNIT_NAMES`, not hardcoded per file.
- `check_sma_crossover` — 20/50-day SMA crossover on gold futures daily closes, no config threshold
- `check_rsi_alerts` — RSI(14) on gold spot only (`RSI_PERIOD`), computed from Twelve Data 15-min
  candles via `data_fetcher.fetch_gold_candles()`/`compute_rsi()` (Wilder's formula, the same helpers
  the dashboard's RSI panel uses). Like `check_sma_crossover`, this is a crossing check (compares the
  two most recent RSI values), not a poll-to-poll or rising-edge-window comparison, so it fires once
  when RSI crosses above `RSI_OVERBOUGHT_THRESHOLD` (70) or below `RSI_OVERSOLD_THRESHOLD` (30), not on
  every poll spent past the threshold. The Telegram message states the RSI value and which threshold it
  crossed, prefixed with a 🟠 (`rules.RSI_ALERT_PREFIX`) rather than the 🟡 gold-price prefix.

**Weekly market open/close notification (`market_hours.py`)**: `check_market_hours_alert()`, called
from `main.poll_once()` before the price fetch (so it still fires even if prices are briefly
unavailable right at the boundary), sends a one-off Telegram message when the market closes for the
week (Friday 5:00 PM ET) and reopens (Sunday 6:00 PM ET) — the standard weekly schedule shared by
XAU/USD and the other intraday indicators (gld/iau/gldm/gdx/gdxj/ring/dxy/us10y), i.e. the daily 5-6 PM
ET settlement break
that simply doesn't reopen until Sunday evening on the week's final session. "ET" here is
`America/New_York`, the same zone the rest of the project already uses (`dashboard.py`'s `DISPLAY_TZ`,
`frequency_check_job.py`) — equivalent to Toronto time, since both share the same UTC offset and DST
transition dates year-round. Checked every poll rather than via a separate cron-job.org-triggered
workflow, so it needs no extra external scheduling setup: the existing 5-minute `poll.yml` cadence
already visits the boundary each side of these two weekly instants, and a `minute < 5` window keeps
each notification firing exactly once.

**Broker automated paper-trading (`broker.py`)**: `check_broker_trades()`, called from
`main.poll_once()` right after this cycle's alerts are saved, is a fully automated imaginary
buy/sell engine layered on top of the alert mechanisms above — see `.claude/agents/broker.md`'s
"Rules" section for the human-readable spec (kept in sync with this code by hand, the same convention
as `docs/market.md` vs. `config.py`). Currently two mirror-image rules (`Consensus5of7-buy`/`-sell`):
buy 1 troy oz of gold spot when at least 5 of 7 intrahour-swing indicators (any window) land alerts in
the `alerts` table within a trailing 10 minutes in the required direction — GLD/IAU/GLDM/GDX/GDXJ/RING
up, DXY down (sell on the exact opposite, and it's 5-of-7, not all 7); US10Y is deliberately excluded
from this indicator set (still alerted/frequency-tested like the others, just never consulted for a
Broker entry — was included when this rule was `Consensus6of8`); close at $10 unrealized profit or loss
either way, using a real 1-minute candle scan (not a single point-in-time price) so a spike that briefly
touched $10 and reversed before the next poll still closes at the true level (`broker._find_exit()`).
Trade state lives in a new Postgres `trades` table (mirrors
`readings`/`alerts` — required since `poll_job.py` is a stateless one-shot run each cloud poll, so
in-memory state can't survive between polls); only one trade open at a time, and a fresh entry only
considers alerts newer than the last trade's open time so a stale alert can't retrigger. Every
open/close sends a Telegram message (`notifier.send_telegram_message`), prefixed with a 🔵
(`broker.TRADE_ALERT_PREFIX`) to visually distinguish it from XAU/USD price alerts' 🟡 prefix
(`rules.XAUUSD_ALERT_PREFIX`) in the chat. Deliberately no
markdown/doc log of trades — the `trades` table (`id`, `rule_name`, `trade_type`, `entry_price`,
`open_ts`, `triggering_alerts`, `exit_price`, `close_ts`, `pnl`, `status`) is the only record, so a
trade never requires a repo commit; `poll.yml` doesn't need write access to the repo for this reason.

**Forex broker (`forex_broker.py`, `forex_client.py`) — built but deliberately disconnected**: a second
paper-trading engine, the "Forex" broker, that runs the *identical* entry rules (exits differ — see below) as `broker.py`'s
Broker (`forex_broker.py` imports `_match_entry_rule`/`_triggering_text`/`_pnl`/
`ENTRY_WINDOW_MINUTES`/`EXIT_THRESHOLD` directly from `broker.py` rather than re-implementing them,
so the two rule sets can't drift apart) but, instead of only writing an imaginary trade to Postgres,
places and closes real orders against a FOREX.com **demo** account via `forex_client.ForexClient`
(GAIN Capital's session-based REST "TradingAPI" — login with `FOREX_USERNAME`/`FOREX_PASSWORD`/
`FOREX_APP_KEY`, then market search / order placement / opposite-direction close). Trade state lives in
its own `forex_trades` table (same shape as `trades` plus `forex_order_id`/`forex_close_order_id` for
traceability against the real orders) — entirely separate from `broker.py`'s `trades` table, so the two
engines' open positions and watermarks never interact.

**Exits are on the platform, not in `forex_broker.py`**: right after an entry fills, the broker attaches a
take-profit and a stop-loss at fill ± `EXIT_THRESHOLD` ($10) via `ForexClient.attach_take_profit_and_stop_loss()`
(`/order/updatetradeorder` with `IfDone [{Stop, Limit}]`, confirmed live on position 1032567283: both
orders appear on the forex.com platform, linked one-cancels-the-other, good-till-cancelled), so forex.com
closes the position the moment either level trades. Each later run just reconciles: position gone from
`/order/openpositions` → record the close in `forex_trades`, exit price from `/order/tradehistory`
(`find_closing_trade()`; a closing entry's shape hasn't been observed live yet, so if none is found it
falls back to the nearer TP/SL level and flags the price as an estimate in Telegram); position still open
with TP/SL → do nothing. The old "send our own close once |P/L| ≥ $10" path only runs for a position with
**no** TP/SL attached (attachment failed, which also sends an "UNPROTECTED" Telegram warning) — sending
our own close alongside live TP/SL orders could double-close and flip the position. This is a deliberate
divergence from `broker.py`'s exit logic (which scans 1-minute candles for the $10 crossing), not drift.

**Only the read-only close-check is wired into the poll** — `main.poll_once()` calls
`forex_broker.check_forex_closes()` every poll, right after `check_broker_trades()`, inside its own
`try/except` so a forex.com or credentials problem only logs and never breaks the rest of the poll. It
never places, closes, or modifies an order. It reconciles `forex_trades` with forex.com's open positions:
a tracked trade whose position is gone gets its close recorded, with a Telegram alert. If nothing is
tracked, the oldest open XAU/USD position on the account gets tracked as a `rule_name = 'Manual'` trade
(entry and open time taken from forex.com, "now tracking" Telegram message), so trades placed by hand on
the platform are alerted on close too. `forex_trades` tracks one open trade at a time; any further
untracked positions are picked up one per poll as each closes. It no-ops with a log line if the `FOREX_*`
secrets aren't set in the GitHub repo (`poll.yml` passes them through). The full broker
(`check_forex_broker_trades()`: rule-triggered entries, TP/SL attachment, the unprotected-position
fallback close) is still **not** wired in: `python forex_broker.py` by hand is the only way to run it,
checking the rules once against the real demo account without looping or scheduling itself. Keep it
disconnected from `main.poll_once()` unless the user explicitly asks to connect it — this is a
deliberate, standing exception to "adding an indicator/mechanism makes it show up everywhere
automatically." While a `Manual` trade is open, the full broker won't open another, and its fallback
close never touches a `Manual` trade (that position is the user's to manage).

`forex_client.py`'s endpoint paths/JSON field names were reconstructed from third-party client
implementations, not forex.com's official (login-gated) API reference — its module docstring lists what
to verify against the real docs portal — now just whether the demo account nets an opposite-direction
order into a close vs. needing a dedicated close call. Login, the account lookup, the price fetch
(`python forex_client.py` — connectivity only, never places an order), and order placement (one manual
test buy of 1 oz XAU/USD, 2026-09-22) are confirmed working against the live demo account. The order
response's executed price is in its `Orders[]` entry, not the top level — `_fill_price()` reads it from
there (reading the top level silently fell back to the pre-trade quote, $0.34 off on the test order). Spot gold is market `XAU/USD`
(MarketId 401153870, min size 0.1); the lookup uses `/cfd/markets` with an exact name match, because
`/market/search` ignores its name filter and returns the whole catalog (it silently resolved "Spot Gold" to
an unrelated stock). **Orders are locked to XAU/USD only**: `TRADABLE_MARKET_NAME`/`TRADABLE_MARKET_ID` are hardcoded
(not env-overridable), `place_market_order()`/`close_position()` take no market argument, and every order
first re-resolves the name and refuses (raising `ForexClientError`, nothing sent) unless it maps to exactly
that ID. Letting the Forex broker trade anything else is a deliberate code change, not a config tweak. **Order
placement (and TP/SL attachment) is a single attempt** — the one deliberate exception to "every external call is wrapped in
`retry.with_retries()`", since retrying a request that forex.com may already have filled could double the
position. A 4xx or explicit rejection raises `ForexClientError` (nothing placed); a timeout, dropped
connection, 5xx, or unreadable response raises `ForexOrderUncertainError` (outcome unknown, with the
account's open positions from `/order/openpositions` attached). `forex_broker.py` catches the latter, sends
a Telegram warning to check the demo account, and records nothing in `forex_trades`. Credentials are three
optional env vars (`FOREX_USERNAME`/`FOREX_PASSWORD`/`FOREX_APP_KEY`, see `.env.example`) read the same
`load_dotenv()`-then-`os.environ.get()` way as every other secret in this project. `poll.yml` passes all
three through from repo secrets of the same names for the close-check above; until those repo secrets
exist they arrive empty, `ForexClient()` raises `ForexClientError`, and the close-check skips with only a
log line.

**NFP fundamental-analysis data (`nfp_reports` table)**: `docs/fundamental-analyst-nfp-log.md` used to
hold a hand-maintained markdown table of Non-Farm Payrolls release data (previous/expected/actual
figures plus gold spot's reaction at +5/10/30min/1h/2h) — that raw data now lives in Postgres instead,
in a `nfp_reports` table (`release_ts`, `data_month`, `previous_value`/`expected_value`/`actual_value`,
`gold_at_release`, `gold_5min`/`gold_10min`/`gold_30min`/`gold_1h`/`gold_2h`, `notes`), same reasoning
as the Broker's `trades` table: a routine update (a new release's figures) shouldn't need a code change
or a repo commit. `storage.insert_nfp_report()`/`get_nfp_reports()` are the write/read paths; nothing
in the poll loop touches this table automatically — unlike `readings`/`alerts`/`trades`, there's no
existing automated source for NFP consensus ("expected") figures or precise post-release candle
reactions, so a new row is still added the same way the original 12 were compiled (a one-off research
session), just written to Postgres instead of appended to the doc. `storage.update_nfp_report_reaction()`
fills in `gold_5min`/`gold_10min`/`gold_30min`/`gold_1h`/`gold_2h` on an already-inserted row (matched by
`release_ts`) once those windows are observable, so recording a release the moment it prints doesn't
require waiting on the later reaction columns first. The doc itself is kept for descriptive/methodology
content (what NFP is, sourcing method, shutdown-disruption caveats, narrative findings) — see its own
text for the current split. `backfill_nfp_reports.py` was a one-time migration of the 12 releases that
used to be the doc's table; it no-ops if the table already has rows.

**ADP NEC fundamental-analysis data (`adp_reports` table)**: same shape and reasoning as `nfp_reports`
above, for the separate ADP National Employment Change report (`adp_employment`, FRED `ADPMNUSNERSA`,
released a couple of days before BLS NFP each month at 8:15am ET rather than NFP's 8:30am ET — see
`docs/market.md`'s intro note on not conflating the two). The `adp_reports` table has the identical
column set (`release_ts`, `data_month`, `previous_value`/`expected_value`/`actual_value`,
`gold_at_release`, `gold_5min`/`gold_10min`/`gold_30min`/`gold_1h`/`gold_2h`, `notes`), written/read via
`storage.insert_adp_report()`/`get_adp_reports()`/`update_adp_report_reaction()`. `backfill_adp_reports.py`
loaded the last 12 releases (Oct 2025 – Sep 2026 print dates) the same one-off-research way
`backfill_nfp_reports.py` did; it no-ops if the table already has rows. `docs/fundamental-analyst-adp-log.md`
holds the descriptive/methodology content and the retrospective analysis of those 12 releases, the same
split `docs/fundamental-analyst-nfp-log.md` uses for NFP — see that doc for the finding worth noting
here: ADP NEC's immediate (+5min) reaction tracks the beat/miss direction far more consistently than NFP's
does (92% hit rate vs. NFP's roughly coin-flip record), but that edge decays to near-chance by +1h.

**ADP/NFP release trigger (`routine_trigger.py`)**: the live analysis+Telegram+`notes` write for a fresh
ADP/NFP release (the automated equivalent of `fundamental-analyst`'s New-release recommendation
workflow) runs as a Claude Code Routine ("ADP/NFP release watcher"), not as project code — it's scheduled
infrastructure outside this repo (see the "Subagents" section below), and on its own schedule alone it
only rechecks FRED hourly. `routine_trigger.trigger_release_analysis()` closes that latency gap: in
`main.poll_once()`, right after `check_value_change_alerts()`'s alert loop saves/sends each alert, any
alert for `adp_employment`/`nonfarm_payrolls` (`routine_trigger.RELEASE_TRIGGER_NAMES`) also POSTs to
that Routine's API-trigger endpoint (`ROUTINE_FIRE_URL`/`ROUTINE_FIRE_TOKEN`), so the Routine re-checks
FRED and fires within the same ~5-minute poll cycle instead of waiting up to an hour. The Routine's own
hourly schedule stays on as a fallback (harmless and non-duplicating, since the Routine's own logic
already no-ops when the release it would record is already in `adp_reports`/`nfp_reports`) in case the
API call itself fails. Both env vars are optional -- `routine_trigger.py` no-ops with a log line if
either is unset, and `main.poll_once()` catches any exception from the fire call so a Routines-API
hiccup never blocks the rest of that poll cycle (the remaining alerts and `check_broker_trades()` still
run). This is the only place project code talks to the Routines API; the Routine itself is still
never allowed to edit repository files when it fires, live-trigger or scheduled alike.

**Same-minute release detection (`release_watch_job.py`)**: the mechanism above still ultimately
depends on FRED having ingested the fresh ADP/NFP number, which can lag the real BLS/ADP release by
anywhere from minutes to hours -- so `check_value_change_alerts`'s detection (and the Routine nudge it
triggers) is "eventually," not "same-minute." `release_watch_job.py` is a separate, faster, purely
additive path: triggered by cron-job.org (`.github/workflows/release_watch_adp.yml`/
`release_watch_nfp.yml`) at 8:14am/8:29am ET weekdays -- a few minutes before ADP's 8:15am and NFP's
8:30am ET scheduled releases -- it checks FMP's economic-calendar endpoint (`FMP_API_KEY`,
`config`'s `docs/data-sources.md` has the source comparison) for today's matching release, and if one
is scheduled, burst-polls that same endpoint every `BURST_POLL_INTERVAL_SECONDS` (15) for up to
`BURST_POLL_MAX_MINUTES` (6) -- unlike every other job in this repo, this one is not a pure one-shot,
since cron-job.org itself can't reliably schedule sub-minute triggers. The moment FMP's `actual` field
goes from `null` to a real number, it sends a Telegram alert (prefixed 🟣, `RELEASE_ALERT_PREFIX`)
immediately and exits. On the large majority of weekday mornings neither release is scheduled that
day, so the job is a single FMP call and an immediate no-op. Deliberately does **not** write to
`nfp_reports`/`adp_reports` or fire `routine_trigger.trigger_release_analysis()` itself -- the existing
Routine still owns recording the release and sending its recommendation message, and its own no-op
check keys off whether the release is already in those tables; writing the row here first would make
the Routine think its job was already done and skip its recommendation entirely. This job only adds a
faster "the number just printed" alert on top of that unchanged pipeline.

**API Weekly Crude Oil Stock data (`oil_weekly_reports` table)**: the first indicator in this project
sourced from neither FRED/yfinance/Twelve Data nor the regular poll loop at all -- see
`docs/fundamental-analyst-oil-weekly-log.md`. FRED has no matching series (checked directly; only the
official EIA report exists there, not the API's, and this project doesn't track EIA's either), so this
one is FMP-only (`/stable/economic-calendar`, same endpoint `release_watch_job.py` uses). Real-world
release timing is Tuesday evenings but at a much less precise minute than ADP/NFP (observed anywhere
from ~19:00-22:00 UTC / 3pm-6pm ET), so a short burst-poll like `release_watch_job.py`'s doesn't fit --
instead `oil_weekly_job.py` is a pure one-shot (check FMP once, act or exit) that cron-job.org triggers
repeatedly across that whole window (`.github/workflows/oil_weekly_watch.yml`), same philosophy as
`poll.yml`'s own repeated-external-trigger pattern, just scoped to Tuesday evenings instead of running
continuously. Unlike `release_watch_job.py`, this job **does** write directly to Postgres -- there's no
competing Routine for this indicator, so it's the sole source of truth: the moment `actual` appears for
a week not already in `oil_weekly_reports`, it sends a Telegram alert (🟣, same prefix as the ADP/NFP
same-minute alerts) and records the release (`storage.insert_oil_weekly_report()`) in one step.
`storage.oil_weekly_report_exists()` plus the table's `UNIQUE (week_ending)` constraint make repeated
invocations after the real release (there will be several, since the job fires on a schedule not tied
to the actual release minute) a safe no-op rather than a duplicate alert. `week_ending` is a real `DATE`
(parsed from FMP's event-name suffix, e.g. `(Sep/18)`), not a text label like `nfp_reports`/
`adp_reports`' `data_month` -- the natural per-release key for a weekly report.
`backfill_oil_weekly_reports.py` loaded the last 52 weeks, fetched live from FMP (paginated in ~85-day
chunks around a silent per-call history cap FMP's economic-calendar endpoint turned out to have,
confirmed empirically -- see `docs/data-sources.md`) -- unlike the original 12-row NFP/ADP backfills,
this one needed no manual research, since FMP already has clean structured history for this specific
event. `gold_at_release` and the reaction columns are left `NULL` for every backfilled row (fillable
later via `storage.update_oil_weekly_report_reaction()`); only the release figures themselves
(`previous_value`/`expected_value`/`actual_value`/`week_ending`) are backfilled.

**Weekday threshold audit trail (`threshold_history` table)**: same move as the two tables above —
`docs/frequency-test-thresholds.md` used to have a "Threshold history" table that
`frequency_check_job.py` appended one row to every weekday run (the date plus that run's final value for
all twenty-four GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y 15/10/5-min thresholds, whether or not any
changed); that now
goes straight to a `threshold_history` table in Postgres (`storage.insert_threshold_history_row()`/
`get_threshold_history()`) instead, so the log entry doesn't need a repo commit — `poll.yml`
and `frequency_check.yml`'s automated commits are both now purely "when a value actually changed", not
"every scheduled run". `backfill_threshold_history.py` migrated the doc's one existing row.

**`data_fetcher.fetch_gold_candles()`/`compute_rsi()`** are now only called by `rules.check_rsi_alerts()`
(uncached, called every poll) — `dashboard.py` no longer fetches candles or renders RSI/ADX itself (see
"Dashboard layout" below), so the Twelve Data fetch/retry logic that helper wraps has a single caller.

**Dashboard layout (`dashboard.py`)**: no charts — the dashboard is a single compact HTML table (built
by hand and rendered via `st.markdown(..., unsafe_allow_html=True)`, not `st.dataframe`/`st.metric`, for
tight control over font size and column widths) with one row per `DASHBOARD_INDICATOR_NAMES` entry and
a column each for the current price and the price/percentage change over five trailing windows —
5/10/15/30/60 min (`CHANGE_WINDOWS`) — computed from the same `readings` rows the poll job writes
(`change_over()`: latest reading vs. the last reading at or before N minutes ago; `None`/`—` if that
much history doesn't exist yet, e.g. right after a fresh deploy). Deliberately sized for a phone screen
(tuned against a Samsung S24 Ultra viewport) so every row is visible without scrolling — small fonts, a
fixed `<colgroup>` so columns can't overflow the viewport width, kept in the CSS block at the top of the
file rather than per-element `style=` (the per-cell `style=` that remains is just the red/green
up/down color, computed from the sign of each change). The `$` unit shown on price/change cells is
picked per-name (`DOLLAR_UNIT_NAMES`) the same way `rules.py` picks it for alert messages. Below the
symbols table sit two more `st.dataframe` tables (not the hand-built HTML above — no per-cell layout
control is needed here, so the plain Streamlit widget is enough): "Recent Trades" (`load_trades()`, the
`trades` table Broker's `broker.py` writes — see "Broker automated paper-trading" above — most recent
20 by `open_ts`, columns renamed for display and `$`-formatted; `exit_price`/`close_ts`/`pnl` are `—`
for the still-open trade, if any) above "Recent Alerts" (`load_alerts()`, unchanged). `readings`/
`alerts`/`trades` timestamps are all stored as UTC (`storage.py`'s `datetime.now(timezone.utc)`)
regardless of where the poll job or dashboard happen to run — the "last loaded" caption and the Recent
Trades/Recent Alerts tables are the only places that convert to a human timezone for display, all
through the shared `to_display_str()` helper, to `DISPLAY_TZ` (`America/New_York`, matching the
project's existing scheduling convention — see "Scheduling" below). The `readings` timestamps behind
the change-window table stay in UTC internally; that's fine since `change_over()` only ever compares
two of them to each other, never renders one directly.

**Storage is Postgres (Neon), not SQLite** — despite `market_data.db` and `streamlit.log` still sitting
in the repo root (gitignored, unused leftovers from an earlier local-SQLite version). `storage.py` and
`dashboard.py` both read `DATABASE_URL` and share the same DB across the poll job and the dashboard,
which run in two completely separate deployments.

**Scheduling**: GitHub Actions' own `schedule:` cron trigger was tried and found unreliable (confirmed
via the Actions API: it didn't fire at all for 90+ minutes on a 5-minute cron). The workflow
(`.github/workflows/poll.yml`) now only declares `workflow_dispatch`, and an external service
(cron-job.org) calls the `POST /repos/.../actions/workflows/poll.yml/dispatches` API every 5 minutes to
trigger it — this is the actual scheduler. `.github/workflows/frequency_check.yml` follows the same
pattern for `frequency_check_job.py`, but weekday mornings (6 AM America/New_York, Monday–Friday)
instead of every 5 min — same reasoning for avoiding GitHub's own `schedule:` (UTC-only, no DST
handling), plus cron-job.org lets both the specific time and the weekday-only restriction be set
directly, in `America/New_York`, without any code in this repo.
`.github/workflows/release_watch_adp.yml`/`release_watch_nfp.yml` follow the same pattern for
`release_watch_job.py` (see "Same-minute release detection" above), weekdays at 8:14am/8:29am
America/New_York respectively. `.github/workflows/oil_weekly_watch.yml` follows the same pattern again
for `oil_weekly_job.py` (see "API Weekly Crude Oil Stock data" above), but triggered *repeatedly* —
roughly every 10 minutes across a Tuesday-evening window (~3pm-6pm ET) — rather than once, since that
report's release minute is far less precise than ADP/NFP's. All five workflows need their own
cron-job.org job pointed at their `workflow_dispatch` endpoint — that setup (including the weekday
exclusion, the two release-watch workflows' specific 8:14am/8:29am trigger times, and the oil-weekly
workflow's Tuesday-only repeated-trigger window) lives in the cron-job.org account, not in this repo.
`frequency_check_job.py` reruns `frequency_test.py`'s companion-swing study fresh (see the "Two
separate frequency-test workflows" entry above for the full methodology) and rewrites
`intrahour_swing_thresholds.json` with whichever of the twenty-four indicator/window combinations'
newly computed averages actually differ from what's on disk — see the "Automatic (weekday mornings,
unattended)" workflow above for how this differs from an interactive session's frequency test. A
Telegram message is sent every run either way, listing all twenty-four indicator/window combinations and
whether each was left unchanged or updated (old threshold -> new threshold).
`.github/workflows/frequency_check.yml` is what actually applies the change: after the script runs, if
`intrahour_swing_thresholds.json` changed, the workflow commits it on a new branch, opens a PR, and
merges it (`gh pr merge --squash`, no `--admin` bypass) — so on a `main` with branch protection
requiring reviews or passing checks, that merge step simply fails and the PR sits open for a human to
merge instead of silently forcing it through. Whether the default `GITHUB_TOKEN` is allowed to
create/merge PRs at all, and whether this workflow's runs are exempted from `main`'s review
requirement, are repository settings the owner configures directly in GitHub — not something this
workflow file controls, same as the cron-job.org scheduling setup above. `poll.yml` itself never
commits anything back to the repo — `check_broker_trades()`'s trades go straight to the `trades` table
in Postgres, not to a file, so `poll.yml` only needs the read/query secrets it already had
(`DATABASE_URL` etc.), not repo write access.

**Secrets arrive three different ways** depending on where the code runs:
- Locally: `.env` file + `python-dotenv` (`load_dotenv()` in `data_fetcher.py`, `storage.py`, `notifier.py`)
- GitHub Actions: repo secrets injected as env vars in `poll.yml`
- Streamlit Community Cloud: `st.secrets`, bridged into `os.environ` at the top of `dashboard.py` before
  `storage` is imported (since `storage.DATABASE_URL` is read once at import time)

Required env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TWELVE_DATA_API_KEY`, `FRED_API_KEY`,
`DATABASE_URL` (see `.env.example`).

**Poll interval is 5 minutes** (`config.POLL_INTERVAL_MINUTES`) — not arbitrary. Twelve Data's free
tier caps at 800 requests/day (1-minute polling of gold alone would need 1,440/day), and `inflation`/
`financial_stress` only update daily/weekly from FRED regardless of poll frequency, so polling faster
wouldn't add real signal, just burn through rate limits faster.

## Subagents

`.claude/agents/technical-analyst.md` defines a **read-only** subagent (no `Edit`/`Write` tools) for
studying gold price action/indicators using this project's real data. It's instructed to ask
clarifying questions, plan any recommended change, and explicitly request permission before
implementation — it cannot self-edit code even if asked to. Note: `.claude/agents/` files are only
loaded at session start, so a newly-added or edited agent definition won't be callable until the next
session.

`.claude/agents/broker.md` defines the **Broker** subagent — unlike the other three mechanisms above,
its actual trading logic is NOT this subagent; it's the fully automated `broker.py` (see the
"Broker automated paper-trading" entry above), which runs every poll with no human/session involved.
The subagent itself is **read-only** (`Read`, `Grep`, `Glob`, `Bash` — no `Edit`/`Write`, same as
`technical-analyst`): it explains rules, explains why a specific trade in the Postgres `trades` table
fired, and analyzes performance by rule, querying the `trades`/`alerts` tables directly (there is no
markdown trade log to read instead). Its own "Rules" section is the human-readable spec for what
`broker.py` implements — the two are kept in sync by hand — but the subagent never edits either one; a
proposed rule change is drafted in prose and handed off for the user or a coding session to apply to
both files together. It's invoked on demand like `technical-analyst`.

`.claude/agents/fundamental-analyst.md` defines a subagent (no `Edit`/`Write` tools, same as
`technical-analyst` and `broker`) for analyzing scheduled macro data releases (NFP, CPI, PPI, retail
sales, jobless claims, etc.) and how they move gold. Unlike `technical-analyst`, it *is* meant to query
Postgres — release-by-release data (e.g. the `nfp_reports` table) lives there, not in markdown, per the
"NFP fundamental-analysis data" entry above. It reads `docs/fundamental-analyst-*.md` for
context/methodology (`docs/fundamental-analyst-nfp-log.md`, `docs/fundamental-analyst-adp-log.md`, and
`docs/fundamental-analyst-oil-weekly-log.md`; more will be added the same way as other releases get
their own research), the same way `technical-analyst` reads `docs/technical-analyst-*-log.md`. It's
read-only and plans-then-asks for everything **except** one pre-approved live action, scoped to
`nfp_reports`/`adp_reports` only (not `oil_weekly_reports` — see "API Weekly Crude Oil Stock data"
above for why that one's fully automated instead): when a release just printed, its "New-release
recommendation workflow" lets it send exactly one Telegram message (`notifier.send_telegram_message()`)
recommending gold's likely
5/10/15-minute move, and record the release in `nfp_reports` via `storage.insert_nfp_report()`/
`update_nfp_report_reaction()` — without stopping to ask first, since the user has already authorized
that specific pairing of actions. It still never touches any other table, edits any file, or sends
Telegram outside that one workflow; everything else (a threshold change, a new indicator, a dashboard
tweak) is a plan handed back to the user or a coding session, same as the other two subagents.
