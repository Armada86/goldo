"""Second dashboard page: realized P&L per broker by day, with a total row for each week."""

from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from storage import get_connection

DISPLAY_TZ = ZoneInfo("America/New_York")

# (column label, table, extra WHERE clause). Broker A stays out of the dashboard (user request, 13 Oct 2026);
# Broker F counts live demo-account trades only, like the P&L line on the main page.
BROKER_TABLES = [
    ("Broker B", "broker_b_trades", ""),
    ("Broker F", "forex_b_trades", " AND mode = 'live'"),
    ("Broker K", "broker_k_trades", ""),
]


def load_daily_pnl() -> pd.DataFrame:
    """One row per ET calendar day with a closed trade, one column per broker (days a broker didn't trade are 0)."""
    frames = []
    with get_connection() as conn, conn.cursor() as cur:
        for label, table, extra in BROKER_TABLES:
            # Tables are created by init_db() / the Worker on first use, so tolerate one not existing yet.
            cur.execute("SELECT to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute(
                f"SELECT (close_ts AT TIME ZONE 'America/New_York')::date, SUM(pnl) FROM {table} "
                f"WHERE status = 'Closed' AND close_ts IS NOT NULL{extra} GROUP BY 1"
            )
            rows = cur.fetchall()
            frames.append(pd.Series({r[0]: float(r[1] or 0) for r in rows}, name=label, dtype=float))
    if not frames:
        return pd.DataFrame(columns=[b[0] for b in BROKER_TABLES])
    df = pd.concat(frames, axis=1).fillna(0.0)
    for label, _, _ in BROKER_TABLES:
        if label not in df.columns:
            df[label] = 0.0
    df.index = pd.to_datetime(df.index)
    return df[[b[0] for b in BROKER_TABLES]].sort_index()


def _cell(value: float, bold: bool = False) -> str:
    color = "#1a7f37" if value > 0 else ("#cf222e" if value < 0 else "#888")
    weight = "700" if bold else "400"
    return f"<td style='text-align:right;color:{color};font-weight:{weight};'>${value:+,.2f}</td>"


def render() -> None:
    st.subheader("Profit / loss by week")
    st.caption("Realized P&L of trades by the day they closed (ET). Weeks run Monday to Sunday.")
    try:
        daily = load_daily_pnl()
    except Exception as e:
        st.error(f"Could not load broker P&L: {e}")
        return
    if daily.empty:
        st.caption("No closed trades yet.")
        return

    brokers = list(daily.columns)
    week_start = daily.index - pd.to_timedelta(daily.index.weekday, unit="D")
    header = "".join(f"<th style='text-align:right;'>{b}</th>" for b in brokers)
    html = [
        "<style>.goldo-weekly {width:100%;border-collapse:collapse;font-size:13px;}"
        ".goldo-weekly th,.goldo-weekly td {padding:4px 8px;border-bottom:1px solid rgba(128,128,128,.25);}"
        ".goldo-weekly tr.wk td {background:rgba(128,128,128,.15);padding-top:8px;}"
        ".goldo-weekly th {text-align:left;}</style>",
        f"<table class='goldo-weekly'><tr><th>Date</th>{header}</tr>",
    ]
    for ws in sorted(set(week_start), reverse=True):
        week = daily[week_start == ws]
        we = ws + pd.Timedelta(days=6)
        totals = week.sum()
        html.append(
            f"<tr class='wk'><td><b>Week {ws:%b %d} – {we:%b %d}</b></td>"
            + "".join(_cell(totals[b], bold=True) for b in brokers)
            + "</tr>"
        )
        for day, row in week.sort_index(ascending=False).iterrows():
            html.append(f"<tr><td>{day:%a %b %d}</td>" + "".join(_cell(row[b]) for b in brokers) + "</tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)
