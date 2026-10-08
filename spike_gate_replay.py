"""Replay of the spike gate (broker_b._spike_confirms()) over every closed Broker B trade. The gate is LIVE since 8 Oct 2026
(split: fades and breakouts have their own multiple, tuned by `start BRA`); this script is the quick, trades-only view of it.

For each closed Broker B trade with an entry_context this recomputes, from the logged 15-min ATR(14) and the entry-side
15-minute one-minute-bar range (Buy = ask range, Sell = bid range), the range/ATR ratio, whether the gate would have blocked it
at each candidate multiple (one multiple for every rule, then the current split), and what the blocked trades made. Prints a
table and a summary per setting. Read-only: talks to Neon over its HTTPS SQL endpoint (see broker_review.py), writes nothing.

    python spike_gate_replay.py

Limits: it removes a blocked trade's P/L from the total but does not model what a blocked trade would have changed next (the
one-position-at-a-time rule could let a later trade open instead), and it only covers trades whose entry_context has both
numbers (Telegram-opened and pre-30 Sep 2026 trades are skipped). Touches blocked by the spike gate itself never became trades,
so this view can't judge loosening it -- `start BRA` (block_rules_analysis.py) replays those too. Small sample: a hint, not a calibration."""

from block_rules import default_rules
from broker_b import ZONE_SCENARIOS, _spike_confirms
from broker_review import _sql

MULTIPLES = (1.25, 1.5, 1.75, 2.0, 2.5)
RULE_TO_SCENARIO = {rule: scenario for scenario, (_type, rule) in ZONE_SCENARIOS.items()}


def load_trades() -> list[dict]:
    rows = []
    for r in _sql(
        "SELECT id, rule_name, trade_type, open_ts, pnl, entry_context FROM broker_b_trades "
        "WHERE status = 'Closed' AND entry_context IS NOT NULL ORDER BY open_ts"
    ):
        scenario = RULE_TO_SCENARIO.get(r["rule_name"])
        if scenario is None:  # Telegram-opened trades
            continue
        ctx = r["entry_context"] or {}
        side = "ask" if r["trade_type"] == "Buy" else "bid"
        rng, atr = ctx.get(f"range_15m_{side}"), ctx.get("atr14")
        if rng is None or atr is None or float(atr) <= 0:
            continue
        rows.append({
            "id": r["id"], "rule": r["rule_name"], "scenario": scenario, "open": str(r["open_ts"])[:16],
            "pnl": float(r["pnl"]), "range": float(rng), "atr": float(atr), "ratio": float(rng) / float(atr),
        })
    return rows


def summarise(label: str, trades: list[dict], rules: dict, total: float) -> None:
    blocked = [t for t in trades if not _spike_confirms(t["scenario"], t["range"], t["atr"], rules)[0]]
    b_pnl = sum(t["pnl"] for t in blocked)
    wins = sum(1 for t in blocked if t["pnl"] > 0)
    print(f"{label:>16}{len(blocked):>9}{b_pnl:>13.2f}{wins:>14}{total - b_pnl:>10.2f}{-b_pnl:>+9.2f}")


def main() -> None:
    trades = load_trades()
    print(f"{len(trades)} closed Broker B trades with range + ATR logged\n")
    print(f"{'trade':<8}{'rule':<18}{'opened (UTC)':<18}{'range':>7}{'ATR':>7}{'ratio':>7}{'P/L':>9}")
    for t in sorted(trades, key=lambda t: -t["ratio"]):
        print(f"B#{t['id']:<6}{t['rule']:<18}{t['open']:<18}{t['range']:>7.2f}{t['atr']:>7.2f}"
              f"{t['ratio']:>7.2f}{t['pnl']:>9.2f}")
    total = sum(t["pnl"] for t in trades)
    print(f"\nall trades: n={len(trades)} P/L={total:+.2f}\n")
    print(f"{'setting':>16}{'blocked':>9}{'blocked P/L':>13}{'blocked wins':>14}{'kept P/L':>10}{'change':>9}")
    for m in MULTIPLES:
        summarise(f"all rules {m:g}x", trades, {"spike_fade": m, "spike_breakout": m}, total)
    d = default_rules()
    summarise(f"split {d['spike_fade']:g}/{d['spike_breakout']:g}", trades, d, total)


if __name__ == "__main__":
    main()
