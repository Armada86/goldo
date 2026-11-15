"""Immediate-entry study (study 44, 15 Nov 2026) -- observation only.

Question: today Broker B / F wait until price reaches one of the TA run's four levels (fade or breakout) and only then trade. What if a
trade is opened the moment a new TA run lands, and held until price reaches a level (or the live exit rule closes it)? Does that make
money, and does it add to what the waiting engine already earns?

It changes no rule or setting, sends nothing and writes nothing. Reads `ta_forecasts` over Neon's HTTPS SQL endpoint
(broker_review._sql) and the cached Twelve Data 1-minute bars of ta_levels_study.py (`python ta_levels_study.py --fetch` first).

Method (one replay engine for everything, mid prices, every trade pays SPREAD_COST):
  * WAIT (baseline, the live idea): for each TA run (valid until the next run or 5 PM ET), the four rules sell_resistance / buy_support
    (fades, entry at the level, invalid if price already broke the zone's far side) and bull_breakout / bear_breakdown (entry at the
    trigger). A touch only counts after price was first seen on the approach side, one position at a time, at most 2 trades per
    (run, rule), a loss or a win under $3 retires the rule for that run. Exits: the live rule (stop loss 20 -- fades capped at 15 --,
    trail 10/10, fade near-trail 10/5 and lock at the opposite level, +$20 profit lock) via stop_loss_analysis.replay(), until 5 PM ET.
  * NOW (the idea): at each TA run's timestamp open one trade at the price of the next 1-minute bar, only if no other trade is open.
    Direction rules: `fade_nearest` (sell if price is nearer the resistance, buy if nearer the support), its mirror `break_nearest`,
    `bias` (combined bias label Bullish = buy, Bearish = sell, Neutral = none), `lean` (Bullish/Bearish lean only), and the controls
    `always_buy` / `always_sell` (the period is a downtrend, so a one-way rule must be read against these).
    Exit styles: `live` (the same live exit rule, trade held to the next TA run or 5 PM ET, then closed at market);
    `levels` (structural: take profit at the opposite fade level, stop at the scenario stop beyond the nearer level, else closed at the
    next run / 5 PM); `handoff` (live exit rule, but closed at market the moment the WAIT engine would have taken a level, so the
    waiting engine then proceeds as normal -- this is the "add it on top" question).
  * FILTERS: optional replay of today's entry filters (ATR >= 12 blocks everything; fades blocked at ADX >= 28 and rising, or at RSI
    >= 68 (sell) / <= 32 (buy); breakouts blocked at ADX < 20 or RSI >= 70 (buy) / <= 30 (sell)), on 15-minute candles resampled from the
    same bars. DXY and the spike gate are not replayed (no data / needs the live bars).
Usage: python ta_immediate_entry_study.py [--no-filters-only]
"""

import argparse
import math
import sys

import numpy as np
import pandas as pd

# the sandbox lacks yfinance/psycopg2; the study only needs data_fetcher's pure indicator maths and stop_loss_analysis.replay()
import types
for _m in ("yfinance", "psycopg2", "psycopg2.extras", "dotenv"):
    if _m not in sys.modules:
        try:
            __import__(_m)
        except ImportError:
            sys.modules[_m] = types.ModuleType(_m)
if not hasattr(sys.modules["dotenv"], "load_dotenv"):
    sys.modules["dotenv"].load_dotenv = lambda *a, **k: None

import config
import data_fetcher as df_
import stop_loss_analysis as sla
from ta_levels_study import CACHE, DISPLAY_TZ, load_forecasts

SPREAD_COST = 0.70
STOP_LOSS, ACTIVATION, DISTANCE = 20.0, 10.0, 10.0  # live settings (stop_settings_history, 6 Nov 2026)
MAX_PER_LEVEL = 2
REARM_MIN_WIN = 3.0
ADX_TREND = 28.0  # BRA's current adx_trending
ADX_CHOP = 20.0
ATR_MAX = 12.0
FADE_RSI_HI, FADE_RSI_LO, BRK_RSI_HI, BRK_RSI_LO = 68.0, 32.0, 70.0, 30.0


