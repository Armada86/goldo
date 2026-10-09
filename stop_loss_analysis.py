"""Stop loss analysis (SLA): replay every closed Broker A / Broker B trade against a grid of stop-loss and
trailing-stop settings and advise which to use.

Triggered from Telegram ("start stop loss analysis" / "start SLA" -> Worker -> stop_loss_analysis.yml ->
stop_loss_analysis_job.py). This module is the pure part (no DB, no network) so it can be tested directly.

How it works: each trade is replayed from its real entry price and time against 1-minute bars (side-correct
FOREX.com bid/ask where the bars reach back, Twelve Data mid for older trades), using the SAME exit rule the
brokers run live (broker._scan_exit_crossing()): a stop that starts at -stop_loss and, once the trade has been
`activation` in profit, follows `distance` behind the best price (never below -stop_loss, only ever moving in
the trade's favour); no fixed take-profit; each bar is checked against the stop as it stood before that bar can
raise the peak; the exit fills at the stop level. A trade the stop never catches is marked to the last bar.
The replay is vectorised (numpy) so a few hundred settings x tens of trades is instant; its parity with the live
scan is checked in the tests/verification, not assumed.

Broker B fades (TA-Zone-* trades, 9 Oct 2026) use a different exit, which the replay models for the paths flagged `fade`
(see TradePath, build_path() and broker._scan_exit_crossing(fade=True)): the hard stop is capped at FADE_STOP_LOSS_CAP and, when
the trade's lock level (the opposite fade level) is known, the ordinary trailing stop is OFF until a bar reaches that level; from
the next bar the stop is at least that level's profit and trails FADE_LOCK_TRAIL_DISTANCE behind the best price. A fade without a
lock level keeps the ordinary trail (with the capped stop). The grid's activation / distance therefore only drive the other trades.

One position at a time: the brokers only ever hold one trade each (Broker A and Broker B are independent), so
the replay is sequential per broker. A trade is skipped (counts 0) if the previous simulated trade of that broker
is still open when it enters, which is what stops a wide trailing stop from "holding" a runner for hours while
also getting credit for every later trade. Only the trades that really happened are known signals; signals that
were blocked at the time cannot be replayed.

Horizon: each trade is followed only until its stop is hit or 5 PM ET on its trading day (HORIZON_HOUR_ET),
then marked to the price there. Without that cap a wide stop lets one trade run for days, which the live
one-position-at-a-time rule would not allow, and the totals just measure gold's trend over the whole period.

Choosing a setting: the raw best of a grid is dominated by noise with only dozens of trades, so each setting is
scored by the mean total P/L of itself and its neighbours one grid step away in every direction (a plateau beats
a lucky spike). The advice is "change" only when the recommended setting beats the CURRENT live setting by a
real margin, otherwise "keep"."""

from dataclasses import dataclass
from datetime import timedelta, timezone
from itertools import product
from zoneinfo import ZoneInfo

import numpy as np

from config import FADE_LOCK_TRAIL_DISTANCE, FADE_STOP_LOSS_CAP

DISPLAY_TZ = ZoneInfo("America/New_York")
SLA_PREFIX = "\U0001F4CA "  # bar chart -- distinct from the gold (yellow), RSI (orange), broker (blue) and release (purple) prefixes

# Candidate settings, $ per troy ounce. Neighbour smoothing works in index space, so keep each list ascending.
STOP_LOSS_GRID = [5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0]
ACTIVATION_GRID = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]
DISTANCE_GRID = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]

OLD_FIXED_STOP = 10.0  # the pre-2 Oct 2026 rule, shown for reference: fixed +$10 target, $10 stop
OLD_FIXED_TARGET = 10.0

HORIZON_HOUR_ET = 17  # a trade is followed until its stop is hit or 5 PM ET (the brokers' trading window ends), then marked there
GRID_EDGE_NOTE = "The advised value sits at the edge of the range tested, so a value beyond it might score even better; treat it with caution."

MIN_TRADES = 10  # below this the advice would be meaningless
CHANGE_MIN_GAIN = 10.0  # $; the recommended setting must beat the current one by at least this ...
CHANGE_MIN_GAIN_PCT = 0.10  # ... and by this share of the current total, whichever is larger


@dataclass
class TradePath:
    """One trade's price path after entry, as per-bar excursions in $ per oz."""

    label: str
    broker: str  # "A" or "B": each broker holds one position at a time, independently of the other
    open_ts: np.datetime64  # entry time (UTC, naive), for the one-position-at-a-time rule
    times: np.ndarray  # datetime64 start of each bar (UTC, naive)
    adv: np.ndarray  # worst move against the trade within each bar
    fav: np.ndarray  # best move in the trade's favour within each bar
    last_pnl: float  # P/L if marked to the last bar (used when the stop is never hit)
    source: str  # "bid/ask" or "mid"
    fade: bool = False  # Broker B TA-Zone-* trade: capped hard stop, and (with a lock level) no ordinary trail before the lock
    lock_profit: float | None = None  # profit in $ at the fade's opposite level (the lock level), None if unknown / not in the trade's favour


