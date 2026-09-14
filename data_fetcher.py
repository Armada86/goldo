"""Pulls current prices and recent history for configured indicators."""

import os

import requests
import yfinance as yf   #Imports values from the yfinance lib of YAHOO and saves it as yf for use in this file
from dotenv import load_dotenv

from config import FRED_SERIES, GOLD_SPOT_SYMBOL, INDICATORS

load_dotenv()

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
FRED_API_KEY = os.environ.get("FRED_API_KEY")


def fetch_gold_spot_price() -> float | None:
    """Live XAU/USD spot price from Twelve Data (yfinance has no working spot symbol)."""
    if not TWELVE_DATA_API_KEY:
        print("[data_fetcher] TWELVE_DATA_API_KEY not set, skipping spot gold")
        return None
    response = requests.get(
        "https://api.twelvedata.com/price",
        params={"symbol": GOLD_SPOT_SYMBOL, "apikey": TWELVE_DATA_API_KEY},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if "price" not in payload:
        print(f"[data_fetcher] Twelve Data error: {payload}")
        return None
    return float(payload["price"])


def fetch_fred_latest(series_id: str) -> float | None:
    """Most recent published value for a FRED series (e.g. T10YIE)."""
    if not FRED_API_KEY:
        print("[data_fetcher] FRED_API_KEY not set, skipping", series_id)
        return None
    response = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        },
        timeout=10,
    )
    response.raise_for_status()
    observations = response.json().get("observations", [])
    for obs in observations:
        # FRED uses "." for days with no published value (holidays etc).
        if obs["value"] != ".":
            return float(obs["value"])
    return None


def fetch_latest_prices() -> dict[str, float]: #picks up the data and formats it
    """Returns {name: last_price} for every configured indicator."""
    prices = {}
    for name, ticker in INDICATORS.items():
        if name == "gold":
            price = fetch_gold_spot_price()
            if price is not None:
                prices[name] = price
            continue
        data = yf.Ticker(ticker).history(period="1d", interval="1m")
        if data.empty:
            # Market closed (weekend/holiday) and no intraday bars yet;
            # fall back to the most recent daily close.
            data = yf.Ticker(ticker).history(period="5d", interval="1d")
        if data.empty:
            continue
        prices[name] = float(data["Close"].iloc[-1])

    for name, series_id in FRED_SERIES.items():
        value = fetch_fred_latest(series_id)
        if value is not None:
            prices[name] = value

    return prices


def fetch_daily_history(name: str, period: str = "6mo"):
    """Returns a DataFrame of daily closes for one configured indicator."""
    ticker = INDICATORS[name]
    return yf.Ticker(ticker).history(period=period, interval="1d")
