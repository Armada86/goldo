"""Broker K settings study (study 36, 3 Nov 2026) -- rerun at the monthly combined review.

Question: how do Broker K's RSI / ADX / ATR entry settings and its fixed take-profit / stop-loss / time stop perform, and can
they be improved? Observation only: this changes no rule or setting.

Two sources:
  * the live paper record (`broker_k_trades`, read over Neon's HTTPS SQL endpoint, as broker_review.py does, because a raw
    Postgres connection is blocked in the Claude Code sandbox) -- the real result, but only a handful of trades;
  * a replay of the live rule on Twelve Data 1-minute bars (mid prices plus an assumed spread), using the same engine as
    broker_k_backtest.py (simulated poll every 5 minutes, 7am-5pm ET weekdays, indicators from the last 96 15-minute candles
    including the forming one, side-correct bar scan, stop wins a same-bar tie, one trade at a time).

Because the settings were picked on 9 Sep - 22 Oct 2026, bars from `DESIGN_END` on are a genuine out-of-sample test of the
*current* rule; variants are judged by in-sample vs out-of-sample total, positive weeks, and whether the gain survives dropping
the best trades. The number of variants tried is printed: every extra variant raises the odds that the best one is luck.

Usage:  python broker_k_study.py --fetch [--days 60]   # download the 1-minute bars (needs TWELVE_DATA_API_KEY, ~20 calls)
        python broker_k_study.py                        # live record + replay + variants
        python broker_k_study.py --spread 0.8 --delay 2 # harsher fills
The bar cache is BROKER_K_CACHE (default broker_k_gold1m.pkl, gitignored). Add --no-live to skip the database.
"""

import argparse
import os
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import broker_k_backtest as bt
from config import (
    K_ADX_MIN, K_ATR_MAX, K_ATR_MIN, K_RSI_BUY_MAX, K_RSI_BUY_MIN, K_STOP_LOSS, K_TAKE_PROFIT, K_TIME_STOP_MINUTES,
)

ET = ZoneInfo("America/New_York")
DESIGN_END = np.datetime64("2026-10-22T20:00:00")  # the settings were chosen on data up to here; later polls are out of sample

CURRENT = {
    "adx_min": K_ADX_MIN, "adx_rising": False, "rsi_lo": K_RSI_BUY_MIN, "rsi_hi": K_RSI_BUY_MAX,
    "atr_min": K_ATR_MIN, "atr_max": K_ATR_MAX, "cooldown": 0,
    "tp": K_TAKE_PROFIT, "sl": K_STOP_LOSS, "tstop": K_TIME_STOP_MINUTES,
    "start_h": 7, "end_h": 17, "di": False, "trail": None, "tp_atr": None, "sl_atr": None,
}


