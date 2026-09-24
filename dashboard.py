"""Streamlit dashboard reading the same Postgres DB that the poll job populates."""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()  # local runs: .env into os.environ. No-op on Streamlit Cloud (no .env there).

# Streamlit Community Cloud secrets arrive via st.secrets instead of a .env
# file — mirror them into os.environ before storage.py/this module read them
# at import time. Only reached for keys load_dotenv() above didn't already
# set, so a missing secrets.toml (e.g. any local run) never gets checked.
if "DATABASE_URL" not in os.environ and "DATABASE_URL" in st.secrets:
    os.environ["DATABASE_URL"] = st.secrets["DATABASE_URL"]

from config import DASHBOARD_INDICATOR_NAMES, DOLLAR_UNIT_NAMES
from storage import get_connection, get_latest_ta_forecast
from ta_forecast_job import render_diagram_svg

st.set_page_config(page_title="Goldo", layout="wide")
# 5 min, matching the poll interval -- a manual pull-to-refresh (native mobile browser gesture, not
# handled by this app) still reloads the page immediately regardless of this interval.
st.markdown('<meta http-equiv="refresh" content="300">', unsafe_allow_html=True)

# Compact layout/fonts so every symbol's row fits on one phone screen
# (tuned against a Samsung S24 Ultra viewport) without scrolling.
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
    .goldo-table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 9px; }
    .goldo-table th, .goldo-table td {
        padding: 2px 1px; text-align: right; overflow-wrap: break-word; line-height: 1.15;
    }
    .goldo-table th:first-child, .goldo-table td:first-child { text-align: left; }
    .goldo-table th { font-size: 8px; color: #888; font-weight: 600; }
    .goldo-table td.symbol { font-weight: 700; font-size: 10px; }
    .goldo-table td.price { font-weight: 700; font-size: 9.5px; }
    .goldo-table td.change span { display: block; }
    .goldo-table td.change span.pct { font-size: 8px; opacity: 0.85; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Readings/alerts are stored as UTC (storage.py uses datetime.now(timezone.utc))
# regardless of where the poll job or dashboard happen to run — this is the one
# place that converts to a human timezone for display.
DISPLAY_TZ = ZoneInfo("America/New_York")

st.title("Goldo")
now_local = datetime.now(timezone.utc).astimezone(DISPLAY_TZ)
st.caption(f"Page refreshes every 5 min · last loaded {now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")

# The daily XAU/USD technical forecast (ta_forecast_job.py, 7am/midday ET weekdays) -- shown at the
# very top, above the symbols table, per the user's request. Always re-rendered here via
# render_diagram_svg() from that row's stored `levels` (never the job's own cached `diagram_svg`
# column, which only ever covers the "today, no candle" case) -- one code path for both "today" and a
# browsed historical date, and it always reflects the diagram code's current look even for an old row.
FORECAST_DATE_KEY = "forecast_date_picker"


def load_forecast_for_date(d) -> dict | None:
    """The Morning forecast for a specific ET date, or None if there isn't one (a weekend/holiday, or
    a day the job didn't run) -- historical date browsing always shows the Morning run, since it's the
    one that pairs with the full day's OHLC candle; the Midday run is for same-day use only."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ts, analysis, levels FROM ta_forecasts "
            "WHERE forecast_date = %s AND levels->>'session' = 'Morning' ORDER BY ts LIMIT 1",
            (d,),
        )
        row = cur.fetchone()
    return {"ts": row[0], "analysis": row[1], "levels": row[2]} if row else None


def load_forecast_date_bounds():
    """Earliest forecast_date in ta_forecasts, for the date picker's min_value; None if the table's
    still empty (e.g. right after a fresh deploy, before the first scheduled run)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MIN(forecast_date) FROM ta_forecasts")
        row = cur.fetchone()
    return row[0] if row else None


def load_gold_day_ohlc(d):
    """Open/high/low/close for gold spot readings within one ET calendar day -- the historical-date
    candle overlay. None if there are no readings that day (before polling started, or a quiet
    weekend)."""
    start = pd.Timestamp(d, tz=DISPLAY_TZ)
    end = start + pd.Timedelta(days=1)
    with get_connection() as conn:
        df = pd.read_sql(
            "SELECT price FROM readings WHERE name = 'gold' AND ts >= %s AND ts < %s ORDER BY ts",
            conn, params=(start.tz_convert("UTC"), end.tz_convert("UTC")),
        )
    if df.empty:
        return None
    return {
        "open": float(df["price"].iloc[0]), "high": float(df["price"].max()),
        "low": float(df["price"].min()), "close": float(df["price"].iloc[-1]),
    }


try:
    min_forecast_date = load_forecast_date_bounds()
except Exception as e:
    st.error(f"Could not load the forecast date range: {e}")
    min_forecast_date = None

if min_forecast_date is not None:
    today_et = now_local.date()
    if FORECAST_DATE_KEY not in st.session_state:
        st.session_state[FORECAST_DATE_KEY] = today_et

    nav_prev, nav_date, nav_next = st.columns([1, 5, 1])
    with nav_prev:
        if st.button("◀", key="forecast_date_prev",
                     disabled=st.session_state[FORECAST_DATE_KEY] <= min_forecast_date):
            st.session_state[FORECAST_DATE_KEY] -= timedelta(days=1)
    with nav_next:
        if st.button("▶", key="forecast_date_next",
                     disabled=st.session_state[FORECAST_DATE_KEY] >= today_et):
            st.session_state[FORECAST_DATE_KEY] += timedelta(days=1)
    with nav_date:
        st.date_input(
            "Forecast date", min_value=min_forecast_date, max_value=today_et,
            key=FORECAST_DATE_KEY, label_visibility="collapsed",
        )
    selected_date = st.session_state[FORECAST_DATE_KEY]

    try:
        if selected_date == today_et:
            forecast = get_latest_ta_forecast()
            candle = None
        else:
            forecast = load_forecast_for_date(selected_date)
            candle = load_gold_day_ohlc(selected_date) if forecast else None
    except Exception as e:
        st.error(f"Could not load the forecast for {selected_date}: {e}")
        forecast, candle = None, None

    if forecast and forecast.get("levels"):
        levels = forecast["levels"] or {}
        forecast_local = forecast["ts"].astimezone(DISPLAY_TZ)
        st.subheader("Today's Forecast" if selected_date == today_et else f"Forecast — {selected_date:%b %d, %Y}")
        # "\$" everywhere here, not "$" -- st.caption() renders markdown, and Streamlit treats a pair
        # of literal $ as inline LaTeX; two or more dollar amounts in the same string (the candle line
        # below) silently mangled into math notation before this was escaped.
        caption = (
            f"{forecast_local.strftime('%Y-%m-%d %H:%M %Z')} ({levels.get('session', '?')}) · "
            f"\\${levels.get('price', 0):,.2f} · {levels.get('bias', '?')} "
            f"(score {levels.get('bias_score', 0):+d}/6)"
        )
        if candle:
            caption += (
                f" · Day: O \\${candle['open']:,.2f} H \\${candle['high']:,.2f} "
                f"L \\${candle['low']:,.2f} C \\${candle['close']:,.2f}"
            )
        st.caption(caption)
        st.markdown(
            render_diagram_svg(
                levels["price"], levels["resistances"], levels["supports"], levels["scenarios"], candle
            ),
            unsafe_allow_html=True,
        )
        with st.expander("Full forecast text"):
            st.text(forecast["analysis"])
    elif selected_date != today_et:
        st.caption(f"No forecast recorded for {selected_date:%Y-%m-%d} (weekend, holiday, or a day the job didn't run).")

# (name, minutes) columns shown next to each symbol's current price.
CHANGE_WINDOWS = [("5m", 5), ("10m", 10), ("15m", 15), ("30m", 30), ("1h", 60)]


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


def load_trades() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql(
            "SELECT rule_name, trade_type, entry_price, open_ts, triggering_alerts, "
            "exit_price, close_ts, pnl, status FROM trades ORDER BY open_ts DESC LIMIT 20",
            conn,
            parse_dates=["open_ts", "close_ts"],
        )


def to_display_str(ts: pd.Series) -> pd.Series:
    """UTC (or tz-naive, just in case) timestamp column -> DISPLAY_TZ string; NaT stays NaN."""
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    return ts.dt.tz_convert(DISPLAY_TZ).dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def change_over(series: pd.DataFrame, minutes: int) -> tuple[float, float] | None:
    """(delta, pct) between the latest reading and the last reading at or
    before `minutes` ago, or None if there's no reading that far back yet."""
    latest_ts = series["ts"].iloc[-1]
    latest_price = series["price"].iloc[-1]
    past = series[series["ts"] <= latest_ts - pd.Timedelta(minutes=minutes)]
    if past.empty:
        return None
    past_price = past["price"].iloc[-1]
    if past_price == 0:
        return None
    delta = latest_price - past_price
    return delta, delta / past_price * 100


def format_change_cell(change: tuple[float, float] | None, unit: str) -> str:
    if change is None:
        return '<td class="change">—</td>'
    delta, pct = change
    color = "#1a7f37" if delta > 0 else ("#cf222e" if delta < 0 else "#888")
    return (
        f'<td class="change" style="color:{color}">'
        f'<span>{unit}{delta:+.2f}</span><span class="pct">{pct:+.1f}%</span></td>'
    )


try:
    readings = load_readings()
except Exception as e:
    st.error(f"Could not load readings: {e}")
    readings = pd.DataFrame(columns=["ts", "name", "price"])

if readings.empty:
    st.info("No data yet — start main.py to begin polling.")
else:
    header_cells = "".join(f"<th>{label}</th>" for label, _ in CHANGE_WINDOWS)
    rows_html = []
    for name in DASHBOARD_INDICATOR_NAMES:
        series = readings[readings["name"] == name].sort_values("ts")
        if series.empty:
            continue
        unit = "$" if name in DOLLAR_UNIT_NAMES else ""
        latest = series["price"].iloc[-1]
        cells = "".join(
            format_change_cell(change_over(series, minutes), unit) for _, minutes in CHANGE_WINDOWS
        )
        rows_html.append(
            f'<tr><td class="symbol">{name.upper()}</td>'
            f'<td class="price">{unit}{latest:,.2f}</td>{cells}</tr>'
        )

    colgroup = (
        '<colgroup><col style="width:15%"><col style="width:17%">'
        + '<col style="width:13.6%">' * len(CHANGE_WINDOWS)
        + '</colgroup>'
    )
    table_html = (
        f'<table class="goldo-table">{colgroup}<thead><tr><th>Symbol</th><th>Price</th>'
        f'{header_cells}</tr></thead><tbody>{"".join(rows_html)}</tbody></table>'
    )
    st.markdown(table_html, unsafe_allow_html=True)

st.subheader("Recent Trades")
try:
    trades = load_trades()
except Exception as e:
    st.error(f"Could not load trades: {e}")
    trades = pd.DataFrame(
        columns=["rule_name", "trade_type", "entry_price", "open_ts", "triggering_alerts",
                 "exit_price", "close_ts", "pnl", "status"]
    )

if not trades.empty:
    trades["open_ts"] = to_display_str(trades["open_ts"])
    trades["close_ts"] = to_display_str(trades["close_ts"])
    for col in ("entry_price", "exit_price"):
        trades[col] = trades[col].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
    trades["pnl"] = trades["pnl"].map(lambda v: f"${v:+,.2f}" if pd.notna(v) else "—")
    trades["close_ts"] = trades["close_ts"].fillna("—")
    trades = trades.rename(columns={
        "rule_name": "Rule", "trade_type": "Type", "entry_price": "Entry",
        "open_ts": "Opened", "triggering_alerts": "Trigger", "exit_price": "Exit",
        "close_ts": "Closed", "pnl": "P&L", "status": "Status",
    })

if trades.empty:
    st.write("No trades recorded yet.")
else:
    st.dataframe(trades, width='stretch')

st.subheader("Recent Alerts")
try:
    alerts = load_alerts()
except Exception as e:
    st.error(f"Could not load alerts: {e}")
    alerts = pd.DataFrame(columns=["ts", "message"])

if not alerts.empty:
    alerts["ts"] = to_display_str(alerts["ts"])

if alerts.empty:
    st.write("No alerts recorded yet.")
else:
    st.dataframe(alerts, width='stretch')
