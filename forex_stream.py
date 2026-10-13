"""Live bid/ask ticks for XAU/USD from FOREX.com's streaming feed (CIAPI over Lightstreamer), for forex_watcher.py.

Why: the REST tick history (ForexClient.get_ticks / get_quote) is refreshed by the server in batches about once a minute, so its
"latest tick" is routinely 60-130 s old (measured 13 Oct 2026) and a touch noticed through it is already stale. The stream pushes
~4 ticks per second within ~50 ms of the market.

Connection: https://push.cityindex.com, adapter set STREAMINGALL, user = FOREX_USERNAME, password = the CIAPI session token,
item PRICE.<MarketId> (MERGE), data adapter PRICES, fields MarketId / TickDate / Bid / Offer / Price. Confirmed live 13 Oct 2026
from the dev sandbox (CONNECTED:WS-STREAMING). Read-only: nothing here can place an order.

Ticks arrive on the library's own thread and are put on a queue as {"bid", "ask", "ts"} (ts = the tick's UTC time); the watcher
drains the queue on its own thread. `last_tick_at` (monotonic) is when the latest tick arrived, for the watchdog.
"""

import logging
import queue
import re
import time
from datetime import datetime, timezone

from lightstreamer.client import ClientListener, LightstreamerClient, Subscription, SubscriptionListener

log = logging.getLogger("forex-stream")

PUSH_URL = "https://push.cityindex.com"
ADAPTER_SET = "STREAMINGALL"
DATA_ADAPTER = "PRICES"
FIELDS = ["MarketId", "TickDate", "Bid", "Offer", "Price"]

_DATE_RE = re.compile(r"(-?\d+)")


def parse_tick_date(value: str | None) -> datetime | None:
    """'\\/Date(1791921678032)\\/' (or '/Date(...)/') -> aware UTC datetime."""
    if not value:
        return None
    match = _DATE_RE.search(value)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)


class _Listener(SubscriptionListener):
    def __init__(self, stream: "PriceStream"):
        self.stream = stream

    def onSubscription(self):
        log.info("subscribed to %s", self.stream.item)

    def onSubscriptionError(self, code, message):
        log.error("subscription error %s: %s", code, message)
        self.stream.last_error = f"subscription error {code}: {message}"

    def onItemUpdate(self, update):
        try:
            ts = parse_tick_date(update.getValue("TickDate"))
            bid, ask = update.getValue("Bid"), update.getValue("Offer")
            if ts is None or bid is None or ask is None:
                return
            self.stream._push({"bid": float(bid), "ask": float(ask), "ts": ts})
        except Exception:  # a bad update must never kill the library thread
            log.exception("could not read a stream update")


class _ClientListener(ClientListener):
    def __init__(self, stream: "PriceStream"):
        self.stream = stream

    def onStatusChange(self, status):
        log.info("stream status: %s", status)
        self.stream.status = status

    def onServerError(self, code, message):
        log.error("stream server error %s: %s", code, message)
        self.stream.last_error = f"server error {code}: {message}"


class PriceStream:
    """One streaming connection for one market. start() / stop() / restart(); read ticks from `ticks` (queue.Queue)."""

    def __init__(self, username: str, session_token_getter, market_id: int):
        self.username = username
        self._session_token_getter = session_token_getter  # called at every (re)start so a refreshed session is used
        self.item = f"PRICE.{market_id}"
        self.ticks: "queue.Queue[dict]" = queue.Queue()
        self.status = "NEW"
        self.last_error: str | None = None
        self.last_tick_at = time.monotonic()  # monotonic time the latest tick arrived (start time until the first one)
        self.ticks_received = 0
        self._client: LightstreamerClient | None = None

    def _push(self, tick: dict) -> None:
        self.last_tick_at = time.monotonic()
        self.ticks_received += 1
        self.ticks.put(tick)

    def start(self) -> None:
        self.stop()
        client = LightstreamerClient(PUSH_URL, ADAPTER_SET)
        client.connectionDetails.setUser(self.username)
        client.connectionDetails.setPassword(self._session_token_getter())
        client.addListener(_ClientListener(self))
        subscription = Subscription("MERGE", [self.item], FIELDS)
        subscription.setDataAdapter(DATA_ADAPTER)
        subscription.addListener(_Listener(self))
        client.connect()
        client.subscribe(subscription)
        self._client = client
        self.last_tick_at = time.monotonic()
        log.info("stream started for %s", self.item)

    def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                log.exception("stream disconnect failed")

    def restart(self) -> None:
        self.start()

    def seconds_since_tick(self) -> float:
        return time.monotonic() - self.last_tick_at

    def drain(self, timeout: float = 0.25) -> list[dict]:
        """Waits up to `timeout` s for a tick, then returns every tick queued (oldest first); [] if none arrived."""
        batch: list[dict] = []
        try:
            batch.append(self.ticks.get(timeout=timeout))
        except queue.Empty:
            return batch
        while True:
            try:
                batch.append(self.ticks.get_nowait())
            except queue.Empty:
                return batch
