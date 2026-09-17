"""Indicators to track and the rules that trigger an alert."""

# yfinance tickers. GC=F is COMEX gold futures (used only for the daily-close
# SMA crossover trend, not the live price); DX-Y.NYB is the US Dollar Index;
# ^TNX is the 10-year Treasury yield in percent (e.g. 4.97 = 4.97%); GLD is
# the SPDR Gold Shares ETF (~1/10 oz of gold per share, minus accumulated
# expense-ratio drag — trades close to but not exactly spot, unlike the
# XAU/USD price used for the "gold" indicator above).
INDICATORS = {
    "gold": "GC=F",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "gld": "GLD",
}

# Live gold price comes from Twelve Data instead of yfinance: yfinance no
# longer serves a working spot-gold quote (XAUUSD=X/XAU=X return "not
# found"), only the GC=F futures contract, which trades at a premium/
# discount to spot. Free tier: https://twelvedata.com/pricing
GOLD_SPOT_SYMBOL = "XAU/USD"

# FRED (Federal Reserve Economic Data) series for indicators not available
# via yfinance/Twelve Data. T10YIE = 10-Year Breakeven Inflation Rate, the
# market-implied inflation expectation (TIPS yield vs nominal Treasury
# yield), updated daily. STLFSI4 = St. Louis Fed Financial Stress Index, a
# weekly composite of market stress (~0 = average, positive = more stress,
# negative = calmer than average) — oscillates around zero, so it's tracked
# but deliberately left out of PCT_CHANGE_ALERT_THRESHOLD below (a % change
# near zero is meaningless/explosive). DFF = Daily Federal Funds Rate, the
# Fed's policy rate — flat for weeks/months at a time and only moves in
# discrete steps on FOMC decision days, so like STLFSI4 it's tracked via
# VALUE_CHANGE_ALERT_NAMES below rather than a % threshold. Free API key:
# https://fredaccount.stlouisfed.org/apikeys
FRED_SERIES = {
    "inflation": "T10YIE",
    "financial_stress": "STLFSI4",
    "interest_rate": "DFF",
    # Scheduled macro releases, added for report alerting/logging rather than
    # continuous tracking. Each one is flat between releases and jumps once
    # when the new report prints, so they're wired into
    # VALUE_CHANGE_ALERT_NAMES below (same "alert on any change" mechanism
    # as interest_rate/financial_stress) rather than a %/abs poll-to-poll
    # threshold. Series IDs below are FRED's headline/most-cited vintage of
    # each report, all seasonally adjusted:
    "empire_state_manufacturing": "GACDISA066MSFRBNY",  # NY Fed Empire State Mfg Survey, general business conditions, monthly
    "retail_sales": "RSAFS",  # Advance Retail Sales: Retail Trade and Food Services, monthly
    "industrial_production": "INDPRO",  # Industrial Production: Total Index, monthly
    "capacity_utilization": "TCU",  # Capacity Utilization: Total Industry, monthly
    "housing_starts": "HOUST",  # Housing Starts: Total New Privately Owned Units, monthly
    "adp_employment": "ADPMNUSNERSA",  # ADP Total Nonfarm Private Payroll Employment, monthly
    "nonfarm_payrolls": "PAYEMS",  # All Employees, Total Nonfarm (NFP), monthly
    "unemployment_rate": "UNRATE",  # Unemployment Rate, monthly
    "initial_jobless_claims": "ICSA",  # Initial Jobless Claims (IJC), weekly
    "cpi": "CPIAUCSL",  # CPI for All Urban Consumers: All Items, monthly
    "ppi": "PPIFIS",  # PPI by Commodity: Final Demand, monthly
}

# The scheduled-macro-report subset of FRED_SERIES above — kept out of the
# dashboard for now (see DASHBOARD_INDICATOR_NAMES below): 11 more series with
# wildly different scales/frequencies would clutter the one shared price
# chart. They're still polled, logged, and alerted on same as everything else.
MACRO_REPORT_NAMES = [
    "empire_state_manufacturing",
    "retail_sales",
    "industrial_production",
    "capacity_utilization",
    "housing_starts",
    "adp_employment",
    "nonfarm_payrolls",
    "unemployment_rate",
    "initial_jobless_claims",
    "cpi",
    "ppi",
]

