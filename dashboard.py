"""Streamlit dashboard reading the same Postgres DB that the poll job populates."""

import os
from datetime import date, datetime, timedelta, timezone
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

import broker
import broker_b
from block_rules import OFF_VALUES, get_block_rules
from config import (
    FADE_LOCK_TRAIL_DISTANCE,
    PROFIT_LOCK_ACTIVATION,
    PROFIT_LOCK_DISTANCE,
    FADE_NEAR_TRAIL_ACTIVATION,
    FADE_NEAR_TRAIL_DISTANCE,
    FADE_NO_TRAIL_MAX_LOCK_DISTANCE,
    FADE_STOP_LOSS_CAP,
)
from storage import get_connection, get_stop_settings_as_of, get_ta_short_forecasts_for_date
import broker_r_view
import weekly_pnl
from compare_diagram import render_compare_svg
from ta_forecast_job import render_diagram_svg

st.set_page_config(page_title="Goldo", layout="wide")
# 5 min, matching the poll interval -- a manual pull-to-refresh (native mobile browser gesture, not
# handled by this app) still reloads the page immediately regardless of this interval.
st.markdown('<meta http-equiv="refresh" content="300">', unsafe_allow_html=True)

# Compact layout/fonts so every symbol's row fits on one phone screen (tuned against a Samsung S24
# Ultra viewport) without scrolling. This is the mobile-first *default* -- unconditional, no media
# query -- so a phone viewer's experience is completely unchanged by the laptop/desktop block below.
# LAPTOP_BREAKPOINT_PX (700) is the one place that width is chosen; both here and in the media query
# below reference it via an f-string so the two can't drift apart.
LAPTOP_BREAKPOINT_PX = 700
st.markdown(
    f"""
    <style>
        /* Weekend days in the date picker's calendar popup: grayed out and unclickable. Streamlit's
           st.date_input has no per-day disable option, so this targets the popup's day cells by their
           aria-label ("Saturday, September 26, 2026"); the server-side snap to the previous weekday
           below still covers typed/keyboard input. */
        div[role="button"][aria-label^="Saturday"], div[role="button"][aria-label^="Sunday"] {{
            opacity: 0.35; pointer-events: none; cursor: not-allowed;
        }}
    .block-container {{ padding-top: 1.5rem; padding-bottom: 1rem; }}
    /* Streamlit's st.columns() puts every column on its own line below a container-width breakpoint
       (each gets min-width: calc(100% - 24px), which wraps them via flex-wrap once they can't all
       fit) -- that's what stacked the forecast date navigator's ◀/date/▶ row on a phone screen. The
       only st.columns() calls in this file are that nav row and the session-picker row below it, so
       this override is safe file-wide; if a differently-behaved one is ever added elsewhere, scope
       this instead of dropping it. */
    div[data-testid="stHorizontalBlock"] {{ flex-wrap: nowrap !important; }}
    div[data-testid="stHorizontalBlock"] div[data-testid="stColumn"] {{
        min-width: 0 !important; width: auto !important;
    }}

    /* Laptop/desktop layout: a real browser window is wide enough to read comfortably without the
       phone's tight fonts and stacked $/% change cells, so this widens things up -- but reacts to
       actual viewport width, not device type (see CLAUDE.md's "Dashboard layout" for why that's the
       deliberate, standard way to do this: the same page, the same URL, one CSS block, no separate
       "mobile site"/"desktop site"). Purely additive -- everything above still applies below this
       breakpoint, so the phone view is byte-for-byte the same as before. `.block-container`'s
       max-width keeps line lengths/row widths reasonable on an ultra-wide monitor too, rather than
       stretching a single-column table edge-to-edge just because `layout="wide"` has the room. */
    @media (min-width: {LAPTOP_BREAKPOINT_PX}px) {{
        .block-container {{ max-width: 1000px; margin: 0 auto; padding-top: 2.5rem; }}
        /* render_diagram_svg()'s SVG is `width:100%;height:auto`, so it fills whatever contains it --
           on a wide screen that means stretching (and, since the viewBox aspect ratio is preserved,
           growing just as tall as it is wide), which looks oversized next to the single-column table
           below it. Capping and centering the wrapper (dashboard.py wraps the SVG in
           .goldo-diagram-wrap specifically for this) keeps it at a comfortable reading size instead. */
        .goldo-diagram-wrap {{ max-width: 640px; margin: 0 auto; }}
    }}
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

# Top menu. The page lives in the URL (?page=weekly) so the 5-minute meta refresh above doesn't bounce the
# viewer back to the first page.
PAGES = {"dashboard": "Dashboard", "weekly": "Weekly P&L"}
_current = st.query_params.get("page", "dashboard")
if _current not in PAGES:
    _current = "dashboard"
_choice = st.radio(
    "Menu", list(PAGES), index=list(PAGES).index(_current), format_func=PAGES.get,
    horizontal=True, label_visibility="collapsed", key="menu_page",
)
if _choice != _current:
    st.query_params["page"] = _choice
    st.rerun()
if _choice == "weekly":
    weekly_pnl.render()
    st.stop()

# The daily XAU/USD technical forecast (ta_forecast_job.py, 12am/midday ET weekdays) -- shown at the
# very top, above the symbols table, per the user's request. Always re-rendered here via
# render_diagram_svg() from that row's stored `levels` (never the job's own cached `diagram_svg`
# column, which only ever covers the "today, no candle" case) -- one code path for both "today" and a
# browsed historical date, and it always reflects the diagram code's current look even for an old row.
FORECAST_DATE_KEY = "forecast_date_picker"
FORECAST_RUN_KEY = "forecast_run_picker"  # 1-based run number within the selected date (TA1, TA2, ...)
FORECAST_RUN_DATE_KEY = "forecast_run_picker_date"  # the date FORECAST_RUN_KEY was last reset for


# First ET date whose forecast runs show the Broker B entry-rules block. The DXY/ADX/RSI/ATR gates were all in
# place from the Monday 5 Oct 2026 session on; earlier sessions traded under different rules, so showing the
# current rules under them would misdescribe what Broker B actually did.
BROKER_B_RULES_SHOWN_FROM = date(2026, 10, 5)
# The fade exit rule (broker.py: capped stop, no ordinary trail before a near lock level, then the lock) went live on 9 Oct 2026 at 9:56 AM ET;
# the 9:55 AM TA2 run is the first whose levels traded under it, so a forecast run from then on shows its "Fade exit" line, earlier ones don't.
FADE_EXIT_RULE_SHOWN_FROM = datetime(2026, 10, 9, 9, 55, tzinfo=DISPLAY_TZ)
# The +$20 profit lock (config.PROFIT_LOCK_*) applies to every trade from 26 Oct 2026; earlier forecast runs don't mention it.
PROFIT_LOCK_SHOWN_FROM = datetime(2026, 10, 26, 12, 0, tzinfo=DISPLAY_TZ)


def broker_b_entry_rules_html(as_of=None) -> str:
    """Compact summary of Broker B's DXY/ADX/RSI/ATR/spike entry filters (see broker_b.py), plus the stop loss and trailing stop
    in force (stop_settings_history, set by the stop loss analysis / make SL / make trail; broker.py defaults if none). The values are the active
    block_rules row (see block_rules.py -- what the block rules analysis last set), with config.py's values for
    anything the table can't supply, so this shows what the bot is actually using. `as_of` (the forecast run's
    time) shows the rules that were in force then, so a past session isn't relabelled by a later change. Plain "$" is fine: it's one
    HTML block (see the LaTeX gotcha note on the P&L line)."""
    rules = get_block_rules(as_of)
    try:
        st = get_stop_settings_as_of(as_of) if as_of is not None else {}
    except Exception:
        st = {}
    stop = st.get("stop_loss") or broker.STOP_LOSS_THRESHOLD
    act = st.get("activation") or broker.TRAILING_STOP_ACTIVATION
    dist = st.get("distance") or broker.TRAILING_STOP_DISTANCE
    exit_text = (
        f"Stop loss &minus;${stop:g}; trailing stop starts at +${act:g} profit and follows ${dist:g} behind the best price."
    )
    if as_of is not None and as_of >= PROFIT_LOCK_SHOWN_FROM:
        exit_text += f" From +${PROFIT_LOCK_ACTIVATION:g} profit it trails ${PROFIT_LOCK_DISTANCE:g} behind the best price."

    def v(key: str) -> str:
        return "off" if rules[key] == OFF_VALUES[key] else f"{rules[key]:.4f}".rstrip("0").rstrip(".")

    def m(key: str) -> str:  # dollar amount, or "off"
        return "off" if rules[key] == OFF_VALUES[key] else f"${rules[key]:g}"

    rows = [
        ("DXY", f"Skip a Buy if DXY rose, or a Sell if it fell, by &ge; {v('dxy_threshold')} over 15 min, on 3 polls in a row."),
        (
            "ADX(14)",
            f"Fades blocked at ADX &ge; {v('adx_trending')} while ADX is still rising; breakouts blocked at ADX &lt; {v('adx_chop')}.",
        ),
        (
            "RSI(14)",
            f"Breakout buy blocked at &ge; {v('rsi_overbought')}, breakout sell at &le; {v('rsi_oversold')}; "
            f"fade sell blocked at &ge; {v('fade_rsi_overbought')}, fade buy at &le; {v('fade_rsi_oversold')}.",
        ),
        ("ATR(14)", f"All rules blocked when 15-min ATR &ge; ${v('atr_max')}."),
        (
            "Spike",
            f"Last 15 one-minute bars' range blocks fades at &ge; {v('spike_fade')}&times; ATR or &ge; {m('spike_fade_ceiling')}; "
            f"breakouts at &ge; {v('spike_breakout')}&times; ATR or &ge; {m('spike_breakout_ceiling')}.",
        ),
        ("Exits", exit_text),
    ]
    if as_of is not None and as_of >= FADE_EXIT_RULE_SHOWN_FROM:
        rows.append(
            (
                "Fade exit",
                f"Fade trades (TA-Zone) only: stop loss capped at &minus;${FADE_STOP_LOSS_CAP:g}; if the opposite fade level is within "
                f"${FADE_NO_TRAIL_MAX_LOCK_DISTANCE:g} of entry the stop trails ${FADE_NEAR_TRAIL_DISTANCE:g} behind the best price once "
                f"${FADE_NEAR_TRAIL_ACTIVATION:g} in profit (from 16 Oct 2026; no trail before), and from the moment price reaches that level "
                f"${FADE_LOCK_TRAIL_DISTANCE:g} behind the best price. A further level keeps the normal trailing stop.",
            )
        )
    body = "".join(f"<div><b>{name}</b> &mdash; {text}</div>" for name, text in rows)
    return (
        "<div style='font-size:12px;color:#444;line-height:1.5;margin:0 0 0.5rem 0;'>"
        "<div style='font-weight:600;'>Broker B rules in force</div>"
        f"{body}</div>"
    )


def load_forecast_runs_for_date(d) -> list[dict]:
    """Every ta_forecasts row for this ET date, oldest first. Their position is the run's name: the first
    is TA1, the second TA2, and so on -- every run is reachable (scheduled or started by the Telegram
    "run TA" command), instead of the old Morning/Midday slots where a second run replaced the first.
    Includes `id` -- needed to look up each row's own Broker B trades for the diagram's per-level
    outcome markers (see `load_broker_b_outcomes()`)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, ts, analysis, levels FROM ta_forecasts WHERE forecast_date = %s ORDER BY ts, id",
            (d,),
        )
        rows = cur.fetchall()
    return [{"id": r[0], "ts": r[1], "analysis": r[2], "levels": r[3]} for r in rows]


