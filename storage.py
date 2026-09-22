"""Postgres time-series store for polled prices (shared by the cloud poll job and dashboard)."""

import os
from datetime import date, datetime, timezone

import psycopg2
from dotenv import load_dotenv

from retry import with_retries

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")


@with_retries()
def get_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db() -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                ts TIMESTAMPTZ NOT NULL,
                name TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                ts TIMESTAMPTZ NOT NULL,
                message TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                rule_name TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                open_ts TIMESTAMPTZ NOT NULL,
                triggering_alerts TEXT NOT NULL,
                exit_price DOUBLE PRECISION,
                close_ts TIMESTAMPTZ,
                pnl DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT 'Open'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS forex_trades (
                id SERIAL PRIMARY KEY,
                rule_name TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                open_ts TIMESTAMPTZ NOT NULL,
                triggering_alerts TEXT NOT NULL,
                forex_order_id TEXT NOT NULL,
                exit_price DOUBLE PRECISION,
                close_ts TIMESTAMPTZ,
                pnl DOUBLE PRECISION,
                forex_close_order_id TEXT,
                status TEXT NOT NULL DEFAULT 'Open'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS threshold_history (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                gld_15min DOUBLE PRECISION NOT NULL,
                gld_10min DOUBLE PRECISION NOT NULL,
                gld_5min DOUBLE PRECISION NOT NULL,
                dxy_15min DOUBLE PRECISION NOT NULL,
                dxy_10min DOUBLE PRECISION NOT NULL,
                dxy_5min DOUBLE PRECISION NOT NULL,
                us10y_15min DOUBLE PRECISION NOT NULL,
                us10y_10min DOUBLE PRECISION NOT NULL,
                us10y_5min DOUBLE PRECISION NOT NULL,
                iau_15min DOUBLE PRECISION,
                iau_10min DOUBLE PRECISION,
                iau_5min DOUBLE PRECISION,
                gldm_15min DOUBLE PRECISION,
                gldm_10min DOUBLE PRECISION,
                gldm_5min DOUBLE PRECISION,
                gdx_15min DOUBLE PRECISION,
                gdx_10min DOUBLE PRECISION,
                gdx_5min DOUBLE PRECISION,
                gdxj_15min DOUBLE PRECISION,
                gdxj_10min DOUBLE PRECISION,
                gdxj_5min DOUBLE PRECISION,
                ring_15min DOUBLE PRECISION,
                ring_10min DOUBLE PRECISION,
                ring_5min DOUBLE PRECISION
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nfp_reports (
                id SERIAL PRIMARY KEY,
                release_ts TIMESTAMPTZ NOT NULL,
                data_month TEXT NOT NULL,
                previous_value TEXT,
                expected_value TEXT,
                actual_value TEXT,
                gold_at_release DOUBLE PRECISION,
                gold_5min DOUBLE PRECISION,
                gold_10min DOUBLE PRECISION,
                gold_30min DOUBLE PRECISION,
                gold_1h DOUBLE PRECISION,
                gold_2h DOUBLE PRECISION,
                notes TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS adp_reports (
                id SERIAL PRIMARY KEY,
                release_ts TIMESTAMPTZ NOT NULL,
                data_month TEXT NOT NULL,
                previous_value TEXT,
                expected_value TEXT,
                actual_value TEXT,
                gold_at_release DOUBLE PRECISION,
                gold_5min DOUBLE PRECISION,
                gold_10min DOUBLE PRECISION,
                gold_30min DOUBLE PRECISION,
                gold_1h DOUBLE PRECISION,
                gold_2h DOUBLE PRECISION,
                notes TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oil_weekly_reports (
                id SERIAL PRIMARY KEY,
                release_ts TIMESTAMPTZ NOT NULL,
                week_ending DATE NOT NULL,
                previous_value TEXT,
                expected_value TEXT,
                actual_value TEXT,
                gold_at_release DOUBLE PRECISION,
                gold_5min DOUBLE PRECISION,
                gold_10min DOUBLE PRECISION,
                gold_30min DOUBLE PRECISION,
                gold_1h DOUBLE PRECISION,
                gold_2h DOUBLE PRECISION,
                notes TEXT,
                UNIQUE (week_ending)
            )
            """
        )


def save_readings(prices: dict[str, float]) -> None:
    ts = datetime.now(timezone.utc)
    with get_connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO readings (ts, name, price) VALUES (%s, %s, %s)",
            [(ts, name, price) for name, price in prices.items()],
        )


def save_alert(message: str) -> None:
    ts = datetime.now(timezone.utc)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO alerts (ts, message) VALUES (%s, %s)", (ts, message))


def get_previous_reading(name: str) -> float | None:
    """Second-to-last stored price for `name`, used to compute % change."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT price FROM readings WHERE name = %s ORDER BY ts DESC LIMIT 2",
            (name,),
        )
        rows = cur.fetchall()
    if len(rows) < 2:
        return None
    return rows[1][0]


def get_recent_readings(name: str, minutes: int) -> list[tuple[datetime, float]]:
    """(ts, price) rows for `name` from the trailing `minutes`, oldest first."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ts, price FROM readings WHERE name = %s AND ts >= NOW() - (%s * INTERVAL '1 minute') "
            "ORDER BY ts",
            (name, minutes),
        )
        return cur.fetchall()


def get_recent_alerts(minutes: int) -> list[tuple[datetime, str]]:
    """(ts, message) rows from the trailing `minutes`, oldest first -- used by broker.py to look for
    a fresh correlated-alert trading signal without re-deriving it from raw readings."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ts, message FROM alerts WHERE ts >= NOW() - (%s * INTERVAL '1 minute') ORDER BY ts",
            (minutes,),
        )
        return cur.fetchall()


def get_open_trade() -> dict | None:
    """The Broker's single open imaginary trade, if any (see broker.py; only one trade is ever open
    at a time, so this is unambiguous)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, rule_name, trade_type, entry_price, open_ts, triggering_alerts "
            "FROM trades WHERE status = 'Open' ORDER BY open_ts DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "rule_name": row[1],
        "trade_type": row[2],
        "entry_price": row[3],
        "open_ts": row[4],
        "triggering_alerts": row[5],
    }


def get_last_trade_open_ts() -> datetime | None:
    """Open timestamp of the most recent trade (open or closed) -- the watermark broker.py uses so
    an already-acted-on alert can't trigger a second trade."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT open_ts FROM trades ORDER BY open_ts DESC LIMIT 1")
        row = cur.fetchone()
    return row[0] if row else None


def get_all_trades() -> list[dict]:
    """Every trade, oldest first -- for ad hoc querying/analysis (e.g. by the Broker subagent); not
    used by broker.py's own trading logic, which only needs the single open trade."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rule_name, trade_type, entry_price, open_ts, triggering_alerts, "
            "exit_price, close_ts, pnl, status FROM trades ORDER BY open_ts"
        )
        rows = cur.fetchall()
    return [
        {
            "rule_name": r[0],
            "trade_type": r[1],
            "entry_price": r[2],
            "open_ts": r[3],
            "triggering_alerts": r[4],
            "exit_price": r[5],
            "close_ts": r[6],
            "pnl": r[7],
            "status": r[8],
        }
        for r in rows
    ]


def insert_trade(
    rule_name: str, trade_type: str, entry_price: float, open_ts: datetime, triggering_alerts: str
) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trades (rule_name, trade_type, entry_price, open_ts, triggering_alerts, status) "
            "VALUES (%s, %s, %s, %s, %s, 'Open')",
            (rule_name, trade_type, entry_price, open_ts, triggering_alerts),
        )


def close_trade_row(trade_id: int, exit_price: float, close_ts: datetime, pnl: float) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE trades SET exit_price = %s, close_ts = %s, pnl = %s, status = 'Closed' WHERE id = %s",
            (exit_price, close_ts, pnl, trade_id),
        )


def get_open_forex_trade() -> dict | None:
    """The Forex broker's single open real (demo-account) trade, if any -- see forex_broker.py. Mirrors
    get_open_trade() but reads the separate forex_trades table, so it never sees broker.py's imaginary
    trades or vice versa."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, rule_name, trade_type, entry_price, open_ts, triggering_alerts, forex_order_id "
            "FROM forex_trades WHERE status = 'Open' ORDER BY open_ts DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "rule_name": row[1],
        "trade_type": row[2],
        "entry_price": row[3],
        "open_ts": row[4],
        "triggering_alerts": row[5],
        "forex_order_id": row[6],
    }


def get_last_forex_trade_open_ts() -> datetime | None:
    """Open timestamp of the most recent forex_trades row (open or closed) -- the watermark
    forex_broker.py uses so an already-acted-on alert can't trigger a second trade."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT open_ts FROM forex_trades ORDER BY open_ts DESC LIMIT 1")
        row = cur.fetchone()
    return row[0] if row else None


def get_all_forex_trades() -> list[dict]:
    """Every Forex-broker trade, oldest first -- for ad hoc querying/analysis; not used by
    forex_broker.py's own trading logic, which only needs the single open trade."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rule_name, trade_type, entry_price, open_ts, triggering_alerts, forex_order_id, "
            "exit_price, close_ts, pnl, forex_close_order_id, status FROM forex_trades ORDER BY open_ts"
        )
        rows = cur.fetchall()
    return [
        {
            "rule_name": r[0],
            "trade_type": r[1],
            "entry_price": r[2],
            "open_ts": r[3],
            "triggering_alerts": r[4],
            "forex_order_id": r[5],
            "exit_price": r[6],
            "close_ts": r[7],
            "pnl": r[8],
            "forex_close_order_id": r[9],
            "status": r[10],
        }
        for r in rows
    ]


