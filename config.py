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
    "inflation": 1.0,
}

# Absolute move (since previous poll) that triggers an alert — a fixed
# amount instead of a percentage. Gold uses dollars, since a flat dollar
# threshold is more meaningful than a % of a ~$4,300 price. us10y uses
# yield points (^TNX is quoted in percent, e.g. 4.97 = 4.97%).
ABS_CHANGE_ALERT_THRESHOLD = {
    "gold": 10.0,
    "us10y": 0.02,
}

# Indicators that alert on ANY change from the previous poll, instead of a
# percentage threshold (used for financial_stress, which oscillates around
# zero and updates weekly — any change at all is noteworthy).
VALUE_CHANGE_ALERT_NAMES = ["financial_stress"]

# Absolute high-low swing over the trailing 60 minutes (from our own 5-min
# polled readings, not a separate data source) that triggers an alert —
# different from PCT_CHANGE_ALERT_THRESHOLD/ABS_CHANGE_ALERT_THRESHOLD, which
# only compare consecutive polls and would miss a slower climb/drop that
# adds up over the hour. $3 was chosen from historical analysis: GLD swung
# >$3 within an hour ~21 times over a 30-day sample (mostly right at market
# open), >$5 only 3 times. dxy's 0.2-point threshold was requested directly
# (see docs/technical-analyst-gld-log.md for the supporting frequency
# analysis: ~13 hourly events/30 days at 0.2 pts, vs. 6 at 0.3 pts).
INTRAHOUR_SWING_ALERT_THRESHOLD = {
    "gld": 3.0,
    "dxy": 0.2,
}

# Simple moving-average crossover on gold price, evaluated on daily closes.
SMA_SHORT = 20
SMA_LONG = 50

# Postgres connection string (DATABASE_URL env var, read in storage.py) is
# what both the poll job and the dashboard read/write — no local DB file.