def outcome_arrays(bars: pd.DataFrame, snaps: pd.DataFrame, half: float, tp, sl, tstop: int, trail=None) -> dict:
    """{'buy'|'sell': (pnl[], exit_time[])} for a trade opened at every snapshot. `tp` / `sl` are a number or a per-snapshot
    array. `trail` = (activation, distance): once the best price so far (bars before the current one) is `activation` in
    profit, the stop follows `distance` behind it and never loosens. Exit price is the limit level, a time stop closes at the
    last in-window bar's close, a bar touching both limits counts as the stop (same conventions as broker_k.scan_exit)."""
    o, h, lo, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    times = bars["datetime"].to_numpy()
    n = len(snaps)
    tp_a = np.broadcast_to(np.asarray(tp, dtype=float), (n,))
    sl_a = np.broadcast_to(np.asarray(sl, dtype=float), (n,))
    res = {}
    for side in ("buy", "sell"):
        pnl = np.zeros(n)
        exit_t = np.empty(n, dtype="datetime64[ns]")
        for k, i in enumerate(snaps["fill_idx"].to_numpy()):
            s = slice(i, i + tstop)
            if side == "buy":
                entry = o[i] + half
                fav, adv = h[s] - half - entry, entry - (lo[s] - half)
                close_pnl = c[i + tstop - 1] - half - entry
            else:
                entry = o[i] - half
                fav, adv = entry - (lo[s] + half), (h[s] + half) - entry
                close_pnl = entry - (c[i + tstop - 1] + half)
            stop = np.full(tstop, -sl_a[k])  # stop level as profit, per bar
            if trail is not None:
                peak_before = np.concatenate(([0.0], np.maximum.accumulate(fav)[:-1]))
                stop = np.where(peak_before >= trail[0], np.maximum(stop, peak_before - trail[1]), stop)
            hit_sl = np.flatnonzero(adv >= -stop)  # adverse excursion reaches the stop level
            hit_tp = np.flatnonzero(fav >= tp_a[k])
            j_sl = hit_sl[0] if len(hit_sl) else tstop
            j_tp = hit_tp[0] if len(hit_tp) else tstop
            if j_sl == tstop and j_tp == tstop:
                pnl[k], j = close_pnl, tstop - 1
            elif j_sl <= j_tp:
                pnl[k], j = stop[j_sl], j_sl
            else:
                pnl[k], j = tp_a[k], j_tp
            exit_t[k] = times[i + j] + np.timedelta64(1, "m")
        res[side] = (pnl, exit_t)
    return res


def prepare(snaps: pd.DataFrame) -> pd.DataFrame:
    loc = snaps["t"].dt.tz_localize("UTC").dt.tz_convert(ET)
    snaps = snaps.copy()
    snaps["hour_et"] = loc.dt.hour.to_numpy()
    snaps["week"] = loc.dt.strftime("%G-W%V").to_numpy()
    return snaps


def run(snaps: pd.DataFrame, res: dict, p: dict) -> pd.DataFrame:
    """Walk the polls taking signals one at a time; one row per trade (poll index, time, side, pnl, week)."""
    rsi, adx, atr = snaps["rsi"].to_numpy(), snaps["adx"].to_numpy(), snaps["atr"].to_numpy()
    ok = (adx >= p["adx_min"]) & (atr >= p["atr_min"]) & (atr <= p["atr_max"])
    ok &= (snaps["hour_et"].to_numpy() >= p["start_h"]) & (snaps["hour_et"].to_numpy() < p["end_h"])
    if p["adx_rising"]:
        ok &= snaps["adx_rising"].to_numpy()
    buy = ok & (rsi >= p["rsi_lo"]) & (rsi <= p["rsi_hi"])
    sell = ok & (rsi <= 100 - p["rsi_lo"]) & (rsi >= 100 - p["rsi_hi"])
    if p["di"]:
        up = snaps["di_up"].to_numpy()
        buy &= up
        sell &= ~up
    t = snaps["t"].to_numpy().astype("datetime64[ns]")
    free_at = np.datetime64("1970-01-01T00:00:00", "ns")
    cool = np.timedelta64(p["cooldown"], "m")
    rows = []
    for k in np.flatnonzero(buy | sell):
        if t[k] < free_at:
            continue
        side = "buy" if buy[k] else "sell"
        pnl, exit_t = res[side]
        rows.append({"k": k, "t": t[k], "side": side, "pnl": pnl[k], "week": snaps["week"].iloc[k]})
        free_at = exit_t[k] + cool
    return pd.DataFrame(rows, columns=["k", "t", "side", "pnl", "week"])


def summary(tr: pd.DataFrame) -> dict:
    if tr.empty:
        return {"n": 0, "total": 0.0, "avg": 0.0, "win": 0.0, "is": 0.0, "oos": 0.0, "n_oos": 0, "wk": "0/0", "no_top3": 0.0, "dd": 0.0}
    pnl = tr["pnl"].to_numpy()
    oos = tr["t"].to_numpy() >= DESIGN_END
    weeks = tr.groupby("week")["pnl"].sum()
    eq = np.cumsum(pnl)
    return {
        "n": len(tr), "total": pnl.sum(), "avg": pnl.mean(), "win": (pnl > 0).mean(), "is": pnl[~oos].sum(), "oos": pnl[oos].sum(),
        "n_oos": int(oos.sum()), "wk": f"{(weeks > 0).sum()}/{len(weeks)}", "no_top3": pnl.sum() - np.sort(pnl)[-3:].sum(),
        "dd": float((np.maximum.accumulate(eq) - eq).max()),
    }


