"""Entry point: polls indicators on a schedule, checks rules, sends alerts."""

import logging  # logging library like print but with levels and timestamps

from apscheduler.schedulers.blocking import BlockingScheduler   #runs in the foreground and blocks execution until the job finishes

from broker import check_broker_trades
from broker_b import check_broker_b_trades
from config import (
    ATR_PERIOD,
    ADX_PERIOD,
    INTRAHOUR_SWING_SEND_TELEGRAM,
    POLL_INTERVAL_MINUTES,
    RSI_PERIOD,
    TECHNICAL_READING_NAMES,
) #goes to config page and gets the value of POLL_INTERVAL_MINUTES
from data_fetcher import fetch_gold_candles, fetch_latest_prices, gold_rsi_adx_atr_from_candles
from forex_broker import check_forex_closes
from market_hours import check_market_hours_alert, is_market_closed
from notifier import send_telegram_message
from routine_trigger import RELEASE_TRIGGER_NAMES, trigger_release_analysis
from rules import (
    check_abs_change_alerts,
    check_intrahour_swing_alerts,
    check_pct_change_alerts,
    check_rsi_alerts,
    check_value_change_alerts,
)
from storage import init_db, save_alert, save_readings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("gold-monitor")


def poll_once() -> None:
    # Checked before the price fetch (and its early-return below) so the weekly open/close
    # notification still fires even if prices are briefly unavailable right at the market boundary.
    check_market_hours_alert()

    if is_market_closed():
        # Friday 5pm ET - Sunday 6pm ET: XAU/USD and every other intraday indicator here is shut, so
        # there's nothing new to fetch -- skip the Twelve Data/yfinance calls entirely rather than
        # burning API budget on ~49 hours/week of unchanged prices. Broker A/B's own Twelve Data calls
        # (candle-scan exit/entry checks) never run either, since they're only reached from below this
        # point -- an open position's unrealized P/L is frozen over the weekend regardless.
        log.info("Market closed for the weekend -- skipping price fetch")
        return

    prices = fetch_latest_prices()
    if not prices:
        log.warning("No prices fetched this cycle")
        return

    # Save first: get_previous_reading() compares against the 2nd-most-recent
    # row, so the current price must already be stored as the most recent one
    # before we check for changes (otherwise a real change gets alerted on
    # twice, once per poll, until the DB catches up).
    save_readings(prices)
    alerts = check_pct_change_alerts(prices)
    alerts += check_abs_change_alerts(prices)
    alerts += check_value_change_alerts(prices)
    # SMA crossover alerts are disabled at the user's request (they repeated every poll in Telegram).
    # One 15-min candle fetch feeds both the RSI alert and the dashboard's RSI/ADX/ATR readings (saved to
    # `readings` under TECHNICAL_READING_NAMES). A failed fetch skips both for this cycle instead of
    # taking down the rest of the poll.
    try:
        candles = fetch_gold_candles()
    except Exception:
        log.exception("Gold candle fetch failed -- skipping RSI alert and technical readings this cycle")
        candles = None
    if candles is not None:
        alerts += check_rsi_alerts(candles)
        technical = dict(zip(
            TECHNICAL_READING_NAMES,
            gold_rsi_adx_atr_from_candles(candles, RSI_PERIOD, ADX_PERIOD, ATR_PERIOD),
        ))
        technical = {name: value for name, value in technical.items() if value is not None}
        if technical:
            try:
                save_readings(technical)
            except Exception:
                log.exception("Could not save technical readings")
    swing_alerts = check_intrahour_swing_alerts(prices)

    log.info("Prices: %s", prices)
    # Always saved (broker.py's entry rules read them from the alerts table); Telegram only if enabled.
    for alert in swing_alerts:
        log.info("ALERT: %s", alert)
        save_alert(alert)
        if INTRAHOUR_SWING_SEND_TELEGRAM:
            send_telegram_message(alert)
    for alert in alerts:
        log.info("ALERT: %s", alert)
        save_alert(alert)
        send_telegram_message(alert)
        if any(alert.startswith(name.upper()) for name in RELEASE_TRIGGER_NAMES):
            try:
                trigger_release_analysis(alert)
            except Exception:
                log.exception("Failed to fire release-analysis Routine for: %s", alert)

    # Runs after alerts are saved: check_broker_trades() looks for its entry signal in the alerts
    # table this same cycle's swing alerts just landed in.
    check_broker_trades(prices)

    # Broker B -- fully independent of Broker A above (own table, own open-trade tracking), trading
    # the latest TA forecast's price zones instead of the alert-consensus signal. Isolated in its own
    # try/except so a Twelve Data/DB hiccup in this newer path can never break the rest of the poll.
    try:
        check_broker_b_trades(prices)
    except Exception:
        log.exception("Broker B check failed")

    # Read-only toward forex.com (never places an order): records Forex-broker positions its TP/SL
    # closed and starts tracking manual ones, alerting on each. Isolated so a forex.com/credentials
    # problem can never break the rest of the poll.
    try:
        check_forex_closes(prices)
    except Exception:
        log.exception("Forex close check failed")


def main() -> None:
    init_db()
    poll_once()  # run once immediately, then on schedule

    scheduler = BlockingScheduler()
    scheduler.add_job(poll_once, "interval", minutes=POLL_INTERVAL_MINUTES)
    log.info("Starting scheduler: polling every %s minute(s)", POLL_INTERVAL_MINUTES)
    scheduler.start()


if __name__ == "__main__":
    main()
