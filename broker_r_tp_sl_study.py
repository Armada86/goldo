"""Take-profit / stop-loss grid replay for Broker R (6 Nov 2026). Read-only: writes nothing to Postgres, sends no Telegram.

Broker R's idea is many short trades with a strict +TP / -SL exit (config R_TAKE_PROFIT / R_STOP_LOSS, both $5 to start). This
script answers "would a different pair have done better on the trades Broker R actually took?" and is meant to be rerun as
trades accumulate:

    python broker_r_tp_sl_study.py                  # grid TP 2..10 x SL 2..10 on every closed Broker R trade
    python broker_r_tp_sl_study.py --since 2026-11-10 --tp 3-8 --sl 3-8
    python broker_r_tp_sl_study.py --bidask         # side-correct FOREX.com bars (last ~2.8 days only), no cost fudge
    python broker_r_tp_sl_study.py --cost 0.8 --csv grid.csv

How it works
  - Entries are the real ones (`broker_r_trades`: entry_price, open_ts, trade_type); only the exit changes.
  - Each trade is walked forward on 1-minute bars strictly after open_ts (exactly broker_r.scan_exit()): stop first if one bar
    touches both, exit at the limit price. A trade still open at 5 PM ET closes at that bar's close ("EOD").
  - One position at a time, like the live broker: if a replayed trade is still open (by more than OVERLAP_TOLERANCE_MIN minutes, to
    absorb price-feed noise) when the next real entry happened, that entry is skipped (counted in "skip"). Wider targets therefore cost trades; tighter ones can't add any (see limits).
  - Prices: Twelve Data 1-minute MID bars by default, minus a per-trade `cost` for the bid/ask spread and fill slippage
    (DEFAULT_COST $0.50 unless --cost; an assumption -- the run prints how many booked outcomes the replay at the live TP/SL reproduces).
    --bidask uses FOREX.com bid/ask bars instead (Buy enters ask / exits bid, Sell the reverse) with cost 0, but those only reach
    back ~2.8 days, so older trades are left out.

Reading the output: net P&L, per-trade expectancy, win rate against the break-even win rate for that TP/SL, t-stat of the
per-trade mean, average hold, P&L per hour of position time (the "many short trades" measure), max drawdown, and how many ET
days finished positive. Cells with fewer than MIN_TRADES_FOR_ADVICE trades are marked and never recommended.

Limits (read before acting on a number): entries are fixed, so a tighter exit that would have freed the slot for an earlier
re-entry is not credited; the grid is searched on the same trades it scores, so the best cell is optimistic -- the neighbours and
the by-day column matter more than the top row; every cell is correlated with its neighbours; a few dozen trades cannot
distinguish a $0.30 per-trade edge from noise (look at the t-stat). It never changes config.py -- a change is the user's decision."""

import argparse
import math
import os
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from config import R_STOP_LOSS, R_TAKE_PROFIT

DISPLAY_TZ = ZoneInfo("America/New_York")
SESSION_END_HOUR_ET = 17
MIN_TRADES_FOR_ADVICE = 100
BAR_PAGE = 5000
DEFAULT_COST = 0.50          # $ per trade, mid-price mode: assumed spread + slippage
OVERLAP_TOLERANCE_MIN = 2.0  # a replayed trade may overrun the next real entry by this much before that entry is skipped


def parse_range(text: str) -> list[float]:
    """'3-8' -> 3..8 step 1; '2.5-6:0.5' -> step 0.5; '4,5,7' -> a list."""
    if "," in text:
        return [float(x) for x in text.split(",")]
    step = 1.0
    if ":" in text:
        text, s = text.split(":")
        step = float(s)
    lo, hi = (float(x) for x in text.split("-"))
    return [round(v, 4) for v in np.arange(lo, hi + step / 2, step)]


# ---------------------------------------------------------------- data

