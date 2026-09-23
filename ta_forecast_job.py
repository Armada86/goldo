"""One-shot XAU/USD technical forecast generator -- writes one row to the `ta_forecasts` table.

Modelled on two styles of third-party gold analysis (see docs/technical-analyst-forecast-log.md for
both examples and what was taken from each):
  1. an indicator snapshot -- 1h EMA20/50/100/200 stack, RSI(14), MACD(12,26,9), pivot, overall bias;
  2. a conditional trading plan -- sell/buy zones with a hard stop, a breakout trigger that flips the
     trade, and a ladder of targets in each direction, plus a review of the previous plan.

Everything is computed from real candles (Twelve Data XAU/USD 15min/1h/4h/1day) plus yfinance daily
DXY/US10Y for context -- no copied levels. Each run also grades the previous forecast's scenarios
against the 15-min candles since it was written (triggered? stop or targets first?), so the review
section is computed, not self-reported.

`analysis` holds the readable text; `levels` (JSONB) holds the same numbers structured, which is what
the next run's review reads back.

Triggered externally by cron-job.org via .github/workflows/ta_forecast.yml, same pattern as every
other scheduled job in this repo. Uses 4 Twelve Data calls per run (5 when grading a previous
forecast) -- negligible against the 800/day cap.

Run: python ta_forecast_job.py            # generate + save to Postgres
     python ta_forecast_job.py --dry-run  # generate + print only (no DB read or write)
"""

import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from config import GOLD_SPOT_SYMBOL
from data_fetcher import compute_rsi, fetch_candles, fetch_daily_history

DISPLAY_TZ = ZoneInfo("America/New_York")

EMA_PERIODS = (20, 50, 100, 200)
ZONE_MERGE_DOLLARS = 6.0     # candidate levels this close together are shown as one "4318/4315" zone
LEVELS_PER_SIDE = 4          # resistance/support zones listed each side of price
SWING_LOOKBACK_4H = 180      # ~30 trading days of 4h bars scanned for swing highs/lows
SWING_WING = 5               # a swing high/low must beat this many 4h bars (~20h) on each side
ROUND_NUMBER_STEP = 50       # $4,300 / $4,350 style psychological levels
MIN_STOP_BUFFER = 5.0        # stop distance beyond a zone, floored here, else 10% of daily ATR
MIN_LEVEL_GAP_ATR = 0.15     # listed zones at least this x daily ATR apart (~$15 at a $100 ATR)

# How much a level source counts when two candidate zones are too close to both be listed -- the
# heavier one is kept. Longer-timeframe / more widely watched levels weigh more.
LABEL_WEIGHTS = {
    "20-day high": 3, "20-day low": 3,
    "prior-day high": 2, "prior-day low": 2,
    "1h EMA200": 2, "4h EMA100": 2, "daily SMA20": 2, "daily SMA50": 2, "pivot P": 2,
}


