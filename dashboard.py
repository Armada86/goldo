"""Streamlit dashboard reading the same Postgres DB that the poll job populates."""

import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

# Streamlit Community Cloud secrets arrive via st.secrets, not the OS
# environment — mirror them into os.environ before storage.py/this module
# read them at import time. Local runs already have these via .env/dotenv.
for _key in ("DATABASE_URL", "TWELVE_DATA_API_KEY"):
    if _key not in os.environ and _key in st.secrets:
        os.environ[_key] = st.secrets[_key]

from config import ALL_INDICATOR_NAMES, GOLD_SPOT_SYMBOL
from retry import with_retries
from storage import get_connection

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")

st.set_page_config(page_title="Goldo", layout="wide")
st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)

st.title("Goldo")
st.caption(f"Page refreshes every 60s · last loaded {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


@st.cache_data(ttl=300)  # matches the 5-min poll interval; keeps Twelve Data usage bounded
@with_retries()
def fetch_gold_candles(interval: str = "15min", outputsize: int = 96) -> pd.DataFrame:
    """Real OHLC candles for gold spot, straight from Twelve Data (the point
    readings in Postgres are single prices, not bars, so they can't make
    candles on their own)."""
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


def load_readings() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT ts, name, price FROM readings ORDER BY ts", conn, parse_dates=["ts"]
        )


def load_alerts() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT ts, message FROM alerts ORDER BY ts DESC LIMIT 20",
            conn,
            parse_dates=["ts"],
        )


try:
    readings = load_readings()
except Exception as e:
    st.error(f"Could not load readings: {e}")
    readings = pd.DataFrame(columns=["ts", "name", "price"])

if readings.empty:
    st.info("No data yet — start main.py to begin polling.")
else:
    cols = st.columns(len(ALL_INDICATOR_NAMES))
    for col, name in zip(cols, ALL_INDICATOR_NAMES):
        series = readings[readings["name"] == name].sort_values("ts")
        if series.empty:
            continue
        latest = series["price"].iloc[-1]
        delta = series["price"].iloc[-1] - series["price"].iloc[-2] if len(series) > 1 else None
        col.metric(name.upper(), f"{latest:,.2f}", f"{delta:+.2f}" if delta is not None else None)

    st.subheader("Charts")

    other_names = [name for name in ALL_INDICATOR_NAMES if name != "gold"]
    other_series = {}
    for name in other_names:
        series = readings[readings["name"] == name].sort_values("ts").set_index("ts")["price"]
        if not series.empty:
            other_series[name] = series

    try:
        candles = fetch_gold_candles()
        gold_ok = True
    except Exception as e:
        st.error(f"Could not load gold candles: {e}")
        gold_ok = False

    row_names = (["gold"] if gold_ok else []) + list(other_series)
    if row_names:
        # One combined figure with a shared/linked x-axis (time) across every
        # row, so panning or zooming any panel — gold included — moves them
        # all together, the same way a real trading terminal syncs a price
        # chart with the indicator panels stacked under it. Each row keeps
        # its own independent, auto-ranging y-axis.
        row_heights = [0.45 if name == "gold" else 0.55 / len(other_series) for name in row_names]
        fig = make_subplots(
            rows=len(row_names),
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=row_heights,
            subplot_titles=[name.upper() for name in row_names],
        )

        for i, name in enumerate(row_names, start=1):
            if name == "gold":
                fig.add_trace(
                    go.Candlestick(
                        x=candles["datetime"],
                        open=candles["open"],
                        high=candles["high"],
                        low=candles["low"],
                        close=candles["close"],
                        showlegend=False,
                    ),
                    row=i,
                    col=1,
                )
                fig.update_xaxes(rangeslider_visible=False, row=i, col=1)
            else:
                series = other_series[name]
                fig.add_trace(
                    go.Scatter(x=series.index, y=series.values, mode="lines", showlegend=False),
                    row=i,
                    col=1,
                )
            fig.update_yaxes(autorange=True, fixedrange=False, row=i, col=1)

        fig.update_layout(
            height=450 + 220 * len(other_series),
            margin=dict(l=0, r=0, t=30, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

st.subheader("Recent Alerts")
try:
    alerts = load_alerts()
except Exception as e:
    st.error(f"Could not load alerts: {e}")
    alerts = pd.DataFrame(columns=["ts", "message"])

if alerts.empty:
    st.write("No alerts recorded yet.")
else:
    st.dataframe(alerts, use_container_width=True)
