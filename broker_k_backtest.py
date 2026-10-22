"""Broker K backtest: would a short-term RSI/ADX/ATR trend-follower on gold spot have made money after spread?

Broker K (design discussion, 22 Oct 2026) is a paper broker that enters when gold is trending (ADX high and
rising), volatile enough to pay for its tiny target (ATR floor) but not chaotic (ATR ceiling), and RSI says
which way (above 50 up, below 50 down, not yet exhausted), then exits on a very small fixed take-profit /
stop-loss or a time stop. Nothing here is wired into the poll; this only replays history to decide whether the
idea has an edge and which values to start from.

Method (mirrors what the live poll would see):
  - 1-minute XAU/USD bars from Twelve Data (mid prices; `--fetch` downloads and caches ~44 days).
  - A "poll" every 5 minutes, 7:00am-5:00pm ET weekdays (the brokers' trading window). At each poll the last 96
    15-minute candles (the forming one included, as Twelve Data returns it) give RSI(14)/ADX(14)/ATR(14) via the
    real `data_fetcher` functions. ADX "rising" = above the previous 15-min candle's, as Broker B's ADX filter.
  - The fill is the open of the 1-minute bar `--delay` minutes after the poll (the poll runs a little late), on the
    ask for a Buy / the bid for a Sell; exits scan the following 1-minute bars side-correctly (a Buy exits on the
    bid, a Sell on the ask), the way price_bars.py/broker._find_exit() do. Half the assumed spread is added each side.
    A bar that touches both take-profit and stop-loss counts as the stop (conservative).
  - One trade at a time; a new one can open at the first poll at or after the exit plus the cooldown.
  - Parameter sweep on the first ~2/3 of the days, reported on the last ~1/3 untouched, so a lucky grid cell is
    visible as one (large train profit, test profit gone).

Usage:  python broker_k_backtest.py --fetch          # download bars (needs TWELVE_DATA_API_KEY, ~15 API calls)
        python broker_k_backtest.py                  # sweep + report
The bar cache path is BROKER_K_CACHE (default broker_k_gold1m.pkl next to this file, gitignored).
"""

import argparse
import itertools
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from data_fetcher import compute_adx, compute_atr, compute_rsi

CACHE = os.environ.get("BROKER_K_CACHE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker_k_gold1m.pkl")
ET = ZoneInfo("America/New_York")
WINDOW_START_HOUR, WINDOW_END_HOUR = 7, 17
CANDLES = 96  # what data_fetcher.fetch_gold_candles() returns by default
HORIZON = 61  # bars to look ahead per entry (covers the longest time stop)


def fetch(days: int = 44) -> None:
    import requests

    key = os.environ["TWELVE_DATA_API_KEY"]
    end = datetime.now(timezone.utc).replace(microsecond=0)
    cursor = end - timedelta(days=days)
    parts = []
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=3), end)
        payload = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": "XAU/USD", "interval": "1min", "outputsize": 5000, "timezone": "UTC",
                "start_date": cursor.strftime("%Y-%m-%d %H:%M:%S"), "end_date": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            },
            headers={"Authorization": f"apikey {key}"},
            timeout=30,
        ).json()
        if payload.get("status") == "ok":
            parts.append(pd.DataFrame(payload["values"]))
            cursor = chunk_end
        elif "credits" in str(payload).lower():  # per-minute cap: wait and retry the same chunk
            time.sleep(30)
            continue
        else:
            raise RuntimeError(payload)
        time.sleep(9)  # free tier: 8 calls a minute
    df = pd.concat(parts)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True).to_pickle(CACHE)
    print(f"saved {len(df)} bars to {CACHE}")


