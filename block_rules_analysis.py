"""Block rules analysis (BRA): tune Broker B's entry filters (DXY, ADX, RSI, ATR, spike) from history.

Triggered from Telegram ("start block rules analysis" / "start BRA" -> Worker -> block_rules_analysis.yml ->
block_rules_analysis_job.py). This module is the pure part (no DB, no network) so it can be tested directly;
the job fetches the data, calls analyse(), sends the report, and writes the result to the `block_rules` table.

Events: every Broker B touch we know of is replayed -- the trades that really opened (their real entry), plus the
touches that one of these filters blocked (broker_b_blocked; entered at the level price at the recorded touch
time, an approximation: the stored time is when the poll saw the bar, not the exact touch). Each event carries the
conditions at that moment (RSI/ADX/ATR(14) on 15-min candles, DXY's 15-min move, the entry-side range of the last 15 one-minute bars) and a price path from
stop_loss_analysis.build_path(), replayed with the SAME exit rule the brokers run live and the stop / trailing
settings currently in force. Broker B fade events (TA-Zone-*) get their own exit rule (stop capped at config.FADE_STOP_LOSS_CAP, no
ordinary trail before the opposite fade level, then the lock; 9 Oct 2026), exactly as stop_loss_analysis.replay() models it.

A candidate set of rules is scored by running the REAL gate functions (broker_b._dxy_confirms_changes() / _adx_confirms() /
_rsi_confirms() / _atr_confirms() / _spike_confirms()) on every event: events that pass are taken (one position at a time, like
Broker B), the rest are blocked. So the analysis can't drift from what the live gates do.

Choosing values: greedy coordinate search over units. Each single rule, and each family's spike pair (ATR multiple x dollar
ceiling, fades and breakouts separately, added 4 Nov 2026), is swept over its own grid with the others
held at their current setting; a grid point is scored by the mean total P/L of itself and its neighbours one step
away (a plateau beats a lucky spike). The best change is applied only if it beats the current total by a real
margin AND still wins without the single event that gained the most; then the sweep repeats from the new rules
until nothing qualifies. Guards (added 4 Oct 2026, after a first run switched both ADX blocks off from 40 events): a rule
moves at most one grid step per run (per axis for a spike pair), and moving one to its OFF value needs twice the margin and the margin without
its best event. With fewer than MIN_EVENTS events nothing changes.

Limits, stated in the report: the event pool only contains touches that were recorded (traded, or blocked by one of
these filters), so a value that would have let in a touch no filter ever saw can't be judged; blocked touches are
replayed from an approximate entry time."""

import itertools
from dataclasses import dataclass

import numpy as np

import stop_loss_analysis as sla
from block_rules import OFF_VALUES, RULE_KEYS
from config import ADX_CHOP_FLOOR
from broker_b import _adx_confirms, _atr_confirms, _dxy_confirms_changes, _rsi_confirms, _spike_confirms

BRA_PREFIX = "\U0001F6E1️ "  # shield -- distinct from the other Telegram prefixes

MIN_EVENTS = 12  # below this the analysis changes nothing
CHANGE_MIN_GAIN = 10.0  # $: a change must beat the current total by at least this ...
CHANGE_MIN_GAIN_PCT = 0.10  # ... and by this share of the current total, whichever is larger
OFF_MARGIN_FACTOR = 2.0  # switching a block fully off needs this multiple of that margin