def insert_forex_trade(
    rule_name: str,
    trade_type: str,
    entry_price: float,
    open_ts: datetime,
    triggering_alerts: str,
    forex_order_id,
) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO forex_trades (rule_name, trade_type, entry_price, open_ts, triggering_alerts, "
            "forex_order_id, status) VALUES (%s, %s, %s, %s, %s, %s, 'Open')",
            (rule_name, trade_type, entry_price, open_ts, triggering_alerts, str(forex_order_id)),
        )


def close_forex_trade_row(
    trade_id: int, exit_price: float, close_ts: datetime, pnl: float, forex_close_order_id
) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE forex_trades SET exit_price = %s, close_ts = %s, pnl = %s, "
            "forex_close_order_id = %s, status = 'Closed' WHERE id = %s",
            (exit_price, close_ts, pnl, str(forex_close_order_id), trade_id),
        )


def insert_nfp_report(
    release_ts: datetime,
    data_month: str,
    previous_value: str | None,
    expected_value: str | None,
    actual_value: str | None,
    gold_at_release: float | None,
    gold_5min: float | None,
    gold_10min: float | None,
    gold_30min: float | None,
    gold_1h: float | None,
    gold_2h: float | None,
    notes: str | None = None,
) -> None:
    """Records one Non-Farm Payrolls release's figures and gold spot's reaction -- see
    docs/fundamental-analyst-nfp-log.md for what this replaces (a hand-maintained markdown table) and
    why (a routine update here no longer needs a repo commit). Dollar/percentage deltas vs.
    gold_at_release aren't stored -- derive them from the raw prices when reading."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO nfp_reports (
                release_ts, data_month, previous_value, expected_value, actual_value,
                gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                release_ts, data_month, previous_value, expected_value, actual_value,
                gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes,
            ),
        )


def get_nfp_reports() -> list[dict]:
    """Every recorded NFP release, oldest first."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT release_ts, data_month, previous_value, expected_value, actual_value, "
            "gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes "
            "FROM nfp_reports ORDER BY release_ts"
        )
        rows = cur.fetchall()
    return [
        {
            "release_ts": r[0],
            "data_month": r[1],
            "previous_value": r[2],
            "expected_value": r[3],
            "actual_value": r[4],
            "gold_at_release": r[5],
            "gold_5min": r[6],
            "gold_10min": r[7],
            "gold_30min": r[8],
            "gold_1h": r[9],
            "gold_2h": r[10],
            "notes": r[11],
        }
        for r in rows
    ]


