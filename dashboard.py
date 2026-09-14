"""Streamlit dashboard reading the same Postgres DB that the poll job populates."""

import os
from datetime import datetime

import pandas as pd
import streamlit as st

# Streamlit Community Cloud secrets arrive via st.secrets, not the OS
# environment — mirror them into os.environ before storage.py reads
# DATABASE_URL at import time. Local runs already have it via .env/dotenv.
if "DATABASE_URL" not in os.environ and "DATABASE_URL" in st.secrets:
    os.environ["DATABASE_URL"] = st.secrets["DATABASE_URL"]

from config import ALL_INDICATOR_NAMES
from storage import get_connection

st.set_page_config(page_title="Goldo", layout="wide")
st.markdown('<meta http-equiv="refresh" content="60">', unsafe_allow_html=True)

st.title("Goldo")
st.caption(f"Page refreshes every 60s · last loaded {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


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

    st.subheader("Price History")
    for name in ALL_INDICATOR_NAMES:
        series = readings[readings["name"] == name].sort_values("ts").set_index("ts")["price"]
        if series.empty:
            continue
        st.caption(name.upper())
        st.line_chart(series)

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
