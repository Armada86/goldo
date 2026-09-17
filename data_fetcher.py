"""Pulls current prices and recent history for configured indicators."""

import os

import pandas as pd
import requests
import yfinance as yf   #Imports values from the yfinance lib of YAHOO and saves it as yf for use in this file
from dotenv import load_dotenv

from config import FRED_SERIES, GOLD_SPOT_SYMBOL, INDICATORS
from retry import with_retries

load_dotenv()

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")
FRED_API_KEY = os.environ.get("FRED_API_KEY")


@with_retries()
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


@with_retries()
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


@with_retries()
def _fetch_yfinance_price(ticker: str) -> float | None:
    data = yf.Ticker(ticker).history(period="1d", interval="1m")
    if data.empty:
        # Market closed (weekend/holiday) and no intraday bars yet;
        # fall back to the most recent daily close.
        data = yf.Ticker(ticker).history(period="5d", interval="1d")
    if data.empty:
        return None
    return float(data["Close"].iloc[-1])


def fetch_latest_prices() -> dict[str, float]: #picks up the data and formats it
    """Returns {name: last_price} for every configured indicator. A single
    indicator failing (even after retries) is logged and skipped rather than
    aborting the whole poll — the other indicators still get saved/checked."""
    prices = {}
    for name, ticker in INDICATORS.items():
        try:
            price = fetch_gold_spot_price() if name == "gold" else _fetch_yfinance_price(ticker)
        except Exception as e:
            print(f"[data_fetcher] {name} fetch failed after retries: {e}")
            continue
        if price is not None:
            prices[name] = price

    for name, series_id in FRED_SERIES.items():
        try:
            value = fetch_fred_latest(series_id)
        except Exception as e:
            print(f"[data_fetcher] {name} fetch failed after retries: {e}")
            continue
        if value is not None:
            prices[name] = value

    return prices


def fetch_daily_history(name: str, period: str = "6mo"):
    """Returns a DataFrame of daily closes for one configured indicator."""
    ticker = INDICATORS[name]
    return yf.Ticker(ticker).history(period=period, interval="1d")


@with_retries()
def fetch_gold_candles(interval: str = "15min", outputsize: int = 96) -> pd.DataFrame:
    """Real OHLC candles for gold spot, straight from Twelve Data (the point
    readings in Postgres are single prices, not bars, so they can't make
    candles on their own). Shared by the dashboard's price panel and
    rules.check_rsi_alerts, which both need a close-price series rather than
    just the latest point reading."""
    response = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": GOLD_SPOT_SYMBOL,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVE_DATA_API_KEY,
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise RuntimeError(f"Twelve Data error: {payload}")
    df = pd.DataFrame(payload["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df.sort_values("datetime")


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI — the standard formula (exponential smoothing with
    alpha=1/period), matching what most trading platforms show."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
