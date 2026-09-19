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
one — not a correctness test, but a historical backtest against live yfinance data (see its docstring)
for tuning `INTRAHOUR_SWING_ALERT_THRESHOLD`, one indicator/window combination's worth of updates at a
time (see `docs/technical-analyst-*-log.md` for what past runs found and which thresholds they led to).
`INTRAHOUR_SWING_ALERT_THRESHOLD` itself lives in `intrahour_swing_thresholds.json`, not inline in
`config.py`, specifically so `frequency_check_job.py` can rewrite it programmatically (see below)
without touching hand-maintained source.

**Two separate frequency-test workflows now exist** — an interactive, human-approved one for ad hoc
requests in a Claude Code session, and a fully automatic one that runs nightly. Don't conflate them:

- **Interactive ("Standing frequency test workflow")**: when a user asks *you* (in a Claude Code
  session) to run a frequency test, (1) run `frequency_test.py` against the *current*
  `INTRAHOUR_SWING_ALERT_THRESHOLD` values and report each indicator/window combination's actual event
  count over the last `FREQUENCY_TEST_LOOKBACK_DAYS` days (60 — the most 5-min-resolution history
  yfinance serves for intraday bars); (2) for any combination off-target, search for a new threshold
  that lands within `FREQUENCY_TEST_TARGET +/- FREQUENCY_TEST_TOLERANCE` rising-edge events (60 +/- 4 —
  see the 2026-09-16 entries in `docs/technical-analyst-*-log.md` for the method: the
  threshold-vs-event-count curve is non-monotonic, picks the higher-threshold/post-peak side) and
  propose it; (3) **do not edit `intrahour_swing_thresholds.json` or commit anything until the user
  approves the suggested thresholds** — report and wait. This is for a human explicitly asking in a
  session; it's the only path that touches `config.py` itself (e.g. changing
  `INTRAHOUR_SWING_WINDOWS_MINUTES` or the target/tolerance), since those aren't things the automatic
  job below ever rewrites.
- **Automatic (nightly, unattended)**: `frequency_check_job.py` runs the same backtest once a day (see
  Scheduling below) but does *not* wait for approval — for any indicator/window combination outside
  target, it searches a new threshold itself (`threshold_search.search_threshold`, same
  post-peak-side convention) and rewrites `intrahour_swing_thresholds.json` in place. The GitHub Actions
  workflow then commits that file, opens a PR, and merges it — see Scheduling below for exactly how and
  its limits. A Telegram report is sent every run either way, naming every one of the nine
  indicator/window combinations and whether it was left unchanged or updated (old threshold/count ->
  new threshold/count).

Installing/updating deps: `pip install -r requirements.txt` (into `./venv`).

## Architecture

**Data flow**: `data_fetcher.fetch_latest_prices()` → `main.poll_once()` → `storage.save_readings()`
→ `rules.check_*_alerts()` → `notifier.send_telegram_message()`. Both `main.py` (local, continuous)
and `poll_job.py` (cloud, one-shot) call the same `poll_once()`.

**Three different data sources, per indicator** (all defined in `config.py`):
- `dxy`, `us10y`, `gld` — yfinance (`INDICATORS` dict, generic path in `data_fetcher._fetch_yfinance_price`)
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
  (`ABS_CHANGE_ALERT_THRESHOLD`) instead of a %. Used for gold only now (flat $ threshold matters more
  than a % of a ~$4,300 price). `gold` and `inflation` are mutually exclusive between this and
  `check_pct_change_alerts` — an indicator should only be in one of the two threshold dicts.
- `check_value_change_alerts` — any change at all (`VALUE_CHANGE_ALERT_NAMES`), for indicators like
  `financial_stress` where a % threshold breaks down near zero
