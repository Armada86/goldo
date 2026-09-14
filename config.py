"""Indicators to track and the rules that trigger an alert."""

# yfinance tickers. GC=F is COMEX gold futures (used only for the daily-close
# SMA crossover trend, not the live price); DX-Y.NYB is the US Dollar Index;
# ^TNX is the 10-year Treasury yield in percent (e.g. 4.97 = 4.97%).
INDICATORS = {
    "gold": "GC=F",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
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
# near zero is meaningless/explosive). Free API key:
# https://fredaccount.stlouisfed.org/apikeys
FRED_SERIES = {
    "inflation": "T10YIE",
    "financial_stress": "STLFSI4",
}

# Every tracked indicator name, across all data sources (yfinance, Twelve
# Data, FRED) — used by the poll loop and the dashboard.
ALL_INDICATOR_NAMES = list(INDICATORS) + list(FRED_SERIES)

# How often to poll, in minutes. yfinance has no official rate limit but
# polling faster than this risks temporary IP blocks.
POLL_INTERVAL_MINUTES = 5

# Percentage move (since previous poll) that triggers an alert.
PCT_CHANGE_ALERT_THRESHOLD = {
    "gold": 0.1,
    "dxy": 0.3,
    "us10y": 1.0,
    "inflation": 1.0,
}

# Indicators that alert on ANY change from the previous poll, instead of a
# percentage threshold (used for financial_stress, which oscillates around
# zero and updates weekly — any change at all is noteworthy).
VALUE_CHANGE_ALERT_NAMES = ["financial_stress"]

# Simple moving-average crossover on gold price, evaluated on daily closes.
SMA_SHORT = 20
SMA_LONG = 50

# Postgres connection string (DATABASE_URL env var, read in storage.py) is
# what both the poll job and the dashboard read/write — no local DB file.