# Candidate values, ascending. dxy_threshold's grid is built from its current value (see grid_for()).
GRIDS = {
    "atr_max": [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 16.0, 20.0, 99.0],
    "adx_trending": [20.0, 22.0, 25.0, 28.0, 30.0, 35.0, 99.0],
    "adx_chop": [float(ADX_CHOP_FLOOR), 22.0, 25.0],  # never below the floor (6 Oct 2026), so it can no longer be switched off
    "rsi_overbought": [65.0, 70.0, 75.0, 80.0, 101.0],
    "rsi_oversold": [0.0, 20.0, 25.0, 30.0, 35.0],
    "fade_rsi_overbought": [60.0, 64.0, 66.0, 68.0, 70.0, 72.0, 101.0],
    "fade_rsi_oversold": [0.0, 28.0, 30.0, 32.0, 34.0, 36.0, 40.0],
    "spike_fade": [1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 99.0],  # x ATR(14); fades want a spike, so no value below 1.75
    "spike_breakout": [1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 99.0],
    # Dollar ceilings on the same 15-minute range (4 Nov 2026). Tuned together with the multiple above, per family (see UNITS).
    "spike_fade_ceiling": [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 25.0, 99.0],
    "spike_breakout_ceiling": [8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 25.0, 99.0],
}
# What is searched together. A single rule is its own unit; the spike gate of each family is one 2-D unit (ATR multiple x dollar
# ceiling), because the two interact: a $16 ceiling with a 2.0x multiple scored +$7 on fades while $16 with 3.0x scored +$18
# (study 26, 4 Nov 2026). Fades and breakouts are separate units, so each family gets its own study.
UNITS = tuple((k,) for k in RULE_KEYS if k not in ("spike_fade", "spike_breakout", "spike_fade_ceiling", "spike_breakout_ceiling")) + (
    ("spike_fade", "spike_fade_ceiling"),
    ("spike_breakout", "spike_breakout_ceiling"),
)
DXY_FACTORS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

LABELS = {
    "dxy_threshold": "DXY 15-min move",
    "adx_trending": "ADX: block fades at >=",
    "adx_chop": "ADX: block breakouts below",
    "rsi_overbought": "RSI: block breakout buy at >=",
    "rsi_oversold": "RSI: block breakout sell at <=",
    "fade_rsi_overbought": "RSI: block fade sell at >=",
    "fade_rsi_oversold": "RSI: block fade buy at <=",
    "atr_max": "ATR: block everything at >= $",
    "spike_fade": "Spike: block fades at range >= x ATR",
    "spike_breakout": "Spike: block breakouts at range >= x ATR",
    "spike_fade_ceiling": "Spike: block fades at range >= $",
    "spike_breakout_ceiling": "Spike: block breakouts at range >= $",
}


@dataclass
class Event:
    label: str
    scenario: str  # sell_resistance / buy_support / bull_breakout / bear_breakdown
    trade_type: str  # "Buy" or "Sell"
    was_trade: bool  # True: really opened; False: blocked by one of the filters
    rsi: float | None
    adx: float | None
    atr: float | None
    dxy_change: float | None  # DXY's net move over the trailing 15 min at the event
    path: sla.TradePath
    dxy_changes: list | None = None  # that move at each of the last 3 polls up to the event (oldest first), for the 3-in-a-row DXY gate
    adx_rising: bool | None = None  # ADX above the previous 15-min candle's at the event (fade gate)
    range_15m: float | None = None  # entry-side high-low range of the 15 one-minute bars up to the event (spike gate)


def passes(ev: Event, rules: dict) -> bool:
    """True if the live gates, run with `rules`, would let this event through."""
    return (
        _dxy_confirms_changes(ev.trade_type, ev.dxy_changes, rules)[0]
        and _rsi_confirms(ev.scenario, ev.rsi, ev.adx, rules)[0]
        and _adx_confirms(ev.scenario, ev.adx, rules, ev.adx_rising)[0]
        and _atr_confirms(ev.atr, rules)[0]
        and _spike_confirms(ev.scenario, ev.range_15m, ev.atr, rules)[0]
    )


def event_pnls(events: list[Event], rules: dict, stop_loss: float, activation: float, distance: float) -> list[float]:
    """P/L of each event under `rules` (0 for a blocked event, or one skipped because the previous Broker B
    position was still open)."""
    taken = [i for i, ev in enumerate(events) if passes(ev, rules)]
    pnls = [0.0] * len(events)
    if taken:
        sub, _, _ = sla.simulate([events[i].path for i in taken], stop_loss, activation, distance)
        for i, p in zip(taken, sub):
            pnls[i] = p
    return pnls