- `check_intrahour_swing_alerts` — absolute high-low range over three independent trailing windows,
  15/10/5 minutes (`INTRAHOUR_SWING_WINDOWS_MINUTES`), each with its own threshold
  (`INTRAHOUR_SWING_ALERT_THRESHOLD[name][window]`), computed from our own 5-min polled readings via
  `storage.get_recent_readings()`. Catches a slow climb/drop that never trips the poll-to-poll %
  check. Uses a rising-edge comparison per window (current window over threshold, the window as of one
  poll ago wasn't) so a sustained swing alerts once per window, not every 5 minutes for the rest of the
  window — so a single poll can produce up to one alert per window (up to 3 per indicator, 9 total).
  This is the mechanism for all three of gld ($1.65/$1.42/$1.08 for 15/10/5 min, dollars, see
  `docs/technical-analyst-gld-log.md`), dxy (0.102/0.084/0.064 index points, see
  `docs/technical-analyst-dxy-log.md`), and us10y (0.0140/0.0123/0.0100 yield points, see
  `docs/technical-analyst-us10y-log.md`) — there is no single 60-min window anymore; it was replaced by
  these three shorter windows so the same three price/rate indicators alert at multiple timescales.
  Current values were tuned with `frequency_test.py` (60-day lookback, the max yfinance serves for 5-min
  bars) to each land at ~60 rising-edge events/60 days, and are kept there automatically:
  `frequency_check_job.py` re-tunes any off-target value every night and
  `.github/workflows/frequency_check.yml` merges the change (see Scheduling below), so the numbers above
  are current as of the last successful nightly run, not necessarily what's in this file's git history.
  Each Telegram message states the window, direction
  (up/down), the swing size, the threshold, and the current price; the `$` vs. no-unit formatting is
  picked per-name in `rules.py`, not hardcoded.
- `check_sma_crossover` — 20/50-day SMA crossover on gold futures daily closes, no config threshold
- `check_rsi_alerts` — RSI(14) on gold spot only (`RSI_PERIOD`), computed from Twelve Data 15-min
  candles via `data_fetcher.fetch_gold_candles()`/`compute_rsi()` (Wilder's formula, the same helpers
  the dashboard's RSI panel uses). Like `check_sma_crossover`, this is a crossing check (compares the
  two most recent RSI values), not a poll-to-poll or rising-edge-window comparison, so it fires once
  when RSI crosses above `RSI_OVERBOUGHT_THRESHOLD` (70) or below `RSI_OVERSOLD_THRESHOLD` (30), not on
  every poll spent past the threshold. The Telegram message states the RSI value and which threshold it
  crossed.

**Broker automated paper-trading (`broker.py`)**: `check_broker_trades()`, called from
`main.poll_once()` right after this cycle's alerts are saved, is a fully automated imaginary
buy/sell engine layered on top of the alert mechanisms above — see `.claude/agents/broker.md`'s
"Rules" section for the human-readable spec (kept in sync with this code by hand, the same convention
as `docs/market.md` vs. `config.py`). Currently two mirror-image rules: buy 1 troy oz of gold spot when
GLD/DXY/US10Y intrahour-swing alerts (any window) land in the `alerts` table within a trailing 15
minutes in the directions GLD up/DXY down/US10Y down (sell on the exact opposite); close at $10
unrealized profit or loss either way. Trade state lives in a new Postgres `trades` table (mirrors
`readings`/`alerts` — required since `poll_job.py` is a stateless one-shot run each cloud poll, so
in-memory state can't survive between polls); only one trade open at a time, and a fresh entry only
considers alerts newer than the last trade's open time so a stale alert can't retrigger. Every
open/close sends a Telegram message (`notifier.send_telegram_message`). Deliberately no
markdown/doc log of trades — the `trades` table (`id`, `rule_name`, `trade_type`, `entry_price`,
`open_ts`, `triggering_alerts`, `exit_price`, `close_ts`, `pnl`, `status`) is the only record, so a
trade never requires a repo commit; `poll.yml` doesn't need write access to the repo for this reason.

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

**Nightly threshold audit trail (`threshold_history` table)**: same move as the two tables above —
`docs/frequency-test-thresholds.md` used to have a "Threshold history" table that
`frequency_check_job.py` appended one row to every night (the date plus that night's final value for
all nine GLD/DXY/US10Y 15/10/5-min thresholds, whether or not any changed); that now goes straight to
a `threshold_history` table in Postgres (`storage.insert_threshold_history_row()`/
`get_threshold_history()`) instead, so the nightly log entry doesn't need a repo commit — `poll.yml`
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
pattern for `frequency_check_job.py` (daily instead of every 5 min — same reasoning, plus GitHub's
`schedule:` is UTC-only with no DST handling, and cron-job.org lets the trigger be set directly in
`America/New_York`). Both workflows need their own cron-job.org job pointed at their
`workflow_dispatch` endpoint — that setup lives in the cron-job.org account, not in this repo.
`frequency_check_job.py` runs `frequency_test.py` against the live `INTRAHOUR_SWING_ALERT_THRESHOLD`
values, and for any indicator/window combination outside `FREQUENCY_TEST_TARGET +/-
FREQUENCY_TEST_TOLERANCE` searches a new threshold (`threshold_search.search_threshold`) and rewrites
`intrahour_swing_thresholds.json` with just the changed entries — see the "Automatic (nightly,
unattended)" workflow above for how this differs from an interactive session's frequency test. A
Telegram message is sent every run either way, listing all nine indicator/window combinations and
whether each was left unchanged or updated (old threshold/count -> new threshold/count).
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
context/methodology (currently just `docs/fundamental-analyst-nfp-log.md`; more will be added the same
way as other releases get their own research), the same way `technical-analyst` reads
`docs/technical-analyst-*-log.md`. It's read-only and plans-then-asks for everything **except** one
pre-approved live action: when a release just printed, its "New-release recommendation workflow" lets it
send exactly one Telegram message (`notifier.send_telegram_message()`) recommending gold's likely
5/10/15-minute move, and record the release in `nfp_reports` via `storage.insert_nfp_report()`/
`update_nfp_report_reaction()` — without stopping to ask first, since the user has already authorized
that specific pairing of actions. It still never touches any other table, edits any file, or sends
Telegram outside that one workflow; everything else (a threshold change, a new indicator, a dashboard
tweak) is a plan handed back to the user or a coding session, same as the other two subagents.
