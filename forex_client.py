"""REST client for FOREX.com's demo trading API (GAIN Capital's shared "TradingAPI" infrastructure,
also used by its sister brand City Index) -- session login, instrument lookup, and market order
placement/flattening.

CAVEAT: forex.com's official API reference (docs.labs.gaincapital.com) is gated behind a login this
session doesn't have, so the endpoint paths and JSON field names below are reconstructed from
independent third-party client implementations (gcapi-python, the `forexcom` PyPI package, and an
archived docs.labs.cityindex.com HTTP-services page) rather than the official spec. Still unconfirmed:
  - whether this account nets an opposite-direction order into a closed position (assumed in
    close_position() below) or requires a dedicated close/cancel call referencing the original order.
    /order/openpositions reports PositionMethodId 1 for this account, likely netting, but no close has
    been exercised yet.

Confirmed against the live demo account (connectivity check, 2026-09-22): login, the account lookup,
/cfd/markets and /market/{id}/tickhistory all work as written. Spot gold is market "XAU/USD" (MarketId
401153870, min size 0.1, max 1000). /market/search turned out to ignore its MarketName filter entirely and
return the whole catalog in MarketId order (so the first result was an unrelated stock), which is why the
lookup below uses /cfd/markets and requires an exact name match rather than taking the first hit.

Confirmed by a real test order (buy 1 XAU/USD, 2026-09-22 18:25 ET, OrderId 1032567283): the
/order/newtradeorder request fields below are accepted as-is. The response's top-level has OrderId/Status/
StatusReason but NO Price -- the executed price is only in Orders[] (the entry whose OrderId matches), e.g.
{"Status": 1, "OrderId": 1032567283, "Orders": [{"OrderId": 1032567283, "Price": 4364.4, "Quantity": 1.0,
"Status": 3, "CommissionCharge": -0.4, ...}], ...}.

Nothing in this project imports or calls this module automatically -- it only talks to forex.com when
something explicitly constructs a ForexClient. See forex_broker.py, which is itself not wired into the
automatic poll loop either.
"""

import os
import re
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from retry import with_retries

load_dotenv()

USERNAME = os.environ.get("FOREX_USERNAME")
PASSWORD = os.environ.get("FOREX_PASSWORD")
APP_KEY = os.environ.get("FOREX_APP_KEY")

# GAIN Capital's shared TradingAPI host -- the same REST infrastructure forex.com and City Index both
# run on, distinguished by which AppKey/account logs in. Override via env var if forex.com's demo
# environment turns out to need a different host -- verify against the docs portal before trusting this.
BASE_URL = os.environ.get("FOREX_API_BASE_URL", "https://ciapi.cityindex.com/TradingAPI")

# The ONLY instrument this client will ever place an order on: spot gold, XAU/USD. Both the name and the
# market ID are hardcoded (deliberately not env-overridable) and both must match before an order is sent
# -- see _assert_tradable_market(). The name is matched exactly (not as a prefix/substring) against
# /cfd/markets results, since that search is a loose substring match that also returns "Gold - Cash",
# gold futures CFDs, gold ETFs, and mining stocks. Changing what this client trades is a code change here,
# on purpose, not a config tweak.
TRADABLE_MARKET_NAME = "XAU/USD"
TRADABLE_MARKET_ID = 401153870

# Default market for read-only price quotes (get_price()); orders ignore this and always use the above.
MARKET_NAME = TRADABLE_MARKET_NAME

# Trade size, troy oz -- matches broker.py's paper-trading size so P/L stays directly comparable.
# Confirm this is a valid tradable quantity for the instrument before ever placing a real order.
TRADE_QUANTITY = float(os.environ.get("FOREX_TRADE_QUANTITY", "1"))


# How many times (1s apart) attach_take_profit_and_stop_loss() looks for a just-opened position before
# giving up -- the live test order took under 3s to show up in /order/openpositions.
POSITION_LOOKUP_ATTEMPTS = 5