def load_trades(since: str | None) -> pd.DataFrame:
    from broker_review import _sql  # Neon HTTPS SQL endpoint: works where port 5432 is blocked

    where = "status = 'Closed'" + (" AND open_ts >= %s" if since else "")
    rows = _sql(
        f"SELECT id, rule_name, trade_type, entry_price, open_ts, close_ts, pnl FROM broker_r_trades WHERE {where} ORDER BY open_ts",
        [since] if since else None,
    )
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    d["open_ts"] = pd.to_datetime(d["open_ts"], utc=True)
    d["close_ts"] = pd.to_datetime(d["close_ts"], utc=True)
    d["entry_price"] = d["entry_price"].astype(float)
    d["pnl"] = d["pnl"].astype(float)
    return d


def fetch_mid_bars(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Twelve Data XAU/USD 1-minute bars (UTC, mid) from start to end, paged past the 5000-row cap and paced to the free tier."""
    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not key:
        raise SystemExit("TWELVE_DATA_API_KEY is not set (or use --bidask with FOREX_* credentials).")
    frames, cursor = [], start
    while cursor < end:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": "XAU/USD", "interval": "1min", "outputsize": BAR_PAGE, "timezone": "UTC",
                    "start_date": cursor.strftime("%Y-%m-%d %H:%M:%S"), "end_date": end.strftime("%Y-%m-%d %H:%M:%S")},
            headers={"Authorization": f"apikey {key}"}, timeout=30,
        ).json()
        values = r.get("values")
        if not values:
            if r.get("status") == "error" and "No data" not in str(r.get("message")):
                raise SystemExit(f"Twelve Data: {r.get('message')}")
            break
        f = pd.DataFrame(values)
        f["datetime"] = pd.to_datetime(f["datetime"], utc=True)
        for c in ("open", "high", "low", "close"):
            f[c] = f[c].astype(float)
        f = f.sort_values("datetime")
        frames.append(f)
        if len(f) < BAR_PAGE:
            break
        cursor = f["datetime"].iloc[-1] + timedelta(minutes=1)
        time.sleep(8)
    if not frames:
        raise SystemExit("No 1-minute bars returned for the trade period.")
    return pd.concat(frames).drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)


def fetch_bidask_bars() -> dict[str, pd.DataFrame]:
    from forex_client import ForexClient

    client = ForexClient()
    return {side: client.get_bars(4000, side.upper()) for side in ("bid", "ask")}


# ---------------------------------------------------------------- replay

class Series:
    """A price series as numpy arrays for fast scanning."""

    def __init__(self, df: pd.DataFrame):
        self.t = df["datetime"].values.astype("datetime64[ns]")
        self.hi = df["high"].values
        self.lo = df["low"].values
        self.cl = df["close"].values


def walk(trade, exit_series: Series, tp: float, sl: float) -> tuple[float, pd.Timestamp, str] | None:
    """(gross pnl, exit time, 'TP'|'SL'|'EOD') for one trade, or None when there are no bars after the entry."""
    sign = 1.0 if trade.trade_type == "Buy" else -1.0
    open_ts = trade.open_ts
    start = np.searchsorted(exit_series.t, np.datetime64(open_ts.tz_convert("UTC").tz_localize(None)), side="right")
    local_open = open_ts.astimezone(DISPLAY_TZ)
    end_ts = local_open.replace(hour=SESSION_END_HOUR_ET, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))
    stop = np.searchsorted(exit_series.t, np.datetime64(end_ts.replace(tzinfo=None)), side="left")
    if start >= stop:
        return None
    hi, lo = exit_series.hi[start:stop], exit_series.lo[start:stop]
    best = (hi - trade.entry_price) if sign > 0 else (trade.entry_price - lo)
    worst = (trade.entry_price - lo) if sign > 0 else (hi - trade.entry_price)
    sl_hit = np.flatnonzero(worst >= sl)
    tp_hit = np.flatnonzero(best >= tp)
    i_sl = sl_hit[0] if len(sl_hit) else None
    i_tp = tp_hit[0] if len(tp_hit) else None
    if i_sl is not None and (i_tp is None or i_sl <= i_tp):  # a bar touching both counts as the stop
        return -sl, pd.Timestamp(exit_series.t[start + i_sl], tz="UTC"), "SL"
    if i_tp is not None:
        return tp, pd.Timestamp(exit_series.t[start + i_tp], tz="UTC"), "TP"
    last = start + len(hi) - 1
    return sign * (exit_series.cl[last] - trade.entry_price), pd.Timestamp(exit_series.t[last], tz="UTC"), "EOD"


def replay(trades: pd.DataFrame, series_for, tp: float, sl: float, cost: float) -> pd.DataFrame:
    """Run one TP/SL pair over all trades, one position at a time. Returns one row per replayed trade."""
    out, busy_until = [], None
    for t in trades.itertuples():
        if busy_until is not None and t.open_ts + timedelta(minutes=OVERLAP_TOLERANCE_MIN) < busy_until:
            continue  # the replayed trade would still have been open: the live broker could not have entered
        res = walk(t, series_for(t.trade_type), tp, sl)
        if res is None:
            continue
        gross, exit_ts, reason = res
        busy_until = exit_ts
        out.append({"id": t.id, "open_ts": t.open_ts, "exit_ts": exit_ts, "reason": reason, "pnl": gross - cost,
                    "held": (exit_ts - t.open_ts).total_seconds() / 60.0})
    return pd.DataFrame(out, columns=["id", "open_ts", "exit_ts", "reason", "pnl", "held"])


def summarise(r: pd.DataFrame, total_candidates: int, tp: float, sl: float, cost: float) -> dict:
    n = len(r)
    base = {"tp": tp, "sl": sl, "n": n, "skip": total_candidates - n}
    if n == 0:
        return {**base, "net": 0.0, "per_trade": 0.0, "win": float("nan"), "be_win": (sl + cost) / (tp + sl), "t": float("nan"),
                "hold": float("nan"), "per_hour": float("nan"), "dd": 0.0, "days_pos": "0/0"}
    pnl = r["pnl"].values
    sd = pnl.std(ddof=1) if n > 1 else float("nan")
    t = pnl.mean() / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    curve = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:] - curve).max())
    days = r.assign(day=r["open_ts"].dt.tz_convert(DISPLAY_TZ).dt.date).groupby("day")["pnl"].sum()
    held_h = max(r["held"].sum() / 60.0, 1e-9)
    return {**base, "net": float(pnl.sum()), "per_trade": float(pnl.mean()), "win": float((r["reason"] == "TP").mean()),
            "be_win": (sl + cost) / (tp + sl), "t": t, "hold": float(r["held"].mean()), "per_hour": float(pnl.sum() / held_h),
            "dd": dd, "days_pos": f"{int((days > 0).sum())}/{len(days)}"}


# ---------------------------------------------------------------- output

def heatmap(grid: pd.DataFrame, value: str, title: str, fmt: str) -> None:
    pv = grid.pivot(index="tp", columns="sl", values=value)
    print(f"\n{title}  (rows = take-profit $, columns = stop-loss $)")
    print("      " + "".join(f"{c:>8g}" for c in pv.columns))
    for tp, row in pv.iterrows():
        print(f"{tp:>5g} " + "".join(f"{v:>8{fmt}}" for v in row.values))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tp", default="2-10", help="take-profit values: 'lo-hi', 'lo-hi:step' or 'a,b,c' (default 2-10)")
    ap.add_argument("--sl", default="2-10", help="stop-loss values, same syntax (default 2-10)")
    ap.add_argument("--since", help="only trades opened on/after this date (YYYY-MM-DD)")
    ap.add_argument("--cost", type=float, help="per-trade spread/slippage in $ (default: DEFAULT_COST)")
    ap.add_argument("--bidask", action="store_true", help="side-correct FOREX.com bars (last ~2.8 days only), cost 0")
    ap.add_argument("--csv", help="write the full grid to this file")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    trades = load_trades(args.since)
    if trades.empty:
        raise SystemExit("No closed Broker R trades.")

    if args.bidask:
        bars = fetch_bidask_bars()
        covered_from = min(b["datetime"].iloc[0] for b in bars.values())
        dropped = int((trades["open_ts"] < covered_from).sum())
        trades = trades[trades["open_ts"] >= covered_from].reset_index(drop=True)
        if trades.empty:
            raise SystemExit("No Broker R trades inside the FOREX.com bar window.")
        series = {"Buy": Series(bars["bid"]), "Sell": Series(bars["ask"])}  # exit side: Buy exits on the bid, Sell on the ask
        series_for = lambda trade_type: series[trade_type]
        cost, cost_note = 0.0, f"bid/ask bars, cost 0 ({dropped} older trades left out)"
    else:
        mid = Series(fetch_mid_bars(trades["open_ts"].min() - timedelta(minutes=5), pd.Timestamp.now(tz="UTC")))
        series_for = lambda trade_type: mid
        cost = DEFAULT_COST if args.cost is None else args.cost
        cost_note = f"Twelve Data mid bars, cost ${cost:.2f}/trade ({'given' if args.cost is not None else 'assumed default'})"

    n_all = len(trades)
    span = f"{trades['open_ts'].min().astimezone(DISPLAY_TZ):%d %b %H:%M} - {trades['open_ts'].max().astimezone(DISPLAY_TZ):%d %b %H:%M} ET"
    print(f"Broker R TP/SL grid replay: {n_all} closed trades, {span}\nPrices: {cost_note}\n"
          f"Live setting: +${R_TAKE_PROFIT:g} / -${R_STOP_LOSS:g}.  One position at a time, stop wins a tie, 5 PM ET close.")

    live = replay(trades, series_for, R_TAKE_PROFIT, R_STOP_LOSS, cost).merge(trades[["id", "pnl"]], on="id", suffixes=("", "_booked"))
    agree = int(((live["reason"] == "TP") == (live["pnl_booked"] > 0)).sum())
    print(f"Check: replay at the live setting reproduces {agree} of {len(live)} booked win/loss outcomes "
          f"(P&L ${live['pnl'].sum():+.2f} replayed vs ${live['pnl_booked'].sum():+.2f} booked). Disagreements are price-feed differences.")

    rows = []
    for tp in parse_range(args.tp):
        for sl in parse_range(args.sl):
            rows.append(summarise(replay(trades, series_for, tp, sl, cost), n_all, tp, sl, cost))
    grid = pd.DataFrame(rows)

    heatmap(grid, "net", "Net P&L ($)", ".1f")
    heatmap(grid, "per_trade", "Expectancy per trade ($)", ".2f")
    heatmap(grid, "per_hour", "P&L per hour in position ($)", ".1f")
    heatmap(grid, "n", "Trades taken (the rest skipped: still in a replayed trade)", ".0f")

    show = ["tp", "sl", "n", "skip", "net", "per_trade", "win", "be_win", "t", "hold", "per_hour", "dd", "days_pos"]
    fmt = lambda df: df[show].to_string(index=False, float_format=lambda v: f"{v:.2f}")
    live_row = grid[(grid.tp == R_TAKE_PROFIT) & (grid.sl == R_STOP_LOSS)]
    print(f"\nTop {args.top} by P&L per trade (win = share of TP exits; be_win = break-even win rate at that TP/SL; dd = max drawdown $):")
    print(fmt(grid.sort_values("per_trade", ascending=False).head(args.top)))
    if not live_row.empty:
        print("\nLive setting for comparison:")
        print(fmt(live_row))

    enough = n_all >= MIN_TRADES_FOR_ADVICE
    print(f"\nSample: {n_all} trades -- " + (
        "enough to compare settings, but still prefer a region of neighbouring cells over the single best one." if enough else
        f"below {MIN_TRADES_FOR_ADVICE}, so treat every cell as a hint, not a recommendation (a t-stat under ~2 is indistinguishable from noise)."))
    if args.csv:
        grid.to_csv(args.csv, index=False)
        print(f"Grid written to {args.csv}")


if __name__ == "__main__":
    main()
