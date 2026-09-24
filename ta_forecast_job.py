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

Triggered externally by cron-job.org via .github/workflows/ta_forecast.yml twice each weekday --
12:00am ET (Morning) and 12:00pm ET (Midday) -- same pattern as every other scheduled job in this repo.
Each run grades whichever forecast came before it, so the midday run grades the morning plan and the
next morning's run grades the midday one. Uses 4 Twelve Data calls per run (5 when grading a previous
forecast) -- negligible against the 800/day cap.

The saved text is also sent to Telegram (prefixed with rules.XAUUSD_ALERT_PREFIX, like every other
spot-gold message), after the row is saved.

Run: python ta_forecast_job.py            # generate + save to Postgres + send to Telegram
     python ta_forecast_job.py --dry-run  # generate + print only (no DB, no Telegram)
"""

import math
import sys
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd

from config import GOLD_SPOT_SYMBOL
from data_fetcher import compute_rsi, fetch_candles, fetch_daily_history
from notifier import send_telegram_message
from rules import XAUUSD_ALERT_PREFIX

DISPLAY_TZ = ZoneInfo("America/New_York")
MIDDAY_FROM_HOUR_ET = 10     # runs from 10:00 ET on are labelled Midday, earlier ones Morning
TELEGRAM_MAX_CHARS = 4000    # Telegram's hard limit is 4096 per message

# Diagram sizing -- a fixed mobile width (dashboard.py embeds this raw, no horizontal scroll), not
# the hand-placed per-run coordinates a static reference image would use, so it renders unattended.
DIAGRAM_WIDTH = 380
DIAGRAM_PLOT_HEIGHT = 340
DIAGRAM_TOP = 26
DIAGRAM_AXIS_X = 46
DIAGRAM_BAND_X = 52
DIAGRAM_BAND_WIDTH = 80
DIAGRAM_LABEL_X = 140
DIAGRAM_MIN_LABEL_GAP = 13   # px between stacked zone-label rows, so close zones never overlap
DIAGRAM_LEGEND_HEIGHT = 50
DIAGRAM_CANDLE_BODY_WIDTH = 14        # the optional day-candle sits inside the band column, not a
                                      # separate side margin -- see render_diagram_svg()'s docstring
DIAGRAM_COLOR_RESISTANCE = "#cf222e"  # same red/green as dashboard.py's up/down cells, and (below) a
DIAGRAM_COLOR_SUPPORT = "#1a7f37"     # bearish/bullish day candle
DIAGRAM_COLOR_PRICE = "#9c700c"
DIAGRAM_COLOR_MUTED = "#767c82"
DIAGRAM_COLOR_INK = "#1c2125"
DIAGRAM_COLOR_RULE = "#d5d9d1"

EMA_PERIODS = (20, 50, 100, 200)
ZONE_MERGE_DOLLARS = 6.0     # candidate levels this close together are shown as one "4318/4315" zone
LEVELS_PER_SIDE = 4          # resistance/support zones listed each side of price
SWING_LOOKBACK_4H = 180      # ~30 trading days of 4h bars scanned for swing highs/lows
SWING_WING = 5               # a swing high/low must beat this many 4h bars (~20h) on each side
ROUND_NUMBER_STEP = 50       # $4,300 / $4,350 style psychological levels
MIN_STOP_BUFFER = 5.0        # stop distance beyond a zone, floored here, else 10% of daily ATR
MIN_LEVEL_GAP_ATR = 0.15     # listed zones at least this x daily ATR apart (~$15 at a $100 ATR)
DAILY_SWING_LOOKBACK = 125   # ~6 months of finished weekday daily bars scanned for daily swing points
DAILY_SWING_WING = 3         # a daily swing high/low must beat this many days on each side

# How much a level source counts when two candidate zones are too close to both be listed -- the
# heavier one is kept. Longer-timeframe / more widely watched levels weigh more.
LABEL_WEIGHTS = {
    "20-day high": 3, "20-day low": 3,
    "prior-day high": 2, "prior-day low": 2,
    "1h EMA200": 2, "4h EMA100": 2, "daily SMA20": 2, "daily SMA50": 2, "pivot P": 2,
    "daily SMA100": 2, "daily SMA200": 3,
}
# Daily swing labels carry their date and role ("Jul 6 swing high, now support"), so they're weighted
# by prefix rather than an exact LABEL_WEIGHTS key.
DAILY_SWING_WEIGHT = 2


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


def _swing_levels(bars: pd.DataFrame, wing: int = SWING_WING) -> tuple[list[float], list[float]]:
    """Fractal swing highs/lows: a bar whose high (low) beats `wing` bars on each side."""
    highs, lows = bars["high"].to_numpy(), bars["low"].to_numpy()
    swing_highs, swing_lows = [], []
    for i in range(wing, len(bars) - wing):
        window = slice(i - wing, i + wing + 1)
        if highs[i] == highs[window].max():
            swing_highs.append(float(highs[i]))
        if lows[i] == lows[window].min():
            swing_lows.append(float(lows[i]))
    return swing_highs, swing_lows


def _daily_swing_candidates(daily: pd.DataFrame, price: float) -> list[tuple[float, str]]:
    """Daily-chart swing highs/lows over the last ~6 months, dated, with role reversal spelled out --
    an old swing high now below price is support ("Jul 6 swing high, now support"), an old swing low
    now above it is resistance. Gives the ladder real levels beyond the ~30-day reach of the 4h swings,
    where it otherwise only had round numbers (a third-party reference used exactly these: the Jul 6
    high $4,202 and the Jul 29 low $3,996)."""
    bars = daily.tail(DAILY_SWING_LOOKBACK).reset_index(drop=True)
    out = []
    for i in range(DAILY_SWING_WING, len(bars) - DAILY_SWING_WING):
        window = bars.iloc[i - DAILY_SWING_WING:i + DAILY_SWING_WING + 1]
        day = f"{bars['datetime'].iloc[i]:%b} {bars['datetime'].iloc[i].day}"
        high, low = float(bars["high"].iloc[i]), float(bars["low"].iloc[i])
        if high == window["high"].max():
            out.append((high, f"{day} swing high" + (", now support" if high < price else "")))
        if low == window["low"].min():
            out.append((low, f"{day} swing low" + (", now resistance" if low > price else "")))
    return out


def _completed_weekday_bars(daily: pd.DataFrame) -> pd.DataFrame:
    """Twelve Data's XAU/USD daily series (UTC) includes near-empty Saturday/Sunday bars and today's
    still-forming bar -- drop both so pivots/ATR/prior-day levels come from real, finished sessions."""
    today = datetime.now(timezone.utc).date()
    mask = (daily["datetime"].dt.dayofweek < 5) & (daily["datetime"].dt.date < today)
    return daily[mask]


def compute_snapshot() -> dict:
    h1 = fetch_candles(GOLD_SPOT_SYMBOL, "1h", 500)
    h4 = fetch_candles(GOLD_SPOT_SYMBOL, "4h", 300)
    # 400 calendar bars (Twelve Data's daily XAU/USD series includes weekend stubs) leaves ~280 finished
    # weekdays -- enough for the 200-day SMA plus the daily RSI's warm-up.
    daily_all = fetch_candles(GOLD_SPOT_SYMBOL, "1day", 400)
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
        "sma100_1d": round(float(cd.rolling(100).mean().iloc[-1]), 2),
        "sma200_1d": round(float(cd.rolling(200).mean().iloc[-1]), 2),
        "rsi_1d": round(float(compute_rsi(cd).iloc[-1]), 1),
        "rsi_1d_prev": round(float(compute_rsi(cd).iloc[-2]), 1),
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
        (ind["sma100_1d"], "daily SMA100"),
        (ind["sma200_1d"], "daily SMA200"),
        (ind["prev_day"]["high"], "prior-day high"),
        (ind["prev_day"]["low"], "prior-day low"),
        (ind["range_20d"]["high"], "20-day high"),
        (ind["range_20d"]["low"], "20-day low"),
    ]
    candidates += [(v, f"pivot {k}") for k, v in ind["pivot"].items() if k != "session"]
    candidates += [(v, "4h swing high") for v in swing_highs]
    candidates += [(v, "4h swing low") for v in swing_lows]
    candidates += _daily_swing_candidates(daily, price)
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


def _label_weight(label: str) -> int:
    if " swing " in label and not label.startswith("4h"):
        return DAILY_SWING_WEIGHT
    return LABEL_WEIGHTS.get(label, 1)


def _weight(zone: dict) -> int:
    return sum(_label_weight(label) for label in zone["labels"])


def _zone_gap(a: dict, b: dict) -> float:
    """Distance between two zones' nearest edges (0 if they overlap)."""
    return max(0.0, max(a["low"], b["low"]) - min(a["high"], b["high"]))


