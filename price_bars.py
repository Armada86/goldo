"""One-minute XAU/USD bars for the Broker A/B candle scans, from the FOREX.com demo account with Twelve
Data as the fallback.

Why: Twelve Data's 1-minute candles turned out to disagree with the platform the user actually trades on
-- on 29 Sep 2026 a $10 take-profit (11:47 ET bid high $4,161.71 vs Twelve Data's $4,158.30) and a
$4,150.00 support touch (12:21 ET ask low $4,150.00 vs Twelve Data's $4,150.44) were both real on the
platform yet invisible to the scans, so Broker B never closed / never bought. The scans now use the same
prices a real order would fill and trigger on:

  - a Buy fills on the ASK (entry) and its take-profit / stop-loss trigger on the BID (exit)
  - a Sell fills on the BID (entry) and its exit triggers on the ASK

so callers pick a `side` ("bid"/"ask") -- see broker._find_exit() and broker_b's entry scan.

Any failure (credentials missing, login or request error) falls back to Twelve Data candles, the previous
behavior, so a FOREX.com hiccup never blocks a trade from opening or closing. One ForexClient session is
reused across a poll's calls (the poll is a one-shot process, so it never outlives the session)."""

import logging

from config import GOLD_SPOT_SYMBOL
from data_fetcher import fetch_candles
from forex_client import ForexClient

log = logging.getLogger("gold-monitor")

_client: ForexClient | None = None


def fetch_gold_bars(minutes: int, side: str):
    """The last `minutes` one-minute gold-spot bars priced on `side` ("bid" or "ask"): FOREX.com demo
    prices when available, else Twelve Data candles (single-priced, so `side` is ignored there).
    Same columns as data_fetcher.fetch_candles(): `datetime` (UTC), open/high/low/close, ascending."""
    global _client
    try:
        if _client is None:
            _client = ForexClient()
        return _client.get_bars(minutes, side.upper())
    except Exception as e:
        _client = None  # a failed session/login shouldn't be reused
        log.warning("FOREX.com %s bars unavailable (%s) -- falling back to Twelve Data", side, e)
        return fetch_candles(GOLD_SPOT_SYMBOL, interval="1min", outputsize=minutes)
