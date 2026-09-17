"""Streamlit dashboard reading the same Postgres DB that the poll job populates."""

import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # local runs: .env into os.environ. No-op on Streamlit Cloud (no .env there).

# Streamlit Community Cloud secrets arrive via st.secrets instead of a .env
# file — mirror them into os.environ before storage.py/this module read them
# at import time. Only reached for keys load_dotenv() above didn't already
# set, so a missing secrets.toml (e.g. any local run) never gets checked.
for _key in ("DATABASE_URL", "TWELVE_DATA_API_KEY"):
    if _key not in os.environ and _key in st.secrets:
        os.environ[_key] = st.secrets[_key]

from config import ALL_INDICATOR_NAMES
from data_fetcher import compute_rsi
from data_fetcher import fetch_gold_candles as _fetch_gold_candles
from storage import get_connection

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")

st.set_page_config(page_title="Goldo", layout="wide")
st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)

st.title("Goldo")
st.caption(f"Page refreshes every 60s · last loaded {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


@st.cache_data(ttl=300)  # matches the 5-min poll interval; keeps Twelve Data usage bounded
def fetch_gold_candles(interval: str = "15min", outputsize: int = 96) -> pd.DataFrame:
    """Real OHLC candles for gold spot — fetch/retry logic lives in
    data_fetcher (shared with rules.check_rsi_alerts), this just adds
    Streamlit's caching on top."""
    return _fetch_gold_candles(interval, outputsize)


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
    st.caption(
        "Click a name in the legend to toggle it on/off; double-click to isolate one. "
        "Pan/zoom on any panel moves all of them together."
    )

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
        # Everything below — the overlaid price panel and the RSI/ADX
        # panels — lives on ONE shared x-axis (time), so panning or
        # zooming anywhere moves all of them together. This can't use
        # plotly's make_subplots helper: that only gives one secondary
        # y-axis per row, but the price panel alone overlays up to 6 series
        # (gold ~4300 vs inflation ~2.3 need independent y-axes so they
        # don't flatten each other out) — so the layout is built by hand:
        # every axis anchors to the same default x-axis, and each panel is
        # just a vertical `domain` slice of the figure instead of a
        # separate subplot row.
        has_ta = gold_ok  # RSI/ADX need the same OHLC candles as the price panel
        price_domain = [0.44, 1.0] if has_ta else [0.0, 1.0]

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
            axis_key = "yaxis" if i == 0 else f"yaxis{i + 1}"
            if i == 0:
                layout_updates[axis_key] = dict(
                    autorange=True, fixedrange=False, title=name.upper(), domain=price_domain
                )
            else:
                layout_updates[axis_key] = dict(
                    overlaying="y", side="right", visible=False, autorange=True, domain=price_domain
                )

        shapes = []
        if has_ta:
            rsi_domain = [0.24, 0.40]
            adx_domain = [0.0, 0.20]
            next_axis_num = len(row_names) + 1  # continue numbering past the price panel's axes
            # Two different naming conventions in Plotly: a trace's `yaxis`
            # and a shape's `yref` both use the short form ("y7"), but the
            # layout dict key for that same axis is the long form ("yaxis7").
            rsi_yref, adx_yref = f"y{next_axis_num}", f"y{next_axis_num + 1}"
            rsi_axis_key, adx_axis_key = f"yaxis{next_axis_num}", f"yaxis{next_axis_num + 1}"

            rsi = compute_rsi(candles["close"])
            adx = compute_adx(candles["high"], candles["low"], candles["close"])

            fig.add_trace(
                go.Scatter(
                    x=candles["datetime"], y=rsi, mode="lines", name="RSI",
                    yaxis=rsi_yref, showlegend=False,
                )
            )
            layout_updates[rsi_axis_key] = dict(range=[0, 100], domain=rsi_domain, anchor="x")
            for level in (70, 30):
                shapes.append(
                    dict(type="line", xref="paper", x0=0, x1=1, yref=rsi_yref,
                         y0=level, y1=level, line=dict(color="gray", dash="dash", width=1))
                )

            fig.add_trace(
                go.Scatter(
                    x=candles["datetime"], y=adx, mode="lines", name="ADX",
                    yaxis=adx_yref, showlegend=False,
                )
            )
            layout_updates[adx_axis_key] = dict(range=[0, 100], domain=adx_domain, anchor="x")
            shapes.append(
                dict(type="line", xref="paper", x0=0, x1=1, yref=adx_yref,
                     y0=25, y1=25, line=dict(color="gray", dash="dash", width=1))
            )

            annotations = [
                dict(text="RSI (14)", xref="paper", yref="paper", x=0, y=rsi_domain[1],
                     showarrow=False, xanchor="left", yanchor="bottom", font=dict(size=12)),
                dict(text="ADX (14)", xref="paper", yref="paper", x=0, y=adx_domain[1],
                     showarrow=False, xanchor="left", yanchor="bottom", font=dict(size=12)),
            ]
        else:
            annotations = []

        fig.update_layout(
            **layout_updates,
            xaxis=dict(rangeslider_visible=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(l=0, r=0, t=40, b=0),
            height=850 if has_ta else 550,
            shapes=shapes,
            annotations=annotations,
        )
        st.plotly_chart(fig, width='stretch')

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
