"""Candlestick-pattern study (12 Nov 2026, read-only, changes nothing). Run:

    python candle_pattern_study.py --parity   # candle_patterns.py vs TA-Lib (needs `pip install ta-lib`, local only): how often do they agree?
    python candle_pattern_study.py --edge     # do the patterns predict gold's next 15-min candles? (52 days of Twelve Data candles, 7am-5pm ET weekdays)
    python candle_pattern_study.py --trades   # Broker F's closed live trades: pnl when a supporting reversal candle preceded the entry vs not (needs DATABASE_URL; run in Actions)

`--edge` forward move = (close k candles later - close of the pattern candle) x pattern direction, in ATR(14) units, with a t-stat against zero; baseline is every
candle in the same hours given a random-looking direction, i.e. mean 0, so a pattern is only interesting if t is about 2 or more AND it holds on n >= 100."""

import sys
from math import sqrt

import numpy as np
import pandas as pd

import candle_patterns as cp
from data_fetcher import compute_atr, fetch_candles

TZ = "America/New_York"
HORIZONS = (1, 2, 4)


def frame() -> pd.DataFrame:
    df = fetch_candles("XAU/USD", "15min", 5000).reset_index(drop=True)
    df["atr"] = compute_atr(df)
    return df


def parity() -> None:
    import talib

    df = frame()
    mine = cp.detect(df)  # no ATR filter here, so the comparison is on shape alone
    args = [df[k].to_numpy(float) for k in ("open", "high", "low", "close")]
    ref = {
        "bull_engulfing": talib.CDLENGULFING(*args) > 0, "bear_engulfing": talib.CDLENGULFING(*args) < 0,
        "hammer": talib.CDLHAMMER(*args) != 0, "shooting_star": talib.CDLSHOOTINGSTAR(*args) != 0, "doji": talib.CDLDOJI(*args) != 0,
    }
    print(f"{len(df)} candles; pure-Python vs TA-Lib (flags per pattern)")
    for name, r in ref.items():
        m = mine[name].to_numpy()
        both, only_m, only_t = int((m & r).sum()), int((m & ~r).sum()), int((~m & r).sum())
        print(f"  {name:15s} both={both:4d} mine-only={only_m:4d} talib-only={only_t:4d}")


def edge() -> None:
    df = frame()
    det = cp.detect(df, df["atr"])
    local = df["datetime"].dt.tz_convert(TZ)
    inside = (local.dt.hour >= 7) & (local.dt.hour < 17) & (local.dt.weekday < 5)
    print(f"{len(df)} candles, {df['datetime'].min():%d %b} - {df['datetime'].max():%d %b %Y}, {int(inside.sum())} in 7am-5pm ET")
    print(f"forward move in pattern direction, ATR units (mean [t], hit%) -- directional patterns only")
    for name in ("bull_engulfing", "bear_engulfing", "hammer", "shooting_star"):
        side = cp.direction(name)
        idx = det.index[det[name] & inside]
        parts = []
        for k in HORIZONS:
            moves = []
            for i in idx:
                if i + k < len(df) and df["atr"].iloc[i] > 0:
                    moves.append(side * (df["close"].iloc[i + k] - df["close"].iloc[i]) / df["atr"].iloc[i])
            a = np.array(moves)
            if len(a) < 3:
                parts.append(f"+{k}: n={len(a)}")
                continue
            t = a.mean() / (a.std(ddof=1) / sqrt(len(a)))
            parts.append(f"+{k}: n={len(a)} {a.mean():+.2f} [t {t:+.1f}] {100 * (a > 0).mean():.0f}%")
        print(f"  {name:15s} " + " | ".join(parts))
    # Pooled: any bullish vs any bearish pattern, direction-adjusted
    for k in HORIZONS:
        moves = []
        for name in ("bull_engulfing", "bear_engulfing", "hammer", "shooting_star"):
            side = cp.direction(name)
            for i in det.index[det[name] & inside]:
                if i + k < len(df) and df["atr"].iloc[i] > 0:
                    moves.append(side * (df["close"].iloc[i + k] - df["close"].iloc[i]) / df["atr"].iloc[i])
        a = np.array(moves)
        if len(a) > 2:
            print(f"  ALL directional, +{k}: n={len(a)} {a.mean():+.2f} ATR [t {a.mean() / (a.std(ddof=1) / sqrt(len(a))):+.1f}] {100 * (a > 0).mean():.0f}% hit")


def trades() -> None:
    import storage

    df = frame()
    det = cp.detect(df, df["atr"])
    df["label"] = [cp.label(det.iloc[i]) for i in range(len(df))]
    rows = []
    with storage.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, rule_name, trade_type, open_ts, pnl FROM forex_b_trades WHERE status='Closed' AND mode='live' AND pnl IS NOT NULL ORDER BY open_ts")
        for tid, rule, tt, ots, pnl in cur.fetchall():
            closed = df[df["datetime"] + pd.Timedelta(minutes=15) <= ots]  # candles already finished at the entry
            if len(closed) < 2:
                continue
            side = 1 if tt == "Buy" else -1
            pats = [closed["label"].iloc[-1], closed["label"].iloc[-2]]
            support = any(cp.direction(p) == side for p in pats)
            against = any(cp.direction(p) == -side for p in pats)
            rows.append({"id": tid, "rule": rule, "support": support, "against": against, "pnl": float(pnl), "pat": pats[0]})
    t = pd.DataFrame(rows)
    print(f"{len(t)} closed Broker F trades with candles")
    for title, mask in (("supporting reversal in last 2 closed candles", t["support"]), ("opposing reversal", t["against"])):
        for flag in (True, False):
            s = t[mask == flag]
            print(f"  {title}: {flag!s:5s} n={len(s):3d} total ${s['pnl'].sum():+.2f} avg ${s['pnl'].mean() if len(s) else 0:+.2f} wins {100 * (s['pnl'] > 0).mean() if len(s) else 0:.0f}%")
    print("by rule, supporting vs not:")
    print(t.groupby(["rule", "support"])["pnl"].agg(["count", "sum", "mean"]).round(2).to_string())


if __name__ == "__main__":
    {"--parity": parity, "--edge": edge, "--trades": trades}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
