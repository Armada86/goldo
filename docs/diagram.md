# Logical Diagram

High-level view of how data moves through the system. Kept deliberately summarized — see `CLAUDE.md`
for the full breakdown of each piece.

```mermaid
flowchart TD
    subgraph Sources["Data sources"]
        YF["yfinance\n(dxy, us10y, gld, GC=F)"]
        TD["Twelve Data\n(gold spot + RSI candles)"]
        FRED["FRED\n(inflation, financial_stress)"]
    end

    Cron["cron-job.org\n(external scheduler)"] -->|every 5 min| Poll
    Cron -->|nightly| FreqCheck["frequency_check_job.py"]

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

**Read it as:** an external cron service drives polling since GitHub Actions' own `schedule:` trigger
proved unreliable; each poll fetches prices from three different sources, saves them to Postgres, runs
them through the alert rules, and fans out to Telegram (direct alerts) and the broker (automated paper
trades). The dashboard reads the same Postgres tables independently. Nightly, a separate job backtests
and re-tunes the intrahour-swing thresholds those rules use.