def _pick_side(zones: list[dict], price: float, min_gap: float) -> list[dict]:
    """Keep zones at least `min_gap` apart -- otherwise every minor 4h swing makes the ladder a
    $5-step scalp list rather than the $15-30 steps the reference analyses use. Zones are accepted
    strongest-first (ties: nearer price first), each only if it clears every zone already accepted,
    then listed nearest-first. Strongest-first matters: comparing each zone only with its neighbour
    let a heavier zone replace a lighter one and then be replaced in turn, so a chain of close
    levels (e.g. 4256 -> 4253 -> 4238) could slide the pick far from the cluster and leave a gap."""
    def distance(zone: dict) -> float:
        return zone["low"] - price if zone["low"] > price else price - zone["high"]

    picked: list[dict] = []
    dropped: list[dict] = []
    for zone in sorted(zones, key=lambda z: (-_weight(z), distance(z))):
        if all(_zone_gap(zone, other) >= min_gap for other in picked):
            picked.append({**zone, "nearby": []})
        else:
            dropped.append(zone)
    # A dropped zone isn't lost: it's attached to the closest kept zone as "nearby", so e.g. a 1h
    # EMA200 hidden by an equally weighted pivot $6 away still shows up on that pivot's line.
    for zone in dropped:
        host = min(picked, key=lambda z: _zone_gap(zone, z))
        host["nearby"].append({"low": zone["low"], "high": zone["high"], "labels": zone["labels"]})
    for zone in picked:
        zone["nearby"].sort(key=distance)
    return sorted(picked, key=distance)[:LEVELS_PER_SIDE]


