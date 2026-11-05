"""Dashboard page for Broker R: the half-hourly short-range technical analysis chart (ta_short.py), the day's Broker R P&L
and its trades. Same price-ladder diagram as the main page (ta_forecast_job.render_diagram_svg), with $5 ticks."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from config import R_STOP_LOSS, R_TAKE_PROFIT
from storage import get_connection, get_ta_short_forecasts_for_date
from ta_forecast_job import render_diagram_svg

DISPLAY_TZ = ZoneInfo("America/New_York")
DATE_KEY = "short_ta_date"
RUN_KEY = "short_ta_run"
RUN_DATE_KEY = "short_ta_run_date"  # the date RUN_KEY was last reset for


def _prev_weekday(d: date) -> date:
    d = date.fromordinal(d.toordinal() - 1)
    while d.weekday() > 4:
        d = date.fromordinal(d.toordinal() - 1)
    return d


def _next_weekday(d: date) -> date:
    d = date.fromordinal(d.toordinal() + 1)
    while d.weekday() > 4:
        d = date.fromordinal(d.toordinal() + 1)
    return d


def load_trades_for_date(d: date) -> pd.DataFrame:
    """Broker R trades that closed (or are still open, opened) on ET date `d`, oldest first."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('broker_r_trades')")
        if cur.fetchone()[0] is None:
            return pd.DataFrame()
        cur.execute(
            "SELECT id, rule_name, trade_type, entry_price, open_ts, exit_price, close_ts, pnl, exit_reason, ta_short_id, status "
            "FROM broker_r_trades WHERE (open_ts AT TIME ZONE 'America/New_York')::date = %s "
            "OR (close_ts AT TIME ZONE 'America/New_York')::date = %s ORDER BY open_ts",
            (d, d),
        )
        rows = cur.fetchall()
    cols = ["id", "rule", "type", "entry", "open_ts", "exit", "close_ts", "pnl", "reason", "ta_id", "status"]
    return pd.DataFrame(rows, columns=cols)


def load_latest_gold_price() -> float | None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT price FROM readings WHERE name = 'gold' ORDER BY ts DESC LIMIT 1")
        row = cur.fetchone()
    return float(row[0]) if row else None


def outcomes_for(trades: pd.DataFrame, ta_id: int) -> dict[str, dict]:
    """{scenario: {"results": [bool, ...]}} for one short-range forecast row (closed trades only, True = win), for the diagram's marks."""
    names = {"R-Fade-sell": "sell_resistance", "R-Fade-buy": "buy_support"}
    out: dict[str, dict] = {}
    if trades.empty:
        return out
    for t in trades[(trades["ta_id"] == ta_id) & (trades["status"] == "Closed")].itertuples():
        out.setdefault(names.get(t.rule, t.rule), {"results": []})["results"].append(bool(t.pnl > 0))
    return out


def _fmt_ts(ts) -> str:
    return "—" if ts is None or pd.isna(ts) else pd.Timestamp(ts).tz_convert(DISPLAY_TZ).strftime("%H:%M")


def render() -> None:
    st.subheader("Broker R · short-range technical analysis")
    st.caption(
        f"A new analysis every 30 minutes (7:00 AM–5:00 PM ET, weekdays). Levels are only \\${3:g}–\\${10:g} from price; "
        f"Broker R fades them for a strict +\\${R_TAKE_PROFIT:g} / −\\${R_STOP_LOSS:g}, no trail, no filters."
    )
    today = datetime.now(timezone.utc).astimezone(DISPLAY_TZ).date()
    while today.weekday() > 4:
        today = _prev_weekday(today)
    if DATE_KEY not in st.session_state:
        st.session_state[DATE_KEY] = today
    selected = st.session_state[DATE_KEY]

    left, mid, right = st.columns([1, 3, 1])
    with left:
        if st.button("◀", key="short_ta_prev"):
            st.session_state[DATE_KEY] = _prev_weekday(selected)
            st.rerun()
    with mid:
        picked = st.date_input("Date", value=selected, max_value=today, key=DATE_KEY, label_visibility="collapsed")
    with right:
        if st.button("▶", key="short_ta_next", disabled=selected >= today):
            st.session_state[DATE_KEY] = min(_next_weekday(selected), today)
            st.rerun()
    selected = picked

    try:
        runs = get_ta_short_forecasts_for_date(selected)
        trades = load_trades_for_date(selected)
    except Exception as e:
        st.error(f"Could not load Broker R data: {e}")
        return
    if not runs:
        st.caption("No short-range analysis recorded for this date.")
        return

    # Run picker: newest by default, reset whenever the date changes.
    labels = [f"{r['levels'].get('session', '?')} · {_fmt_ts(pd.Timestamp(r['ts']))} ET" for r in runs]
    if st.session_state.get(RUN_DATE_KEY) != selected or st.session_state.get(RUN_KEY) not in labels:
        st.session_state[RUN_KEY] = labels[-1]
        st.session_state[RUN_DATE_KEY] = selected
    choice = st.selectbox("Run", labels, key=RUN_KEY)
    run = runs[labels.index(choice)]
    levels = run["levels"] or {}

    closed = trades[trades["status"] == "Closed"] if not trades.empty else trades
    day_pnl = float(closed["pnl"].sum()) if not closed.empty else 0.0
    color = "#1a7f37" if day_pnl > 0 else "#cf222e" if day_pnl < 0 else "#888"
    st.markdown(
        f"Broker R P&L this day: <b style='color:{color}'>${day_pnl:+,.2f}</b> · {len(closed)} closed trade(s)",
        unsafe_allow_html=True,
    )

    live = load_latest_gold_price() if selected == today else None
    svg = render_diagram_svg(
        levels["price"], levels.get("resistances", []), levels.get("supports", []), levels.get("scenarios", []),
        live_price=live, scenario_outcomes=outcomes_for(trades, run["id"]), tick_step=5,
        stop_legend=f"Stop-loss (−${R_STOP_LOSS:g})",
    )
    st.markdown(f'<div class="goldo-diagram-wrap">{svg}</div>', unsafe_allow_html=True)
    with st.expander("Full analysis text"):
        st.text(run["analysis"])

    st.markdown("**Broker R trades**")
    if trades.empty:
        st.caption("No Broker R trades on this date.")
        return
    table = pd.DataFrame({
        "#": trades["id"], "rule": trades["rule"], "opened": trades["open_ts"].map(_fmt_ts), "entry": trades["entry"].map("${:,.2f}".format),
        "closed": trades["close_ts"].map(_fmt_ts),
        "exit": trades["exit"].map(lambda v: "—" if pd.isna(v) else f"${v:,.2f}"),
        "P&L": trades["pnl"].map(lambda v: "—" if pd.isna(v) else f"${v:+,.2f}"),
        "reason": trades["reason"].fillna("open"),
    })
    st.dataframe(table, hide_index=True, use_container_width=True)
