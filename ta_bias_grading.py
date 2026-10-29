"""Grades the TA forecast's directional calls against what gold actually did (study 28 in docs/studies.md).

Until 29 Oct 2026 `ta_forecast_job.review_previous()` graded only the four scenarios; nothing checked whether the bias was right.
For every forecast this stores, in `ta_bias_grades`, gold's move after it at four horizons -- +1h, +3h, until the next forecast
run, until 5pm ET that day -- next to the three calls that were made: the combined bias (`bias_score`, -6..+6), the regime
(slow filters, -3..+3) and the lean (fast signals, "No edge" when they disagree). Observation only: it changes no forecast text
(beyond one REVIEW line), no broker and no threshold.

Prices are Twelve Data 15-minute XAU/USD candles: the price at time t is the close of the last candle that ended at or before t (and
none if the market was closed, i.e. the candle is more than MAX_PRICE_GAP_MINUTES old). A move smaller than FLAT_BAND_DOLLARS is
"flat" and counts as neither a hit nor a miss. A hit is reported against the always-short and always-long baselines on the same rows
(during a selloff "Bearish" is right most of the time), and a "No edge" lean is tracked separately.

Rows carry `source`: 'live' = the forecast was written with `regime`/`lean` in its levels (29 Oct 2026 onward); 'backfill' =
the regime and lean were recomputed afterwards from the indicators stored with the older forecast. The lean's design was chosen
after looking at the backfilled period, so only 'live' rows are an out-of-sample test of it; the report keeps the two apart.

    python ta_bias_grading.py backfill     # grade every stored forecast (idempotent)
    python ta_bias_grading.py report       # hit rates by call and horizon, live vs backfill
"""

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

DISPLAY_TZ = ZoneInfo("America/New_York")
FLAT_BAND_DOLLARS = 2.0
MAX_PRICE_GAP_MINUTES = 30
COMPLETE_AFTER_HOURS = 48  # past this age a horizon that is still unobservable (market closed, no candles) is left NULL for good
CANDLE_MINUTES = 15
HORIZONS = {"r1h": "+1h", "r3h": "+3h", "r_next": "to next run", "r_eod": "to 5pm ET"}
CALLS = {"bias_score": "combined bias", "regime_score": "regime", "lean_score": "lean"}


def _price_at(candles: pd.DataFrame, t: datetime) -> float | None:
    """Close of the last candle that ended at or before `t`; None if there is none or it is older than MAX_PRICE_GAP_MINUTES."""
    ends = candles["end"]
    i = ends.searchsorted(t, side="right") - 1
    if i < 0:
        return None
    if t - ends.iloc[i] > timedelta(minutes=MAX_PRICE_GAP_MINUTES):
        return None
    return float(candles["close"].iloc[i])


def prepare_candles(df: pd.DataFrame) -> pd.DataFrame:
    out = df.sort_values("datetime").reset_index(drop=True)
    out["end"] = out["datetime"] + timedelta(minutes=CANDLE_MINUTES)
    return out


def calls_for(levels: dict) -> tuple[dict, str]:
    """(bias_score, regime_score, lean_score, lean) of a stored forecast, and its source. Older forecasts are recomputed from their
    stored indicators with the same code that prints them now."""
    if levels.get("lean") is not None:
        return {k: levels.get(k) for k in ("bias_score", "regime_score", "lean_score", "lean")}, "live"
    import ta_forecast_job as tf  # deferred: imports the data fetchers

    ind = dict(levels["indicators"])
    ind["ema_1h"] = {int(k): v for k, v in ind["ema_1h"].items()}  # JSON turns the integer period keys into strings
    split = tf._regime_and_lean(levels["price"], ind)
    return {"bias_score": levels.get("bias_score"), **{k: split[k] for k in ("regime_score", "lean_score", "lean")}}, "backfill"