def poll_snapshots(bars: pd.DataFrame, delay: int) -> pd.DataFrame:
    """One row per simulated poll: time, RSI/ADX/ATR, ADX rising, and the index of the fill bar."""
    b = bars.set_index("datetime")
    c15 = b.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    times = bars["datetime"].to_numpy()
    rows = []
    first_poll = c15.index[0] + pd.Timedelta(hours=26)  # indicator warm-up
    for t in pd.date_range(first_poll.ceil("5min"), bars["datetime"].iloc[-1], freq="5min"):
        local = t.tz_localize("UTC").astimezone(ET)
        if local.weekday() >= 5 or not (WINDOW_START_HOUR <= local.hour < WINDOW_END_HOUR):
            continue
        fill_idx = int(np.searchsorted(times, np.datetime64(t + pd.Timedelta(minutes=delay))))
        if fill_idx + HORIZON >= len(bars):
            continue
        slot = t.floor("15min")
        done = c15[c15.index < slot].tail(CANDLES - 1)
        forming = b[(b.index >= slot) & (b.index < t)]
        if forming.empty:
            continue
        cur = pd.DataFrame(
            [{"open": forming["open"].iloc[0], "high": forming["high"].max(), "low": forming["low"].min(), "close": forming["close"].iloc[-1]}],
            index=[slot],
        )
        frame = pd.concat([done, cur])
        adx = compute_adx(frame).dropna()
        if len(adx) < 2:
            continue
        rows.append({
            "t": t, "rsi": float(compute_rsi(frame["close"]).iloc[-1]), "adx": float(adx.iloc[-1]),
            "adx_rising": bool(adx.iloc[-1] > adx.iloc[-2]), "atr": float(compute_atr(frame).dropna().iloc[-1]),
            "fill_idx": fill_idx,
        })
    return pd.DataFrame(rows)


def outcomes(bars: pd.DataFrame, snaps: pd.DataFrame, tp: float, sl: float, tstop: int, half: float):
    """(pnl, exit_time) arrays for a Buy and a Sell opened at every snapshot, for one exit setting."""
    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    times = bars["datetime"].to_numpy()
    n = len(snaps)
    res = {}
    for side in ("buy", "sell"):
        pnl = np.zeros(n)
        exit_t = np.empty(n, dtype="datetime64[ns]")
        for k, i in enumerate(snaps["fill_idx"].to_numpy()):
            sl_ = slice(i, i + tstop)
            if side == "buy":
                entry = o[i] + half
                fav = h[sl_] - half - entry  # bid high vs entry
                adv = entry - (l[sl_] - half)  # bid low
                close_pnl = c[i + tstop - 1] - half - entry
            else:
                entry = o[i] - half
                fav = entry - (l[sl_] + half)  # ask low
                adv = (h[sl_] + half) - entry  # ask high
                close_pnl = entry - (c[i + tstop - 1] + half)
            hit_sl = np.flatnonzero(adv >= sl)
            hit_tp = np.flatnonzero(fav >= tp)
            j_sl = hit_sl[0] if len(hit_sl) else tstop
            j_tp = hit_tp[0] if len(hit_tp) else tstop
            if j_sl == tstop and j_tp == tstop:
                pnl[k], j = close_pnl, tstop - 1
            elif j_sl <= j_tp:  # stop first, or the same bar (conservative)
                pnl[k], j = -sl, j_sl
            else:
                pnl[k], j = tp, j_tp
            exit_t[k] = times[i + j] + np.timedelta64(1, "m")
        res[side] = (pnl, exit_t)
    return res


def simulate(snaps: pd.DataFrame, res, p: dict, lo: int, hi: int):
    """Walk the polls in [lo, hi) taking signals one at a time. Returns the list of trade pnls."""
    rsi, adx, rising, atr = (snaps[k].to_numpy() for k in ("rsi", "adx", "adx_rising", "atr"))
    t = snaps["t"].to_numpy().astype("datetime64[ns]")
    base = (adx >= p["adx_min"]) & (atr >= p["atr_min"]) & (atr <= p["atr_max"])
    if p["adx_rising"]:
        base &= rising
    buy = base & (rsi >= p["rsi_lo"]) & (rsi <= p["rsi_hi"])
    sell = base & (rsi <= 100 - p["rsi_lo"]) & (rsi >= 100 - p["rsi_hi"])
    free_at = np.datetime64("1970-01-01T00:00:00", "ns")
    out = []
    cooldown = np.timedelta64(p["cooldown"], "m")
    for k in np.flatnonzero(buy | sell):
        if k < lo or k >= hi or t[k] < free_at:
            continue
        side = "buy" if buy[k] else "sell"
        pnl, exit_t = res[side]
        out.append(pnl[k])
        free_at = exit_t[k] + cooldown
    return out


def stats(pnls) -> dict:
    a = np.array(pnls, dtype=float)
    if len(a) == 0:
        return {"n": 0, "win": 0.0, "total": 0.0, "avg": 0.0, "dd": 0.0}
    eq = np.cumsum(a)
    return {"n": len(a), "win": float((a > 0).mean()), "total": float(a.sum()), "avg": float(a.mean()),
            "dd": float((np.maximum.accumulate(eq) - eq).max())}


def fmt(s: dict) -> str:
    return f"n={s['n']:4d} win={s['win']:5.1%} total={s['total']:+8.1f} avg={s['avg']:+6.3f} maxDD={s['dd']:6.1f}"


