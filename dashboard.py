"""Streamlit dashboard reading the same Postgres DB that the poll job populates."""

import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv
from plotly.subplots import make_subplots

load_dotenv()  # local runs: .env into os.environ. No-op on Streamlit Cloud (no .env there).

# Streamlit Community Cloud secrets arrive via st.secrets instead of a .env
# file — mirror them into os.environ before storage.py/this module read them
# at import time. Only reached for keys load_dotenv() above didn't already
# set, so a missing secrets.toml (e.g. any local run) never gets checked.
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


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ADX (trend strength, 0-100; conventionally >25 = trending)."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


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

    st.subheader("Chart")
    st.caption("Click a name in the legend to toggle it on/off; double-click to isolate one.")

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
        # All series overlaid on ONE plot area instead of separate panels.
        # Each gets its own y-axis (all but the first hidden) so wildly
        # different scales — gold ~4300 vs inflation ~2.3 — don't flatten
        # each other out; only the first series' axis is drawn to avoid a
        # wall of axis labels. Plotly's legend already supports click to
        # toggle a series and double-click to isolate one, so that's the
        # on/off switch, no custom widget needed.
        fig = go.Figure()
        layout_updates = {}
        for i, name in enumerate(row_names):
            axis_id = "y" if i == 0 else f"y{i + 1}"
            if name == "gold":
                fig.add_trace(
                    go.Candlestick(
                        x=candles["datetime"],
                        open=candles["open"],
                        high=candles["high"],
                        low=candles["low"],
                        close=candles["close"],
                        name="GOLD",
                        yaxis=axis_id,
                    )
                )
            else:
                series = other_series[name]
                fig.add_trace(
                    go.Scatter(
                        x=series.index,
                        y=series.values,
                        mode="lines",
                        name=name.upper(),
                        yaxis=axis_id,
                    )
                )
            if i == 0:
                layout_updates["yaxis"] = dict(autorange=True, fixedrange=False, title=name.upper())
            else:
                layout_updates[f"yaxis{i + 1}"] = dict(
                    overlaying="y", side="right", visible=False, autorange=True
                )

        fig.update_layout(
            **layout_updates,
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=0, r=0, t=40, b=0),
            height=550,
        )
        st.plotly_chart(fig, width='stretch')

    if gold_ok:
        st.subheader("Technical Indicators (Gold)")
        rsi = compute_rsi(candles["close"])
        adx = compute_adx(candles["high"], candles["low"], candles["close"])

        ta_fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            subplot_titles=["RSI (14)", "ADX (14)"],
        )
        ta_fig.add_trace(
            go.Scatter(x=candles["datetime"], y=rsi, mode="lines", name="RSI"), row=1, col=1
        )
        ta_fig.add_hline(y=70, line=dict(color="gray", dash="dash", width=1), row=1, col=1)
        ta_fig.add_hline(y=30, line=dict(color="gray", dash="dash", width=1), row=1, col=1)
        ta_fig.update_yaxes(range=[0, 100], row=1, col=1)

        ta_fig.add_trace(
            go.Scatter(x=candles["datetime"], y=adx, mode="lines", name="ADX"), row=2, col=1
        )
        ta_fig.add_hline(y=25, line=dict(color="gray", dash="dash", width=1), row=2, col=1)
        ta_fig.update_yaxes(range=[0, 100], row=2, col=1)

        ta_fig.update_layout(height=420, margin=dict(l=0, r=0, t=40, b=0), showlegend=False)
        st.plotly_chart(ta_fig, width='stretch')

st.subheader("Recent Alerts")
try:
    alerts = load_alerts()
except Exception as e:
    st.error(f"Could not load alerts: {e}")
    alerts = pd.DataFrame(columns=["ts", "message"])

if alerts.empty:
    st.write("No alerts recorded yet.")
else:
    st.dataframe(alerts, width='stretch')
