"""Replay of the spike gate (broker_b._spike_confirms()) over every closed Broker A / Broker B trade -- REPLAY ONLY.

The gate is not wired into live trading. For each closed trade with an entry_context this recomputes, from the logged
15-min ATR(14) and the entry-side 15-minute one-minute-bar range (Buy = ask range, Sell = bid range), the range/ATR
ratio, whether the gate would have blocked it at each candidate multiple, and what the blocked trades made. Prints a table
and a summary per multiple. Read-only: talks to Neon over its HTTPS SQL endpoint (see broker_review.py), writes nothing.

    python spike_gate_replay.py

Limits: it removes a blocked trade's P/L from the total but does not model what a blocked trade would have changed next (the
one-position-at-a-time rule could let a later trade open instead), and it only covers trades whose entry_context has both
numbers (Telegram-opened and pre-30 Sep 2026 trades are skipped). Blocked-by-other-filters touches (broker_b_blocked) have
no entry_context, so only real trades are replayed. Small sample: treat the result as a hint, not a calibration."""

from broker_b import _spike_confirms
from broker_review import _sql

MULTIPLES = (1.25, 1.5, 1.75, 2.0, 2.5)


def load_trades() -> list[dict]:
    rows = []
    for broker, table in (("A", "trades"), ("B", "broker_b_trades")):
        for r in _sql(
            f"SELECT id, rule_name, trade_type, open_ts, pnl, entry_context FROM {table} "
            "WHERE status = 'Closed' AND entry_context IS NOT NULL ORDER BY open_ts"
        ):
            ctx = r["entry_context"] or {}
            side = "ask" if r["trade_type"] == "Buy" else "bid"
            rng, atr = ctx.get(f"range_15m_{side}"), ctx.get("atr14")
            if rng is None or atr is None or float(atr) <= 0:
                continue
            rows.append({
                "broker": broker, "id": r["id"], "rule": r["rule_name"], "open": str(r["open_ts"])[:16],
                "pnl": float(r["pnl"]), "range": float(rng), "atr": float(atr), "ratio": float(rng) / float(atr),
            })
    return rows


def main() -> None:
    trades = load_trades()
    print(f"{len(trades)} closed trades with range + ATR logged\n")
    print(f"{'trade':<8}{'rule':<18}{'opened (UTC)':<18}{'range':>7}{'ATR':>7}{'ratio':>7}{'P/L':>9}")
    for t in sorted(trades, key=lambda t: -t["ratio"]):
        print(f"{t['broker']}#{t['id']:<6}{t['rule']:<18}{t['open']:<18}{t['range']:>7.2f}{t['atr']:>7.2f}"
              f"{t['ratio']:>7.2f}{t['pnl']:>9.2f}")
    total = sum(t["pnl"] for t in trades)
    print(f"\nall trades: n={len(trades)} P/L={total:+.2f}\n")
    print(f"{'multiple':>8}{'blocked':>9}{'blocked P/L':>13}{'blocked wins':>14}{'kept P/L':>10}{'change':>9}")
    for m in MULTIPLES:
        blocked = [t for t in trades if not _spike_confirms(t["range"], t["atr"], m)[0]]
        b_pnl = sum(t["pnl"] for t in blocked)
        wins = sum(1 for t in blocked if t["pnl"] > 0)
        print(f"{m:>8g}{len(blocked):>9}{b_pnl:>13.2f}{wins:>14}{total - b_pnl:>10.2f}{-b_pnl:>+9.2f}")


if __name__ == "__main__":
    main()