def load_levels_rows() -> dict[int, dict]:
    import json
    from broker_review import _sql
    rows = {}
    for r in _sql("select id, ts, levels from ta_forecasts order by ts"):
        lv = json.loads(r["levels"]) if isinstance(r["levels"], str) else r["levels"]
        rows[r["id"]] = lv
    return rows


class Market:
    def __init__(self, bars: pd.DataFrame):
        b = bars.copy()
        b["datetime"] = pd.to_datetime(b["datetime"], utc=True)
        self.df = b.sort_values("datetime").reset_index(drop=True)
        self.t = self.df["datetime"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
        self.o, self.h, self.l, self.c = (self.df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        c15 = (self.df.set_index("datetime").resample("15min", label="left", closed="left")
               .agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
        self.c15 = c15
        self.rsi = df_.compute_rsi(c15["close"])
        self.adx = df_.compute_adx(c15.reset_index())
        self.atr = df_.compute_atr(c15.reset_index())
        self.t15 = c15.index.tz_convert("UTC").tz_localize(None).to_numpy("datetime64[ns]")
        self.adx = self.adx.to_numpy() if hasattr(self.adx, "to_numpy") else np.asarray(self.adx)
        self.atr = self.atr.to_numpy() if hasattr(self.atr, "to_numpy") else np.asarray(self.atr)
        self.rsi = self.rsi.to_numpy()

    def idx_after(self, ts) -> int:
        """Index of the first bar starting at or after ts."""
        return int(np.searchsorted(self.t, np.datetime64(ts.tz_convert("UTC").tz_localize(None), "ns"), side="left"))

    def indicators(self, i: int):
        """RSI/ADX/ATR/adx_rising from the 15-min candles known at 1-min bar i (the forming candle included, like the live read)."""
        j = int(np.searchsorted(self.t15, self.t[i], side="right")) - 1
        if j < 30:
            return None
        sub = self.c15.iloc[: j + 1].copy()
        sub.iloc[-1] = [self.o[self._first_of_15(i)], self.h[self._first_of_15(i): i + 1].max(),
                        self.l[self._first_of_15(i): i + 1].min(), self.c[i]]
        d = sub.reset_index()
        rsi = float(df_.compute_rsi(d["close"]).iloc[-1])
        adx_s = df_.compute_adx(d)
        atr = float(df_.compute_atr(d).iloc[-1])
        adx = float(adx_s.iloc[-1])
        rising = bool(adx_s.iloc[-1] > adx_s.iloc[-2]) if len(adx_s) > 1 else False
        return {"rsi": rsi, "adx": adx, "atr": atr, "rising": rising}

    def _first_of_15(self, i: int) -> int:
        j = int(np.searchsorted(self.t15, self.t[i], side="right")) - 1
        return int(np.searchsorted(self.t, self.t15[j], side="left"))


def et(ts):
    return ts.tz_convert(DISPLAY_TZ)


def horizon_end_ts(ts, next_ts):
    end = et(ts).replace(hour=17, minute=0, second=0, microsecond=0).tz_convert("UTC")
    return min(end, next_ts) if next_ts is not None else end


def day_end_ts(ts):
    return et(ts).replace(hour=17, minute=0, second=0, microsecond=0).tz_convert("UTC")


def rule_defs(lv: dict):
    """The four rules of a TA run: name -> dict(kind, side ('Buy'/'Sell'), level, stop). None if the run lacks a scenario."""
    sc = {s["name"]: s for s in lv.get("scenarios", [])}
    out = {}
    if "sell_resistance" in sc:
        out["sell_resistance"] = dict(kind="fade", side="Sell", level=sc["sell_resistance"]["entry"]["low"], stop=sc["sell_resistance"]["stop"])
    if "buy_support" in sc:
        out["buy_support"] = dict(kind="fade", side="Buy", level=sc["buy_support"]["entry"]["high"], stop=sc["buy_support"]["stop"])
    if "bull_breakout" in sc:
        out["bull_breakout"] = dict(kind="break", side="Buy", level=sc["bull_breakout"]["trigger"], stop=None)
    if "bear_breakdown" in sc:
        out["bear_breakdown"] = dict(kind="break", side="Sell", level=sc["bear_breakdown"]["trigger"], stop=None)
    return out


def path_for(mk: Market, i_open: int, i_end: int, side: str, entry: float, fade: bool, lock_level):
    """TradePath over bars (i_open, i_end] (mid prices), for stop_loss_analysis.replay()."""
    if i_end <= i_open + 1:
        return None
    bars = mk.df.iloc[i_open + 1: i_end + 1][["datetime", "high", "low", "close"]]
    open_ts = pd.Timestamp(mk.t[i_open]).tz_localize("UTC").to_pydatetime()
    return sla.build_path("x", "F", side, entry, open_ts, bars, "mid", fade=fade, lock_level=lock_level)


def run_exit(mk: Market, i_open: int, i_end: int, side: str, entry: float, fade: bool, lock_level):
    """(pnl before spread, exit bar index) under the live exit rule; held to bar i_end if never stopped."""
    p = path_for(mk, i_open, i_end, side, entry, fade, lock_level)
    if p is None:
        return 0.0, i_open
    pnl, resolved, exit_time = sla.replay(p, STOP_LOSS, ACTIVATION, DISTANCE)
    ei = int(np.searchsorted(mk.t, exit_time, side="left"))
    return pnl, min(ei, i_end)


def filters_ok(mk: Market, i: int, kind: str, side: str, on: bool):
    if not on:
        return True, ""
    ind = mk.indicators(i)
    if ind is None:
        return True, ""
    if ind["atr"] >= ATR_MAX:
        return False, "ATR"
    if kind == "fade":
        if ind["adx"] >= ADX_TREND and ind["rising"]:
            return False, "ADX"
        if side == "Sell" and ind["rsi"] >= FADE_RSI_HI or side == "Buy" and ind["rsi"] <= FADE_RSI_LO:
            return False, "RSI"
    else:
        if ind["adx"] < ADX_CHOP:
            return False, "ADX"
        if side == "Buy" and ind["rsi"] >= BRK_RSI_HI or side == "Sell" and ind["rsi"] <= BRK_RSI_LO:
            return False, "RSI"
    return True, ""


def lock_level_for(rules: dict, side: str):
    other = rules.get("buy_support" if side == "Sell" else "sell_resistance")
    return other["level"] if other else None


def simulate_wait(mk: Market, runs: list[dict], use_filters: bool):
    """The waiting engine over all runs. Returns trades (list of dict) and the first-touch times per run (for the hand-off)."""
    trades, first_touch = [], {}
    busy_until_i = -1
    for k, run in enumerate(runs):
        nxt = runs[k + 1]["ts"] if k + 1 < len(runs) and et(runs[k + 1]["ts"]).date() == et(run["ts"]).date() else None
        end_ts = horizon_end_ts(run["ts"], nxt)
        i0, i1 = mk.idx_after(run["ts"]), mk.idx_after(end_ts)
        if i1 <= i0 + 2:
            continue
        rules = rule_defs(run["lv"])
        price0 = mk.o[i0]
        # approach side: the level must have been seen on the near side of price first
        approached = {n: (price0 < r["level"] if (r["kind"] == "fade") == (r["side"] == "Sell") else price0 > r["level"]) for n, r in rules.items()}
        # fade sell / bull breakout approach from below; fade buy / bear breakdown approach from above
        for n, r in rules.items():
            up = (n in ("sell_resistance", "bull_breakout"))
            approached[n] = price0 < r["level"] if up else price0 > r["level"]
        count = {n: 0 for n in rules}
        retired = set()
        i = i0
        while i < i1:
            for n, r in rules.items():
                if n in retired or count[n] >= MAX_PER_LEVEL:
                    continue
                up = n in ("sell_resistance", "bull_breakout")
                touched = (mk.h[i] >= r["level"]) if up else (mk.l[i] <= r["level"])
                if not touched:
                    approached[n] = True
                    continue
                if not approached[n]:
                    continue
                first_touch.setdefault(k, (i, r["level"]))
                if i <= busy_until_i:
                    approached[n] = False  # level crossed while a trade held the slot: needs a fresh approach
                    continue
                if r["kind"] == "fade":  # invalid if price already ran through the far side in the same bar
                    if (r["side"] == "Sell" and mk.h[i] >= r["stop"]) or (r["side"] == "Buy" and mk.l[i] <= r["stop"]):
                        approached[n] = False
                        continue
                ok, why = filters_ok(mk, i, r["kind"], r["side"], use_filters)
                if not ok:
                    approached[n] = False  # blocked touch consumed
                    continue
                entry = r["level"]
                fade = r["kind"] == "fade"
                lock = lock_level_for(rules, r["side"]) if fade else None
                pnl, ei = run_exit(mk, i, i1 if True else i1, r["side"], entry, fade, lock)
                ei = max(ei, i)
                net = pnl - SPREAD_COST
                trades.append(dict(run=k, rule=n, side=r["side"], kind=r["kind"], i_open=i, i_close=ei, entry=entry, pnl=net,
                                   day=et(run["ts"]).date(), session=run["session"]))
                count[n] += 1
                busy_until_i = ei
                if net < REARM_MIN_WIN:
                    retired.add(n)
                approached[n] = False
            i += 1
    return trades, first_touch


def direction_for(rule: str, lv: dict, price: float, rules: dict):
    res = rules.get("sell_resistance")
    sup = rules.get("buy_support")
    if rule in ("fade_nearest", "break_nearest"):
        if not res or not sup:
            return None
        near_res = (res["level"] - price) < (price - sup["level"])
        sell = near_res if rule == "fade_nearest" else not near_res
        return "Sell" if sell else "Buy"
    if rule == "bias":
        b = lv.get("bias") or ""
        return "Buy" if b.startswith("Bullish") else "Sell" if b.startswith("Bearish") else None
    if rule == "lean":
        b = lv.get("lean") or ""
        return "Buy" if b == "Bullish lean" else "Sell" if b == "Bearish lean" else None
    return {"always_buy": "Buy", "always_sell": "Sell"}[rule]


def simulate_now(mk: Market, runs: list[dict], rule: str, style: str, use_filters: bool, wait_trades: list[dict], first_touch: dict):
    """One immediate trade per TA run. `wait_trades` supply the busy windows of the waiting engine for the standalone one-position rule."""
    trades = []
    busy_until_i = -1
    wait_intervals = [(t["i_open"], t["i_close"]) for t in wait_trades]
    for k, run in enumerate(runs):
        nxt = runs[k + 1]["ts"] if k + 1 < len(runs) and et(runs[k + 1]["ts"]).date() == et(run["ts"]).date() else None
        end_ts = horizon_end_ts(run["ts"], nxt)
        i0, i1 = mk.idx_after(run["ts"]), mk.idx_after(end_ts)
        if i1 <= i0 + 2:
            continue
        rules = rule_defs(run["lv"])
        if not rules:
            continue
        price = mk.o[i0]
        side = direction_for(rule, run["lv"], price, rules)
        if side is None:
            continue
        if i0 <= busy_until_i:
            continue
        if style != "handoff" and any(a <= i0 <= b for a, b in wait_intervals):
            continue  # standalone comparison: only if nothing else is open (the waiting engine's open trade)
        kind = "fade" if rule == "fade_nearest" else "now"
        ok, why = filters_ok(mk, i0, "fade" if kind == "fade" else "break", side, use_filters)
        if not ok:
            continue
        res, sup = rules.get("sell_resistance"), rules.get("buy_support")
        lock = lock_level_for(rules, side)
        end_i = i1
        if style == "handoff" and k in first_touch:
            end_i = min(i1, first_touch[k][0])  # closed at the level the waiting engine would have traded
        if style == "levels":
            pnl, ei = levels_exit(mk, i0, i1, side, price, rules)
        else:
            pnl, ei = run_exit(mk, i0, end_i, side, price, True if kind == "fade" else False, lock if kind == "fade" else None)
            if style == "handoff" and ei >= end_i and k in first_touch and end_i < i1:
                pnl = (mk.c[end_i] - price) if side == "Buy" else (price - mk.c[end_i])
                lvl = first_touch[k][1]
                pnl = (lvl - price) if side == "Buy" else (price - lvl)  # closed at the level price (the touch)
        net = pnl - SPREAD_COST
        trades.append(dict(run=k, rule=rule, side=side, kind=kind, i_open=i0, i_close=ei, entry=price, pnl=net,
                           day=et(run["ts"]).date(), session=run["session"]))
        busy_until_i = ei
    return trades


def levels_exit(mk: Market, i0: int, i1: int, side: str, entry: float, rules: dict):
    """Structural exit: take profit at the opposite fade level, stop at the scenario stop beyond the near level, else the run's end."""
    res, sup = rules["sell_resistance"], rules["buy_support"]
    if side == "Sell":
        tp, sl = sup["level"], res["stop"]
    else:
        tp, sl = res["level"], sup["stop"]
    for j in range(i0 + 1, i1):
        if side == "Sell":
            if mk.h[j] >= sl:
                return entry - sl, j
            if mk.l[j] <= tp:
                return entry - tp, j
        else:
            if mk.l[j] <= sl:
                return sl - entry, j
            if mk.h[j] >= tp:
                return tp - entry, j
    j = max(i1 - 1, i0)
    return ((mk.c[j] - entry) if side == "Buy" else (entry - mk.c[j])), j


def stats(trades: list[dict]) -> dict:
    if not trades:
        return dict(n=0, total=0.0, avg=0.0, win=0.0, t=0.0)
    p = np.array([t["pnl"] for t in trades])
    n = len(p)
    sd = p.std(ddof=1) if n > 1 else 0.0
    return dict(n=n, total=float(p.sum()), avg=float(p.mean()), win=float((p > 0).mean()),
                t=float(p.mean() / (sd / math.sqrt(n))) if sd > 0 else 0.0)


def fmt(label: str, s: dict) -> str:
    return f"  {label:<38} n={s['n']:>3}  total {s['total']:>+8.2f}  avg {s['avg']:>+6.2f}  win {100 * s['win']:>3.0f}%  t={s['t']:>+5.2f}"


def by_week(trades):
    out = {}
    for t in trades:
        wk = pd.Timestamp(t["day"]).to_period("W-SUN").start_time.date()
        out[wk] = out.get(wk, 0.0) + t["pnl"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-filters-only", action="store_true")
    args = ap.parse_args()
    bars = pd.read_pickle(CACHE)
    mk = Market(bars)
    lvrows = load_levels_rows()
    runs = []
    for f in load_forecasts():
        lv = lvrows[f["id"]]
        if not lv.get("scenarios"):
            continue
        runs.append({"id": f["id"], "ts": f["ts"], "session": f["session"], "lv": lv})
    # only runs with bars after them
    last_bar = pd.Timestamp(mk.t[-1]).tz_localize("UTC")
    runs = [r for r in runs if r["ts"] < last_bar - pd.Timedelta(hours=1)]
    print(f"{len(runs)} TA runs, {runs[0]['ts']:%d %b} to {runs[-1]['ts']:%d %b %Y}; bars to {last_bar:%d %b %H:%M} UTC; spread cost ${SPREAD_COST}/trade")
    print(f"exit rule: stop loss {STOP_LOSS} (fade cap {config.FADE_STOP_LOSS_CAP}), trail {ACTIVATION}/{DISTANCE}, profit lock {config.PROFIT_LOCK_ACTIVATION}/{config.PROFIT_LOCK_DISTANCE}")
    for use_filters in ([False] if args.no_filters_only else [False, True]):
        print(f"\n===== entry filters {'ON (ATR/ADX/RSI, no DXY/spike)' if use_filters else 'OFF'} =====")
        wt, ft = simulate_wait(mk, runs, use_filters)
        # waiting time
        waits = []
        notouch = 0
        for k, run in enumerate(runs):
            i0 = mk.idx_after(run["ts"])
            mine = [t for t in wt if t["run"] == k]
            if mine:
                waits.append((mine[0]["i_open"] - i0))
            else:
                notouch += 1
        print(f"WAIT engine: {len(wt)} trades over {len(runs)} runs; {notouch} runs ({100 * notouch / len(runs):.0f}%) never traded; "
              f"median minutes from the TA run to the first trade {np.median(waits) if waits else float('nan'):.0f} (mean {np.mean(waits) if waits else float('nan'):.0f})")
        print(fmt("WAIT (all four rules)", stats(wt)))
        print(fmt("  fades only", stats([t for t in wt if t["kind"] == "fade"])))
        print(fmt("  breakouts only", stats([t for t in wt if t["kind"] == "break"])))
        base_total = stats(wt)["total"]
        print("\nNOW (immediate trade at each TA run), standalone = only when no waiting-engine trade is open:")
        results = {}
        for rule in ("fade_nearest", "break_nearest", "bias", "lean", "always_buy", "always_sell"):
            for style in ("live", "levels"):
                tr = simulate_now(mk, runs, rule, style, use_filters, wt, ft)
                results[(rule, style)] = tr
                print(fmt(f"{rule} / exit {style}", stats(tr)))
        print("\nNOW + WAIT (add the immediate trade on top, closed at the level the waiting engine would take):")
        print(fmt("WAIT alone", stats(wt)))
        for rule in ("fade_nearest", "break_nearest", "bias", "lean", "always_buy", "always_sell"):
            tr = simulate_now(mk, runs, rule, "handoff", use_filters, wt, ft)
            both = stats(wt + tr)
            print(fmt(f"WAIT + {rule} (hand-off)", stats(tr)) + f"   combined total {both['total']:+.2f} (vs WAIT {base_total:+.2f}, diff {both['total'] - base_total:+.2f})")
            results[(rule, "handoff")] = tr
        if not use_filters:
            print("\nBy week (total net $):")
            keys = [("WAIT", wt), ("fade_nearest live", results[("fade_nearest", "live")]), ("break_nearest live", results[("break_nearest", "live")]),
                    ("bias live", results[("bias", "live")]), ("always_buy live", results[("always_buy", "live")]),
                    ("always_sell live", results[("always_sell", "live")])]
            weeks = sorted({w for _, t in keys for w in by_week(t)})
            print("  week        " + "".join(f"{n:>20}" for n, _ in keys))
            for w in weeks:
                print(f"  {w}  " + "".join(f"{by_week(t).get(w, 0.0):>20.2f}" for _, t in keys))
            print("\nBy TA run (fade_nearest live):")
            for s in ("TA1", "TA2", "TA3", "TA4"):
                print(fmt(f"  {s}", stats([t for t in results[('fade_nearest', 'live')] if t["session"] == s])))
            fn = results[("fade_nearest", "live")]
            print("\nfade_nearest live: Buy vs Sell")
            print(fmt("  Buy", stats([t for t in fn if t["side"] == "Buy"])))
            print(fmt("  Sell", stats([t for t in fn if t["side"] == "Sell"])))
            # distance from price to near level
        print("\nDrift check: gold first to last bar:", f"{mk.c[mk.idx_after(runs[0]['ts'])]:.0f} -> {mk.c[-1]:.0f}")


if __name__ == "__main__":
    sys.exit(main())
