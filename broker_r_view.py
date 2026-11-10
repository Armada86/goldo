"""Broker R's data helpers and trades table for the main dashboard page (the separate "Short TA (R)" page was removed, 10 Nov 2026).
The short-range chart itself is drawn by compare_diagram.render_compare_svg() beside Broker B/F's."""

from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from storage import get_connection

DISPLAY_TZ = ZoneInfo("America/New_York")


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


def traded_levels(levels: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(resistances, supports, scenarios) reduced to the two levels Broker R actually trades (the scenarios' entries), with no
    stop-loss lines: older rows were stored with extra zones and the diagram would draw all of them."""
    scenarios = levels.get("scenarios", [])
    entry = {sc["name"]: sc["entry"]["low"] for sc in scenarios}

    def traded(zones, name, edge):
        hit = [z for z in zones if name in entry and abs(z[edge] - entry[name]) < 0.01]
        return hit or zones[:1]

    return (
        traded(levels.get("resistances", []), "sell_resistance", "low"),
        traded(levels.get("supports", []), "buy_support", "high"),
        [{k: v for k, v in sc.items() if k != "stop"} for sc in scenarios],
    )


def render_trades(d: date) -> None:
    """Broker R's trades for ET date `d`, same look as the Recent Trades table: "#" first, green (win) / red (loss)."""
    try:
        trades = load_trades_for_date(d)
    except Exception as e:
        st.caption(f"Could not load Broker R trades: {e}")
        return
    if trades.empty:
        st.caption("No Broker R trades on this date.")
        return
    # The "#" is a real data column because st.dataframe ignores Styler styling on the index.
    trade_won = [None if pd.isna(v) else (True if v > 0 else (False if v < 0 else None)) for v in trades["pnl"]]
    table = pd.DataFrame({
        "#": trades["id"],
        "P&L": trades["pnl"].map(lambda v: "—" if pd.isna(v) else f"${v:+,.2f}"),
        "rule": trades["rule"], "opened": trades["open_ts"].map(_fmt_ts), "entry": trades["entry"].map("${:,.2f}".format),
        "closed": trades["close_ts"].map(_fmt_ts),
        "exit": trades["exit"].map(lambda v: "—" if pd.isna(v) else f"${v:,.2f}"),
        "reason": trades["reason"].fillna("open"),
    })

    def _color_row_number(_col):
        return [
            "background-color: #1a7f37; color: white; font-weight: 700;" if won is True
            else "background-color: #cf222e; color: white; font-weight: 700;" if won is False
            else ""
            for won in trade_won
        ]

    st.dataframe(table.style.apply(_color_row_number, subset=["#"]), hide_index=True, use_container_width=True)
