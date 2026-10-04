"""One-shot stop loss analysis, run by .github/workflows/stop_loss_analysis.yml when you send "start stop loss
analysis" or "start SLA" on Telegram (the Worker dispatches the workflow, same as "run TA").

Reads every closed Broker A / Broker B trade from Postgres, fetches 1-minute gold bars covering them
(FOREX.com bid/ask where they reach back ~2.8 days, Twelve Data mid for older trades), replays each trade
against a grid of stop-loss / trailing-stop settings with stop_loss_analysis.py, and sends the advice to
Telegram. When the advice is to change, it APPLIES it automatically (since 4 Oct 2026): the new stop loss and trailing stop
are written to the stop_loss_setting / trailing_stop_setting tables, the same rows "make SL" / "make trail" write, so both
brokers use them from their next check (open trades included). It also runs by itself on weekdays at 6:15 AM ET.

    python stop_loss_analysis_job.py             # analyse and send to Telegram
    python stop_loss_analysis_job.py --dry-run   # analyse and print only"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

import broker
import stop_loss_analysis as sla
from notifier import send_telegram_message
from retry import with_retries
from storage import (
    get_closed_trades_for_analysis,
    insert_stop_settings_history,
    set_stop_loss_override,
    set_trailing_stop_override,
)

load_dotenv()

LOOKBACK_DAYS = 45  # how far back trades are considered; older ones are ignored
TD_CHUNK_DAYS = 2  # Twelve Data returns at most 5000 1-minute bars per call
TD_CALL_SPACING_SECONDS = 9  # the free tier allows 8 calls a minute
MAX_ENTRY_GAP_MINUTES = 10  # the first bar after a trade's open must start this close to it, else the price data has a hole
MAX_ENTRY_PRICE_DISTANCE = 10.0  # ... and its price must be this close to the trade's entry, else the data does not match the trade
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY")


@with_retries(attempts=4, delay=20)
def _td_chunk(start: datetime, end: datetime) -> pd.DataFrame:
    response = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": "XAU/USD", "interval": "1min", "outputsize": 5000, "timezone": "UTC",
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"), "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
        },
        headers={"Authorization": f"apikey {TWELVE_DATA_API_KEY}"},  # in a header, so the key never appears in a URL or log line
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise RuntimeError(f"Twelve Data error: {payload}")
    return pd.DataFrame(payload["values"])


def fetch_twelve_data_bars(start: datetime, end: datetime) -> pd.DataFrame | None:
    parts = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=TD_CHUNK_DAYS), end)
        try:
            frame = _td_chunk(cursor, chunk_end)
        except Exception as e:
            print(f"[sla] Twelve Data chunk {cursor:%Y-%m-%d %H:%M} failed: {type(e).__name__}")
        else:
            parts.append(frame)
        cursor = chunk_end
        if cursor < end:
            time.sleep(TD_CALL_SPACING_SECONDS)
    if not parts:
        return None
    df = pd.concat(parts)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)


def fetch_forex_bars() -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """(bid, ask) 1-minute bars for the last ~2.8 days from the FOREX.com demo account, or None."""
    try:
        from forex_client import ForexClient

        client = ForexClient()
        return client.get_bars(4000, "BID"), client.get_bars(4000, "ASK")
    except Exception as e:
        print(f"[sla] FOREX.com bars unavailable ({e}); using Twelve Data for everything")
        return None


def _plausible(trade, bars) -> bool:
    """True if the bars start right after the trade opened and near its entry price."""
    first = bars.iloc[0]
    gap = (first["datetime"] - trade["open_ts"]).total_seconds() / 60
    return gap <= MAX_ENTRY_GAP_MINUTES and abs(float(first["open"]) - trade["entry_price"]) <= MAX_ENTRY_PRICE_DISTANCE


def build_paths(trades, forex, td):
    """TradePath per trade, plus counts of how many used bid/ask, mid, or had no data."""
    paths, n_bid_ask, n_mid, n_skipped = [], 0, 0, 0
    forex_start = forex[0]["datetime"].iloc[0] if forex else None
    for t in trades:
        label = f"{t['broker']} #{t['id']}"
        if forex is not None and t["open_ts"] > forex_start:
            frame, source = (forex[0] if t["trade_type"] == "Buy" else forex[1]), "bid/ask"  # a Buy exits on the bid, a Sell on the ask
        else:
            frame, source = td, "mid"
        bars = None if frame is None else frame[(frame["datetime"] > t["open_ts"]) & (frame["datetime"] <= sla.horizon_end(t["open_ts"]))]
        path = sla.build_path(label, t["broker"], t["trade_type"], t["entry_price"], t["open_ts"], bars, source)
        if path is not None and not _plausible(t, bars):
            path = None  # a hole in the price data (or bars that do not match the trade): better to skip than to replay garbage
        if path is None:
            n_skipped += 1
            continue
        paths.append(path)
        if source == "bid/ask":
            n_bid_ask += 1
        else:
            n_mid += 1
    return paths, n_bid_ask, n_mid, n_skipped


def run(dry_run: bool = False) -> None:
    now = datetime.now(timezone.utc)
    trades = get_closed_trades_for_analysis(now - timedelta(days=LOOKBACK_DAYS))
    if len(trades) < sla.MIN_TRADES:
        message = (f"{sla.SLA_PREFIX}STOP LOSS ANALYSIS: only {len(trades)} closed trades so far, "
                   f"need at least {sla.MIN_TRADES} for advice that means anything.")
        print(message) if dry_run else send_telegram_message(message)
        return

    first_open = trades[0]["open_ts"]
    forex = fetch_forex_bars()
    td = fetch_twelve_data_bars(first_open - timedelta(minutes=5), now)
    paths, n_bid_ask, n_mid, n_skipped = build_paths(trades, forex, td)
    if len(paths) < sla.MIN_TRADES:
        message = f"{sla.SLA_PREFIX}STOP LOSS ANALYSIS: price data was available for only {len(paths)} trades, not enough for advice."
        print(message) if dry_run else send_telegram_message(message)
        return

    activation, distance = broker.trailing_stop_params()
    result = sla.analyse(paths, broker.stop_loss_threshold(), activation, distance)
    applied = None  # None: nothing to apply, or a dry run
    if result["change"] and not dry_run:
        rec = result["recommended"]
        try:
            set_stop_loss_override(rec["stop_loss"])
            set_trailing_stop_override(rec["activation"], rec["distance"])
            applied = True
            try:  # display-only log for the dashboard; a failure here must not undo or hide the applied settings
                insert_stop_settings_history("SLA", rec["stop_loss"], rec["activation"], rec["distance"])
            except Exception as e:
                print(f"[sla] could not log the stop settings history: {e}")
        except Exception as e:
            print(f"[sla] could not write the new stop settings: {e}")
            applied = False
    report = sla.format_report(result, first_open, now, n_bid_ask, n_mid, n_skipped, applied)
    print(report)
    if not dry_run:
        send_telegram_message(report)


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
