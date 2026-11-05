"""TA levels study (study 40, 5 Nov 2026) -- rerun at the monthly combined review.

Question: how did the support/resistance zones in every stored TA forecast (`ta_forecasts.levels`) perform against what gold
actually did in the 2 hours after the forecast (and, separately, in hours 1-2), and how could the level selection be improved?
Observation only: this changes no rule or setting, reads no trades and sends nothing.

Data: every `ta_forecasts` row (read over Neon's HTTPS SQL endpoint via broker_review._sql, because a raw Postgres connection is
blocked in the Claude Code sandbox) against Twelve Data XAU/USD 1-minute bars (mid prices; the brokers fill on FOREX.com bid/ask,
about $0.7 wider).

Definitions (all in dollars per troy ounce):
  * A zone's ENTRY EDGE is the edge price reaches first, as the brokers use it: the low edge of a resistance, the high edge of a support.
  * REACHED: a 1-minute bar in the window trades through the entry edge (and the edge was not already reached before the window starts).
  * On the first reach the outcome is BOUNCE if price retreats BOUNCE_DOLLARS from the edge before it runs BOUNCE_DOLLARS through it,
    BREAK if it runs through first (a tie within one bar counts as BREAK), DRIFT if neither happens within OUTCOME_MINUTES.
  * NEAR MISS: not reached, but the window's extreme came within NEAR_DOLLARS of the edge.
  * TURNING POINT: the window's high (or low), counted as confirmed when price then reverses at least BOUNCE_DOLLARS within 60 minutes.
    Its offset is measured to the nearest zone of the same side (0 inside the zone, else the distance to the nearest zone edge).
  * CHANCE BASELINE: the same turning points measured against the same forecast's zones shifted by a random +/-15 to 40 dollars.

Usage:  python ta_levels_study.py --fetch      # download the 1-minute bars (needs TWELVE_DATA_API_KEY, ~15 calls, about 2 minutes)
        python ta_levels_study.py              # report on the cached bars
The bar cache is TA_LEVELS_CACHE (default ta_levels_gold1m.pkl, gitignored).
"""

import argparse
import json
import os
import re
import time

import numpy as np
import pandas as pd
import requests

from broker_review import _sql
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("America/New_York")

CACHE = os.environ.get("TA_LEVELS_CACHE", "ta_levels_gold1m.pkl")
BOUNCE_DOLLARS = 8.0
NEAR_DOLLARS = 5.0
OUTCOME_MINUTES = 90
TURN_CONFIRM_MINUTES = 60
WINDOWS = {"0-2h": (0, 120), "1-2h": (60, 120)}
RNG = np.random.default_rng(40)


# ---------------------------------------------------------------------------------------------------
# Data


def fetch_bars(first_day: str = "2026-09-22") -> pd.DataFrame:
    key = os.environ["TWELVE_DATA_API_KEY"]
    end = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S")
    stop = pd.Timestamp(first_day, tz="UTC")
    parts = []
    while True:
        r = requests.get("https://api.twelvedata.com/time_series", params={
            "symbol": "XAU/USD", "interval": "1min", "outputsize": 5000, "end_date": end, "apikey": key, "timezone": "UTC"},
            timeout=60).json()
        if r.get("status") != "ok":
            raise RuntimeError(f"Twelve Data error: {r}")
        df = pd.DataFrame(r["values"])
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        parts.append(df)
        first = df["datetime"].min()
        print(f"  {len(df)} bars back to {first}", flush=True)
        if first <= stop:
            break
        end = (first - pd.Timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(9)  # free tier: 8 calls a minute
    out = pd.concat(parts).drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].astype(float)
    return out


def load_forecasts() -> list[dict]:
    out = []
    for r in _sql("select id, ts, levels from ta_forecasts order by ts"):
        lv = json.loads(r["levels"]) if isinstance(r["levels"], str) else r["levels"]
        if not lv.get("resistances") or not lv.get("supports"):
            continue
        ts = pd.Timestamp(r["ts"])
        ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
        if ts.tz_convert(DISPLAY_TZ).weekday() >= 5:
            continue
        out.append({"id": r["id"], "ts": ts, "session": lv.get("session") or "TA?", "price": float(lv["price"]),
                    "res": lv["resistances"], "sup": lv["supports"]})
    return out


# ---------------------------------------------------------------------------------------------------
# Level helpers

CATEGORIES = [
    ("4h swing", r"4h swing"), ("daily swing", r"\b[A-Z][a-z]{2} \d{1,2} swing"), ("pivot", r"pivot"),
    ("round number", r"round number"), ("moving average", r"EMA|SMA"), ("prior-day", r"prior-day"),
    ("20-day range", r"20-day"), ("Fibonacci", r"Fib"),
]


def categories(zone: dict) -> set[str]:
    found = set()
    for label in zone["labels"]:
        for name, pattern in CATEGORIES:
            if re.search(pattern, label):
                found.add(name)
    return found or {"other"}


def edge(zone: dict, side: str) -> float:
    return zone["low"] if side == "res" else zone["high"]  # the edge price reaches first from the current price


