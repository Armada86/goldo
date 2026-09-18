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
relationship (same direction / opposite / mixed) to the gold price, and which mechanism (if any) sends
it to Telegram.

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

**`data_fetcher.fetch_gold_candles()`/`compute_rsi()`** are shared by two callers: `rules.check_rsi_alerts()`
(uncached, called every poll) and `dashboard.py`'s own `fetch_gold_candles()` wrapper, which adds
`st.cache_data(ttl=300)` on top for the dashboard's RSI/ADX panels — the underlying Twelve Data fetch
and retry logic lives in one place either way.

**Dashboard charting (`dashboard.py`)**: gold's live price panel uses real OHLC candles from Twelve
Data's `/time_series` endpoint (`fetch_gold_candles()`, cached 5 min via `st.cache_data`) rather than
the point-in-time readings in Postgres, since those are single prices per poll, not bars. All series —
the gold candlestick, the other indicators, and the RSI(14)/ADX(14) panels (RSI shared with
`rules.check_rsi_alerts`, ADX computed locally — both Wilder's formulas from the same cached candles,
zero extra API cost) — live in ONE hand-built Plotly figure, not `make_subplots`: that helper only
supports one secondary y-axis per row, but the price panel alone overlays up to 6 series on independent
y-axes (gold ~4300 vs inflation ~2.3 need separate scales). Every panel is a vertical `domain` slice of
a single shared x-axis instead of a subplot row, which is what lets panning/zooming any one panel move
all of them together.

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