# ---------------------------------------------------------------------------------------------------
# Indicators


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(daily: pd.DataFrame, period: int = 14) -> float:
    prev_close = daily["close"].shift()
    tr = pd.concat(
        [daily["high"] - daily["low"], (daily["high"] - prev_close).abs(), (daily["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])


def _swing_levels(bars: pd.DataFrame) -> tuple[list[float], list[float]]:
    """Fractal swing highs/lows: a bar whose high (low) beats SWING_WING bars on each side."""
    highs, lows = bars["high"].to_numpy(), bars["low"].to_numpy()
    swing_highs, swing_lows = [], []
    for i in range(SWING_WING, len(bars) - SWING_WING):
        window = slice(i - SWING_WING, i + SWING_WING + 1)
        if highs[i] == highs[window].max():
            swing_highs.append(float(highs[i]))
        if lows[i] == lows[window].min():
            swing_lows.append(float(lows[i]))
    return swing_highs, swing_lows


def _completed_weekday_bars(daily: pd.DataFrame) -> pd.DataFrame:
    """Twelve Data's XAU/USD daily series (UTC) includes near-empty Saturday/Sunday bars and today's
    still-forming bar -- drop both so pivots/ATR/prior-day levels come from real, finished sessions."""
    today = datetime.now(timezone.utc).date()
    mask = (daily["datetime"].dt.dayofweek < 5) & (daily["datetime"].dt.date < today)
    return daily[mask]


def compute_snapshot() -> dict:
    h1 = fetch_candles(GOLD_SPOT_SYMBOL, "1h", 500)
    h4 = fetch_candles(GOLD_SPOT_SYMBOL, "4h", 300)
    daily_all = fetch_candles(GOLD_SPOT_SYMBOL, "1day", 120)
    daily = _completed_weekday_bars(daily_all)

    price = float(h1["close"].iloc[-1])
    c1, c4, cd = h1["close"], h4["close"], daily["close"]

    macd = _ema(c1, 12) - _ema(c1, 26)
    signal = _ema(macd, 9)
    hist = macd - signal

    prev = daily.iloc[-1]
    pivot = (prev["high"] + prev["low"] + prev["close"]) / 3
    rng = prev["high"] - prev["low"]
    atr = _atr(daily)

    last20 = daily.tail(20)
    swing_highs, swing_lows = _swing_levels(h4.tail(SWING_LOOKBACK_4H))

    ind = {
        "ema_1h": {p: round(float(_ema(c1, p).iloc[-1]), 2) for p in EMA_PERIODS},
        "ema100_4h": round(float(_ema(c4, 100).iloc[-1]), 2),
        "sma100_4h": round(float(c4.rolling(100).mean().iloc[-1]), 2),
        "sma20_1d": round(float(cd.rolling(20).mean().iloc[-1]), 2),
        "sma50_1d": round(float(cd.rolling(50).mean().iloc[-1]), 2),
        "rsi_1h": round(float(compute_rsi(c1).iloc[-1]), 1),
        "rsi_4h": round(float(compute_rsi(c4).iloc[-1]), 1),
        "macd_1h": round(float(macd.iloc[-1]), 2),
        "macd_signal_1h": round(float(signal.iloc[-1]), 2),
        "macd_hist_1h": round(float(hist.iloc[-1]), 2),
        "macd_hist_1h_prev": round(float(hist.iloc[-2]), 2),
        "atr14_1d": round(atr, 2),
        "pivot": {
            "session": str(prev["datetime"].date()),
            "P": round(pivot, 2),
            "R1": round(2 * pivot - prev["low"], 2),
            "S1": round(2 * pivot - prev["high"], 2),
            "R2": round(pivot + rng, 2),
            "S2": round(pivot - rng, 2),
        },
        "prev_day": {"high": round(float(prev["high"]), 2), "low": round(float(prev["low"]), 2)},
        "range_20d": {"high": round(float(last20["high"].max()), 2), "low": round(float(last20["low"].min()), 2)},
    }

    candidates = [
        (ind["ema_1h"][200], "1h EMA200"),
        (ind["ema100_4h"], "4h EMA100"),
        (ind["sma20_1d"], "daily SMA20"),
        (ind["sma50_1d"], "daily SMA50"),
        (ind["prev_day"]["high"], "prior-day high"),
        (ind["prev_day"]["low"], "prior-day low"),
        (ind["range_20d"]["high"], "20-day high"),
        (ind["range_20d"]["low"], "20-day low"),
    ]
    candidates += [(v, f"pivot {k}") for k, v in ind["pivot"].items() if k != "session"]
    candidates += [(v, "4h swing high") for v in swing_highs]
    candidates += [(v, "4h swing low") for v in swing_lows]
    base = int(price // ROUND_NUMBER_STEP) * ROUND_NUMBER_STEP
    candidates += [(float(base + k * ROUND_NUMBER_STEP), "round number") for k in range(-3, 5)]

    return {
        "price": round(price, 2),
        "price_ts": h1["datetime"].iloc[-1].isoformat(),
        "indicators": ind,
        "zones": _merge_zones(candidates),
        "buffer": round(max(MIN_STOP_BUFFER, 0.1 * atr)),
    }


# ---------------------------------------------------------------------------------------------------
# Levels and plan


def _merge_zones(candidates: list[tuple[float, str]]) -> list[dict]:
    """Collapse candidate levels within ZONE_MERGE_DOLLARS of each other into one zone, keeping every
    source label -- the "4318/4315" style used by the reference analyses, which also absorbs the few
    dollars' difference between data feeds."""
    zones: list[dict] = []
    for value, label in sorted(candidates):
        if zones and value - zones[-1]["low"] <= ZONE_MERGE_DOLLARS:
            zone = zones[-1]
            zone["high"] = round(value, 2)
            if label not in zone["labels"]:
                zone["labels"].append(label)
        else:
            zones.append({"low": round(value, 2), "high": round(value, 2), "labels": [label]})
    return zones


def _weight(zone: dict) -> int:
    return sum(LABEL_WEIGHTS.get(label, 1) for label in zone["labels"])


def _pick_side(zones: list[dict], price: float, min_gap: float) -> list[dict]:
    """Walk outward from price (`zones` nearest-first), keeping zones at least `min_gap` apart; when
    two are closer than that, keep the heavier one -- otherwise every minor 4h swing makes the
    ladder a $5-step scalp list rather than the $15-30 steps the reference analyses use."""
    picked: list[dict] = []
    for zone in zones:
        edge = zone["low"] if zone["low"] > price else zone["high"]
        prev_edge = None
        if picked:
            last = picked[-1]
            prev_edge = last["high"] if zone["low"] > price else last["low"]
        if prev_edge is not None and abs(edge - prev_edge) < min_gap:
            if _weight(zone) > _weight(picked[-1]):
                picked[-1] = zone
            continue
        picked.append(zone)
    return picked[:LEVELS_PER_SIDE]


def _split_zones(zones: list[dict], price: float, min_gap: float) -> tuple[list[dict], list[dict]]:
    """Resistance/support ladders either side of price. A zone price is currently inside counts as
    resistance if price is in its lower half, support otherwise."""
    above = [z for z in zones if (z["low"] + z["high"]) / 2 > price]
    below = [z for z in zones if (z["low"] + z["high"]) / 2 <= price]
    return _pick_side(above, price, min_gap), _pick_side(below[::-1], price, min_gap)


def _bias(price: float, ind: dict) -> tuple[int, str, list[str]]:
    checks = [
        (price > ind["ema_1h"][200], "price vs 1h EMA200"),
        (price > ind["ema100_4h"], "price vs 4h EMA100"),
        (price > ind["sma50_1d"], "price vs daily SMA50"),
        (ind["ema_1h"][20] > ind["ema_1h"][50], "1h EMA20 vs EMA50"),
        (ind["macd_hist_1h"] > 0, "1h MACD histogram"),
    ]
    score = sum(1 if ok else -1 for ok, _ in checks)
    reasons = [f"{name}: {'bullish' if ok else 'bearish'}" for ok, name in checks]
    if ind["rsi_1h"] > 55:
        score += 1
        reasons.append(f"1h RSI {ind['rsi_1h']}: bullish")
    elif ind["rsi_1h"] < 45:
        score -= 1
        reasons.append(f"1h RSI {ind['rsi_1h']}: bearish")
    else:
        reasons.append(f"1h RSI {ind['rsi_1h']}: neutral")
    if score >= 3:
        label = "Bullish"
    elif score >= 1:
        label = "Neutral-to-bullish"
    elif score == 0:
        label = "Neutral"
    elif score >= -2:
        label = "Neutral-to-bearish"
    else:
        label = "Bearish"
    return score, label, reasons


def _zone_dict(z: dict) -> dict:
    return {"low": z["low"], "high": z["high"]}


def build_scenarios(resistances: list[dict], supports: list[dict], buffer: float) -> list[dict]:
    """Four mirrored scenarios, the second reference analysis's structure: fade the nearest
    resistance with a stop just above it (a sustained break above that stop flips to a breakout buy),
    and the same mirrored at the nearest support."""
    scenarios = []
    if resistances:
        r1 = resistances[0]
        stop = round(r1["high"] + buffer, 2)
        scenarios.append({
            "name": "sell_resistance", "direction": "short",
            "entry": _zone_dict(r1), "stop": stop,
            "targets": [_zone_dict(z) for z in supports[:3]],
        })
        scenarios.append({
            "name": "bull_breakout", "direction": "long",
            "trigger": stop, "stop": r1["low"],
            "targets": [_zone_dict(z) for z in resistances[1:4] if z["low"] > stop],
        })
    if supports:
        s1 = supports[0]
        stop = round(s1["low"] - buffer, 2)
        scenarios.append({
            "name": "buy_support", "direction": "long",
            "entry": _zone_dict(s1), "stop": stop,
            "targets": [_zone_dict(z) for z in resistances[:3]],
        })
        scenarios.append({
            "name": "bear_breakdown", "direction": "short",
            "trigger": stop, "stop": s1["high"],
            "targets": [_zone_dict(z) for z in supports[1:4] if z["high"] < stop],
        })
    return scenarios


# ---------------------------------------------------------------------------------------------------
# Grading the previous forecast


def grade_scenario(sc: dict, bars: pd.DataFrame) -> str:
    """Walk 15-min bars in order: was the entry/trigger reached, then which came first -- the stop
    or each target in turn? A bar that spans both the stop and the next target is reported as
    ambiguous, since bar data can't say which printed first."""
    short = sc["direction"] == "short"
    triggered_at = None
    hit: list[int] = []
    for _, bar in bars.iterrows():
        if triggered_at is None:
            if "trigger" in sc:
                reached = bar["low"] <= sc["trigger"] if short else bar["high"] >= sc["trigger"]
            else:
                reached = bar["high"] >= sc["entry"]["low"] if short else bar["low"] <= sc["entry"]["high"]
            if not reached:
                continue
            triggered_at = bar["datetime"]
        stopped = bar["high"] >= sc["stop"] if short else bar["low"] <= sc["stop"]
        nxt = len(hit)
        target_hit = nxt < len(sc["targets"]) and (
            bar["low"] <= sc["targets"][nxt]["high"] if short else bar["high"] >= sc["targets"][nxt]["low"]
        )
        if stopped and target_hit:
            return _fmt_grade(triggered_at, hit, sc, "then stop and next target in the same 15-min bar (ambiguous)")
        if stopped:
            return _fmt_grade(triggered_at, hit, sc, f"then STOPPED at {sc['stop']:.0f}")
        # A single bar can run through several targets at once.
        while nxt < len(sc["targets"]) and (
            bar["low"] <= sc["targets"][nxt]["high"] if short else bar["high"] >= sc["targets"][nxt]["low"]
        ):
            hit.append(nxt)
            nxt += 1
        if sc["targets"] and len(hit) == len(sc["targets"]):
            return _fmt_grade(triggered_at, hit, sc, "all targets reached")
    if triggered_at is None:
        return "not triggered"
    return _fmt_grade(triggered_at, hit, sc, "still open")


def _fmt_grade(triggered_at, hit: list[int], sc: dict, tail: str) -> str:
    when = triggered_at.astimezone(DISPLAY_TZ).strftime("%a %H:%M ET")
    reached = ", ".join(f"T{i + 1} {_fmt_zone(sc['targets'][i])}" for i in hit) or "no target"
    return f"triggered {when}; reached {reached}; {tail}"


def review_previous(prev: dict | None) -> tuple[list[str], dict | None]:
    if not prev or not prev.get("levels") or not prev["levels"].get("scenarios"):
        return ["No previous forecast to review."], None
    since = prev["ts"]
    hours = (datetime.now(timezone.utc) - since).total_seconds() / 3600
    bars = fetch_candles(GOLD_SPOT_SYMBOL, "15min", min(5000, int(hours * 4) + 8))
    bars = bars[bars["datetime"] >= since]
    if bars.empty:
        return ["No 15-min candles since the previous forecast yet."], None
    then = prev["levels"].get("price")
    hi, lo = float(bars["high"].max()), float(bars["low"].min())
    lines = [
        f"Previous forecast {since.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M ET} "
        f"(bias {prev['levels'].get('bias', '?')}, price then {_fmt_price(then)}). "
        f"Since then: high {hi:,.2f}, low {lo:,.2f}."
    ]
    grades = {}
    for sc in prev["levels"]["scenarios"]:
        grade = grade_scenario(sc, bars)
        grades[sc["name"]] = grade
        lines.append(f"- {_describe_scenario(sc)}: {grade}.")
    return lines, {"previous_id": prev.get("id"), "high_since": hi, "low_since": lo, "grades": grades}


# ---------------------------------------------------------------------------------------------------
# Text


def _fmt_price(v) -> str:
    return "?" if v is None else f"${v:,.2f}"


def _fmt_zone(z: dict) -> str:
    lo, hi = round(z["low"]), round(z["high"])
    return f"{hi}/{lo}" if hi != lo else f"{lo}"


def _describe_scenario(sc: dict) -> str:
    targets = ", ".join(_fmt_zone(t) for t in sc["targets"]) or "none identified"
    if sc["name"] == "sell_resistance":
        return f"Sell {_fmt_zone(sc['entry'])}, stop above {sc['stop']:.0f}, targets {targets}"
    if sc["name"] == "buy_support":
        return f"Buy {_fmt_zone(sc['entry'])}, stop below {sc['stop']:.0f}, targets {targets}"
    if sc["name"] == "bull_breakout":
        return f"Sustained break above {sc['trigger']:.0f} = buy signal, stop back below {sc['stop']:.0f}, targets {targets}"
    return f"Break below {sc['trigger']:.0f} = sell signal, stop back above {sc['stop']:.0f}, targets {targets}"


def _macro_context() -> list[str]:
    lines = []
    for name, label, headwind_if_up in (("dxy", "DXY", True), ("us10y", "US10Y", True)):
        try:
            hist = fetch_daily_history(name, "10d")["Close"].dropna()
            last, prev = float(hist.iloc[-1]), float(hist.iloc[-2])
            chg = last - prev
            effect = "headwind" if (chg > 0) == headwind_if_up else "tailwind"
            lines.append(f"{label} {last:.2f} ({chg:+.2f} vs previous close) -- a {effect} for gold (inverse relationship).")
        except Exception as e:
            lines.append(f"{label}: unavailable ({e}).")
    return lines


def render(snap: dict, bias: tuple, resistances, supports, scenarios, review_lines, macro_lines, now) -> str:
    price, ind = snap["price"], snap["indicators"]
    score, label, reasons = bias
    ema = ind["ema_1h"]
    r20 = ind["range_20d"]
    pos = (price - r20["low"]) / (r20["high"] - r20["low"]) if r20["high"] > r20["low"] else 0.5
    third = "upper" if pos > 2 / 3 else "lower" if pos < 1 / 3 else "middle"
    above = [p for p in EMA_PERIODS if price > ema[p]]
    ema_note = (
        "above the whole 1h EMA20/50/100/200 stack" if len(above) == 4
        else "below the whole 1h EMA20/50/100/200 stack" if not above
        else f"inside the 1h EMA cluster (above EMA{'/'.join(map(str, above))})"
    )
    hist_dir = "rising" if ind["macd_hist_1h"] > ind["macd_hist_1h_prev"] else "falling"

    primary = "sell_resistance" if score < 0 else "buy_support" if score > 0 else None
    plan_lines = []
    for sc in scenarios:
        tag = "PRIMARY" if sc["name"] == primary else "Alt"
        plan_lines.append(f"- [{tag}] {_describe_scenario(sc)}.")

    def level_line(z):
        return f"  {_fmt_zone(z):>11}  ({', '.join(z['labels'])})"

    p = ind["pivot"]
    out = [
        f"XAU/USD technical forecast -- {now.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M ET}",
        f"Price {_fmt_price(price)} (Twelve Data 1h close).",
        "",
        f"SUMMARY: {label} (score {score:+d}/6). Price is {ema_note}, "
        f"{'above' if price > ind['ema100_4h'] else 'below'} the 4h EMA100 ({ind['ema100_4h']:,.0f}). "
        f"20-day range {r20['low']:,.0f}-{r20['high']:,.0f}; price is in its {third} third.",
        "",
        f"KEY LEVELS (sources within ${ZONE_MERGE_DOLLARS:.0f} merged into one zone; zones >= ${MIN_LEVEL_GAP_ATR * ind['atr14_1d']:.0f} apart)",
        "Resistance:",
        *[level_line(z) for z in resistances],
        "Support:",
        *[level_line(z) for z in supports],
        f"Pivot (classic, session {p['session']} UTC): P {p['P']:,.0f} | R1 {p['R1']:,.0f} R2 {p['R2']:,.0f} | "
        f"S1 {p['S1']:,.0f} S2 {p['S2']:,.0f}",
        "",
        "INDICATORS",
        f"- 1h EMA20/50/100/200: {ema[20]:,.2f} / {ema[50]:,.2f} / {ema[100]:,.2f} / {ema[200]:,.2f}",
        f"- 4h EMA100 {ind['ema100_4h']:,.2f}, 4h SMA100 {ind['sma100_4h']:,.2f}; "
        f"daily SMA20 {ind['sma20_1d']:,.2f}, SMA50 {ind['sma50_1d']:,.2f}",
        f"- RSI(14): 1h {ind['rsi_1h']}, 4h {ind['rsi_4h']}",
        f"- MACD(12,26,9) 1h: {ind['macd_1h']:+.2f} vs signal {ind['macd_signal_1h']:+.2f} "
        f"(histogram {ind['macd_hist_1h']:+.2f}, {hist_dir})",
        f"- Daily ATR(14) ${ind['atr14_1d']:,.0f} -> stop buffer ${snap['buffer']:.0f}",
        f"- Bias inputs: {'; '.join(reasons)}",
        "",
        "PLAN (zones are fade levels; the stop of each fade is the breakout trigger the other way)",
        *plan_lines,
        "",
        "CONTEXT",
        *[f"- {line}" for line in macro_lines],
        "",
        "REVIEW",
        *review_lines,
    ]
    return "\n".join(out)


def main(dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    snap = compute_snapshot()
    price = snap["price"]
    min_gap = MIN_LEVEL_GAP_ATR * snap["indicators"]["atr14_1d"]
    resistances, supports = _split_zones(snap["zones"], price, min_gap)
    bias = _bias(price, snap["indicators"])
    scenarios = build_scenarios(resistances, supports, snap["buffer"])

    prev = None
    if not dry_run:
        from storage import get_latest_ta_forecast, init_db, insert_ta_forecast

        init_db()
        prev = get_latest_ta_forecast()
    review_lines, review = review_previous(prev) if not dry_run else (["(dry run: previous forecast not read)"], None)

    analysis = render(snap, bias, resistances, supports, scenarios, review_lines, _macro_context(), now)
    levels = {
        "price": price,
        "price_ts": snap["price_ts"],
        "bias": bias[1],
        "bias_score": bias[0],
        "buffer": snap["buffer"],
        "indicators": snap["indicators"],
        "resistances": resistances,
        "supports": supports,
        "scenarios": scenarios,
        "review": review,
    }
    print(analysis)
    if dry_run:
        return
    insert_ta_forecast(now, now.astimezone(DISPLAY_TZ).date(), analysis, levels)
    print("[ta_forecast_job] Saved to ta_forecasts")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