def line(label: str, s: dict) -> str:
    return (f"  {label:<34} n={s['n']:3d} total={s['total']:+7.1f} avg={s['avg']:+6.2f} win={s['win']:4.0%}  "
            f"in-sample={s['is']:+7.1f}  out-of-sample={s['oos']:+7.1f} (n={s['n_oos']:2d})  weeks+={s['wk']:>5}  "
            f"w/o best 3={s['no_top3']:+7.1f}  maxDD={s['dd']:5.1f}")


class Study:
    def __init__(self, bars: pd.DataFrame, snaps: pd.DataFrame, half: float):
        self.bars, self.snaps, self.half = bars, snaps, half
        self._cache: dict = {}
        self.tried = 0

    def result(self, **over) -> dict:
        p = {**CURRENT, **over}
        if p["tp_atr"] is not None:
            key = ("atr", p["tp_atr"], p["sl_atr"], p["tstop"], p["trail"])
            tp, sl = p["tp_atr"] * self.snaps["atr"].to_numpy(), p["sl_atr"] * self.snaps["atr"].to_numpy()
        else:
            key = (p["tp"], p["sl"], p["tstop"], p["trail"])
            tp, sl = p["tp"], p["sl"]
        if key not in self._cache:
            self._cache[key] = outcome_arrays(self.bars, self.snaps, self.half, tp, sl, p["tstop"], p["trail"])
        self.tried += 1
        return summary(run(self.snaps, self._cache[key], p))

    def trades(self, **over) -> pd.DataFrame:
        p = {**CURRENT, **over}
        self.result(**over)
        key = (p["tp"], p["sl"], p["tstop"], p["trail"])
        return run(self.snaps, self._cache[key], p)