def load_broker_b_outcomes(forecast_id: int) -> dict[str, dict]:
    """Broker F's (live `forex_b_trades`, never Broker B's) actual trade record against this forecast row's four scenarios -- the diagram's
    per-level outcome markers (see `render_diagram_svg()`'s `scenario_outcomes` docstring). Keyed by
    *scenario* name (`sell_resistance`/`buy_support`/`bull_breakout`/`bear_breakdown`), translated from
    `broker_b_trades.rule_name` via `broker_b.ZONE_SCENARIOS`, the same mapping `broker_b.py` itself
    uses -- so this can never drift from which rule actually trades which scenario. `results` is one
    bool per closed trade in open order (True = win, `pnl > 0`; False = loss, `pnl < 0`), so the diagram
    shows every result -- a level that won once then lost reads ✓✗. An open trade with no closed
    result yet is left out."""
    rule_to_scenario = {rule_name: name for name, (_, rule_name) in broker_b.ZONE_SCENARIOS.items()}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('forex_b_trades')")
        if cur.fetchone()[0] is None:
            return {}
        cur.execute(
            "SELECT rule_name, pnl, status FROM forex_b_trades "
            "WHERE ta_forecast_id = %s AND mode = 'live' ORDER BY open_ts",
            (forecast_id,),
        )
        rows = cur.fetchall()
    outcomes: dict[str, dict] = {}
    for rule_name, pnl, status in rows:
        scenario_name = rule_to_scenario.get(rule_name)
        if scenario_name is None or status != "Closed" or pnl is None:
            continue
        if pnl != 0:
            outcomes.setdefault(scenario_name, {"results": []})["results"].append(pnl > 0)
    return outcomes


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


