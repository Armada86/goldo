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
from storage import get_connection
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
    /* Streamlit's st.columns() puts every column on its own line below a container-width breakpoint
       (each gets min-width: calc(100% - 24px), which wraps them via flex-wrap once they can't all
       fit) -- that's what stacked the forecast date navigator's ◀/date/▶ row on a phone screen. The
       only st.columns() call in this file is that nav row, so this override is safe file-wide; if a
       second one is ever added elsewhere, scope this instead of dropping it. */
    div[data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; }
    div[data-testid="stHorizontalBlock"] div[data-testid="stColumn"] {
        min-width: 0 !important; width: auto !important;
    }
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

# The daily XAU/USD technical forecast (ta_forecast_job.py, 12am/midday ET weekdays) -- shown at the
# very top, above the symbols table, per the user's request. Always re-rendered here via
# render_diagram_svg() from that row's stored `levels` (never the job's own cached `diagram_svg`
# column, which only ever covers the "today, no candle" case) -- one code path for both "today" and a
# browsed historical date, and it always reflects the diagram code's current look even for an old row.
FORECAST_DATE_KEY = "forecast_date_picker"
FORECAST_SESSION_KEY = "forecast_session_picker"
SESSIONS = ["Morning", "Midday"]


def load_forecast_sessions_for_date(d) -> list[str]:
    """Which of Morning/Midday have a ta_forecasts row for this ET date, in that order -- gates the
    session navigator's arrows and picks the fallback session when the currently selected one isn't
    available for a newly selected date. A row with no `session` key at all (the very first-ever
    forecast row, 23 Sep 2026 ~9:23am ET, predates the Morning/Midday split added later that same day)
    counts as Morning, same as `load_forecast_for_date_session()` below."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT COALESCE(levels->>'session', 'Morning') FROM ta_forecasts WHERE forecast_date = %s",
            (d,),
        )
        found = {r[0] for r in cur.fetchall()}
    return [s for s in SESSIONS if s in found]


def load_forecast_for_date_session(d, session: str) -> dict | None:
    """The forecast for a specific ET date *and* session (Morning/Midday), or None if there isn't one.
    `levels->>'session' IS NULL` counts as Morning: see `load_forecast_sessions_for_date()`'s docstring
    for why."""
    with get_connection() as conn, conn.cursor() as cur:
        if session == "Morning":
            cur.execute(
                "SELECT ts, analysis, levels FROM ta_forecasts WHERE forecast_date = %s "
                "AND (levels->>'session' = 'Morning' OR levels->>'session' IS NULL) ORDER BY ts LIMIT 1",
                (d,),
            )
        else:
            cur.execute(
                "SELECT ts, analysis, levels FROM ta_forecasts WHERE forecast_date = %s "
                "AND levels->>'session' = %s ORDER BY ts LIMIT 1",
                (d, session),
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


def load_latest_gold_price() -> float | None:
    """The single most recent `gold` reading's price -- the diagram's live-price dot for today's
    forecast, distinct from `levels['price']`, which is frozen at whenever the forecast itself ran and
    can be hours stale by the time the dashboard is viewed. None if there are no gold readings yet."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT price FROM readings WHERE name = 'gold' ORDER BY ts DESC LIMIT 1")
        row = cur.fetchone()
    return float(row[0]) if row else None