def grade_forecast(f: dict, next_ts: datetime | None, candles: pd.DataFrame, now: datetime) -> dict | None:
    """One forecast's grade row from `f` ({id, ts, levels}), the next forecast's time (None = latest, or `now` for the review line)
    and the prepared candles. A horizon that is not yet observable is None. None if even the starting price is unknown."""
    p0 = _price_at(candles, f["ts"])
    if p0 is None:
        return None
    calls, source = calls_for(f["levels"])
    et = f["ts"].astimezone(DISPLAY_TZ)
    eod = et.replace(hour=17, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    targets = {"r1h": f["ts"] + timedelta(hours=1), "r3h": f["ts"] + timedelta(hours=3), "r_next": next_ts,
               "r_eod": eod if eod > f["ts"] else None}
    row = {"forecast_id": f["id"], "ts": f["ts"], "source": source, "p0": p0, "next_ts": next_ts, "updated_ts": now, **calls}
    for key, t in targets.items():
        p = _price_at(candles, t) if t is not None and t <= now else None
        row[key] = None if p is None else round(p - p0, 2)
    row["complete"] = now - f["ts"] > timedelta(hours=COMPLETE_AFTER_HOURS) or all(row[k] is not None for k in HORIZONS)
    return row


def grade_all(forecasts: list[dict], candles: pd.DataFrame, now: datetime) -> list[dict]:
    rows = []
    for i, f in enumerate(forecasts):
        nxt = forecasts[i + 1]["ts"] if i + 1 < len(forecasts) else None
        g = grade_forecast(f, nxt, candles, now)
        if g is not None:
            rows.append(g)
    return rows


def fetch_grading_candles(outputsize: int = 5000) -> pd.DataFrame:
    from config import GOLD_SPOT_SYMBOL
    from data_fetcher import fetch_candles

    return prepare_candles(fetch_candles(GOLD_SPOT_SYMBOL, "15min", outputsize))


def review_line(prev: dict | None, now: datetime, candles: pd.DataFrame | None = None) -> str | None:
    """One REVIEW line for the previous forecast's directional calls, graded up to `now`. Best-effort: None on any problem."""
    try:
        if not prev or not prev.get("levels") or not prev["levels"].get("indicators"):
            return None
        candles = candles if candles is not None else fetch_grading_candles(500)
        g = grade_forecast(prev, now, candles, now)
        if g is None:
            return None

        def mv(k):
            return "n/a" if g[k] is None else f"{g[k]:+.2f}"

        lean = g["lean"] or "?"
        return (f"- Directional calls: combined bias {g['bias_score']:+d}/6, regime {g['regime_score']:+d}/3, lean {lean} ({g['lean_score']:+d}/3). "
                f"Gold since: +1h {mv('r1h')}, +3h {mv('r3h')}, to this run {mv('r_next')}, to 5pm ET {mv('r_eod')}.")
    except Exception as exc:  # grading must never break a forecast run
        print(f"[ta_bias_grading] review line skipped: {exc}")
        return None


def update_grades(now: datetime | None = None, backfill: bool = False) -> int:
    """Grade the forecasts that are not complete yet (all of them for the backfill) and save the rows. Returns how many were written."""
    from storage import get_ta_forecasts_for_grading, get_ta_bias_grades, upsert_ta_bias_grade

    now = now or datetime.now(timezone.utc)
    forecasts = get_ta_forecasts_for_grading()
    if not forecasts:
        return 0
    done = set() if backfill else {g["forecast_id"] for g in get_ta_bias_grades() if g["complete"]}
    candles = fetch_grading_candles(5000 if backfill else 800)
    written = 0
    for g in grade_all(forecasts, candles, now):
        if g["forecast_id"] in done:
            continue
        upsert_ta_bias_grade(g)
        written += 1
    return written


# ---------------------------------------------------------------------------------------------------
# Report


def _hit_stats(rows: list[dict], call: str, horizon: str) -> dict:
    """Hit rate of one call at one horizon over rows that made a call (score != 0) and moved by at least FLAT_BAND_DOLLARS."""
    called = [r for r in rows if r.get(call) not in (None, 0) and r.get(horizon) is not None]
    moved = [r for r in called if abs(r[horizon]) >= FLAT_BAND_DOLLARS]
    if call == "lean_score":
        moved = [r for r in moved if r["lean"] != "No edge"]
    hits = sum(1 for r in moved if (r[horizon] > 0) == (r[call] > 0))
    short = sum(1 for r in moved if r[horizon] < 0)
    return {"n": len(moved), "hits": hits, "short": short, "flat": len(called) - len(moved)}


def format_report(grades: list[dict]) -> str:
    out = [f"TA bias grading ({len(grades)} forecasts; flat band +/-${FLAT_BAND_DOLLARS:g}, a hit needs a move beyond it)"]
    for source, title in (("live", "LIVE (regime/lean written with the forecast; out-of-sample for the lean)"),
                          ("backfill", "BACKFILL (regime/lean recomputed afterwards; the lean was designed on this period)")):
        rows = [g for g in grades if g["source"] == source]
        out += ["", f"{title}: {len(rows)} forecasts"]
        if not rows:
            out.append("  none yet")
            continue
        out.append(f"  {'call':14s}" + "".join(f"{label:>30s}" for label in HORIZONS.values()))
        for call, name in CALLS.items():
            cells = []
            for h in HORIZONS:
                s = _hit_stats(rows, call, h)
                cells.append("n/a" if not s["n"] else f"{100 * s['hits'] / s['n']:.0f}% ({s['hits']}/{s['n']}) vs short {100 * s['short'] / s['n']:.0f}%")
            out.append(f"  {name:14s}" + "".join(f"{c:>30s}" for c in cells))
        noedge = [r for r in rows if r["lean"] == "No edge"]
        if noedge:
            parts = []
            for h, label in HORIZONS.items():
                v = [abs(r[h]) for r in noedge if r[h] is not None]
                if v:
                    parts.append(f"{label} avg |move| ${sum(v) / len(v):.1f}")
            out.append(f"  'No edge' lean: {len(noedge)} forecasts; " + ", ".join(parts))
    out.append("")
    out.append("'vs short' = how often the always-short baseline would have been right on the same rows.")
    return "\n".join(out)


def main(argv: list[str]) -> None:
    cmd = argv[1] if len(argv) > 1 else "report"
    if cmd == "backfill":
        from storage import init_db

        init_db()
        print(f"[ta_bias_grading] wrote {update_grades(backfill=True)} grade rows")
    elif cmd == "report":
        from storage import get_ta_bias_grades

        print(format_report(get_ta_bias_grades()))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