class ForexClientError(Exception):
    """Raised for a missing-credentials setup or any unexpected/unsuccessful forex.com API response."""


class ForexOrderUncertainError(ForexClientError):
    """An order request was sent but its outcome is unknown (timeout, dropped connection, 5xx, or an
    unreadable response) -- forex.com may or may not have filled it. Never retry on this: check the
    account's open positions (ForexClient.get_open_positions()) first, or a retry could double the
    position."""


def _fill_price(data: dict, offer_price: float) -> float:
    """Executed price from a /order/newtradeorder response -- it lives on the matching Orders[] entry,
    not the top level (see module docstring). The order was placed either way (the caller already saw an
    OrderId), so a missing price falls back to the pre-trade quote with a loud log line rather than
    raising, which would leave a real position unrecorded."""
    for order in data.get("Orders") or []:
        if order.get("OrderId") == data["OrderId"] and order.get("Price") is not None:
            return float(order["Price"])
    print(
        f"[forex_client] WARNING: no executed price in order {data['OrderId']} response; "
        f"using pre-trade quote {offer_price} as fill_price"
    )
    return float(offer_price)


def _parse_ms_date(value: str | None) -> datetime | None:
    """Parses GAIN's "/Date(1790115901267)/" (ms since the epoch, UTC) timestamps."""
    match = re.fullmatch(r"/Date\((-?\d+)\)/", value or "")
    return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc) if match else None