GRID = {
    "adx_min": [20, 25, 30, 35],
    "adx_rising": [False, True],
    "rsi_lo": [50, 55, 60],
    "rsi_hi": [65, 70, 100],
    "atr_min": [2, 3, 4, 5],
    "atr_max": [8, 12, 100],
    "cooldown": [0, 10, 20],
}
EXITS = [(2, 2), (3, 3), (3, 4), (4, 4), (4, 6), (5, 5), (6, 6), (3, 6)]
TSTOPS = [10, 15, 30]
DEFAULT = {"adx_min": 25, "adx_rising": True, "rsi_lo": 55, "rsi_hi": 68, "atr_min": 4, "atr_max": 12, "cooldown": 10}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--spread", type=float, default=0.4, help="assumed bid-ask spread in dollars")
    ap.add_argument("--delay", type=int, default=1, help="minutes between the poll and the fill")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    if args.fetch:
        fetch()
        return
    bars = pd.read_pickle(CACHE)
    bars["datetime"] = bars["datetime"].dt.tz_convert(None)  # naive UTC keeps the numpy time math simple
    half = args.spread / 2
    snaps = poll_snapshots(bars, args.delay)
    split = int(len(snaps) * 2 / 3)
    print(f"{len(snaps)} polls {snaps['t'].iloc[0]:%Y-%m-%d} .. {snaps['t'].iloc[-1]:%Y-%m-%d}; train < poll {split} <= test; "
          f"spread ${args.spread}, fill delay {args.delay} min")
    print("signal-eligible polls: ADX>=25 %.0f%%, ATR 4-12 %.0f%%" % (
        100 * (snaps["adx"] >= 25).mean(), 100 * snaps["atr"].between(4, 12).mean()))

    cache = {(tp, sl, ts): outcomes(bars, snaps, tp, sl, ts, half) for (tp, sl), ts in itertools.product(EXITS, TSTOPS)}

    # Null: no filter at all, always take the next poll's Buy -- what the spread alone costs.
    print("\nNull (no entry filter, Buy at every free poll):")
    for (tp, sl), ts in itertools.product([(3, 3), (4, 4)], [15]):
        res = cache[(tp, sl, ts)]
        nul = {"adx_min": 0, "adx_rising": False, "rsi_lo": 0, "rsi_hi": 100, "atr_min": 0, "atr_max": 1e9, "cooldown": 0}
        print(f"  TP {tp}/SL {sl}/{ts}m  {fmt(stats(simulate(snaps, res, nul, 0, len(snaps))))}")

    print("\nStarting-point config", DEFAULT, "by exit setting (train | test):")
    for (tp, sl), ts in itertools.product(EXITS, TSTOPS):
        res = cache[(tp, sl, ts)]
        a, b = stats(simulate(snaps, res, DEFAULT, 0, split)), stats(simulate(snaps, res, DEFAULT, split, len(snaps)))
        print(f"  TP {tp}/SL {sl}/{ts:2d}m  train {fmt(a)}\n{' ' * 18}test  {fmt(b)}")

    keys = list(GRID)
    rows = []
    for vals in itertools.product(*GRID.values()):
        p = dict(zip(keys, vals))
        if p["rsi_hi"] <= p["rsi_lo"] or p["atr_max"] <= p["atr_min"]:
            continue
        for (tp, sl), ts in itertools.product(EXITS, TSTOPS):
            res = cache[(tp, sl, ts)]
            tr = stats(simulate(snaps, res, p, 0, split))
            if tr["n"] < 40:
                continue
            rows.append((tr["total"], p, (tp, sl, ts), tr))
    rows.sort(key=lambda r: -r[0])
    print(f"\nTop {args.top} of {len(rows)} grid cells by TRAIN profit (n>=40), with their untouched TEST result:")
    for total, p, ex, tr in rows[: args.top]:
        te = stats(simulate(snaps, cache[ex], p, split, len(snaps)))
        print(f"  {p} exit TP{ex[0]}/SL{ex[1]}/{ex[2]}m\n    train {fmt(tr)}\n    test  {fmt(te)}")
    held = [simulate(snaps, cache[r[2]], r[1], split, len(snaps)) for r in rows if r[0] > 0]
    print(f"\n{len(held)}/{len(rows)} grid cells were profitable in train; of those, profitable in test: "
          f"{sum(1 for h in held if sum(h) > 0)}/{len(held)} (median test total {np.median([sum(h) for h in held]) if held else 0:+.1f})")


if __name__ == "__main__":
    main()