def update_nfp_report_reaction(
    release_ts: datetime,
    gold_5min: float | None = None,
    gold_10min: float | None = None,
    gold_30min: float | None = None,
    gold_1h: float | None = None,
    gold_2h: float | None = None,
) -> None:
    """Fills in gold spot's reaction windows for a release already recorded by insert_nfp_report() --
    for the fundamental-analyst subagent's new-release workflow, where actual/previous/expected and
    gold_at_release are known and inserted immediately, but the later reaction windows aren't observable
    yet. Only columns passed a non-None value are updated; matches the row by release_ts."""
    updates = {
        "gold_5min": gold_5min,
        "gold_10min": gold_10min,
        "gold_30min": gold_30min,
        "gold_1h": gold_1h,
        "gold_2h": gold_2h,
    }
    updates = {col: value for col, value in updates.items() if value is not None}
    if not updates:
        return
    set_clause = ", ".join(f"{col} = %s" for col in updates)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE nfp_reports SET {set_clause} WHERE release_ts = %s",
            (*updates.values(), release_ts),
        )


def insert_adp_report(
    release_ts: datetime,
    data_month: str,
    previous_value: str | None,
    expected_value: str | None,
    actual_value: str | None,
    gold_at_release: float | None,
    gold_5min: float | None,
    gold_10min: float | None,
    gold_30min: float | None,
    gold_1h: float | None,
    gold_2h: float | None,
    notes: str | None = None,
) -> None:
    """Records one ADP National Employment Change release's figures and gold spot's reaction --
    same shape as insert_nfp_report(), see docs/fundamental-analyst-adp-log.md. Dollar/percentage
    deltas vs. gold_at_release aren't stored -- derive them from the raw prices when reading."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO adp_reports (
                release_ts, data_month, previous_value, expected_value, actual_value,
                gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                release_ts, data_month, previous_value, expected_value, actual_value,
                gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes,
            ),
        )


def get_adp_reports() -> list[dict]:
    """Every recorded ADP NEC release, oldest first."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT release_ts, data_month, previous_value, expected_value, actual_value, "
            "gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes "
            "FROM adp_reports ORDER BY release_ts"
        )
        rows = cur.fetchall()
    return [
        {
            "release_ts": r[0],
            "data_month": r[1],
            "previous_value": r[2],
            "expected_value": r[3],
            "actual_value": r[4],
            "gold_at_release": r[5],
            "gold_5min": r[6],
            "gold_10min": r[7],
            "gold_30min": r[8],
            "gold_1h": r[9],
            "gold_2h": r[10],
            "notes": r[11],
        }
        for r in rows
    ]


