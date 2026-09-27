"""One-shot poll, for running as a scheduled GitHub Actions job instead of
main.py's always-on BlockingScheduler (no server needed)."""

import logging

from main import poll_once
from market_hours import check_market_hours_alert, is_market_closed, is_overnight_polling_pause
from storage import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("gold-monitor")

if __name__ == "__main__":
    # Checked here, before any Postgres connection, rather than relying on poll_once()'s own internal
    # checks -- init_db()'s CREATE TABLE IF NOT EXISTS is itself a DB connection that used to run (and
    # wake Neon compute) every single cloud poll regardless of market/pause status, since poll_once()
    # only ever skipped the price fetch, never the init_db() call above it. Skipping the whole DB touch
    # here is what actually lets compute scale to zero during these windows.
    if is_market_closed() or is_overnight_polling_pause():
        # The weekly close/open notification (Friday 5pm / Sunday 6pm ET) falls inside these windows,
        # so it's checked directly here -- it needs no DB, only Telegram -- since poll_once() (which
        # normally calls it) is skipped entirely below.
        check_market_hours_alert()
        log.info("Market closed or in overnight polling pause -- skipping DB connection entirely")
    else:
        init_db()
        poll_once()