def grid_for(key: str, current_rules: dict) -> list[float]:
    """The candidate values for one rule, always including its current value, ascending."""
    if key == "dxy_threshold":
        base = current_rules["dxy_threshold"]
        grid = [round(base * f, 4) for f in DXY_FACTORS] + [OFF_VALUES["dxy_threshold"]]
        if base >= OFF_VALUES["dxy_threshold"]:  # already off: sweep from the auto-tuned default instead
            grid = [OFF_VALUES["dxy_threshold"]]
    else:
        grid = list(GRIDS[key])
    cur = current_rules[key]
    if cur not in grid:
        grid.append(cur)
    return sorted(set(grid))


def _smooth(totals: list[float]) -> list[float]:
    out = []
    for i in range(len(totals)):
        window = totals[max(0, i - 1): i + 2]
        out.append(sum(window) / len(window))
    return out


def analyse(events: list[Event], current_rules: dict, stop_loss: float, activation: float, distance: float) -> dict:
    """The whole analysis. Returns everything the report and the table write need."""
    n_trades = sum(1 for e in events if e.was_trade)
    base = {
        "n_events": len(events),
        "n_trades": n_trades,
        "n_blocked": len(events) - n_trades,
        "current_rules": dict(current_rules),
        "stop": (stop_loss, activation, distance),
    }
    rules = dict(current_rules)
    cur_pnls = event_pnls(events, rules, stop_loss, activation, distance)
    start_total = float(sum(cur_pnls))
    if len(events) < MIN_EVENTS:
        return {**base, "enough": False, "new_rules": rules, "steps": [], "current_total": start_total,
                "new_total": start_total, "changed": False, "per_rule": {}}

    steps = []
    moved = set()  # each unit (a single rule, or a family's multiple + ceiling pair) moves at most once per run
    for _ in range(len(UNITS)):
        cur_total = float(sum(cur_pnls))
        needed = max(CHANGE_MIN_GAIN, CHANGE_MIN_GAIN_PCT * abs(cur_total))
        best = None
        for unit in UNITS:
            if unit in moved:
                continue
            grids = [grid_for(k, rules) for k in unit]
            totals = {
                idx: float(sum(event_pnls(events, {**rules, **{k: grids[d][i] for d, (k, i) in enumerate(zip(unit, idx))}}, stop_loss, activation, distance)))
                for idx in itertools.product(*[range(len(g)) for g in grids])
            }
            cur_idx = tuple(g.index(rules[k]) for g, k in zip(grids, unit))

            def near_of(idx):
                return list(itertools.product(*[range(max(0, i - 1), min(len(g), i + 2)) for i, g in zip(idx, grids)]))

            smooth = {idx: sum(totals[j] for j in near_of(idx)) / len(near_of(idx)) for idx in totals}
            # Guard: a unit moves at most ONE grid step per axis per run (so a block can never be switched off, or jump across
            # its range, on one run's evidence), chosen among the current value and its neighbours (2 in a 1-D grid, up to 8 in the 2-D spike pair).
            near = near_of(cur_idx)
            pick_idx = max(near, key=lambda i: (smooth[i], i == cur_idx))
            if pick_idx == cur_idx:
                continue
            pick = {k: grids[d][i] for d, (k, i) in enumerate(zip(unit, pick_idx))}
            cand_pnls = event_pnls(events, {**rules, **pick}, stop_loss, activation, distance)
            diffs = [a - b for a, b in zip(cand_pnls, cur_pnls)]
            gain = float(sum(diffs))
            best_i = int(np.argmax(diffs))
            gain_without_best = gain - diffs[best_i]
            # Removing a block altogether (every value of the unit off) needs a bigger win: twice the margin, and still clearing
            # the margin without its single best event.
            if all(pick[k] == OFF_VALUES[k] for k in unit):
                ok = gain >= OFF_MARGIN_FACTOR * needed and gain_without_best >= needed
            else:
                ok = gain >= needed and gain_without_best > 0
            if ok and (best is None or gain > best["gain"]):
                best = {"unit": unit, "old": {k: rules[k] for k in unit}, "new": pick, "gain": gain, "needed": needed,
                        "gain_without_best": float(gain_without_best), "best_event": events[best_i].label,
                        "pnls": cand_pnls}
        if best is None:
            break
        rules.update(best["new"])
        moved.add(best["unit"])
        cur_pnls = best.pop("pnls")
        steps.append(best)

    # What each rule looks like at the final setting, for the report (single rules only: the spike pairs are 2-D).
    per_rule = {}
    for key in RULE_KEYS:
        grid = grid_for(key, rules)
        if (key,) in UNITS:
            totals = [float(sum(event_pnls(events, {**rules, key: v}, stop_loss, activation, distance))) for v in grid]
        else:
            totals = None
        per_rule[key] = {"grid": grid, "totals": totals, "current": rules[key]}
    return {**base, "enough": True, "new_rules": rules, "steps": steps, "current_total": start_total,
            "new_total": float(sum(cur_pnls)), "changed": bool(steps), "per_rule": per_rule}


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def fmt_value(key: str, v: float) -> str:
    if v == OFF_VALUES[key]:
        return "off"
    if key.endswith("_ceiling"):
        return f"${v:g}"
    return f"{v:.4f}".rstrip("0").rstrip(".") if key == "dxy_threshold" else f"{v:g}"