def load_trading_pauses_between(start, end) -> list[tuple]:
    """Scheduled Telegram pauses (`trading_pauses`, see trading_control.py) overlapping [start, end), as
    (start_ts, end_ts) oldest first. Empty if the table doesn't exist yet (no pause was ever scheduled).
    "stop trading" overrides aren't listed: only the latest one is stored, with no history."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('trading_pauses')")
        if cur.fetchone()[0] is None:
            return []
        cur.execute(
            "SELECT start_ts, end_ts FROM trading_pauses WHERE start_ts < %s AND end_ts > %s ORDER BY start_ts",
            (end, start),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def pause_note_html(pauses: list[tuple]) -> str:
    """Red 'Trading paused from 7:00 AM to 10:30 AM' note (ET) for the forecast header, '' if none."""
    if not pauses:
        return ""
    def fmt(ts):
        return ts.astimezone(DISPLAY_TZ).strftime("%I:%M %p").lstrip("0")
    text = "; ".join(f"Trading paused from {fmt(s)} to {fmt(e)}" for s, e in pauses)
    return f"<span style='color:#cf222e;font-size:14px;font-weight:600;margin-left:12px;'>{text}</span>"


def _render_compare_section(selected_date, forecast, b_outcomes, live_price, r_run) -> bool:
    """Broker B / F levels (left) beside the chosen Broker R short-range run (right) on one price axis (10 Nov 2026). Both runs come
    from the two dropdowns under the date picker. A missing Broker R run or an error is shown as a caption, never silent.
    Returns True if the chart was drawn (the caller then skips the plain Broker B/F chart)."""
    st.markdown("**Broker B/F vs Broker R — same price axis**")
    if r_run is None:
        st.caption("No Broker R short-range analysis recorded for this date, so showing the Broker B/F chart alone.")
        return False
    try:
        r_levels = r_run["levels"] or {}
        try:
            r_trades = broker_r_view.load_trades_for_date(selected_date)
            r_outcomes = broker_r_view.outcomes_for(r_trades, r_run["id"]) if not r_trades.empty else {}
        except Exception:
            r_outcomes = {}
        r_res, r_sup, r_sc = broker_r_view.traded_levels(r_levels)
        b_levels = forecast["levels"]
        svg = render_compare_svg(
            {"price": b_levels["price"], "resistances": b_levels["resistances"], "supports": b_levels["supports"],
             "scenarios": b_levels["scenarios"], "outcomes": b_outcomes},
            {"price": r_levels["price"], "resistances": r_res, "supports": r_sup, "scenarios": r_sc, "outcomes": r_outcomes},
            live_price=live_price,
        )
        st.markdown(f'<div class="goldo-diagram-wrap">{svg}</div>', unsafe_allow_html=True)
        return True
    except Exception as e:
        st.caption(f"Could not draw the comparison chart: {type(e).__name__}: {e}")
        return False


def load_broker_pnl_for_date(d) -> dict:
    """Broker B (`broker_b_trades`), Broker F (`forex_b_trades`, live demo-account trades only) and Broker K (`broker_k_trades`) realized P&L, each on its own, for
    trades that *closed* within one ET calendar day -- a trade opened the day before but closed today counts as today's. Broker A is
    deliberately not part of any dashboard calculation (user request, 13 Oct 2026), and there is no combined total. Tied to the same
    selected date as the forecast navigator below."""
    start = pd.Timestamp(d, tz=DISPLAY_TZ)
    end = start + pd.Timedelta(days=1)
    start_utc, end_utc = start.tz_convert("UTC"), end.tz_convert("UTC")
    def totals(cur, table: str, extra: str = "") -> dict:
        # Gross profit (winning trades), gross loss (losing trades, negative) and the net, for trades closed in the day.
        cur.execute(
            "SELECT COALESCE(SUM(pnl) FILTER (WHERE pnl > 0), 0), COALESCE(SUM(pnl) FILTER (WHERE pnl < 0), 0), "
            f"COALESCE(SUM(pnl), 0) FROM {table} WHERE status = 'Closed'{extra} AND close_ts >= %s AND close_ts < %s",
            (start_utc, end_utc),
        )
        p, l, t = cur.fetchone()
        return {"profit": float(p), "loss": float(l), "total": float(t)}

    zero = {"profit": 0.0, "loss": 0.0, "total": 0.0}
    with get_connection() as conn, conn.cursor() as cur:
        broker_b = totals(cur, "broker_b_trades")
        # The tables below only exist once init_db() has run with them.
        out = {}
        for key, table, extra in (
            ("broker_f", "forex_b_trades", " AND mode = 'live'"),
            ("broker_k", "broker_k_trades", ""),
            ("broker_r", "broker_r_trades", ""),
        ):
            cur.execute("SELECT to_regclass(%s)", (table,))
            out[key] = totals(cur, table, extra) if cur.fetchone()[0] is not None else dict(zero)
    # Broker B is left out of the total: B and F trade the same rules, so counting both would count each level twice (user request, 5 Nov 2026).
    out["total"] = {k: sum(out[b][k] for b in ("broker_f", "broker_k", "broker_r")) for k in zero}
    out["broker_b"] = broker_b
    return out


try:
    min_forecast_date = load_forecast_date_bounds()
except Exception as e:
    st.error(f"Could not load the forecast date range: {e}")
    min_forecast_date = None

selected_date = datetime.now(DISPLAY_TZ).date()  # fallback when the forecast section is skipped
if min_forecast_date is not None:
    today_et = now_local.date()

    def prev_weekday(d):
        while d.weekday() >= 5:  # Sat/Sun -> the Friday before
            d -= timedelta(days=1)
        return d

    def next_weekday(d):
        while d.weekday() >= 5:  # Sat/Sun -> the Monday after
            d += timedelta(days=1)
        return d

    # Forecasts only run on weekdays, so weekends aren't selectable: the newest selectable date is the
    # latest weekday on or before today (on a Saturday/Sunday that's Friday), and a weekend value
    # (typed in, or via keyboard past the CSS-grayed calendar cells) snaps back to the Friday before it.
    latest_date = prev_weekday(today_et)
    if FORECAST_DATE_KEY not in st.session_state:
        st.session_state[FORECAST_DATE_KEY] = latest_date
    st.session_state[FORECAST_DATE_KEY] = prev_weekday(st.session_state[FORECAST_DATE_KEY])

    # Each button's own disabled= is computed once per script run, before its click (if any) updates
    # session_state further down -- without the st.rerun() below, the *same* rerun that processes a
    # click still renders both arrows from the pre-click state, so a click that lands on a boundary
    # (e.g. reaching today) briefly shows the wrong arrow enabled/disabled until some other widget
    # triggers a fresh run. st.rerun() forces that fresh run immediately, so the arrows are always
    # correct right after the click that caused them to change.
    nav_prev, nav_date, nav_next = st.columns([1, 5, 1])
    with nav_prev:
        if st.button("◀", key="forecast_date_prev",
                     disabled=st.session_state[FORECAST_DATE_KEY] <= min_forecast_date):
            st.session_state[FORECAST_DATE_KEY] = prev_weekday(
                st.session_state[FORECAST_DATE_KEY] - timedelta(days=1)
            )
            st.rerun()
    with nav_next:
        if st.button("▶", key="forecast_date_next",
                     disabled=st.session_state[FORECAST_DATE_KEY] >= latest_date):
            st.session_state[FORECAST_DATE_KEY] = next_weekday(
                st.session_state[FORECAST_DATE_KEY] + timedelta(days=1)
            )
            st.rerun()
    with nav_date:
        st.date_input(
            "Forecast date", min_value=min_forecast_date, max_value=latest_date,
            key=FORECAST_DATE_KEY, label_visibility="collapsed",
        )
    selected_date = st.session_state[FORECAST_DATE_KEY]

    # A second ◀/▶ row picks which of the day's technical-analysis runs (TA1, TA2, ...) to show, so every
    # run is reachable, not just one per Morning/Midday slot. A date change resets it to the latest run.
    try:
        runs = load_forecast_runs_for_date(selected_date)
    except Exception as e:
        st.error(f"Could not load the forecasts for {selected_date}: {e}")
        runs = []
    if (
        st.session_state.get(FORECAST_RUN_DATE_KEY) != selected_date
        or not 1 <= st.session_state.get(FORECAST_RUN_KEY, 0) <= len(runs)
    ):
        st.session_state[FORECAST_RUN_KEY] = max(len(runs), 1)
        st.session_state[FORECAST_RUN_DATE_KEY] = selected_date

    # Two dropdowns side by side under the date (user request, 10 Nov 2026): which Broker B/F analysis (TA1, TA2, ...) and which Broker R
    # short-range run to draw. Both default to the newest run and reset to it on a date change / when a newer run arrives.
    try:
        r_runs = get_ta_short_forecasts_for_date(selected_date)
    except Exception as e:
        st.caption(f"Could not load Broker R's short-range runs: {e}")
        r_runs = []
    sel_b, sel_r = st.columns(2)
    with sel_b:
        cur_run = st.selectbox(
            "Broker B/F analysis", list(range(1, max(len(runs), 1) + 1)), key=FORECAST_RUN_KEY,
            format_func=lambda n: (
                f"TA{n} · {runs[n - 1]['ts'].astimezone(DISPLAY_TZ):%H:%M} ET" if runs else "none"
            ),
            disabled=not runs,
        )
    with sel_r:
        r_labels = [f"{r['levels'].get('session', '?')} · {r['ts'].astimezone(DISPLAY_TZ):%H:%M} ET" for r in r_runs]
        if r_runs:
            # Key includes the newest run's id: a newer run changes the key, so the picker resets to it.
            r_choice = st.selectbox(
                "Broker R analysis", r_labels, index=len(r_labels) - 1,
                key=f"compare_r_run_{selected_date}_{r_runs[-1]['id']}",
            )
            r_run_selected = r_runs[r_labels.index(r_choice)]
        else:
            st.selectbox("Broker R analysis", ["none"], disabled=True, key=f"compare_r_run_none_{selected_date}")
            r_run_selected = None

    try:
        pnl = load_broker_pnl_for_date(selected_date)
    except Exception as e:
        st.error(f"Could not load broker P&L for {selected_date}: {e}")
        pnl = None
    if pnl is not None:
        def pnl_color(value: float) -> str:
            return "#1a7f37" if value > 0 else ("#cf222e" if value < 0 else "#888")

        # Plain "$" here, not "\$" -- unlike a plain-text st.caption()/st.markdown() string (see the
        # LaTeX gotcha noted below), this whole line is one HTML block (starts with "<div"), which
        # CommonMark passes through verbatim rather than scanning for a $-pair to treat as math.
        # One small table, a row per broker, instead of a single run-on line (user request, 5 Nov 2026).
        # The day's total (B + F + K + R) sits above the per-broker rows (user request, 5 Nov 2026).
        def money_cell(value: float, bold: bool = False) -> str:
            return (f"<td style='padding:3px 0 3px 16px;text-align:right;font-weight:{700 if bold else 400};"
                    f"font-size:{18 if bold else 14}px;color:{pnl_color(value)};'>${value:+,.2f}</td>")

        pnl_rows = "".join(
            f"<tr><td style='padding:3px 16px 3px 0;{'font-weight:700;' if key == 'total' else ''}'>{label}</td>"
            + money_cell(pnl[key]["profit"]) + money_cell(pnl[key]["loss"]) + money_cell(pnl[key]["total"], bold=True)
            + "</tr>"
            for label, key in (
                ("Total", "total"), ("Broker <b style='font-size:18px;'>F</b>", "broker_f"),
                ("Broker <b style='font-size:18px;'>R</b>", "broker_r"), ("Broker <b style='font-size:18px;'>K</b>", "broker_k"),
            )
        )
        header_cells = "".join(
            f"<th style='padding:3px 0 3px 16px;text-align:right;font-weight:600;font-size:12px;color:#444;'>{h}</th>"
            for h in ("Profit", "Loss", "Total")
        )
        st.markdown(
            f"<div style='font-size:12px;color:#444;margin-bottom:2px;'>P&amp;L ({selected_date:%b %d})</div>"
            f"<table style='border-collapse:collapse;font-size:14px;'><tr><th></th>{header_cells}</tr>{pnl_rows}</table>",
            unsafe_allow_html=True,
        )

    try:
        forecast = runs[cur_run - 1] if runs else None
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
        # A run "covers" the time from its own ts until the next run's (or the end of that ET day for the
        # last one); any scheduled pause overlapping that stretch is flagged in red beside the header.
        run_start = forecast["ts"]
        if cur_run < len(runs):
            run_end = runs[cur_run]["ts"]
        else:
            day_after = selected_date + timedelta(days=1)
            run_end = datetime(day_after.year, day_after.month, day_after.day, tzinfo=DISPLAY_TZ)
        try:
            pause_note = pause_note_html(load_trading_pauses_between(run_start, run_end))
        except Exception:
            pause_note = ""
        header_text = "Today's Forecast" if selected_date == today_et else f"Forecast — {selected_date:%b %d, %Y}"
        st.markdown(
            f"<h3 style='margin:0;padding:0.5rem 0 0.25rem 0;'>{header_text}{pause_note}</h3>",
            unsafe_allow_html=True,
        )
        # "\$" everywhere here, not "$" -- st.caption() renders markdown, and Streamlit treats a pair
        # of literal $ as inline LaTeX; two or more dollar amounts in the same string (the candle line
        # below) silently mangled into math notation before this was escaped.
        caption = (
            f"{forecast_local.strftime('%Y-%m-%d %H:%M %Z')} (TA{cur_run}) · "
            f"\\${levels.get('price', 0):,.2f} · {levels.get('bias', '?')} "
            f"(score {levels.get('bias_score', 0):+d}/6)"
        )
        if candle:
            caption += (
                f" · Day: O \\${candle['open']:,.2f} H \\${candle['high']:,.2f} "
                f"L \\${candle['low']:,.2f} C \\${candle['close']:,.2f}"
            )
        st.caption(caption)
        try:
            scenario_outcomes = load_broker_b_outcomes(forecast["id"]) if forecast.get("id") else {}
        except Exception as e:
            st.error(f"Could not load Broker B outcomes for this forecast: {e}")
            scenario_outcomes = {}
        # The combined B/F + R chart replaces the plain B/F chart; the plain one is only the fallback when the combined one
        # can't be drawn (no Broker R run that day, or an error -- the caption says which). The day candle is only on the plain chart.
        if not _render_compare_section(selected_date, forecast, scenario_outcomes, live_price, r_run_selected):
            diagram_svg = render_diagram_svg(
                levels["price"], levels["resistances"], levels["supports"], levels["scenarios"],
                candle, live_price, scenario_outcomes,
            )
            st.markdown(f'<div class="goldo-diagram-wrap">{diagram_svg}</div>', unsafe_allow_html=True)
        if forecast_local.date() >= BROKER_B_RULES_SHOWN_FROM:
            st.markdown(broker_b_entry_rules_html(forecast["ts"]), unsafe_allow_html=True)
        with st.expander("Full forecast text"):
            st.text(forecast["analysis"])
    elif selected_date != today_et:
        st.caption(f"No forecast recorded for {selected_date:%Y-%m-%d} (weekend, holiday, or a day the job didn't run).")


def load_trades(d) -> pd.DataFrame:
    """Broker F's `forex_b_trades` (live demo-account trades only, not the shadow-mode records) that opened or closed on ET date `d`
    (the date selected at the top; 10 Nov 2026, user request -- was the most recent 20 regardless of date), oldest first.
    This table used to merge Broker B, F and K; since 10 Nov 2026 (user request) it shows Broker F only."""
    cols = ["rule_name", "trade_type", "entry_price", "open_ts", "triggering_alerts", "exit_price", "close_ts", "pnl", "status"]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('forex_b_trades')")
            if cur.fetchone()[0] is None:  # created by init_db(); absent before Broker F first runs
                return pd.DataFrame(columns=cols)
        return pd.read_sql(
            f"SELECT {', '.join(cols)} FROM forex_b_trades WHERE mode = 'live' "
            "AND ((open_ts AT TIME ZONE 'America/New_York')::date = %(d)s OR (close_ts AT TIME ZONE 'America/New_York')::date = %(d)s) "
            "ORDER BY open_ts",
            conn, params={"d": d}, parse_dates=["open_ts", "close_ts"],
        )


def to_display_str(ts: pd.Series) -> pd.Series:
    """UTC (or tz-naive, just in case) timestamp column -> DISPLAY_TZ string; NaT stays NaN."""
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    return ts.dt.tz_convert(DISPLAY_TZ).dt.strftime("%Y-%m-%d %H:%M:%S %Z")


st.subheader("Broker F trades")
try:
    trades = load_trades(selected_date)
except Exception as e:
    st.error(f"Could not load trades: {e}")
    trades = pd.DataFrame(
        columns=["rule_name", "trade_type", "entry_price", "open_ts", "triggering_alerts",
                 "exit_price", "close_ts", "pnl", "status"]
    )

if not trades.empty:
    # Captured from the raw numeric pnl before it's formatted to a "$+X.XX"/"—" string below --
    # True (win, green), False (loss, red), None (still open or exactly breakeven, left unstyled)
    # colors the "#" row-number column two paragraphs down.
    trade_won = [None if pd.isna(v) else (True if v > 0 else (False if v < 0 else None)) for v in trades["pnl"]]
    trades["open_ts"] = to_display_str(trades["open_ts"])
    trades["close_ts"] = to_display_str(trades["close_ts"])
    for col in ("entry_price", "exit_price"):
        trades[col] = trades[col].map(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
    trades["pnl"] = trades["pnl"].map(lambda v: f"${v:+,.2f}" if pd.notna(v) else "—")
    trades["close_ts"] = trades["close_ts"].fillna("—")
    trades.insert(0, "#", range(len(trades)))
    trades = trades.rename(columns={
        "rule_name": "Rule", "trade_type": "Type", "entry_price": "Entry",
        "open_ts": "Opened", "triggering_alerts": "Trigger", "exit_price": "Exit",
        "close_ts": "Closed", "pnl": "P&L", "status": "Status",
    })
    # P&L third, Opened fourth (counting the "#" column as first).
    trades = trades[["#", "P&L", "Opened", "Rule", "Type", "Entry", "Trigger", "Exit", "Closed", "Status"]]

if trades.empty:
    st.write("No Broker F trades on this date.")
else:
    # st.dataframe doesn't style the plain pandas index (confirmed empirically -- a Styler.apply_index()
    # renders with no visible effect there), so the row-number "#" column above is a real data column
    # instead, colored via Styler.apply() on data cells, which st.dataframe does honor; hide_index=True
    # drops the now-redundant default index next to it.
    def _color_row_number(_col):
        colors = []
        for won in trade_won:
            if won is True:
                colors.append("background-color: #1a7f37; color: white; font-weight: 700;")
            elif won is False:
                colors.append("background-color: #cf222e; color: white; font-weight: 700;")
            else:
                colors.append("")
        return colors

    st.dataframe(trades.style.apply(_color_row_number, subset=["#"]), width='stretch', hide_index=True)

# Broker R's table (own module), then Broker K's below -- order F, R, K; all three titles are st.subheader.
st.subheader("Broker R trades")
broker_r_view.render_trades(selected_date)


def load_trades_k(d) -> pd.DataFrame:
    """Broker K's `broker_k_trades` that opened or closed on ET date `d`, oldest first."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('broker_k_trades')")
            if cur.fetchone()[0] is None:
                return pd.DataFrame()
        return pd.read_sql(
            "SELECT id, rule_name, entry_price, open_ts, exit_price, close_ts, pnl, exit_reason, status FROM broker_k_trades "
            "WHERE (open_ts AT TIME ZONE 'America/New_York')::date = %(d)s OR (close_ts AT TIME ZONE 'America/New_York')::date = %(d)s "
            "ORDER BY open_ts",
            conn, params={"d": d}, parse_dates=["open_ts", "close_ts"],
        )


