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
```

There is no test suite or linter configured in this repo.

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

**Storage is Postgres (Neon), not SQLite** — despite `market_data.db` and `streamlit.log` still sitting
in the repo root (gitignored, unused leftovers from an earlier local-SQLite version). `storage.py` and
`dashboard.py` both read `DATABASE_URL` and share the same DB across the poll job and the dashboard,
which run in two completely separate deployments.

**Scheduling**: GitHub Actions' own `schedule:` cron trigger was tried and found unreliable (confirmed
via the Actions API: it didn't fire at all for 90+ minutes on a 5-minute cron). The workflow
(`.github/workflows/poll.yml`) now only declares `workflow_dispatch`, and an external service
(cron-job.org) calls the `POST /repos/.../actions/workflows/poll.yml/dispatches` API every 5 minutes to
trigger it — this is the actual scheduler.

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