class ForexClient:
    """One authenticated session against the forex.com demo account. Construct a fresh one per use
    (e.g. once per manual run of forex_broker.py) rather than holding it open across polls -- there's
    no confirmation forex.com sessions survive the 5-minute gap between polls, and logging in fresh
    each time keeps this client as stateless as the rest of the project's cloud deployment."""

    def __init__(self):
        if not (USERNAME and PASSWORD and APP_KEY):
            raise ForexClientError(
                "FOREX_USERNAME/FOREX_PASSWORD/FOREX_APP_KEY must all be set (see .env.example) -- "
                "forex_client.py never runs without explicit credentials."
            )
        self._session_token: str | None = None
        self._trading_account_id: int | None = None
        self._client_account_id: int | None = None
        self._market_ids: dict[str, int] = {}
        self._login()

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "UserName": USERNAME,
            "Session": self._session_token,
        }

    @with_retries()
    def _login(self) -> None:
        response = requests.post(
            f"{BASE_URL}/session",
            json={"UserName": USERNAME, "Password": PASSWORD, "AppKey": APP_KEY},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        session_token = data.get("Session")
        if not session_token:
            raise ForexClientError(f"Login succeeded but no Session token in response: {data}")
        self._session_token = session_token

        account_response = requests.get(
            f"{BASE_URL}/UserAccount/ClientAndTradingAccount",
            headers=self._headers(),
            timeout=10,
        )
        account_response.raise_for_status()
        account_data = account_response.json()
        self._client_account_id = account_data.get("ClientAccountId")
        trading_accounts = account_data.get("TradingAccounts") or []
        if not trading_accounts:
            raise ForexClientError(f"No trading accounts on this login: {account_data}")
        self._trading_account_id = trading_accounts[0].get("TradingAccountId")

    @with_retries()
    def _market_id_for(self, market_name: str) -> int:
        if market_name in self._market_ids:
            return self._market_ids[market_name]
        response = requests.get(
            f"{BASE_URL}/cfd/markets",
            headers=self._headers(),
            params={
                "MarketName": market_name,
                "MaxResults": 100,
                "ClientAccountId": self._client_account_id,
            },
            timeout=10,
        )
        response.raise_for_status()
        markets = response.json().get("Markets") or []
        matches = [m for m in markets if m.get("Name") == market_name]
        if len(matches) != 1:
            names = [m.get("Name") for m in markets]
            raise ForexClientError(
                f"Expected exactly one market named {market_name!r}, found {len(matches)} "
                f"(search returned: {names})"
            )
        self._market_ids[market_name] = matches[0]["MarketId"]
        return self._market_ids[market_name]

    @with_retries()
    def get_price(self, market_name: str | None = None) -> float:
        """Latest mid price for `market_name` (default MARKET_NAME), used as the OfferPrice quoted on
        a market order."""
        market_name = market_name or MARKET_NAME
        market_id = self._market_id_for(market_name)
        response = requests.get(
            f"{BASE_URL}/market/{market_id}/tickhistory",
            headers=self._headers(),
            params={"PriceTicks": 1, "priceType": "MID"},
            timeout=10,
        )
        response.raise_for_status()
        ticks = response.json().get("PriceTicks") or []
        if not ticks:
            raise ForexClientError(f"No price ticks returned for market {market_id}")
        return float(ticks[-1]["Price"])

    def _assert_tradable_market(self) -> None:
        """Refuses to go any further unless forex.com's own lookup of TRADABLE_MARKET_NAME resolves to
        exactly TRADABLE_MARKET_ID. Guards against the name ever matching a different market (as
        /market/search once silently did) or the account's market ID changing underneath us -- either
        way, no order is sent until a human re-checks and updates the constants."""
        resolved_id = self._market_id_for(TRADABLE_MARKET_NAME)
        if resolved_id != TRADABLE_MARKET_ID:
            raise ForexClientError(
                f"Refusing to trade: {TRADABLE_MARKET_NAME!r} resolved to market ID {resolved_id}, "
                f"expected {TRADABLE_MARKET_ID}. No order was placed."
            )

    def place_market_order(self, direction: str, *, quantity: float | None = None) -> dict:
        """Places a market order on XAU/USD (TRADABLE_MARKET_ID) -- the only market this client ever
        trades; there is intentionally no market parameter. `direction` is "buy" or "sell". Returns
        {"order_id", "fill_price", "status"}. On a netting account, an opposite-direction order against
        an existing open position closes it rather than opening a new one -- see close_position()."""
        direction = direction.lower()
        if direction not in ("buy", "sell"):
            raise ForexClientError(f"direction must be 'buy' or 'sell', got {direction!r}")
        quantity = TRADE_QUANTITY if quantity is None else quantity
        self._assert_tradable_market()
        # Everything before the POST (market guard, price quote) is read-only and retried as usual.
        offer_price = self.get_price(TRADABLE_MARKET_NAME)
        return self._send_order(direction, quantity, offer_price)

    def _send_order(self, direction: str, quantity: float, offer_price: float) -> dict:
        data = self._post_once(
            "order/newtradeorder",
            {
                "Direction": direction,
                "MarketId": TRADABLE_MARKET_ID,
                "MarketName": TRADABLE_MARKET_NAME,
                "Quantity": quantity,
                "OfferPrice": offer_price,
                "TradingAccountId": self._trading_account_id,
                "ClientAccountId": self._client_account_id,
            },
            f"{direction} {quantity} {TRADABLE_MARKET_NAME} order",
        )
        if data.get("OrderId") is None:
            raise ForexClientError(f"Order rejected or unrecognized response: {data}")
        return {
            "order_id": data["OrderId"],
            "fill_price": _fill_price(data, offer_price),
            "status": data.get("StatusReason") or data.get("Status"),
        }

    def _post_once(self, path: str, body: dict, what: str) -> dict:
        """POSTs an order-changing request exactly once -- deliberately NOT wrapped in with_retries(),
        unlike every other external call in this project. A request that fails after reaching
        forex.com may still have been applied, so retrying it could open a second position or attach
        a second set of stop/limit orders. Failures split two ways: a 4xx means nothing was applied
        (ForexClientError); anything where the outcome is unknown raises ForexOrderUncertainError,
        carrying the account's open positions if they could be read, for a human to reconcile."""
        try:
            response = requests.post(f"{BASE_URL}/{path}", headers=self._headers(), json=body, timeout=10)
        except requests.RequestException as e:
            self._raise_uncertain(what, f"request failed: {e}")
        if 400 <= response.status_code < 500:
            raise ForexClientError(
                f"{what} rejected (HTTP {response.status_code}), not applied: {response.text[:500]}"
            )
        if response.status_code >= 500:
            self._raise_uncertain(what, f"HTTP {response.status_code}: {response.text[:500]}")
        try:
            return response.json()
        except ValueError:
            self._raise_uncertain(what, f"unreadable response: {response.text[:500]}")

    def _raise_uncertain(self, what: str, reason: str) -> None:
        try:
            positions = f"open positions now: {self.get_open_positions()}"
        except Exception as e:
            positions = f"open positions could not be read ({e})"
        raise ForexOrderUncertainError(
            f"{what} outcome unknown ({reason}) -- it may or may not have been applied. NOT retried. "
            f"Check the demo account before acting; {positions}"
        )

    def attach_take_profit_and_stop_loss(
        self, order_id, direction: str, entry_price: float, distance: float, *, quantity: float | None = None
    ) -> dict:
        """Attaches a take-profit (limit) and stop-loss (stop) order, each `distance` dollars from
        `entry_price`, to the already-open position `order_id` -- visible on the forex.com platform as
        that position's TP/SL. `direction` is the POSITION's direction ("buy"/"sell"); both exit orders
        go the opposite way. forex.com links the two as one-cancels-the-other, good-till-cancelled, so
        whichever triggers first closes the position and cancels the other.

        Same XAU/USD lock as orders (the position itself must be on TRADABLE_MARKET_ID) and the same
        single-attempt POST. Confirmed live (2026-09-22, position 1032567283): /order/updatetradeorder
        with IfDone [{Stop, Limit}] on the position's OrderId modifies it in place, no new trade.
        Returns {"stop_order_id", "stop_price", "limit_order_id", "limit_price"}."""
        direction = direction.lower()
        if direction not in ("buy", "sell"):
            raise ForexClientError(f"direction must be 'buy' or 'sell', got {direction!r}")
        quantity = TRADE_QUANTITY if quantity is None else quantity
        self._assert_tradable_market()
        # A just-filled position can take a moment to appear in /order/openpositions (read-only, so
        # polling it is safe).
        position = None
        for attempt in range(POSITION_LOOKUP_ATTEMPTS):
            position = next((p for p in self.get_open_positions() if p.get("OrderId") == order_id), None)
            if position is not None or attempt == POSITION_LOOKUP_ATTEMPTS - 1:
                break
            time.sleep(1)
        if position is None:
            raise ForexClientError(f"No open position with OrderId {order_id} -- nothing to attach TP/SL to")
        if position.get("MarketId") != TRADABLE_MARKET_ID:
            raise ForexClientError(
                f"Refusing: position {order_id} is on market {position.get('MarketId')}, not "
                f"{TRADABLE_MARKET_NAME} ({TRADABLE_MARKET_ID}). Nothing was attached."
            )
        sign = 1 if direction == "buy" else -1
        limit_price = round(entry_price + sign * distance, 2)
        stop_price = round(entry_price - sign * distance, 2)
        exit_direction = "sell" if direction == "buy" else "buy"
        price = self.get_price(TRADABLE_MARKET_NAME)

        def leg(trigger_price: float) -> dict:
            return {
                "TriggerPrice": trigger_price,
                "Direction": exit_direction,
                "Quantity": quantity,
                "Guaranteed": False,
                "Applicability": "GTC",
                "OrderId": 0,
            }

        data = self._post_once(
            "order/updatetradeorder",
            {
                "OrderId": order_id,
                "MarketId": TRADABLE_MARKET_ID,
                "Currency": "USD",
                "AutoRollover": False,
                "Direction": direction,
                "Quantity": quantity,
                "BidPrice": price,
                "OfferPrice": price,
                "TradingAccountId": self._trading_account_id,
                "IfDone": [{"Stop": leg(stop_price), "Limit": leg(limit_price)}],
            },
            f"TP {limit_price}/SL {stop_price} on position {order_id}",
        )
        # OrderTypeId 2 = stop, 3 = limit (as returned for position 1032567283).
        by_type = {o.get("OrderTypeId"): o for o in data.get("Orders") or [] if o.get("OrderId") != order_id}
        if 2 not in by_type or 3 not in by_type:
            raise ForexOrderUncertainError(
                f"TP/SL request on position {order_id} returned no stop/limit orders -- check the demo "
                f"account; response: {data}"
            )
        return {
            "stop_order_id": by_type[2]["OrderId"],
            "stop_price": float(by_type[2]["TriggerPrice"]),
            "limit_order_id": by_type[3]["OrderId"],
            "limit_price": float(by_type[3]["TriggerPrice"]),
        }

    @with_retries()
    def find_closing_trade(self, order_id) -> dict | None:
        """The trade that closed position `order_id` (e.g. its TP/SL triggering), from /order/tradehistory
        -- the history entry, other than the opening trade itself, whose OpeningOrderIds includes
        `order_id`. Returns {"order_id", "price", "closed_at"} or None if none is listed. NOTE: the shape
        of a closing entry hasn't been observed live yet (only an opening one has), so callers must
        handle None with a fallback."""
        response = requests.get(
            f"{BASE_URL}/order/tradehistory",
            headers=self._headers(),
            params={"TradingAccountId": self._trading_account_id, "maxResults": 50},
            timeout=10,
        )
        response.raise_for_status()
        for trade in response.json().get("TradeHistory") or []:
            if trade.get("OrderId") != order_id and order_id in (trade.get("OpeningOrderIds") or []):
                return {
                    "order_id": trade["OrderId"],
                    "price": float(trade["Price"]),
                    "closed_at": _parse_ms_date(trade.get("ExecutedDateTimeUtc")),
                }
        return None

    @with_retries()
    def get_open_positions(self) -> list[dict]:
        """Raw open positions on this trading account (read-only), from {"OpenPositions": [...]}.
        Confirmed live; each entry includes OrderId, MarketId, MarketName, Direction ("buy"/"sell"),
        Quantity, Price (entry), Status, and PositionMethodId, among others."""
        response = requests.get(
            f"{BASE_URL}/order/openpositions",
            headers=self._headers(),
            params={"TradingAccountId": self._trading_account_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("OpenPositions") or []

    def close_position(self, open_direction: str, *, quantity: float | None = None) -> dict:
        """Flattens an open position by placing the opposite-direction order for the same quantity --
        the standard close mechanism on a netting CFD/FX account. Confirm this account is in netting
        (not hedging) mode before relying on this; a hedging account would need a dedicated close call
        referencing the original position/order ID instead."""
        opposite = "sell" if open_direction.lower() == "buy" else "buy"
        return self.place_market_order(opposite, quantity=quantity)


if __name__ == "__main__":
    # Connectivity check only -- logs in, confirms the account/market lookups work, and fetches one
    # price quote. Deliberately never calls place_market_order()/close_position(), so running this
    # script cannot place a trade under any circumstances.
    print(f"[forex_client] Logging in to {BASE_URL} ...")
    client = ForexClient()
    print(f"[forex_client] Logged in. trading_account_id={client._trading_account_id} "
          f"client_account_id={client._client_account_id}")
    client._assert_tradable_market()
    price = client.get_price()
    print(f"[forex_client] {TRADABLE_MARKET_NAME} (market_id={TRADABLE_MARKET_ID}, trading lock "
          f"verified) price: {price}")
    print(f"[forex_client] Open positions: {client.get_open_positions()}")
    print("[forex_client] Connectivity check passed -- no order was placed.")