def update_adp_report_reaction(
    release_ts: datetime,
    gold_5min: float | None = None,
    gold_10min: float | None = None,
    gold_30min: float | None = None,
    gold_1h: float | None = None,
    gold_2h: float | None = None,
) -> None:
    """Fills in gold spot's reaction windows for a release already recorded by insert_adp_report() --
    only columns passed a non-None value are updated; matches the row by release_ts. Same pattern as
    update_nfp_report_reaction()."""
    updates = {
        "gold_5min": gold_5min,
        "gold_10min": gold_10min,
        "gold_30min": gold_30min,
        "gold_1h": gold_1h,
        "gold_2h": gold_2h,
    }
    updates = {col: value for col, value in updates.items() if value is not None}
    if not updates:
        return
    set_clause = ", ".join(f"{col} = %s" for col in updates)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE adp_reports SET {set_clause} WHERE release_ts = %s",
            (*updates.values(), release_ts),
        )


def insert_oil_weekly_report(
    release_ts: datetime,
    week_ending: date,
    previous_value: str | None,
    expected_value: str | None,
    actual_value: str | None,
    gold_at_release: float | None,
    gold_5min: float | None,
    gold_10min: float | None,
    gold_30min: float | None,
    gold_1h: float | None,
    gold_2h: float | None,
    notes: str | None = None,
) -> None:
    """Records one API Crude Oil Stock Change release's figures and gold spot's reaction -- same
    shape as insert_adp_report()/insert_nfp_report(), see docs/fundamental-analyst-oil-weekly-log.md.
    `week_ending` (not release_ts) is the natural per-release key here -- the report always covers a
    Friday-to-Friday week and releases the following Tuesday, so ON CONFLICT (week_ending) DO NOTHING
    makes a duplicate insert (e.g. oil_weekly_job.py firing twice for the same week, or a backfill
    re-run) a safe no-op rather than an error."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO oil_weekly_reports (
                release_ts, week_ending, previous_value, expected_value, actual_value,
                gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (week_ending) DO NOTHING
            """,
            (
                release_ts, week_ending, previous_value, expected_value, actual_value,
                gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes,
            ),
        )


