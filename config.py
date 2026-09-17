"""Indicators to track and the rules that trigger an alert."""

# yfinance tickers.
INDICATORS = {
    "gold": "GC=F",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "gld": "GLD",
}

# Live gold price (Twelve Data spot quote, not yfinance).
GOLD_SPOT_SYMBOL = "XAU/USD"

# FRED series.
FRED_SERIES = {
    "inflation": "T10YIE",
    "financial_stress": "STLFSI4",
    "interest_rate": "DFF",
    # Scheduled macro releases (flat between releases, jumps once per print).
    "empire_state_manufacturing": "GACDISA066MSFRBNY",
    "retail_sales": "RSAFS",
    "industrial_production": "INDPRO",
    "capacity_utilization": "TCU",
    "housing_starts": "HOUST",
    "adp_employment": "ADPMNUSNERSA",
    "nonfarm_payrolls": "PAYEMS",
    "unemployment_rate": "UNRATE",
    "initial_jobless_claims": "ICSA",
    "cpi": "CPIAUCSL",
    "ppi": "PPIFIS",
}

# Scheduled-macro-report subset of FRED_SERIES — excluded from the dashboard.
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

# Every tracked indicator name (used by the poll loop, storage, and alerting).
ALL_INDICATOR_NAMES = list(INDICATORS) + list(FRED_SERIES)

# Indicators shown on the dashboard (tiles + chart).
DASHBOARD_INDICATOR_NAMES = [name for name in ALL_INDICATOR_NAMES if name not in MACRO_REPORT_NAMES]

# Poll interval, in minutes.
POLL_INTERVAL_MINUTES = 5

# Percentage move (since previous poll) that triggers an alert.
PCT_CHANGE_ALERT_THRESHOLD = {
    "inflation": 1.0,
}

# Absolute move (since previous poll) that triggers an alert.
ABS_CHANGE_ALERT_THRESHOLD = {
    "gold": 10.0,
}

# Indicators that alert on any change from the previous poll.
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

# Absolute high-low swing over the trailing 60 minutes that triggers an alert.
INTRAHOUR_SWING_ALERT_THRESHOLD = {
    "gld": 2.25,
    "dxy": 0.139,
    "us10y": 0.021,
}

# Target rising-edge event count (per 30-day frequency_test.py run) and
# tolerance used to detect threshold drift.
FREQUENCY_TEST_TARGET = 30
FREQUENCY_TEST_TOLERANCE = 2

# Simple moving-average crossover on gold price, evaluated on daily closes.
SMA_SHORT = 20
SMA_LONG = 50

# RSI on gold spot only.
RSI_PERIOD = 14
RSI_OVERBOUGHT_THRESHOLD = 70
RSI_OVERSOLD_THRESHOLD = 30