def _split_zones(zones: list[dict], price: float, min_gap: float) -> tuple[list[dict], list[dict]]:
    """Resistance/support ladders either side of price. A zone price is currently inside counts as
    resistance if price is in its lower half, support otherwise."""
    above = [z for z in zones if (z["low"] + z["high"]) / 2 > price]
    below = [z for z in zones if (z["low"] + z["high"]) / 2 <= price]
    return _pick_side(above, price, min_gap), _pick_side(below, price, min_gap)


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
        f"Previous forecast ({prev['levels'].get('session') or session_label(since)}) "
        f"{since.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M ET} "
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


def session_label(ts: datetime) -> str:
    """'Morning' or 'Midday', from the run's ET hour, so the two daily Telegram messages are easy to
    tell apart. Picked by clock time rather than passed in by the workflow, so a manual run (or a
    late cron trigger) still gets a sensible label."""
    return "Midday" if ts.astimezone(DISPLAY_TZ).hour >= MIDDAY_FROM_HOUR_ET else "Morning"


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


def _big_picture_lines(price: float, ind: dict) -> list[str]:
    """The daily-chart view, kept out of the -6..+6 bias score (all short-term inputs) so that score
    stays comparable day to day. Reads the long-term trend off the 200-day SMA and daily momentum off
    the daily RSI -- the frame a third-party reference used when short-term and long-term disagreed."""
    sma200 = ind["sma200_1d"]
    gap = price - sma200
    trend = "up" if gap > 0 else "down"
    rsi, rsi_prev = ind["rsi_1d"], ind["rsi_1d_prev"]
    rsi_side = "above" if rsi >= 50 else "below"
    rsi_dir = "rising" if rsi > rsi_prev else "falling"
    return [
        "BIG PICTURE (daily chart, finished days)",
        f"- Price is ${abs(gap):,.0f} ({abs(gap) / sma200:.1%}) {'above' if gap > 0 else 'below'} the 200-day SMA "
        f"({sma200:,.0f}): long-term trend {trend}.",
        f"- 50/100/200-day SMAs: {ind['sma50_1d']:,.0f} / {ind['sma100_1d']:,.0f} / {sma200:,.0f}.",
        f"- Daily RSI(14) {rsi} ({rsi_side} 50, {rsi_dir} from {rsi_prev}).",
    ]


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
        nearby = "".join(
            f"; nearby {_fmt_zone(n)}: {', '.join(n['labels'])}" for n in z.get("nearby", [])
        )
        return f"  {_fmt_zone(z):>11}  ({', '.join(z['labels'])}{nearby})"

    p = ind["pivot"]
    out = [
        f"XAU/USD technical forecast ({session_label(now)}) -- {now.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M ET}",
        f"Price {_fmt_price(price)} (Twelve Data 1h close).",
        "",
        f"SUMMARY: {label} (score {score:+d}/6). Price is {ema_note}, "
        f"{'above' if price > ind['ema100_4h'] else 'below'} the 4h EMA100 ({ind['ema100_4h']:,.0f}). "
        f"20-day range {r20['low']:,.0f}-{r20['high']:,.0f}; price is in its {third} third.",
        "",
        *_big_picture_lines(price, ind),
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
        f"daily SMA20 {ind['sma20_1d']:,.2f}, SMA50 {ind['sma50_1d']:,.2f}, "
        f"SMA100 {ind['sma100_1d']:,.2f}, SMA200 {ind['sma200_1d']:,.2f}",
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


# ---------------------------------------------------------------------------------------------------
# Diagram


def _short_zone_label(zone: dict) -> str:
    """First couple of source labels plus a '+N' count of anything else (other sources on this zone,
    or levels folded into it as 'nearby') -- the full list is still in the <title> tooltip."""
    shown = ", ".join(zone["labels"][:2])
    extra = len(zone["labels"]) - 2 + len(zone.get("nearby", []))
    return f"{shown} +{extra}" if extra > 0 else shown


def render_diagram_svg(
    price: float,
    resistances: list[dict],
    supports: list[dict],
    scenarios: list[dict],
    candle: dict | None = None,
) -> str:
    """Self-contained SVG price ladder: resistance zones above price in red, support zones below in
    green, a thin price line, and the two breakout/breakdown stop lines -- modelled on the reference
    diagram in docs/technical-analyst-forecast-log.md, redrawn from each run's real zones/price/stops
    at a fixed mobile width instead of that diagram's hand-tuned per-run coordinates. Zone bands (and
    the price line) sit at their true proportional price position; only the label rows are nudged
    apart (never more than DIAGRAM_MIN_LABEL_GAP) to stay legible when two rows land close together --
    price is laid out in that same pass, as just another row, since it commonly sits within a few
    dollars of the nearest zone. The price row is deliberately a hairline plus a label rather than a
    filled badge: an opaque block that width would sit on top of, and hide, whatever zone happens to
    be at the same height. `title` tags carry each zone's full label list (and any 'nearby' levels
    folded into it) as a hover tooltip -- inert on mobile, but free.

    `candle`, if given, is one day's {open, high, low, close} for gold spot (dashboard.py's historical
    date view overlays it; a live/current forecast never has one, since the day isn't finished) --
    drawn as an actual OHLC candlestick (wick + a hollow/outline-only body, green if close >= open else
    red, the same colors as the resistance/support bands) centered in the band column, at its true
    proportional price position, the same as everything else in this diagram -- it commonly overlaps
    one or more zone bands, which is deliberate (this is the normal way a candle and support/resistance
    zones are shown together on a real chart) and exactly why the body is outlined rather than filled:
    a solid body would hide whatever band sits behind it."""
    zones = [(z, True) for z in resistances] + [(z, False) for z in supports]
    stops = {sc["name"]: sc for sc in scenarios}
    stop_lines = [
        s for s in (stops.get("sell_resistance", {}).get("stop"), stops.get("buy_support", {}).get("stop"))
        if s is not None
    ]

    values = [price] + [v for z, _ in zones for v in (z["low"], z["high"])] + stop_lines
    if candle:
        values += [candle["open"], candle["high"], candle["low"], candle["close"]]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.1, 5.0)
    lo, hi = lo - pad, hi + pad

    def y_of(p: float) -> float:
        return DIAGRAM_TOP + (hi - p) / (hi - lo) * DIAGRAM_PLOT_HEIGHT

    span = hi - lo
    step = 100 if span > 500 else 50 if span > 200 else 25 if span > 80 else 10
    ticks = []
    t = math.ceil(lo / step) * step
    while t <= hi:
        ticks.append(t)
        t += step

    # Price is laid out in the same top-to-bottom pass as the zones (not drawn separately afterward)
    # so it gets the same DIAGRAM_MIN_LABEL_GAP anti-collision nudging -- it's common for price to sit
    # within a few dollars of the nearest zone.
    rows = [("zone", z, True) for z in resistances] + [("zone", z, False) for z in supports]
    rows.append(("price", None, None))

    bands, labels = [], []
    prev_label_y = None
    for kind, zone, is_resistance in sorted(rows, key=lambda r: -(price if r[0] == "price" else r[1]["low"])):
        if kind == "price":
            y = y_of(price)
            # A thin line, not a filled badge -- a solid block that width would sit on top of (and
            # hide) whatever zone band/label happens to be at the same height.
            bands.append(
                f'<line x1="{DIAGRAM_AXIS_X}" x2="{DIAGRAM_BAND_X + DIAGRAM_BAND_WIDTH}" y1="{y:.1f}" '
                f'y2="{y:.1f}" stroke="{DIAGRAM_COLOR_PRICE}" stroke-width="1.25"/>'
            )
            label_y = y + 3.3
            if prev_label_y is not None and label_y - prev_label_y < DIAGRAM_MIN_LABEL_GAP:
                label_y = prev_label_y + DIAGRAM_MIN_LABEL_GAP
            prev_label_y = label_y
            labels.append(
                f'<text x="{DIAGRAM_LABEL_X}" y="{label_y:.1f}" font-size="9.5" fill="{DIAGRAM_COLOR_INK}">'
                f'<tspan font-family="IBM Plex Mono, ui-monospace, monospace" font-weight="700" '
                f'fill="{DIAGRAM_COLOR_PRICE}">{price:,.2f}</tspan> current price</text>'
            )
            continue
        color = DIAGRAM_COLOR_RESISTANCE if is_resistance else DIAGRAM_COLOR_SUPPORT
        y_top, y_bot = y_of(zone["high"]), y_of(zone["low"])
        bands.append(
            f'<rect x="{DIAGRAM_BAND_X}" y="{y_top:.1f}" width="{DIAGRAM_BAND_WIDTH}" '
            f'height="{max(3.0, y_bot - y_top):.1f}" fill="{color}" fill-opacity="0.22" '
            f'stroke="{color}" stroke-width="1"/>'
        )
        label_y = (y_top + y_bot) / 2 + 3.3
        if prev_label_y is not None and label_y - prev_label_y < DIAGRAM_MIN_LABEL_GAP:
            label_y = prev_label_y + DIAGRAM_MIN_LABEL_GAP
        prev_label_y = label_y
        tooltip = ", ".join(zone["labels"])
        if zone.get("nearby"):
            tooltip += "; nearby " + "; ".join(f"{_fmt_zone(n)}: {', '.join(n['labels'])}" for n in zone["nearby"])
        labels.append(
            f'<text x="{DIAGRAM_LABEL_X}" y="{label_y:.1f}" font-size="9.5" fill="{DIAGRAM_COLOR_INK}">'
            f'<tspan font-family="IBM Plex Mono, ui-monospace, monospace" font-weight="600" '
            f'fill="{color}">{_fmt_zone(zone)}</tspan> {escape(_short_zone_label(zone))}'
            f'<title>{escape(tooltip)}</title></text>'
        )

    axis_ticks = "".join(
        f'<line x1="{DIAGRAM_AXIS_X - 4}" x2="{DIAGRAM_AXIS_X}" y1="{y_of(tk):.1f}" y2="{y_of(tk):.1f}" '
        f'stroke="{DIAGRAM_COLOR_RULE}"/>'
        f'<text x="{DIAGRAM_AXIS_X - 6}" y="{y_of(tk) + 3:.1f}" font-size="9" fill="{DIAGRAM_COLOR_MUTED}" '
        f'text-anchor="end">{tk:,.0f}</text>'
        for tk in ticks
    )
    stop_svg = "".join(
        f'<line x1="{DIAGRAM_BAND_X}" x2="{DIAGRAM_BAND_X + DIAGRAM_BAND_WIDTH}" y1="{y_of(s):.1f}" '
        f'y2="{y_of(s):.1f}" stroke="{DIAGRAM_COLOR_MUTED}" stroke-width="1" stroke-dasharray="4 3"/>'
        f'<text x="{DIAGRAM_BAND_X + 2}" y="{y_of(s) - 2:.1f}" font-size="7.5" fill="{DIAGRAM_COLOR_MUTED}">'
        f'{s:,.0f}</text>'
        for s in stop_lines
    )

    candle_svg = ""
    if candle:
        candle_x = DIAGRAM_BAND_X + DIAGRAM_BAND_WIDTH / 2
        bull = candle["close"] >= candle["open"]
        candle_color = DIAGRAM_COLOR_SUPPORT if bull else DIAGRAM_COLOR_RESISTANCE
        y_open, y_close = y_of(candle["open"]), y_of(candle["close"])
        y_high, y_low = y_of(candle["high"]), y_of(candle["low"])
        body_top, body_bottom = min(y_open, y_close), max(y_open, y_close)
        # Hollow body (fill="none"), not solid -- it commonly overlaps a zone band at this shared
        # x-position, and a filled body would hide it.
        candle_svg = (
            f'<line x1="{candle_x:.1f}" x2="{candle_x:.1f}" y1="{y_high:.1f}" y2="{y_low:.1f}" '
            f'stroke="{candle_color}" stroke-width="1.5"/>'
            f'<rect x="{candle_x - DIAGRAM_CANDLE_BODY_WIDTH / 2:.1f}" y="{body_top:.1f}" '
            f'width="{DIAGRAM_CANDLE_BODY_WIDTH}" height="{max(2.0, body_bottom - body_top):.1f}" '
            f'fill="none" stroke="{candle_color}" stroke-width="1.5"/>'
        )

    height = DIAGRAM_TOP + DIAGRAM_PLOT_HEIGHT + DIAGRAM_LEGEND_HEIGHT

    candle_legend = (
        f'<line x1="256" x2="256" y1="{height - 11}" y2="{height - 3}" stroke="{DIAGRAM_COLOR_INK}" '
        f'stroke-width="1.2"/>'
        f'<rect x="252" y="{height - 9}" width="8" height="4" fill="none" stroke="{DIAGRAM_COLOR_INK}" '
        f'stroke-width="1"/>'
        f'<text x="264" y="{height - 5}">Day candle</text>'
        if candle else ""
    )

    return (
        f'<svg viewBox="0 0 {DIAGRAM_WIDTH} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block" '
        f'font-family="IBM Plex Sans, Arial, sans-serif" role="img" '
        f'aria-label="Gold price ladder: resistance above {price:,.2f}, support below">'
        f'<line x1="{DIAGRAM_AXIS_X}" x2="{DIAGRAM_AXIS_X}" y1="{DIAGRAM_TOP}" '
        f'y2="{DIAGRAM_TOP + DIAGRAM_PLOT_HEIGHT}" stroke="{DIAGRAM_COLOR_RULE}"/>'
        f'{axis_ticks}{"".join(bands)}{stop_svg}{candle_svg}'
        f'{"".join(labels)}'
        f'<g font-size="9" fill="{DIAGRAM_COLOR_MUTED}">'
        f'<rect x="4" y="{height - 32}" width="10" height="10" fill="{DIAGRAM_COLOR_RESISTANCE}" fill-opacity="0.5"/>'
        f'<text x="18" y="{height - 23}">Resistance</text>'
        f'<rect x="90" y="{height - 32}" width="10" height="10" fill="{DIAGRAM_COLOR_SUPPORT}" fill-opacity="0.5"/>'
        f'<text x="104" y="{height - 23}">Support</text>'
        f'<line x1="170" x2="184" y1="{height - 27}" y2="{height - 27}" stroke="{DIAGRAM_COLOR_PRICE}" '
        f'stroke-width="1.25"/>'
        f'<text x="188" y="{height - 23}">Price</text>'
        f'<line x1="4" x2="18" y1="{height - 8}" y2="{height - 8}" stroke="{DIAGRAM_COLOR_MUTED}" '
        f'stroke-dasharray="4 3"/>'
        f'<text x="22" y="{height - 5}">Breakout/breakdown stop</text>'
        f'{candle_legend}'
        f'</g></svg>'
    )


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
        "session": session_label(now),
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
    diagram_svg = render_diagram_svg(price, resistances, supports, scenarios)
    insert_ta_forecast(now, now.astimezone(DISPLAY_TZ).date(), analysis, levels, diagram_svg)
    print("[ta_forecast_job] Saved to ta_forecasts")
    # Saved first, so a Telegram hiccup can't lose the forecast (or its grading of the next one).
    for chunk in _telegram_chunks(XAUUSD_ALERT_PREFIX + analysis):
        send_telegram_message(chunk)


def _telegram_chunks(text: str) -> list[str]:
    """Split on line boundaries into messages under Telegram's length limit -- a normal forecast is
    ~2-3k chars and fits in one, but a long review section shouldn't make the send fail."""
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > TELEGRAM_MAX_CHARS:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