def get_oil_weekly_reports() -> list[dict]:
    """Every recorded API Crude Oil Stock Change release, oldest first."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT release_ts, week_ending, previous_value, expected_value, actual_value, "
            "gold_at_release, gold_5min, gold_10min, gold_30min, gold_1h, gold_2h, notes "
            "FROM oil_weekly_reports ORDER BY week_ending"
        )
        rows = cur.fetchall()
    return [
        {
            "release_ts": r[0],
            "week_ending": r[1],
            "previous_value": r[2],
            "expected_value": r[3],
            "actual_value": r[4],
            "gold_at_release": r[5],
            "gold_5min": r[6],
            "gold_10min": r[7],
            "gold_30min": r[8],
            "gold_1h": r[9],
            "gold_2h": r[10],
            "notes": r[11],
        }
        for r in rows
    ]


def oil_weekly_report_exists(week_ending: date) -> bool:
    """True if a row for this week is already recorded -- used by oil_weekly_job.py to decide
    whether a detected release still needs inserting (it's triggered repeatedly across a multi-hour
    window each Tuesday, since the report's exact release time varies more than ADP/NFP's fixed
    minute, so the same week's release could otherwise be seen -- and alerted on -- more than once)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM oil_weekly_reports WHERE week_ending = %s", (week_ending,))
        return cur.fetchone() is not None


def update_oil_weekly_report_reaction(
    release_ts: datetime,
    gold_5min: float | None = None,
    gold_10min: float | None = None,
    gold_30min: float | None = None,
    gold_1h: float | None = None,
    gold_2h: float | None = None,
) -> None:
    """Fills in gold spot's reaction windows for a release already recorded by
    insert_oil_weekly_report() -- only columns passed a non-None value are updated; matches the row
    by release_ts. Same pattern as update_adp_report_reaction()/update_nfp_report_reaction()."""
    updates = {
        "gold_5min": gold_5min,
        "gold_10min": gold_10min,
        "gold_30min": gold_30min,
        "gold_1h": gold_1h,
        "gold_2h": gold_2h,
    }
    updates = {col: value for col, value in updates.items() if value is not None}
    if not updates:
        return
    set_clause = ", ".join(f"{col} = %s" for col in updates)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE oil_weekly_reports SET {set_clause} WHERE release_ts = %s",
            (*updates.values(), release_ts),
        )


# Column order for threshold_history -- gld/dxy/us10y first (the original three, matching the
# table's existing column order) then iau/gldm/gdx/gdxj/ring appended after, so existing rows/columns
# are untouched by the newer indicators. Kept here (not read from config.INTRAHOUR_SWING_ALERT_THRESHOLD)
# so this module doesn't need to import config just for one fixed column order. sgol was tracked here
# briefly and dropped (see CLAUDE.md) before any weekday run ever wrote a value to its columns, so no
# historical sgol_* data was lost by removing it.
THRESHOLD_HISTORY_INDICATORS = ["gld", "dxy", "us10y", "iau", "gldm", "gdx", "gdxj", "ring"]
THRESHOLD_HISTORY_WINDOWS = [15, 10, 5]


def insert_threshold_history_row(row_date: date, thresholds: dict[str, dict[int, float]]) -> None:
    """One row per weekday's frequency_check_job.py run -- `row_date` (America/New_York) plus that
    run's final value for all twenty-four GLD/DXY/US10Y/IAU/GLDM/GDX/GDXJ/RING 15/10/5-min
    intrahour-swing thresholds, whether or not any of them changed that run. Replaces the old
    "Threshold history" table that used to live in docs/frequency-test-thresholds.md, same reasoning
    as the Broker's `trades` table: a routine log entry shouldn't need a repo commit."""
    # Column names are built from THRESHOLD_HISTORY_INDICATORS/_WINDOWS above (fixed constants, never
    # external input), so interpolating them into the query string is safe here.
    columns = [f"{name}_{window}min" for name in THRESHOLD_HISTORY_INDICATORS for window in THRESHOLD_HISTORY_WINDOWS]
    # .get() rather than direct indexing: tolerates a `thresholds` dict that doesn't cover every
    # indicator (e.g. an older caller/backfill predating a newer indicator) by writing NULL instead
    # of raising -- the fifteen newer columns above (iau/gldm/gdx/gdxj/ring) are nullable for exactly
    # this reason.
    values = [
        thresholds.get(name, {}).get(window)
        for name in THRESHOLD_HISTORY_INDICATORS
        for window in THRESHOLD_HISTORY_WINDOWS
    ]
    placeholders = ", ".join(["%s"] * len(values))
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO threshold_history (date, {', '.join(columns)}) VALUES (%s, {placeholders})",
            [row_date, *values],
        )


def get_threshold_history() -> list[dict]:
    """Every logged run's thresholds, oldest first."""
    columns = [f"{name}_{window}min" for name in THRESHOLD_HISTORY_INDICATORS for window in THRESHOLD_HISTORY_WINDOWS]
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT date, {', '.join(columns)} FROM threshold_history ORDER BY date")
        rows = cur.fetchall()
    return [{"date": r[0], **dict(zip(columns, r[1:]))} for r in rows]
