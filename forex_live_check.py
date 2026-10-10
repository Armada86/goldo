"""Verifies, on the FOREX.com DEMO account, the order behaviours forex_watcher.py's live mode depends on, using the production size (forex_client.TRADE_QUANTITY, 1 oz) of XAU/USD.

Run once the market is open (Sunday 6pm ET onward) and BEFORE switching Broker F to live. Sequence (each step prints the raw response):
  1. buy 1 oz at market                          -> fill price, order id
  2. attach a stop-only order 20 below           -> the position shows StopOrder.TriggerPrice
  3. move that stop up to 15 below               -> the SAME stop order now shows the new trigger (amend works)
  4. cancel the stop                             -> the position stays open with no StopOrder
  5. attach a stop again, then close with the opposite market order WITHOUT cancelling it first (only with --test-close-with-stop)
                                                 -> shows whether the position nets to flat and whether the stop disappears or a
                                                    reverse position appears
  6. always: flatten whatever XAU/USD position is left, then print /order/tradehistory for the closing trade.
Exits non-zero and sends a Telegram summary on any failed expectation. Refuses to run without --yes, when the market is closed, or
when the account already has an XAU/USD position. Orders are locked to XAU/USD by forex_client; nothing here can trade another market.
"""

import argparse
import sys
import time

from forex_client import TRADABLE_MARKET_ID, TRADE_QUANTITY, ForexClient
from market_hours import is_market_closed
from notifier import send_telegram_message

QTY = TRADE_QUANTITY  # the size Broker F really trades (1 oz), so the test exercises exactly what production will
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return ok


def positions(client: ForexClient) -> list[dict]:
    return [p for p in client.get_open_positions() if p.get("MarketId") == TRADABLE_MARKET_ID]


def flatten(client: ForexClient) -> None:
    for p in positions(client):
        print(f"flattening leftover position {p.get('OrderId')} ({p.get('Direction')} {p.get('Quantity')})")
        try:
            client.cancel_order(p["StopOrder"]["OrderId"]) if p.get("StopOrder") else None
        except Exception as e:
            print("  cancel of its stop failed:", e)
        client.close_position(p["Direction"], quantity=float(p["Quantity"]))
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="confirm that 1 oz demo orders may be placed")
    parser.add_argument("--test-close-with-stop", action="store_true", help="also test an opposite order while a stop is attached")
    args = parser.parse_args()
    if not args.yes:
        print("Refusing to place orders without --yes (1 oz XAU/USD on the demo account).")
        return 2
    if is_market_closed():
        print("Market is closed (Fri 5pm - Sun 6pm ET) -- orders cannot fill. Run again after Sunday 6pm ET.")
        return 2
    client = ForexClient()
    opened = None
    if positions(client):
        print("The account already has an XAU/USD position -- close it first.")
        return 2
    try:
        opened = client.place_market_order("buy", quantity=QTY)
        oid, fill = opened["order_id"], opened["fill_price"]
        check(f"market buy {QTY:g} oz", True, f"order {oid} filled @ {fill}")
        time.sleep(1)
        placed = client.attach_stop(oid, "buy", fill - 20, quantity=QTY)
        pos = next((p for p in positions(client) if p["OrderId"] == oid), None)
        stop = (pos or {}).get("StopOrder")
        check("attach stop-only order", bool(stop) and abs(float(stop["TriggerPrice"]) - (fill - 20)) < 0.011,
              f"stop order {placed['stop_order_id']} @ {placed['stop_price']}; position shows {stop}")
        if stop:
            client.move_stop(oid, stop["OrderId"], "buy", fill - 15, quantity=QTY)
            pos = next((p for p in positions(client) if p["OrderId"] == oid), None)
            moved = (pos or {}).get("StopOrder")
            check("move the stop in place", bool(moved) and moved["OrderId"] == stop["OrderId"] and abs(float(moved["TriggerPrice"]) - (fill - 15)) < 0.011,
                  f"now {moved}")
            client.cancel_order((moved or stop)["OrderId"])
            time.sleep(1)
            pos = next((p for p in positions(client) if p["OrderId"] == oid), None)
            check("cancel the stop, position stays open", pos is not None and not pos.get("StopOrder"), f"position now {pos}")
        if args.test_close_with_stop:
            if next((p for p in positions(client) if p["OrderId"] == oid), None):
                client.attach_stop(oid, "buy", fill - 20, quantity=QTY)
                client.close_position("buy", quantity=QTY)
                time.sleep(2)
                left = positions(client)
                check("opposite order with a stop attached nets to flat", not left, f"positions after: {left}")
                print("  active stop orders can be checked on the platform; any reverse position is flattened below")
    except Exception as e:
        check("unexpected error", False, repr(e))
    finally:
        flatten(client)
        time.sleep(1)
        if opened is not None:
            try:
                print("closing trade for the first position:", client.find_closing_trade(int(opened["order_id"])))
            except Exception as e:
                print("trade history lookup failed:", e)
        check("account flat at the end", not positions(client))
    ok = all(r[1] for r in results)
    summary = "FOREX LIVE CHECK (demo, 1 oz): " + ("ALL PASSED" if ok else "FAILURES") + "\n" + "\n".join(
        f"{'✅' if r[1] else '❌'} {r[0]}" for r in results
    )
    send_telegram_message(summary)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