def live_record() -> None:
    from broker_review import _sql

    rows = _sql(
        "SELECT id, rule_name, entry_price, exit_price, open_ts, close_ts, pnl, exit_reason, entry_context FROM broker_k_trades "
        "WHERE status = 'Closed' ORDER BY id"
    )
    df = pd.DataFrame(rows)
    if df.empty:
        print("No closed live trades.")
        return
    df["pnl"] = df["pnl"].astype(float)
    ctx = pd.json_normalize(df["entry_context"].tolist())
    df = pd.concat([df.drop(columns=["entry_context"]), ctx], axis=1)
    df["held_min"] = (pd.to_datetime(df["close_ts"], format="ISO8601") - pd.to_datetime(df["open_ts"], format="ISO8601")).dt.total_seconds() / 60
    print(f"\n=== Live paper record: {len(df)} closed trades, total {df['pnl'].sum():+.2f}, avg {df['pnl'].mean():+.2f}, "
          f"win {(df['pnl'] > 0).mean():.0%}, spread median {df['spread'].median():.2f} ===")
    print(df.groupby("exit_reason")["pnl"].agg(["count", "sum", "mean"]).round(2).to_string())
    print(df.groupby("rule_name")["pnl"].agg(["count", "sum", "mean"]).round(2).to_string())
    df["late_entry"] = df["hour_et"] >= 15
    print(df.groupby("late_entry")["pnl"].agg(["count", "sum", "mean"]).round(2).to_string())
    print(df[["id", "rule_name", "hour_et", "adx14", "atr14", "rsi14", "adx_rising", "spread", "held_min", "exit_reason", "pnl"]]
          .round(2).to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--spread", type=float, default=0.4)
    ap.add_argument("--delay", type=int, default=1)
    ap.add_argument("--no-live", action="store_true")
    a = ap.parse_args()
    if a.fetch:
        bt.fetch(a.days)
        return
    if not a.no_live:
        live_record()

    bars = pd.read_pickle(bt.CACHE)
    bars["datetime"] = bars["datetime"].dt.tz_convert(None)
    snaps = prepare(bt.poll_snapshots(bars, a.delay))
    st = Study(bars, snaps, a.spread / 2)
    print(f"\nReplay: {len(snaps)} polls {snaps['t'].iloc[0]:%Y-%m-%d} .. {snaps['t'].iloc[-1]:%Y-%m-%d}, spread ${a.spread}, fill delay "
          f"{a.delay} min; out of sample = polls from {str(DESIGN_END)[:10]} (settings were chosen on 9 Sep - 22 Oct).")

    print("\n=== A. Current rule ===")
    base = st.result()
    print(line("current (ADX35 ATR3-12 RSI50-70, 12/8/60m)", base))
    for sp, dl in ((0.8, 2), (1.2, 2)):
        s2 = Study(bars, prepare(bt.poll_snapshots(bars, dl)), sp / 2)
        print(line(f"  same, spread ${sp} delay {dl}m", s2.result()))
    wk = st.trades().groupby("week")["pnl"].agg(["count", "sum"]).round(1)
    print("  by week:", ", ".join(f"{w} n={int(r['count'])} {r['sum']:+.1f}" for w, r in wk.iterrows()))
    tr = st.trades()
    print("  by side:", "; ".join(f"{s} n={len(g)} {g['pnl'].sum():+.1f}" for s, g in tr.groupby("side")))
    tr["hour"] = snaps["hour_et"].to_numpy()[tr["k"].to_numpy()]
    print("  by entry hour ET:", ", ".join(f"{h}: n={len(g)} {g['pnl'].sum():+.1f}" for h, g in tr.groupby("hour")))
    print("  null (no filter, Buy at every free poll, 12/8/60m):",
          fmt_short(st.result(adx_min=0, atr_min=0, atr_max=1e9, rsi_lo=0, rsi_hi=100)))

    print("\n=== B. One setting at a time (everything else as current) ===")
    groups = {
        "ADX minimum": [("adx_min", v) for v in (25, 30, 35, 40, 45, 50)],
        "ADX rising required": [("adx_rising", True)],
        "ATR minimum": [("atr_min", v) for v in (0, 2, 4, 5, 6)],
        "ATR maximum": [("atr_max", v) for v in (8, 10, 15, 100)],
        "RSI band (buy side; sell mirrored)": [("rsi_lo", 55), ("rsi_lo", 60), ("rsi_hi", 65), ("rsi_hi", 75), ("rsi_hi", 100)],
        "+DI/-DI must agree with the side": [("di", True)],
        "take-profit": [("tp", v) for v in (8, 10, 15, 18, 20, 25)],
        "stop-loss": [("sl", v) for v in (5, 6, 10, 12, 15)],
        "time stop (minutes)": [("tstop", v) for v in (20, 30, 45, 90, 120)],
        "cooldown after a close (minutes)": [("cooldown", v) for v in (15, 30, 60)],
        "last entry hour (exclusive, ET)": [("end_h", v) for v in (16, 15, 14, 13)],
        "first entry hour (ET)": [("start_h", v) for v in (8, 9, 10)],
    }
    for name, opts in groups.items():
        print(f" {name}")
        for key, val in opts:
            print(line(f"{key} = {val}", st.result(**{key: val})))

    print("\n=== C. Exit structure ===")
    for act, dist in ((4, 4), (6, 6), (6, 4), (8, 6), (8, 8), (10, 6)):
        for tp in (12, 20, 1e9):
            print(line(f"trail {act}/{dist}, tp {tp if tp < 1e8 else 'none'}", st.result(trail=(act, dist), tp=tp)))
    for k_tp, k_sl in ((1.0, 0.7), (1.5, 1.0), (2.0, 1.0), (1.5, 1.5), (2.0, 1.5)):
        print(line(f"limits scaled to ATR: tp {k_tp}x sl {k_sl}x", st.result(tp_atr=k_tp, sl_atr=k_sl)))

    print("\n=== D. Combinations of the ideas that held up (see the write-up for which; edit here to retest) ===")
    for label, over in (
        ("late-entry cutoff 15h", {"end_h": 15}),
        ("cutoff 15h + ADX 40", {"end_h": 15, "adx_min": 40}),
        ("cutoff 15h + trail 6/4 tp 20", {"end_h": 15, "trail": (6, 4), "tp": 20}),
    ):
        print(line(label, st.result(**over)))
    print("\n=== E. Is the edge real? ===")
    tr = st.trades()
    pnl = tr["pnl"].to_numpy()
    se = pnl.std(ddof=1) / np.sqrt(len(pnl))
    rng = np.random.default_rng(1)
    boot = np.array([rng.choice(pnl, len(pnl)).sum() for _ in range(5000)])
    print(f"  current rule: avg {pnl.mean():+.2f} per trade, std {pnl.std(ddof=1):.2f}, standard error {se:.2f}, t = {pnl.mean() / se:.2f}; "
          f"bootstrap 90% range of the total: {np.percentile(boot, 5):+.0f} .. {np.percentile(boot, 95):+.0f}; "
          f"{(boot <= 0).mean():.0%} of resamples are <= 0")
    print("  Does the RSI direction matter? Same ADX/ATR trigger, side chosen differently:")
    print(line("RSI picks the side (current)", base))
    sn = snaps.copy()
    for side in ("buy", "sell"):
        sn2 = sn.copy()
        sn2["rsi"] = 60.0 if side == "buy" else 40.0  # inside the Buy band / the Sell band whatever the real RSI is
        st2 = Study(bars, sn2, a.spread / 2)
        print(line(f"ADX/ATR trigger, always {side.title()}", st2.result()))
    print("  Opposite of the RSI side (wrong way round on purpose):")
    sn3 = snaps.copy()
    sn3["rsi"] = 100 - snaps["rsi"]
    print(line("RSI side flipped", Study(bars, sn3, a.spread / 2).result()))

    if not a.no_live:
        from broker_review import _sql
        live = pd.DataFrame(_sql("SELECT id, trade_type, entry_price, open_ts, exit_price, close_ts, pnl FROM broker_k_trades ORDER BY id"))
        live["open_ts"] = pd.to_datetime(live["open_ts"], format="ISO8601", utc=True).dt.tz_convert(None)
        since = live["open_ts"].min().floor("D")
        rp = tr[tr["t"] >= np.datetime64(since)]
        print(f"\n=== F. Replay vs live since the first live trade ({since:%Y-%m-%d}): live {len(live)} trades, replay {len(rp)} ===")
        used = set()
        for r in rp.itertuples():
            gap = (live["open_ts"] - pd.Timestamp(r.t)).abs()
            j = gap.idxmin()
            hit = gap[j] <= pd.Timedelta(minutes=10) and live.loc[j, "trade_type"].lower() == r.side
            print(f"  replay {pd.Timestamp(r.t):%m-%d %H:%M} {r.side:<4} {r.pnl:+6.1f}   ->  "
                  + (f"live #{live.loc[j, 'id']} {live.loc[j, 'open_ts']:%m-%d %H:%M} pnl {float(live.loc[j, 'pnl'] or 0):+.2f}" if hit else "no live trade within 10 min"))
            if hit:
                used.add(j)
        for j in live.index.difference(list(used)):
            print(f"  live #{live.loc[j, 'id']} {live.loc[j, 'trade_type']} {live.loc[j, 'open_ts']:%m-%d %H:%M}: no replay trade within 10 min")

    print(f"\nVariants evaluated in this run: {st.tried} (each one raises the chance that the best looks good by luck).")


def fmt_short(s: dict) -> str:
    return f"n={s['n']} total={s['total']:+.1f} avg={s['avg']:+.2f}"


if __name__ == "__main__":
    main()
