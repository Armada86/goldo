"""Indicators to track and the rules that trigger an alert."""

import json
from datetime import time
from pathlib import Path

# yfinance tickers.
INDICATORS = {
    "gold": "GC=F",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "gld": "GLD",
    "iau": "IAU",
    "gldm": "GLDM",
    "gdx": "GDX",
    "gdxj": "GDXJ",
    "ring": "RING",
}

# Live gold price (Twelve Data spot quote, not yfinance).
GOLD_SPOT_SYMBOL = "XAU/USD"

# Indicators whose price/alert values are dollar-denominated (vs. index points/yield points) --
# picks the "$" unit in rules.py/frequency_test.py/frequency_check_job.py/dashboard.py alike, one
# shared list instead of repeating the same per-name check in each.
DOLLAR_UNIT_NAMES = {"gold", "gld", "iau", "gldm", "gdx", "gdxj", "ring"}

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
    "gold": 5.0,
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
# INTRAHOUR_SWING_WINDOWS_MINUTES that triggers an alert, per indicator. Lives in its own JSON
# file (not inline here) because frequency_check_job.py rewrites it automatically every
# weekday morning -- see docs/frequency-test-thresholds.md for the companion-swing
# methodology, and the "Standing frequency test workflow" in CLAUDE.md.
INTRAHOUR_SWING_THRESHOLDS_PATH = Path(__file__).parent / "intrahour_swing_thresholds.json"

with open(INTRAHOUR_SWING_THRESHOLDS_PATH) as _f:
    INTRAHOUR_SWING_ALERT_THRESHOLD = {
        name: {int(window): value for window, value in by_window.items()}
        for name, by_window in json.load(_f).items()
    }
del _f

# Dollar move gold spot itself must swing, within the matching window, to count as a "gold
# event" for frequency_test.py's companion-swing study -- $5 in 5 min, $10 in 10 min, $15 in
# 15 min. Each of the eight indicators' thresholds is then the average of what that indicator
# was doing, in that same window, at every one of those events (see frequency_test.py).
GOLD_SWING_THRESHOLDS = {5: 5.0, 10: 10.0, 15: 15.0}

# How far back frequency_test.py looks (rolling window, recomputed fresh each run -- not a
# fixed historical range). Twelve Data can paginate true 1-minute bars back this far for gold
# and the six gold ETFs; yfinance cannot (its 1-minute bars are capped at ~7-8 days), which is
# why dxy/us10y -- the two indicators frequency_test.py still sources from yfinance, at 5-min
# resolution -- are the coarser-grained pair. See docs/data-sources.md.
FREQUENCY_TEST_LOOKBACK_DAYS = 30

# Trading hours shared by all eight intrahour-swing indicators (gld/iau/gldm/gdx/gdxj/ring/
# dxy/us10y), America/New_York, weekdays only. frequency_test.py only counts a gold event (and
# the companion swings measured against it) when the *entire* window falls inside this range,
# so every indicator actually has a chance to have moved. The tightest constraint is us10y
# (^TNX only quotes ~8:20am-2:55pm ET); the six equity ETFs trade 9:30am-4pm ET; dxy is
# near-24hr. See docs/data-sources.md for how these hours were determined.
COMMON_SESSION_START_ET = time(9, 30)
COMMON_SESSION_END_ET = time(14, 55)

# Simple moving-average crossover on gold price, evaluated on daily closes.
SMA_SHORT = 20
SMA_LONG = 50

# RSI on gold spot only.
RSI_PERIOD = 14
RSI_OVERBOUGHT_THRESHOLD = 70
RSI_OVERSOLD_THRESHOLD = 30