st.subheader("Broker K trades")
try:
    k_trades = load_trades_k(selected_date)
except Exception as e:
    st.caption(f"Could not load Broker K trades: {e}")
    k_trades = pd.DataFrame()
if k_trades.empty:
    st.caption("No Broker K trades on this date.")
else:
    k_won = [None if pd.isna(v) else (True if v > 0 else (False if v < 0 else None)) for v in k_trades["pnl"]]

    def _k_hm(ts):  # HH:MM in ET, same as Broker R's table
        if pd.isna(ts):
            return "—"
        ts = pd.Timestamp(ts)
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        return ts.tz_convert(DISPLAY_TZ).strftime("%H:%M")

    # Same columns, order and formatting as the Broker R table (broker_r_view.render_trades()).
    k_table = pd.DataFrame({
        "#": k_trades["id"],
        "P&L": k_trades["pnl"].map(lambda v: "—" if pd.isna(v) else f"${v:+,.2f}"),
        "rule": k_trades["rule_name"],
        "opened": k_trades["open_ts"].map(_k_hm),
        "entry": k_trades["entry_price"].map("${:,.2f}".format),
        "closed": k_trades["close_ts"].map(_k_hm),
        "exit": k_trades["exit_price"].map(lambda v: "—" if pd.isna(v) else f"${v:,.2f}"),
        "reason": k_trades["exit_reason"].fillna("open"),
    })

    def _color_k(_col):
        return ["background-color: #1a7f37; color: white; font-weight: 700;" if w is True
                else "background-color: #cf222e; color: white; font-weight: 700;" if w is False else "" for w in k_won]

    st.dataframe(k_table.style.apply(_color_k, subset=["#"]), hide_index=True, use_container_width=True)
