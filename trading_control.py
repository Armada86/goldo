"""Manual trading on/off control for Broker A and Broker B, set from Telegram (telegram_webhook/).

Three inputs, all stored in Postgres by the Worker and read here each poll:
  * a scheduled pause (`trading_pauses`): "Thursday 1 October 2026, stop trading from 7 till 10" -- no
    trading between start_ts and end_ts;
  * "stop trading" (`trading_override.mode = 'stopped'`): no trading until the program's own trading
    window next opens (market_hours.OVERNIGHT_PAUSE_END_HOUR, 7am ET weekdays);
  * "start trading" (`mode = 'started'`): trading allowed until the program's own window next closes
    (OVERNIGHT_PAUSE_START_HOUR, 5pm ET weekdays). It lifts stops and any pause that began before the
    command; a pause scheduled to begin later still applies. It does not widen the entry window itself.

While paused the brokers close any open position at market (mirroring the "stop trading" command, which
closes positions immediately) and open nothing. Fails open: a DB error means "not paused".
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from market_hours import OVERNIGHT_PAUSE_END_HOUR, OVERNIGHT_PAUSE_START_HOUR
from storage import get_active_trading_pauses, get_trading_override

ET = ZoneInfo("America/New_York")


def _next_weekday_boundary(after: datetime, hour: int) -> datetime:
    """First weekday `hour`:00 America/New_York strictly after `after`."""
    day = after.astimezone(ET).date()
    for offset in range(0, 8):
        d = day + timedelta(days=offset)
        candidate = datetime(d.year, d.month, d.day, hour, 0, tzinfo=ET)
        if candidate.weekday() < 5 and candidate > after:
            return candidate
    raise RuntimeError("unreachable: no weekday within 8 days")


def next_window_open(after: datetime) -> datetime:
    return _next_weekday_boundary(after, OVERNIGHT_PAUSE_END_HOUR)


def next_window_close(after: datetime) -> datetime:
    return _next_weekday_boundary(after, OVERNIGHT_PAUSE_START_HOUR)


def evaluate_pause(now: datetime, mode: str | None, set_ts: datetime | None, pauses: list[tuple]) -> str | None:
    """Pure decision: a human-readable reason trading is paused at `now`, or None. `pauses` is a list of
    (start_ts, end_ts) that cover `now`."""
    if mode == "stopped" and set_ts is not None and now < next_window_open(set_ts):
        return "trading stopped by Telegram command (resumes at the next trading window or on \"start trading\")"
    started = mode == "started" and set_ts is not None and now < next_window_close(set_ts)
    for start_ts, end_ts in pauses:
        if started and set_ts >= start_ts:
            continue  # "start trading" came after this pause began -- it wins
        local_end = end_ts.astimezone(ET)
        return f"scheduled pause until {local_end:%Y-%m-%d %H:%M} ET"
    return None


def trading_pause_reason(now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    try:
        mode, set_ts = get_trading_override()
        pauses = get_active_trading_pauses(now)
    except Exception:
        return None
    return evaluate_pause(now, mode, set_ts, pauses)
