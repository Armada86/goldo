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
python frequency_check_job.py     # one-shot: frequency_test.py + Telegram message every run (all-clear or drift alert)
```

There is no test suite or linter configured in this repo. `frequency_test.py` is the closest thing to
one — not a correctness test, but a historical backtest against live yfinance data (see its docstring)
for tuning `INTRAHOUR_SWING_ALERT_THRESHOLD`, one indicator's worth of updates at a time (see
`docs/technical-analyst-*-log.md` for what past runs found and which thresholds they led to).

**Standing "frequency test" workflow**: when asked to run a frequency test, (1) run `frequency_test.py`
against the *current* `INTRAHOUR_SWING_ALERT_THRESHOLD` values and report each indicator's actual
event count over the last 30 days; (2) for any indicator off-target, search for a new threshold that
lands within `FREQUENCY_TEST_TARGET +/- FREQUENCY_TEST_TOLERANCE` rising-edge events (30 +/- 2 — see the
2026-09-16 entries in `docs/technical-analyst-*-log.md` for the method: the threshold-vs-event-count
curve is non-monotonic, picks the higher-threshold/post-peak side) and propose it; (3) **do not edit
`config.py` or commit anything until the user approves the suggested thresholds** — report and wait.
`frequency_check_job.py` runs this same check automatically once a day and sends a Telegram message
every run — an all-clear summary if nothing has drifted, or a drift alert naming the offenders — but
even it never changes a threshold — see Scheduling below.

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
in `docs/market.md` — update frequency and its typical relationship (same direction / opposite / mixed)
to the gold price.

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

**Five alert mechanisms in `rules.py`**, each suited to a different kind of signal:
- `check_pct_change_alerts` — % move since the *previous poll only* (`PCT_CHANGE_ALERT_THRESHOLD`) —
  inflation
- `check_abs_change_alerts` — same previous-poll comparison, but a fixed move
  (`ABS_CHANGE_ALERT_THRESHOLD`) instead of a %. Used for gold only now (flat $ threshold matters more
  than a % of a ~$4,300 price). `gold` and `inflation` are mutually exclusive between this and
  `check_pct_change_alerts` — an indicator should only be in one of the two threshold dicts.
- `check_value_change_alerts` — any change at all (`VALUE_CHANGE_ALERT_NAMES`), for indicators like
  `financial_stress` where a % threshold breaks down near zero
- `check_intrahour_swing_alerts` — absolute high-low range over the trailing 60 minutes
  (`INTRAHOUR_SWING_ALERT_THRESHOLD`), computed from our own 5-min polled readings via
  `storage.get_recent_readings()`. Catches a slow climb/drop that never trips the poll-to-poll %
  check. Uses a rising-edge comparison (current 60-min window over threshold, the window as of one
  poll ago wasn't) so a sustained swing alerts once, not every 5 minutes for the rest of the hour.
  This is the mechanism for all three of gld ($2.25, dollars, see `docs/technical-analyst-gld-log.md`),
  dxy (0.139 index points, see `docs/technical-analyst-dxy-log.md`), and us10y (0.021 yield points, see
  `docs/technical-analyst-us10y-log.md`) — us10y moved here from `check_abs_change_alerts` so all
  three price/rate indicators alert on the same hourly-window basis. Current values were tuned with
  `frequency_test.py` to each land at ~30 rising-edge events/30 days. Each Telegram message states
  direction (up/down), the swing size, the threshold, and the current price; the `$` vs. no-unit
  formatting is picked per-name in `rules.py`, not hardcoded.
- `check_sma_crossover` — 20/50-day SMA crossover on gold futures daily closes, no config threshold

**Dashboard charting (`dashboard.py`)**: gold's live price panel uses real OHLC candles from Twelve
Data's `/time_series` endpoint (`fetch_gold_candles()`, cached 5 min via `st.cache_data`) rather than
the point-in-time readings in Postgres, since those are single prices per poll, not bars. All series —
the gold candlestick, the other indicators, and the RSI(14)/ADX(14) panels (computed locally with
Wilder's formulas from the same cached candles, zero extra API cost) — live in ONE hand-built Plotly
figure, not `make_subplots`: that helper only supports one secondary y-axis per row, but the price
panel alone overlays up to 6 series on independent y-axes (gold ~4300 vs inflation ~2.3 need separate
scales). Every panel is a vertical `domain` slice of a single shared x-axis instead of a subplot row,
which is what lets panning/zooming any one panel move all of them together.

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
values and sends a Telegram message every run: an all-clear summary (with each indicator's 30-day
event count) if everything is within `FREQUENCY_TEST_TARGET +/- FREQUENCY_TEST_TOLERANCE`, or a drift
alert naming the offenders otherwise — either way it never changes a threshold itself (see the
"Standing frequency test workflow" above).

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
