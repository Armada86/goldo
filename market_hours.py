"""Weekly market open/close Telegram notifications.

XAU/USD (and the other intraday indicators -- gld, dxy, us10y) trade on the standard OTC FX-style
weekly schedule: a daily settlement break 5:00-6:00 PM Eastern, and on the week's final session that
break simply doesn't reopen until the following week -- so the market closes Friday 5:00 PM and reopens
Sunday 6:00 PM, both Eastern Time. Toronto and New York share the same UTC offset and DST transition
dates year-round, so America/New_York (the zone the rest of this project already uses -- see
dashboard.py's DISPLAY_TZ, frequency_check_job.py) is equivalent to "Toronto time" here.

Checked every poll (5-minute cadence) rather than a separate scheduled workflow/cron-job.org job, so no
extra external scheduling setup is needed -- the existing poll.yml trigger already visits every 5-minute
boundary, including the one each side of these two weekly instants.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from notifier import send_telegram_message

MARKET_TZ = ZoneInfo("America/New_York")

# datetime.weekday(): Monday=0 ... Sunday=6
MARKET_CLOSE_WEEKDAY = 4  # Friday
MARKET_CLOSE_HOUR = 17
MARKET_OPEN_WEEKDAY = 6  # Sunday
MARKET_OPEN_HOUR = 18

# Deliberate cost-saving pause, every day, NOT a real market-closed fact -- gold spot actually trades
# nearly 24/5 and this window covers real Asian/London session activity. Added to cap Neon Postgres
# compute-hour usage: poll_job.py checks this before making any DB connection at all (see its own
# comment), so the ~15 daily cloud polls in this window don't each wake compute from empty --
# unlike is_market_closed() below, which only ever skipped the price fetch, not the init_db() call
# that ran (and woke compute) every single poll regardless.
OVERNIGHT_PAUSE_START_HOUR = 17  # 5:00 PM ET
OVERNIGHT_PAUSE_END_HOUR = 7  # 7:00 AM ET


def check_market_hours_alert() -> None:
    """Sends a one-off Telegram message on the first poll at/after Friday 5:00 PM (market close) and
    Sunday 6:00 PM (market open), Eastern time. Relies on the 5-minute poll cadence landing on :00/:05/
    etc. boundaries -- the `minute < 5` window is only true for a single poll each week, so this fires
    exactly once per close/open rather than for the rest of that 5-minute window."""
    now = datetime.now(MARKET_TZ)
    if now.weekday() == MARKET_CLOSE_WEEKDAY and now.hour == MARKET_CLOSE_HOUR and now.minute < 5:
        send_telegram_message("Market closed for the week (Friday 5:00 PM ET).")
    elif now.weekday() == MARKET_OPEN_WEEKDAY and now.hour == MARKET_OPEN_HOUR and now.minute < 5:
        send_telegram_message("Market open for the week (Sunday 6:00 PM ET).")


def is_market_closed() -> bool:
    """True during the same weekly dead window check_market_hours_alert() announces -- Friday 5:00 PM
    ET through Sunday 6:00 PM ET -- when XAU/USD and every other intraday indicator this project tracks
    (gld/iau/gldm/gdx/gdxj/ring/dxy/us10y) simply isn't trading. Lets poll_once() skip fetching prices
    (Twelve Data gold spot + RSI candles, the yfinance indicators, and the Broker A/B candle-scan calls
    that ride along with them) for that whole ~49-hour stretch each week -- nothing moves, so there's no
    signal being missed, only API calls saved. Hour-boundary comparisons deliberately match
    check_market_hours_alert()'s own close/open instants exactly (closed from the Friday-close poll
    itself, open again from the Sunday-open poll itself), not the finer `minute < 5` firing window that
    exists only to make the one-off notification fire once."""
    now = datetime.now(MARKET_TZ)
    if now.weekday() == MARKET_CLOSE_WEEKDAY:
        return now.hour >= MARKET_CLOSE_HOUR
    if now.weekday() == MARKET_OPEN_WEEKDAY:
        return now.hour < MARKET_OPEN_HOUR
    # Saturday, and every weekday strictly between Friday close and Sunday open, is fully closed.
    return now.weekday() == 5


def is_overnight_polling_pause() -> bool:
    """True outside 7:00 AM-5:00 PM Eastern on weekdays (all day Saturday/Sunday too) -- a deliberate
    cost-saving pause,
    not a real market-closed signal (see OVERNIGHT_PAUSE_START_HOUR/OVERNIGHT_PAUSE_END_HOUR above).
    poll_job.py checks this (and is_market_closed() above) before making any Postgres connection at all,
    so Neon compute can actually scale to zero overnight instead of a poll landing inside its 5-minute
    suspend timeout every single cycle and keeping it perpetually warm."""
    now = datetime.now(MARKET_TZ)
    if now.weekday() >= 5:
        return True
    return now.hour >= OVERNIGHT_PAUSE_START_HOUR or now.hour < OVERNIGHT_PAUSE_END_HOUR