def fmt_unit(unit: tuple, values: dict) -> str:
    """A step's old/new value: one number, or 'multiple + ceiling' for a spike pair (e.g. '3x ATR / $16')."""
    if len(unit) == 1:
        return fmt_value(unit[0], values[unit[0]])
    mult, ceil = unit
    return f"{fmt_value(mult, values[mult])}x ATR / {fmt_value(ceil, values[ceil])}"


def confidence(n: int) -> str:
    if n < 30:
        return f"low ({n} events; a few more weeks of trading would firm this up)"
    if n < 100:
        return f"medium ({n} events)"
    return f"good ({n} events)"


def format_report(result: dict, first_ts, last_ts, n_skipped: int = 0) -> str:
    """The Telegram message."""
    lines = [
        f"{BRA_PREFIX}BLOCK RULES ANALYSIS",
        f"{result['n_events']} Broker B events {first_ts.astimezone(sla.DISPLAY_TZ):%d %b} - "
        f"{last_ts.astimezone(sla.DISPLAY_TZ):%d %b %Y} ET: {result['n_trades']} trades that opened + "
        f"{result['n_blocked']} touches blocked by DXY/ADX/RSI/ATR/spike"
        + (f" ({n_skipped} skipped, no price data)" if n_skipped else "") + ".",
        "",
    ]
    if not result["enough"]:
        lines.append(f"NOT ENOUGH DATA: need at least {MIN_EVENTS} events, so no rule was changed.")
        lines.append("The block_rules table keeps the same values.")
        return "\n".join(lines)
    stop, act, dist = result["stop"]
    lines.append("RULES UPDATED IN THE block_rules TABLE" if result["changed"] else "NO CHANGE (no setting beats the current rules by a clear margin)")
    if result["changed"]:
        for step in result["steps"]:
            unit = step["unit"]
            label = LABELS[unit[0]] if len(unit) == 1 else ("Spike gate for fades (multiple / ceiling)" if "fade" in unit[0] else "Spike gate for breakouts (multiple / ceiling)")
            lines.append(
                f"- {label}: {fmt_unit(unit, step['old'])} -> {fmt_unit(unit, step['new'])} "
                f"({_money(step['gain'])}; {_money(step['gain_without_best'])} without its best event, {step['best_event']})"
            )
    lines += [
        f"Replay result: {_money(result['new_total'])} with these rules vs {_money(result['current_total'])} with the old ones "
        f"(stop -${stop:g}, trail from +${act:g}, ${dist:g} behind).",
        "",
        "Active rules now:",
    ]
    for key in RULE_KEYS:
        lines.append(f"{LABELS[key]}: {fmt_value(key, result['new_rules'][key])}")
    lines += [
        "",
        f"Confidence: {confidence(result['n_events'])}.",
        "Every touch is replayed with the live exit rule (fades: stop capped at $15, no trailing stop before the opposite level) and the stop settings above, one position at a time. Blocked touches are "
        "replayed from the level price at the recorded touch time (approximate). Each value is scored with its neighbours, and a "
        "change needs to still win without its single best event. A rule moves at most one grid step per run, and switching a block off needs a bigger win. Touches no filter ever recorded can't be judged.",
    ]
    return "\n".join(lines)
