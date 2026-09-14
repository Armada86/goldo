"""Entry point: polls indicators on a schedule, checks rules, sends alerts."""

import logging  # logging library like print but with levels and timestamps

from apscheduler.schedulers.blocking import BlockingScheduler   #runs in the foreground and blocks execution until the job finishes

from config import POLL_INTERVAL_MINUTES #goes to config page and gets the value of POLL_INTERVAL_MINUTES
from data_fetcher import fetch_latest_prices
from notifier import send_telegram_message
from rules import check_pct_change_alerts, check_sma_crossover
from storage import init_db, save_alert, save_readings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("gold-monitor")


def poll_once() -> None:
    prices = fetch_latest_prices()
    if not prices:
        log.warning("No prices fetched this cycle")
        return

    alerts = check_pct_change_alerts(prices)
    save_readings(prices)
    alerts += check_sma_crossover()

    log.info("Prices: %s", prices)
    for alert in alerts:
        log.info("ALERT: %s", alert)
        save_alert(alert)
        send_telegram_message(alert)


def main() -> None:
    init_db()
    poll_once()  # run once immediately, then on schedule

    scheduler = BlockingScheduler()
    scheduler.add_job(poll_once, "interval", minutes=POLL_INTERVAL_MINUTES)
    log.info("Starting scheduler: polling every %s minute(s)", POLL_INTERVAL_MINUTES)
    scheduler.start()


if __name__ == "__main__":
    main()