def load_broker_pnl_for_date(d) -> dict:
    """Broker A (`trades`) + Broker B (`broker_b_trades`) realized P&L for trades that *closed* within
    one ET calendar day -- a trade opened the day before but closed today counts as today's, matching
    how a daily P&L total is normally read. Summed separately per engine plus combined, for the
    top-of-dashboard daily total tied to the same selected date as the forecast navigator below."""
    start = pd.Timestamp(d, tz=DISPLAY_TZ)
    end = start + pd.Timedelta(days=1)
    start_utc, end_utc = start.tz_convert("UTC"), end.tz_convert("UTC")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status = 'Closed' "
            "AND close_ts >= %s AND close_ts < %s",
            (start_utc, end_utc),
        )
        broker_a = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM broker_b_trades WHERE status = 'Closed' "
            "AND close_ts >= %s AND close_ts < %s",
            (start_utc, end_utc),
        )
        broker_b = cur.fetchone()[0]
    return {"broker_a": float(broker_a), "broker_b": float(broker_b), "total": float(broker_a) + float(broker_b)}


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

    # A second ◀/▶ row lets Morning/Midday be picked independently of the date -- without it, once a
    # Midday run prints, the Morning run for that same date becomes unreachable (the original bug
    # report). Falls back to the latest available session for the date whenever the currently selected
    # one doesn't exist there (first load, or after navigating to a date lacking it) -- matches what
    # this section used to show by default (the single most recent row) -- but otherwise leaves a
    # manually chosen session alone across reruns.
    try:
        available_sessions = load_forecast_sessions_for_date(selected_date)
    except Exception as e:
        st.error(f"Could not load available sessions for {selected_date}: {e}")
        available_sessions = []
    if FORECAST_SESSION_KEY not in st.session_state or st.session_state[FORECAST_SESSION_KEY] not in available_sessions:
        st.session_state[FORECAST_SESSION_KEY] = available_sessions[-1] if available_sessions else "Morning"

    sess_prev, sess_label, sess_next = st.columns([1, 5, 1])
    cur_idx = SESSIONS.index(st.session_state[FORECAST_SESSION_KEY])
    with sess_prev:
        if st.button("◀", key="forecast_session_prev",
                     disabled=cur_idx <= 0 or SESSIONS[cur_idx - 1] not in available_sessions):
            st.session_state[FORECAST_SESSION_KEY] = SESSIONS[cur_idx - 1]
    with sess_next:
        if st.button("▶", key="forecast_session_next",
                     disabled=cur_idx >= len(SESSIONS) - 1 or SESSIONS[cur_idx + 1] not in available_sessions):
            st.session_state[FORECAST_SESSION_KEY] = SESSIONS[cur_idx + 1]
    with sess_label:
        st.markdown(
            f"<div style='text-align:center;padding-top:6px;font-size:13px;color:#444;'>"
            f"{st.session_state[FORECAST_SESSION_KEY]}</div>",
            unsafe_allow_html=True,
        )
    selected_session = st.session_state[FORECAST_SESSION_KEY]

    try:
        pnl = load_broker_pnl_for_date(selected_date)
    except Exception as e:
        st.error(f"Could not load broker P&L for {selected_date}: {e}")
        pnl = None
    if pnl is not None:
        total_color = "#1a7f37" if pnl["total"] > 0 else ("#cf222e" if pnl["total"] < 0 else "#888")
        # Plain "$" here, not "\$" -- unlike a plain-text st.caption()/st.markdown() string (see the
        # LaTeX gotcha noted below), this whole line is one HTML block (starts with "<div"), which
        # CommonMark passes through verbatim rather than scanning for a $-pair to treat as math.
        st.markdown(
            f"<div style='font-size:12px;color:#444;'>Broker P&amp;L ({selected_date:%b %d}): "
            f"Broker A <b>${pnl['broker_a']:+,.2f}</b> · Broker B <b>${pnl['broker_b']:+,.2f}</b> · "
            f"Total <b style='color:{total_color}'>${pnl['total']:+,.2f}</b></div>",
            unsafe_allow_html=True,
        )

    try:
        forecast = load_forecast_for_date_session(selected_date, selected_session)
        if forecast and selected_date != today_et:
            candle, live_price = load_gold_day_ohlc(selected_date), None
        else:
            candle, live_price = None, (load_latest_gold_price() if forecast else None)
    except Exception as e:
        st.error(f"Could not load the forecast for {selected_date}: {e}")
        forecast, candle, live_price = None, None, None

    if forecast and forecast.get("levels"):
        levels = forecast["levels"] or {}
        forecast_local = forecast["ts"].astimezone(DISPLAY_TZ)
        st.subheader("Today's Forecast" if selected_date == today_et else f"Forecast — {selected_date:%b %d, %Y}")
        # "\$" everywhere here, not "$" -- st.caption() renders markdown, and Streamlit treats a pair
        # of literal $ as inline LaTeX; two or more dollar amounts in the same string (the candle line
        # below) silently mangled into math notation before this was escaped.
        caption = (
            f"{forecast_local.strftime('%Y-%m-%d %H:%M %Z')} ({levels.get('session') or 'Morning'}) · "
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
                levels["price"], levels["resistances"], levels["supports"], levels["scenarios"],
                candle, live_price,
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