def build_path(label: str, broker: str, trade_type: str, entry: float, open_ts, bars, source: str,
               fade: bool = False, lock_level: float | None = None):
    """TradePath for a trade, from bars that are already side-correct and strictly after the open
    (columns: datetime, high, low, close). None if there are no bars to replay. `fade` / `lock_level`: a Broker B TA-Zone-*
    trade and the price of its opposite fade level (None if unknown); a level that is not beyond the entry in the trade's favour is ignored."""
    if bars is None or len(bars) == 0:
        return None
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    last_close = float(bars["close"].iloc[-1])
    if trade_type == "Buy":
        adv, fav, last = entry - low, high - entry, last_close - entry
    else:
        adv, fav, last = high - entry, entry - low, entry - last_close
    times = bars["datetime"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    open_np = np.datetime64(open_ts.astimezone(timezone.utc).replace(tzinfo=None), "ns")
    lock_profit = None
    if fade and lock_level is not None:
        profit = (float(lock_level) - entry) if trade_type == "Buy" else (entry - float(lock_level))
        lock_profit = profit if profit > 0 else None
    return TradePath(label, broker, open_np, times, adv, fav, float(last), source, bool(fade), lock_profit)


def replay(path: TradePath, stop_loss: float, activation: float, distance: float, target: float | None = None):
    """(pnl, resolved, exit_time) for one trade under one setting. `target` (a fixed take-profit) is only
    used for the old-rule reference; the stop is checked before the target within a bar, like the live scan.
    exit_time is when the trade closes (an unresolved trade is held to its last bar)."""
    fav, adv = path.fav, path.adv
    n = len(fav)
    peak_before = np.zeros(n)
    if n > 1:
        peak_before[1:] = np.maximum.accumulate(np.maximum(fav, 0.0))[:-1]
    fade = path.fade and target is None  # the old fixed-target rule (a reference only) never had the fade exit rule
    if fade:  # Broker B fade exit rule: capped hard stop, no ordinary trail while there is a lock level to reach
        stop_loss = min(stop_loss, FADE_STOP_LOSS_CAP)
        if path.lock_profit is not None:
            activation = distance = float("inf")
    offset = np.where(peak_before >= activation, np.maximum(-stop_loss, peak_before - distance), -stop_loss)
    if fade and path.lock_profit is not None:
        reached = fav >= path.lock_profit
        if reached.any():  # the lock applies from the bar AFTER the first one that reached the level
            first = int(np.argmax(reached)) + 1
            offset[first:] = np.maximum(
                np.maximum(offset[first:], path.lock_profit), peak_before[first:] - FADE_LOCK_TRAIL_DISTANCE
            )
    stop_hit = adv >= -offset
    stop_i = int(np.argmax(stop_hit)) if stop_hit.any() else n
    tp_i = n
    if target is not None:
        tp_hit = fav >= target
        tp_i = int(np.argmax(tp_hit)) if tp_hit.any() else n
    if stop_i < n and stop_i <= tp_i:
        return float(offset[stop_i]), True, path.times[stop_i]
    if tp_i < n:
        return float(target), True, path.times[tp_i]
    return path.last_pnl, False, path.times[-1]


def simulate(paths, stop_loss, activation, distance, target=None):
    """Sequential replay of all trades under one setting, one position at a time per broker.
    Returns (pnls, unresolved, skipped): pnls[i] is trade i's P/L (0 if it was skipped because the broker's
    previous simulated trade was still open at its entry)."""
    pnls = [0.0] * len(paths)
    unresolved = skipped = 0
    busy_until = {}
    for i in sorted(range(len(paths)), key=lambda k: paths[k].open_ts):
        p = paths[i]
        if p.open_ts < busy_until.get(p.broker, np.datetime64("1970-01-01", "ns")):
            skipped += 1
            continue
        pnl, resolved, exit_time = replay(p, stop_loss, activation, distance, target)
        pnls[i] = pnl
        unresolved += 0 if resolved else 1
        busy_until[p.broker] = exit_time
    return pnls, unresolved, skipped


def total_pnl(paths, stop_loss, activation, distance, target=None):
    """(total, unresolved_count, skipped_count) over all trades for one setting."""
    pnls, unresolved, skipped = simulate(paths, stop_loss, activation, distance, target)
    return float(sum(pnls)), unresolved, skipped


def _smooth(totals: np.ndarray) -> np.ndarray:
    """Mean of each cell and its in-bounds neighbours (one step in every direction)."""
    padded = np.pad(totals, 1, mode="constant", constant_values=np.nan)
    stack = []
    for di, dj, dk in product((-1, 0, 1), repeat=3):
        stack.append(
            padded[1 + di : 1 + di + totals.shape[0], 1 + dj : 1 + dj + totals.shape[1], 1 + dk : 1 + dk + totals.shape[2]]
        )
    return np.nanmean(np.stack(stack), axis=0)


def analyse(paths, current_stop_loss: float, current_activation: float, current_distance: float) -> dict:
    """Run the whole grid. Returns everything the report needs (see format_report())."""
    shape = (len(STOP_LOSS_GRID), len(ACTIVATION_GRID), len(DISTANCE_GRID))
    totals = np.zeros(shape)
    unresolved = np.zeros(shape, dtype=int)
    skipped = np.zeros(shape, dtype=int)
    for (i, sl), (j, act), (k, dist) in product(
        enumerate(STOP_LOSS_GRID), enumerate(ACTIVATION_GRID), enumerate(DISTANCE_GRID)
    ):
        totals[i, j, k], unresolved[i, j, k], skipped[i, j, k] = total_pnl(paths, sl, act, dist)
    smooth = _smooth(totals)

    # Best (smoothed) setting overall, and the best for each stop-loss size.
    best_flat = int(np.nanargmax(smooth))
    bi, bj, bk = np.unravel_index(best_flat, shape)
    per_sl = []
    for i, sl in enumerate(STOP_LOSS_GRID):
        j, k = np.unravel_index(int(np.nanargmax(smooth[i])), smooth[i].shape)
        per_sl.append({"stop_loss": sl, "activation": ACTIVATION_GRID[j], "distance": DISTANCE_GRID[k],
                       "total": float(totals[i, j, k]), "smoothed": float(smooth[i, j, k])})
    # A few runners-up, at least one grid step away from the pick so they are genuinely different.
    order = np.argsort(-smooth, axis=None)
    alternatives = []
    for flat in order:
        i, j, k = np.unravel_index(int(flat), shape)
        if (i, j, k) == (bi, bj, bk):
            continue
        if max(abs(i - bi), abs(j - bj), abs(k - bk)) < 2:
            continue
        alternatives.append({"stop_loss": STOP_LOSS_GRID[i], "activation": ACTIVATION_GRID[j],
                             "distance": DISTANCE_GRID[k], "total": float(totals[i, j, k]),
                             "smoothed": float(smooth[i, j, k])})
        if len(alternatives) == 3:
            break

    cur_pnls, current_unresolved, current_skipped = simulate(
        paths, current_stop_loss, current_activation, current_distance)
    current_total = float(sum(cur_pnls))
    old_total, _, _ = total_pnl(paths, OLD_FIXED_STOP, 1e9, 1e9, target=OLD_FIXED_TARGET)  # activation never reached -> plain stop + target
    rec = {"stop_loss": STOP_LOSS_GRID[bi], "activation": ACTIVATION_GRID[bj], "distance": DISTANCE_GRID[bk],
           "total": float(totals[bi, bj, bk]), "smoothed": float(smooth[bi, bj, bk]),
           "unresolved": int(unresolved[bi, bj, bk]), "skipped": int(skipped[bi, bj, bk])}
    gain = rec["total"] - current_total
    needed = max(CHANGE_MIN_GAIN, CHANGE_MIN_GAIN_PCT * abs(current_total))
    # Robustness: how much of the improvement comes from the single trade that improved most?
    rec_pnls, _, _ = simulate(paths, rec["stop_loss"], rec["activation"], rec["distance"])
    diffs = [a - b for a, b in zip(rec_pnls, cur_pnls)]
    best_i = int(np.argmax(diffs))
    gain_without_best = gain - diffs[best_i]
    rec["best_trade"] = paths[best_i].label
    rec["best_trade_gain"] = float(diffs[best_i])
    rec["gain_without_best"] = float(gain_without_best)
    same = (rec["stop_loss"], rec["activation"], rec["distance"]) == (
        current_stop_loss, current_activation, current_distance)
    return {
        "n": len(paths),
        "n_fade": sum(1 for p in paths if p.fade),
        "n_fade_locked": sum(1 for p in paths if p.fade and p.lock_profit is not None),
        "recommended": rec,
        "per_stop_loss": per_sl,
        "alternatives": alternatives,
        "current": {"stop_loss": current_stop_loss, "activation": current_activation,
                    "distance": current_distance, "total": current_total, "unresolved": current_unresolved,
                    "skipped": current_skipped},
        "old_rule_total": old_total,
        "gain": gain,
        "needed_gain": needed,
        "change": (not same) and gain >= needed and gain_without_best > 0,
    }


def confidence(n: int) -> str:
    if n < 30:
        return f"low ({n} trades; a few more weeks of trades would firm this up)"
    if n < 100:
        return f"medium ({n} trades)"
    return f"good ({n} trades)"


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def _num(x: float) -> str:
    return f"{x:g}"


def horizon_end(open_ts):
    """The first 5 PM ET after open_ts (tz-aware datetime): where a trade's replay stops."""
    local = open_ts.astimezone(DISPLAY_TZ)
    end = local.replace(hour=HORIZON_HOUR_ET, minute=0, second=0, microsecond=0)
    if end <= local:
        end += timedelta(days=1)
    return end


def at_grid_edge(rec: dict) -> bool:
    return (rec["stop_loss"] in (STOP_LOSS_GRID[0], STOP_LOSS_GRID[-1])
            or rec["activation"] in (ACTIVATION_GRID[0], ACTIVATION_GRID[-1])
            or rec["distance"] in (DISTANCE_GRID[0], DISTANCE_GRID[-1]))


def format_report(result: dict, first_ts, last_ts, n_bid_ask: int, n_mid: int, n_skipped: int, applied: bool | None = None) -> str:
    """The Telegram message. `applied`: True when the job wrote the advised settings, False when that failed (the commands
    to apply them by hand are shown), None for a dry run."""
    rec, cur = result["recommended"], result["current"]
    lines = [
        f"{SLA_PREFIX}STOP LOSS ANALYSIS",
        f"{result['n']} closed trades, {first_ts.astimezone(DISPLAY_TZ):%d %b} - {last_ts.astimezone(DISPLAY_TZ):%d %b %Y} ET "
        f"({n_bid_ask} on FOREX.com bid/ask prices, {n_mid} on Twelve Data mid prices"
        + (f", {n_skipped} skipped, no price data" if n_skipped else "") + ")",
    ]
    if result.get("n_fade"):
        lines.append(
            f"{result['n_fade']} are Broker B fade trades, replayed with their own exit rule (stop capped at ${_num(FADE_STOP_LOSS_CAP)}, "
            f"no trailing stop until the opposite level, then ${_num(FADE_LOCK_TRAIL_DISTANCE)} behind the best price; "
            f"{result['n_fade'] - result['n_fade_locked']} without a known level keep the normal trail). The settings below drive the other "
            f"{result['n'] - result['n_fade']} trades."
        )
    lines.append("")
    if result["change"]:
        lines.append("ADVICE: CHANGE")
    elif result["gain"] >= result["needed_gain"] and rec["gain_without_best"] <= 0:
        lines.append(f"ADVICE: KEEP CURRENT SETTINGS (the best setting only wins because of one trade, {rec['best_trade']})")
    else:
        lines.append("ADVICE: KEEP CURRENT SETTINGS (no setting beats them by a clear margin)")
    if result["change"]:
        shown, tag, label = rec, "", "this setting"
    else:  # keep: the bullets show what is still in force, not the better-looking setting that was not applied
        shown, tag, label = cur, " (current)", f"the best setting tried (-${_num(rec['stop_loss'])}, trail {_num(rec['activation'])}/{_num(rec['distance'])})"
    lines += [
        f"- Stop loss: -${_num(shown['stop_loss'])}{tag}",
        f"- Trailing stop: starts at +${_num(shown['activation'])}, follows ${_num(shown['distance'])} behind the best price{tag}",
        f"Replay result: {_money(rec['total'])} for {label} vs {_money(cur['total'])} for your current "
        f"(-${_num(cur['stop_loss'])}, trail {_num(cur['activation'])}/{_num(cur['distance'])}). "
        f"The old fixed +$10 / -$10 rule: {_money(result['old_rule_total'])}.",
        f"Needs to beat the current by {_money(result['needed_gain'])} to advise a change; it beats it by {_money(result['gain'])} "
        f"({_money(rec['gain_without_best'])} without its single best trade, {rec['best_trade']}).",
    ]
    if result["change"] and applied:
        lines += [
            "",
            "APPLIED AUTOMATICALLY (both brokers use it from their next check):",
            f"Stop loss -${_num(rec['stop_loss'])}, trailing stop starts at +${_num(rec['activation'])} and follows ${_num(rec['distance'])} behind",
        ]
    elif result["change"]:
        lines += [
            "",
            "COULD NOT APPLY (database error); to apply by hand:" if applied is False else "To apply:",
            f"make SL {_num(rec['stop_loss'])}",
            f"make trail {_num(rec['distance'])} activate {_num(rec['activation'])}",
        ]
    return "\n".join(lines)