def zone_distance(x: float, zone: dict) -> float:
    return 0.0 if zone["low"] <= x <= zone["high"] else min(abs(x - zone["low"]), abs(x - zone["high"]))


class Bars:
    def __init__(self, df: pd.DataFrame):
        self.t = df["datetime"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
        self.h, self.l = df["high"].to_numpy(), df["low"].to_numpy()

    def span(self, start: pd.Timestamp, minutes_from: int, minutes_to: int):
        s = np.datetime64(start.tz_convert("UTC").tz_localize(None), "ns")
        a = np.searchsorted(self.t, s + np.timedelta64(minutes_from, "m"), side="right")
        b = np.searchsorted(self.t, s + np.timedelta64(minutes_to, "m"), side="right")
        return a, b


# ---------------------------------------------------------------------------------------------------
# Zone reactions


def outcome_after(bars: Bars, idx: int, level: float, side: str) -> str:
    """BOUNCE / BREAK / DRIFT after the first bar (index idx) that reached `level`."""
    end = min(len(bars.t), idx + OUTCOME_MINUTES + 1)
    h, l = bars.h[idx:end], bars.l[idx:end]
    through = (h - level) if side == "res" else (level - l)  # how far past the edge, per bar
    back = (level - l) if side == "res" else (h - level)  # how far back from the edge, per bar
    brk = np.flatnonzero(through >= BOUNCE_DOLLARS)
    bnc = np.flatnonzero(back >= BOUNCE_DOLLARS)
    b_i = brk[0] if len(brk) else None
    c_i = bnc[0] if len(bnc) else None
    if b_i is None and c_i is None:
        return "DRIFT"
    if c_i is None or (b_i is not None and b_i <= c_i):
        return "BREAK"
    return "BOUNCE"


def zone_reactions(forecasts: list[dict], bars: Bars, window: str) -> pd.DataFrame:
    a_min, z_min = WINDOWS[window]
    rows = []
    for f in forecasts:
        a0, _ = bars.span(f["ts"], 0, 0)
        a, b = bars.span(f["ts"], a_min, z_min)
        if b - a < (z_min - a_min) * 0.9:
            continue  # market closed / data gap inside the window
        for side, zones in (("res", f["res"]), ("sup", f["sup"])):
            for rank, z in enumerate(zones, start=1):
                e = edge(z, side)
                hit_before = a > a0 and (bars.h[a0:a].max() >= e if side == "res" else bars.l[a0:a].min() <= e)
                if hit_before:
                    continue  # already reached before this window starts: a different question
                hs, ls = bars.h[a:b], bars.l[a:b]
                reached = np.flatnonzero(hs >= e) if side == "res" else np.flatnonzero(ls <= e)
                gap = (e - hs.max()) if side == "res" else (ls.min() - e)  # > 0 = stayed short of the edge
                row = dict(fid=f["id"], session=f["session"], side=side, rank=rank, dist=abs(e - f["price"]),
                           n_labels=len(z["labels"]), width=z["high"] - z["low"], cats=categories(z), gap=gap)
                if len(reached):
                    row["reach"] = True
                    row["outcome"] = outcome_after(bars, a + int(reached[0]), e, side)
                    row["near"] = False
                else:
                    row["reach"] = False
                    row["outcome"] = None
                    row["near"] = gap <= NEAR_DOLLARS
                rows.append(row)
    return pd.DataFrame(rows)


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def reaction_table(d: pd.DataFrame, by: str, title: str, expand_cats: bool = False) -> str:
    if expand_cats:
        d = d.explode("cats").rename(columns={"cats": "cat"})
        by = "cat"
    lines = [f"  {title:<22} zones  reached  bounce/break/drift (of reached)  bounce%  near-miss(<$5)%"]
    for k, g in d.groupby(by, observed=True):
        r = g[g.reach]
        nb, nk, nd = (r.outcome == "BOUNCE").sum(), (r.outcome == "BREAK").sum(), (r.outcome == "DRIFT").sum()
        nm = g[~g.reach]
        lines.append(f"  {str(k):<22} {len(g):>5}  {pct(len(r), len(g)):>7}  {nb:>4}/{nk:>3}/{nd:>3}{'':<19} {pct(nb, len(r)):>6}   {pct(int(nm.near.sum()), len(nm)):>6}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------
# Turning points against the zones


def turning_points(forecasts: list[dict], bars: Bars, window: str, shift: bool = False) -> pd.DataFrame:
    a_min, z_min = WINDOWS[window]
    rows = []
    for f in forecasts:
        a, b = bars.span(f["ts"], a_min, z_min)
        if b - a < (z_min - a_min) * 0.9:
            continue
        for side, zones in (("res", f["res"]), ("sup", f["sup"])):
            hs, ls = bars.h[a:b], bars.l[a:b]
            i = int(np.argmax(hs)) if side == "res" else int(np.argmin(ls))
            x = float(hs[i]) if side == "res" else float(ls[i])
            end = min(len(bars.t), a + i + 1 + TURN_CONFIRM_MINUTES)
            after = (x - bars.l[a + i:end].min()) if side == "res" else (bars.h[a + i:end].max() - x)
            confirmed = after >= BOUNCE_DOLLARS
            off = RNG.choice([-1, 1]) * RNG.uniform(15, 40) if shift else 0.0
            ds = [(zone_distance(x, {"low": z["low"] + off, "high": z["high"] + off}), k) for k, z in enumerate(zones)]
            dist, k = min(ds)
            z = zones[k]
            e = edge(z, side)
            signed = (x - e) if side == "res" else (e - x)  # > 0 = turned beyond the edge (overshoot), < 0 = fell short
            mid = (z["low"] + z["high"]) / 2
            mid_signed = (x - mid) if side == "res" else (mid - x)
            # the zone merge also lists "nearby" candidates it absorbed; would one of them have been the better level?
            everything = [(zone_distance(x, zz), 0) for zz in zones] + [(zone_distance(x, n), 0) for zz in zones for n in zz.get("nearby", [])]
            alt_dist = min(everything)[0]
            rows.append(dict(fid=f["id"], session=f["session"], side=side, x=x, confirmed=bool(confirmed), dist=dist,
                             signed=signed, mid_signed=mid_signed, alt_dist=alt_dist, nearest_rank=k + 1, cats=categories(z),
                             inside=dist == 0))
    return pd.DataFrame(rows)


def turn_summary(t: pd.DataFrame, label: str) -> str:
    c = t[t.confirmed]
    n = len(c)
    return (f"  {label:<18} confirmed turns {n:>3} | inside a zone {pct(int(c.inside.sum()), n):>4} | within $3 {pct(int((c.dist <= 3).sum()), n):>4}"
            f" | within $5 {pct(int((c.dist <= 5).sum()), n):>4} | within $10 {pct(int((c.dist <= 10).sum()), n):>4} | median distance ${c.dist.median():.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    args = ap.parse_args()
    if args.fetch:
        fetch_bars().to_pickle(CACHE)
    bars = Bars(pd.read_pickle(CACHE))
    forecasts = load_forecasts()
    print(f"{len(forecasts)} weekday forecasts, {forecasts[0]['ts']:%d %b} to {forecasts[-1]['ts']:%d %b %Y}; "
          f"bounce = ${BOUNCE_DOLLARS:g} retreat before ${BOUNCE_DOLLARS:g} break, near miss = within ${NEAR_DOLLARS:g}")
    for window in WINDOWS:
        d = zone_reactions(forecasts, bars, window)
        print(f"\n===== window {window}: {d.fid.nunique()} forecasts, {len(d)} zones =====")
        d["dist_bin"] = pd.cut(d.dist, [0, 15, 30, 50, 80, 1000], labels=["<$15", "$15-30", "$30-50", "$50-80", ">$80"])
        d["rank"] = d["rank"].astype(str)
        d["n_labels"] = d.n_labels.clip(upper=4).astype(str)
        for by, title in (("rank", "zone rank (1=nearest)"), ("dist_bin", "distance from price")):
            print(reaction_table(d, by, title))
        d = d[d.dist <= 30]  # zones price can plausibly reach in 2 hours: the population the factor tables below describe
        print(f"  -- zones within $30 of the forecast price only: {len(d)} --")
        for by, title in (("session", "TA run"), ("n_labels", "source labels (4=4+)"), ("side", "side")):
            print(reaction_table(d, by, title))
        print(reaction_table(d, "cats", "source category", expand_cats=True))
        t = turning_points(forecasts, bars, window)
        base = pd.concat([turning_points(forecasts, bars, window, shift=True) for _ in range(200)])
        print("\n  Where the window's high/low turned, against the nearest zone of that side:")
        print(turn_summary(t, "actual zones"))
        print(turn_summary(base, "shifted (chance)"))
        c = t[t.confirmed]
        for side in ("res", "sup"):
            s = c[c.side == side]
            print(f"  {side}: signed offset to the nearest zone's entry edge (+ = turned beyond the edge): median {s.signed.median():+.1f}, "
                  f"mean {s.signed.mean():+.1f}, share short of the edge {pct(int((s.signed < 0).sum()), len(s))}")
        print(f"  Distance of a confirmed turn from: the zone entry edge (median |offset|) ${c.signed.abs().median():.1f}; the zone midpoint "
              f"${c.mid_signed.abs().median():.1f}; the nearest zone or absorbed 'nearby' candidate ${c.alt_dist.median():.1f} "
              f"(within $3: zones {pct(int((c.dist <= 3).sum()), len(c))} vs with nearby candidates {pct(int((c.alt_dist <= 3).sum()), len(c))})")
        near = c[c.dist <= 12].explode("cats")
        if len(near):
            print("  By category of the nearest zone (turns within $12 of it): n, median signed offset from the entry edge")
            for k, g in near.groupby("cats"):
                print(f"    {k:<16} {len(g):>3}  {g.signed.median():+6.1f}  (IQR {g.signed.quantile(.25):+.1f}..{g.signed.quantile(.75):+.1f})")


if __name__ == "__main__":
    main()
