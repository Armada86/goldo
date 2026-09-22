"""REST client for FOREX.com's demo trading API (GAIN Capital's shared "TradingAPI" infrastructure,
also used by its sister brand City Index) -- session login, instrument lookup, and market order
placement/flattening.

CAVEAT: forex.com's official API reference (docs.labs.gaincapital.com) is gated behind a login this
session doesn't have, so the endpoint paths and JSON field names below are reconstructed from
independent third-party client implementations (gcapi-python, the `forexcom` PyPI package, and an
archived docs.labs.cityindex.com HTTP-services page) rather than the official spec. Before this module
is ever used against the real demo account, log in to the docs portal (you have the account for it) and
confirm:
  - the exact request/response field names for /session, market search, and /order/newtradeorder
  - whether this account nets an opposite-direction order into a closed position (assumed in
    close_position() below) or requires a dedicated close/cancel call referencing the original order
  - the exact market name and minimum tradable quantity for spot gold (MARKET_NAME/TRADE_QUANTITY below
    are guesses, not confirmed values)

Nothing in this project imports or calls this module automatically -- it only talks to forex.com when
something explicitly constructs a ForexClient. See forex_broker.py, which is itself not wired into the
automatic poll loop either.
"""

import os

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

# Instrument this client trades -- must match forex.com's exact market name for spot gold; confirm the
# precise string (likely "Spot Gold" or "Gold") via a market search call before first use.
MARKET_NAME = os.environ.get("FOREX_MARKET_NAME", "Spot Gold")

# Trade size, troy oz -- matches broker.py's paper-trading size so P/L stays directly comparable.
# Confirm this is a valid tradable quantity for the instrument before ever placing a real order.
TRADE_QUANTITY = float(os.environ.get("FOREX_TRADE_QUANTITY", "1"))


class ForexClientError(Exception):
    """Raised for a missing-credentials setup or any unexpected/unsuccessful forex.com API response."""


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
        self._market_id: int | None = None
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
        if self._market_id is not None:
            return self._market_id
        response = requests.get(
            f"{BASE_URL}/market/search",
            headers=self._headers(),
            params={"SearchByMarketName": "true", "MarketName": market_name},
            timeout=10,
        )
        response.raise_for_status()
        markets = response.json().get("Markets") or []
        if not markets:
            raise ForexClientError(f"No market found for name {market_name!r}")
        self._market_id = markets[0]["MarketId"]
        return self._market_id

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

    @with_retries()
    def place_market_order(
        self, direction: str, market_name: str | None = None, quantity: float | None = None
    ) -> dict:
        """Places a market order (`direction` is "buy" or "sell") and returns
        {"order_id", "fill_price", "status"}. On a netting account, an opposite-direction order against
        an existing open position closes it rather than opening a new one -- see close_position()."""
        market_name = market_name or MARKET_NAME
        quantity = TRADE_QUANTITY if quantity is None else quantity
        market_id = self._market_id_for(market_name)
        offer_price = self.get_price(market_name)
        response = requests.post(
            f"{BASE_URL}/order/newtradeorder",
            headers=self._headers(),
            json={
                "Direction": direction,
                "MarketId": market_id,
                "MarketName": market_name,
                "Quantity": quantity,
                "OfferPrice": offer_price,
                "TradingAccountId": self._trading_account_id,
                "ClientAccountId": self._client_account_id,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("OrderId") is None:
            raise ForexClientError(f"Order rejected or unrecognized response: {data}")
        return {
            "order_id": data["OrderId"],
            "fill_price": float(data.get("Price", offer_price)),
            "status": data.get("StatusReason") or data.get("Status"),
        }

    def close_position(
        self, open_direction: str, market_name: str | None = None, quantity: float | None = None
    ) -> dict:
        """Flattens an open position by placing the opposite-direction order for the same quantity --
        the standard close mechanism on a netting CFD/FX account. Confirm this account is in netting
        (not hedging) mode before relying on this; a hedging account would need a dedicated close call
        referencing the original position/order ID instead."""
        opposite = "sell" if open_direction.lower() == "buy" else "buy"
        return self.place_market_order(opposite, market_name, quantity)


if __name__ == "__main__":
    # Connectivity check only -- logs in, confirms the account/market lookups work, and fetches one
    # price quote. Deliberately never calls place_market_order()/close_position(), so running this
    # script cannot place a trade under any circumstances.
    print(f"[forex_client] Logging in to {BASE_URL} ...")
    client = ForexClient()
    print(f"[forex_client] Logged in. trading_account_id={client._trading_account_id} "
          f"client_account_id={client._client_account_id}")
    price = client.get_price()
    print(f"[forex_client] {MARKET_NAME} price: {price}")
    print("[forex_client] Connectivity check passed -- no order was placed.")