# Every tracked indicator name, across all data sources (yfinance, Twelve
# Data, FRED) — used by the poll loop, storage, and alerting.
ALL_INDICATOR_NAMES = list(INDICATORS) + list(FRED_SERIES)

# Subset of ALL_INDICATOR_NAMES shown on the dashboard (tiles + chart) —
# excludes MACRO_REPORT_NAMES for now, see the comment there.
DASHBOARD_INDICATOR_NAMES = [name for name in ALL_INDICATOR_NAMES if name not in MACRO_REPORT_NAMES]

# How often to poll, in minutes. yfinance has no official rate limit but
# polling faster than this risks temporary IP blocks.
POLL_INTERVAL_MINUTES = 5

# Percentage move (since previous poll) that triggers an alert.
PCT_CHANGE_ALERT_THRESHOLD = {
    "inflation": 1.0,
}

# Absolute move (since previous poll) that triggers an alert — a fixed
# amount instead of a percentage. Gold uses dollars, since a flat dollar
# threshold is more meaningful than a % of a ~$4,300 price.
ABS_CHANGE_ALERT_THRESHOLD = {
    "gold": 10.0,
}

# Indicators that alert on ANY change from the previous poll, instead of a
# percentage threshold. financial_stress oscillates around zero and updates
# weekly, so any change at all is noteworthy; interest_rate is flat between
# FOMC meetings, so any change is a rate decision, not noise. The scheduled
# macro reports below are the same shape: flat between releases, so any
# change is a new report printing, not a threshold to size.
VALUE_CHANGE_ALERT_NAMES = [
    "financial_stress",
    "interest_rate",
    "empire_state_manufacturing",
    "retail_sales",
    "industrial_production",
    "capacity_utilization",
    "housing_starts",
    "adp_employment",
    "nonfarm_payrolls",
    "unemployment_rate",
    "initial_jobless_claims",
    "cpi",
    "ppi",
]

# Absolute high-low swing over the trailing 60 minutes (from our own 5-min
# polled readings, not a separate data source) that triggers an alert —
# different from PCT_CHANGE_ALERT_THRESHOLD/ABS_CHANGE_ALERT_THRESHOLD, which
# only compare consecutive polls and would miss a slower climb/drop that
# adds up over the hour. Every tracked indicator uses this mechanism now
# (gld, dxy, us10y) so all three alert on the same hourly-window basis.
# Thresholds were tuned with frequency_test.py to each land at ~30 rising-edge
# events/30 days (target requested directly, +/- 2 tolerance) — see
# docs/technical-analyst-gld-log.md, docs/technical-analyst-dxy-log.md, and
# docs/technical-analyst-us10y-log.md for the search and the resulting counts.
INTRAHOUR_SWING_ALERT_THRESHOLD = {
    "gld": 2.25,
    "dxy": 0.139,
    "us10y": 0.021,
}

# Target rising-edge event count (over a 30-day frequency_test.py run) that
# every INTRAHOUR_SWING_ALERT_THRESHOLD value is tuned toward, and the
# tolerance frequency_check_job.py uses to decide whether an indicator has
# drifted enough to alert on Telegram — requested directly, see CLAUDE.md's
# "Standing frequency test workflow" and docs/technical-analyst-*-log.md.
FREQUENCY_TEST_TARGET = 30
FREQUENCY_TEST_TOLERANCE = 2

# Simple moving-average crossover on gold price, evaluated on daily closes.
SMA_SHORT = 20
SMA_LONG = 50

# RSI (Relative Strength Index) on gold spot only, computed from Twelve Data
# 15-min candles (see data_fetcher.fetch_gold_candles/compute_rsi — Wilder's
# formula, the standard used by most trading platforms). Alerts fire once per
# crossing into overbought/oversold territory, not on every poll spent there
# — see rules.check_rsi_alerts.
RSI_PERIOD = 14
RSI_OVERBOUGHT_THRESHOLD = 70
RSI_OVERSOLD_THRESHOLD = 30

# Postgres connection string (DATABASE_URL env var, read in storage.py) is
# what both the poll job and the dashboard read/write — no local DB file.
