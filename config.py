"""Indicators to track and the rules that trigger an alert."""

import json
from pathlib import Path

# yfinance tickers.
INDICATORS = {
    "gold": "GC=F",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "gld": "GLD",
    "iau": "IAU",
    "gldm": "GLDM",
    "sgol": "SGOL",
}

# Live gold price (Twelve Data spot quote, not yfinance).
GOLD_SPOT_SYMBOL = "XAU/USD"

# Indicators whose price/alert values are dollar-denominated (vs. index points/yield points) --
# picks the "$" unit in rules.py/frequency_test.py/frequency_check_job.py/dashboard.py alike, one
# shared list instead of repeating the same per-name check in each.
DOLLAR_UNIT_NAMES = {"gold", "gld", "iau", "gldm", "sgol"}

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

# Trailing-window sizes (minutes) checked by check_intrahour_swing_alerts.
# The old single 60-minute window has been dropped in favor of three shorter,
# independently-thresholded windows.
INTRAHOUR_SWING_WINDOWS_MINUTES = [15, 10, 5]

# Absolute high-low swing over each trailing window in
# INTRAHOUR_SWING_WINDOWS_MINUTES that triggers an alert, per indicator.
# Lives in its own JSON file (not inline here) because frequency_check_job.py
# rewrites it automatically every weekday morning when a threshold drifts off
# target -- see docs/frequency-test-thresholds.md for how, and the "Standing
# frequency test workflow" in CLAUDE.md.
INTRAHOUR_SWING_THRESHOLDS_PATH = Path(__file__).parent / "intrahour_swing_thresholds.json"

with open(INTRAHOUR_SWING_THRESHOLDS_PATH) as _f:
    INTRAHOUR_SWING_ALERT_THRESHOLD = {
        name: {int(window): value for window, value in by_window.items()}
        for name, by_window in json.load(_f).items()
    }
del _f

# Backtest lookback used by frequency_test.py -- the max yfinance allows for
# 5-min bars (see frequency_test.py's docstring).
FREQUENCY_TEST_LOOKBACK_DAYS = 60

# Target rising-edge event count (per FREQUENCY_TEST_LOOKBACK_DAYS-day
# frequency_test.py run, per indicator/window combination) and tolerance used
# to detect threshold drift. Same ~1-event/day rate as the original 30±2/30-day
# target, scaled to the new lookback.
FREQUENCY_TEST_TARGET = 60
FREQUENCY_TEST_TOLERANCE = 4

# Simple moving-average crossover on gold price, evaluated on daily closes.
SMA_SHORT = 20
SMA_LONG = 50

# RSI on gold spot only.
RSI_PERIOD = 14
RSI_OVERBOUGHT_THRESHOLD = 70
RSI_OVERSOLD_THRESHOLD = 30
