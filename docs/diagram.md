# Logical Diagram

High-level view of how data moves through the system. Kept deliberately summarized — see `CLAUDE.md`
for the full breakdown of each piece.

```mermaid
flowchart TD
    subgraph Sources["Data sources"]
        YF["yfinance\n(dxy, us10y, gld, iau, gldm, gdx, gdxj, ring, GC=F)"]
        TD["Twelve Data\n(gold spot + RSI candles)"]
        FRED["FRED\n(inflation, financial_stress)"]
    end

    Cron["cron-job.org\n(external scheduler)"] -->|every 5 min| Poll
    Cron -->|weekday 6am ET| FreqCheck["frequency_check_job.py"]

    Sources --> Fetcher["data_fetcher.py"]
    Fetcher --> Poll["poll_once()\n(main.py / poll_job.py)"]
    Poll --> DB[("Postgres / Neon\nreadings, alerts, trades,\nnfp_reports, threshold_history")]
    DB --> Rules["rules.py\n(6 alert checks)"]
    Rules --> Telegram["notifier.py -> Telegram"]
    Poll --> Broker["broker.py\n(paper trading)"]
    Broker --> DB
    Broker --> Telegram

    DB --> Dashboard["dashboard.py\n(Streamlit Cloud)"]

    FreqCheck --> Thresholds["intrahour_swing_thresholds.json"]
    FreqCheck --> Telegram
    Thresholds -.-> Rules
```

**Step by step:**

1. **cron-job.org -> Poll (every 5 min)** — an external cron service is the real scheduler, since
   GitHub Actions' own `schedule:` trigger proved unreliable.
2. **cron-job.org -> frequency_check_job.py (weekday mornings)** — same external-cron pattern, once a
   day on weekdays only (6 AM ET, Monday-Friday) instead of every 5 minutes.
3. **Sources -> data_fetcher.py** — yfinance, Twelve Data, and FRED are three unrelated APIs, each
   wrapped the same way (`retry.with_retries()`).
4. **data_fetcher.py -> poll_once()** — `main.py` (local, continuous loop) and `poll_job.py` (cloud,
   one-shot) both call this same function so the logic isn't duplicated.
5. **poll_once() -> Postgres** — prices are saved *before* alerts are checked, so each alert compares
   against the previous poll, not the one just saved.
6. **Postgres -> rules.py** — the six alert checks read back what was just saved plus recent history
   (e.g. the intrahour-swing windows).
7. **rules.py -> Telegram** — any rule that fires sends a message immediately via `notifier.py`.
8. **poll_once() -> broker.py** — right after alerts are saved, the automated paper-trading engine
   checks whether a recent alert should open/close a trade.
9. **broker.py -> Postgres / Telegram** — trade state is written to the `trades` table (poll runs are
   stateless, so this is the only place state persists) and every open/close is also messaged to
   Telegram.
10. **Postgres -> dashboard.py** — the Streamlit dashboard reads the same tables independently, in a
    completely separate deployment, so it never touches the poll/alert path directly.
11. **frequency_check_job.py -> intrahour_swing_thresholds.json** — each weekday morning, all
    twenty-four thresholds are recomputed fresh (the average companion swing of each indicator when
    gold itself moves $5/$10/$15) and whichever changed are rewritten in place (a PR is opened and
    merged automatically).
12. **frequency_check_job.py -> Telegram** — a report is sent every run either way, naming all
    twenty-four indicator/window combinations (GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y x 15/10/5 min) and
    whether each changed.
13. **intrahour_swing_thresholds.json -> rules.py** (dotted) — the next poll picks up whatever
    thresholds are currently on disk; this isn't a live data flow, just a config dependency.
