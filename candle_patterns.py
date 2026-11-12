"""Pure-Python candlestick pattern detection (12 Nov 2026) -- no TA-Lib dependency (it needs a C library, awkward on GitHub Actions / Streamlit Cloud).

Four objective patterns, vectorised over a DataFrame with open/high/low/close columns (oldest first). Rules follow TA-Lib's CDLENGULFING / CDLHAMMER /
CDLSHOOTINGSTAR / CDLDOJI / inside-bar definitions in spirit; `candle_pattern_study.py --parity` measures how often they agree with TA-Lib.

    bull_engulfing / bear_engulfing : opposite-colour candle whose body covers the previous body (and is bigger).
    hammer / shooting_star          : body <= 1/3 of the range, one wick >= 2x the body and >= 60% of the range (long lower wick = hammer = bullish,
                                      long upper wick = shooting star = bearish).
    doji                            : body <= 10% of the range (indecision, no direction).
    inside_bar                      : high/low both inside the previous candle (no direction).
A candle smaller than `min_range_atr` x ATR (when an ATR is given) is "noise" and gets no pattern. Used for LOG ONLY (entry_context.shadow_candle_pattern())."""

import pandas as pd

BULLISH = {"bull_engulfing", "hammer"}
BEARISH = {"bear_engulfing", "shooting_star"}
PATTERNS = ["bull_engulfing", "bear_engulfing", "hammer", "shooting_star", "doji", "inside_bar"]


def detect(df: pd.DataFrame, atr: pd.Series | None = None, min_range_atr: float = 0.5) -> pd.DataFrame:
    """Boolean DataFrame, one column per pattern in PATTERNS, same index as df."""
    o, h, l, c = (df[k].astype(float) for k in ("open", "high", "low", "close"))
    rng = h - l
    body = (c - o).abs()
    top, bot = pd.concat([o, c], axis=1).max(axis=1), pd.concat([o, c], axis=1).min(axis=1)
    upper, lower = h - top, bot - l
    ok = rng > 0
    if atr is not None:
        ok &= rng >= min_range_atr * atr
    po, pc, ph, pl = o.shift(), c.shift(), h.shift(), l.shift()
    pbody = (pc - po).abs()
    small_body = body <= rng / 3
    out = pd.DataFrame(index=df.index)
    out["bull_engulfing"] = ok & (pc < po) & (c > o) & (o <= pc) & (c >= po) & (body > pbody)
    out["bear_engulfing"] = ok & (pc > po) & (c < o) & (o >= pc) & (c <= po) & (body > pbody)
    out["hammer"] = ok & small_body & (lower >= 2 * body) & (lower >= 0.6 * rng) & (upper <= rng * 0.2)
    out["shooting_star"] = ok & small_body & (upper >= 2 * body) & (upper >= 0.6 * rng) & (lower <= rng * 0.2)
    out["doji"] = ok & (body <= 0.1 * rng)
    out["inside_bar"] = ok & (h < ph) & (l > pl)
    return out.fillna(False)


def label(row: pd.Series) -> str:
    """The first pattern set on a row of detect()'s output (directional ones first), else 'none'."""
    for name in PATTERNS:
        if bool(row.get(name, False)):
            return name
    return "none"


def direction(pattern: str) -> int:
    return 1 if pattern in BULLISH else -1 if pattern in BEARISH else 0
