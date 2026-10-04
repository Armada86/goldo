"""Postgres time-series store for polled prices (shared by the cloud poll job and dashboard)."""

import os
from datetime import date, datetime, timezone

import psycopg2
from psycopg2.extras import Json
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
            CREATE TABLE IF NOT EXISTS broker_b_trades (
                id SERIAL PRIMARY KEY,
                rule_name TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                open_ts TIMESTAMPTZ NOT NULL,
                triggering_alerts TEXT NOT NULL,
                ta_forecast_id INTEGER NOT NULL,
                exit_price DOUBLE PRECISION,
                close_ts TIMESTAMPTZ,
                pnl DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT 'Open'
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_b_blocked (
                id SERIAL PRIMARY KEY,
                ta_forecast_id INTEGER NOT NULL,
                rule_name TEXT NOT NULL,
                trigger_price DOUBLE PRECISION NOT NULL,
                reasons TEXT NOT NULL,
                detected_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (ta_forecast_id, rule_name, reasons)
            )
            """
        )
        # touch_ts: when the blocked touch actually happened (30 Sep 2026) -- see
        # get_last_blocked_touch_ts_b(). Added after the table already existed, hence the ALTER.
        cur.execute("ALTER TABLE broker_b_blocked ADD COLUMN IF NOT EXISTS touch_ts TIMESTAMPTZ")
        # entry_context: descriptive snapshot of the conditions at entry (30 Sep 2026) -- see
        # entry_context.py. Nullable; older rows and Telegram-opened trades simply have none.
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_context JSONB")
        cur.execute("ALTER TABLE broker_b_trades ADD COLUMN IF NOT EXISTS entry_context JSONB")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_a_blocked (
                id SERIAL PRIMARY KEY,
                rule_name TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                reasons TEXT NOT NULL,
                since_ts TIMESTAMPTZ NOT NULL,
                detected_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (rule_name, reasons, since_ts)
            )
            """
        )
        # signal_ts: the newest alert behind the blocked signal (30 Sep 2026) -- see
        # get_last_blocked_signal_ts_a(). Added after the table already existed, hence the ALTER.
        cur.execute("ALTER TABLE broker_a_blocked ADD COLUMN IF NOT EXISTS signal_ts TIMESTAMPTZ")
        # Manual trading control from Telegram (see trading_control.py). The Worker creates these too.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_override (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                mode TEXT NOT NULL,
                set_ts TIMESTAMPTZ NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS broker_b_rearm (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                rearm_ts TIMESTAMPTZ NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trailing_stop_setting (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                activation DOUBLE PRECISION NOT NULL,
                distance DOUBLE PRECISION NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stop_loss_setting (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                stop_loss DOUBLE PRECISION NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS block_rules (
                id SERIAL PRIMARY KEY,
                set_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source TEXT NOT NULL,
                changed BOOLEAN NOT NULL DEFAULT FALSE,
                n_events INTEGER,
                note TEXT,
                dxy_threshold DOUBLE PRECISION NOT NULL,
                adx_trending DOUBLE PRECISION NOT NULL,
                adx_chop DOUBLE PRECISION NOT NULL,
                rsi_overbought DOUBLE PRECISION NOT NULL,
                rsi_oversold DOUBLE PRECISION NOT NULL,
                fade_rsi_overbought DOUBLE PRECISION NOT NULL,
                fade_rsi_oversold DOUBLE PRECISION NOT NULL,
                atr_max DOUBLE PRECISION NOT NULL
            )
            """
        )
        # First run only: seed the table with the rules currently in config.py (see block_rules.py).
        cur.execute("SELECT 1 FROM block_rules LIMIT 1")
        if cur.fetchone() is None:
            from block_rules import RULE_KEYS, default_rules

            defaults = default_rules()
            cur.execute(
                f"INSERT INTO block_rules (source, changed, note, {', '.join(RULE_KEYS)}) "
                f"VALUES ('seed', FALSE, 'Rules from config.py', {', '.join(['%s'] * len(RULE_KEYS))})",
                [defaults[k] for k in RULE_KEYS],
            )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trading_pauses (
                id SERIAL PRIMARY KEY,
                start_ts TIMESTAMPTZ NOT NULL,
                end_ts TIMESTAMPTZ NOT NULL,
                created_ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
            CREATE TABLE IF NOT EXISTS broker_daily_reviews (
                id SERIAL PRIMARY KEY,
                review_date DATE NOT NULL UNIQUE,
                created_ts TIMESTAMPTZ NOT NULL,
                trade_count INTEGER NOT NULL,
                total_pnl DOUBLE PRECISION,
                analysis TEXT NOT NULL,
                trade_reviews JSONB
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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ta_forecasts (
                id SERIAL PRIMARY KEY,
                forecast_date DATE NOT NULL,
                ts TIMESTAMPTZ NOT NULL,
                analysis TEXT NOT NULL,
                levels JSONB,
                diagram_svg TEXT
            )
            """
        )
        # diagram_svg was added after ta_forecasts already existed in production, so a fresh
        # CREATE TABLE above isn't enough to bring an existing Neon DB's table up to date.
        cur.execute("ALTER TABLE ta_forecasts ADD COLUMN IF NOT EXISTS diagram_svg TEXT")


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
    rule_name: str,
    trade_type: str,
    entry_price: float,
    open_ts: datetime,
    triggering_alerts: str,
    entry_context: dict | None = None,
) -> int:
    """Inserts the open trade and returns its new id (the trade number shown in Telegram messages)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO trades (rule_name, trade_type, entry_price, open_ts, triggering_alerts, status, "
            "entry_context) VALUES (%s, %s, %s, %s, %s, 'Open', %s) RETURNING id",
            (rule_name, trade_type, entry_price, open_ts, triggering_alerts,
             Json(entry_context) if entry_context else None),
        )
        return cur.fetchone()[0]


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
    docs/fundamental-analysts/fundamental-analyst-nfp-log.md for what this replaces (a hand-maintained markdown table) and
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
    same shape as insert_nfp_report(), see docs/fundamental-analysts/fundamental-analyst-adp-log.md. Dollar/percentage
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
    shape as insert_adp_report()/insert_nfp_report(), see docs/fundamental-analysts/fundamental-analyst-oil-weekly-log.md.
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


def insert_ta_forecast(
    ts: datetime, forecast_date: date, analysis: str, levels: dict, diagram_svg: str | None = None
) -> None:
    """One generated XAU/USD technical forecast (ta_forecast_job.py) -- `analysis` is the
    human-readable text, `levels` the same forecast's numbers (indicators, level zones, scenario
    triggers/stops/targets) as JSONB, so the next run can grade this one against real candles.
    `diagram_svg` is the self-contained price-ladder SVG rendered from those same zones/price/stops
    (see ta_forecast_job.py's render_diagram_svg()), which dashboard.py displays as-is."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ta_forecasts (forecast_date, ts, analysis, levels, diagram_svg) "
            "VALUES (%s, %s, %s, %s, %s)",
            (forecast_date, ts, analysis, Json(levels), diagram_svg),
        )


def count_ta_forecasts_for_date(forecast_date: date) -> int:
    """How many ta_forecasts rows exist for this ET date -- the next run is TA<count + 1>."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM ta_forecasts WHERE forecast_date = %s", (forecast_date,))
        return cur.fetchone()[0]


def get_latest_ta_forecast() -> dict | None:
    """Most recent ta_forecasts row, or None if the table is empty. `diagram_svg` is None on rows
    written before that column existed."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, forecast_date, ts, analysis, levels, diagram_svg "
            "FROM ta_forecasts ORDER BY ts DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "forecast_date": row[1], "ts": row[2], "analysis": row[3], "levels": row[4],
        "diagram_svg": row[5],
    }


def get_open_trade_b() -> dict | None:
    """Broker B's single open imaginary trade, if any -- see broker_b.py. Entirely separate from
    Broker A's `trades` table/get_open_trade()."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, rule_name, trade_type, entry_price, open_ts, triggering_alerts, ta_forecast_id "
            "FROM broker_b_trades WHERE status = 'Open' ORDER BY open_ts DESC LIMIT 1"
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
        "ta_forecast_id": row[6],
    }


def insert_trade_b(
    rule_name: str,
    trade_type: str,
    entry_price: float,
    open_ts: datetime,
    triggering_alerts: str,
    ta_forecast_id: int,
    entry_context: dict | None = None,
) -> int:
    """Inserts the open trade and returns its new id (the trade number shown in Telegram messages)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO broker_b_trades "
            "(rule_name, trade_type, entry_price, open_ts, triggering_alerts, ta_forecast_id, status, "
            "entry_context) VALUES (%s, %s, %s, %s, %s, %s, 'Open', %s) RETURNING id",
            (rule_name, trade_type, entry_price, open_ts, triggering_alerts, ta_forecast_id,
             Json(entry_context) if entry_context else None),
        )
        return cur.fetchone()[0]


def close_trade_row_b(trade_id: int, exit_price: float, close_ts: datetime, pnl: float) -> None:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE broker_b_trades SET exit_price = %s, close_ts = %s, pnl = %s, status = 'Closed' "
            "WHERE id = %s",
            (exit_price, close_ts, pnl, trade_id),
        )


def trade_b_level_history(ta_forecast_id: int, rule_name: str) -> dict:
    """What Broker B has already done at one (forecast, rule) level -- the re-arm check in
    broker_b.py: how many trades it has opened there, whether any of them was stopped out (a loss
    means the level broke, so it's never re-traded off this forecast), and when the latest one
    closed (a re-entry only counts touches after that, never the touch that opened the last trade)."""
    with get_connection() as conn, conn.cursor() as cur:
        # Telegram "rearm levels" (see telegram_webhook/): trades opened at or before the rearm time no
        # longer count, neither their number nor any stop-out -- every level gets a fresh budget.
        cur.execute("SELECT to_regclass('broker_b_rearm')")
        rearm_ts = None
        if cur.fetchone()[0] is not None:
            cur.execute("SELECT rearm_ts FROM broker_b_rearm WHERE id = 1")
            row = cur.fetchone()
            rearm_ts = row[0] if row else None
        query = (
            "SELECT COUNT(*), COALESCE(BOOL_OR(pnl < 0), FALSE), MAX(close_ts) FROM broker_b_trades "
            "WHERE ta_forecast_id = %s AND rule_name = %s"
        )
        params: tuple = (ta_forecast_id, rule_name)
        if rearm_ts is not None:
            query += " AND open_ts > %s"
            params += (rearm_ts,)
        cur.execute(query, params)
        count, stopped_out, last_close_ts = cur.fetchone()
    return {"count": count, "stopped_out": stopped_out, "last_close_ts": last_close_ts}


def get_trailing_stop_override() -> tuple[float, float] | None:
    """(activation, distance) in $ per oz last set by the Telegram "make trail <n>" command
    (telegram_webhook/), or None if it was never set. Both brokers' trailing stops use it in place
    of broker.TRAILING_STOP_ACTIVATION / TRAILING_STOP_DISTANCE."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('trailing_stop_setting')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT activation, distance FROM trailing_stop_setting WHERE id = 1")
        row = cur.fetchone()
    return (float(row[0]), float(row[1])) if row else None


def get_closed_trades_for_analysis(since: datetime) -> list[dict]:
    """Every closed Broker A and Broker B trade opened at or after `since`, oldest first, for the
    stop loss analysis (stop_loss_analysis_job.py). Only the entry matters to the replay, so trades that
    were closed by hand or by a "stop trading" command are included like any other."""
    rows: list[dict] = []
    with get_connection() as conn, conn.cursor() as cur:
        for table, broker in (("trades", "A"), ("broker_b_trades", "B")):
            cur.execute(
                f"SELECT id, rule_name, trade_type, entry_price, open_ts FROM {table} "
                "WHERE status = 'Closed' AND open_ts >= %s ORDER BY open_ts",
                (since,),
            )
            for trade_id, rule_name, trade_type, entry_price, open_ts in cur.fetchall():
                rows.append({"broker": broker, "id": trade_id, "rule_name": rule_name, "trade_type": trade_type,
                             "entry_price": float(entry_price), "open_ts": open_ts})
    rows.sort(key=lambda r: r["open_ts"])
    return rows


def get_block_rules_row(as_of: datetime | None = None) -> dict | None:
    """The newest row of the block_rules table (the active Broker B entry-filter thresholds, see
    block_rules.py) as {rule_key: value}, or None if the table is missing or empty. With `as_of`, the newest
    row set at or before that time (None if there isn't one)."""
    from block_rules import RULE_KEYS

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('block_rules')")
        if cur.fetchone()[0] is None:
            return None
        where, params = ("WHERE set_ts <= %s", (as_of,)) if as_of is not None else ("", ())
        cur.execute(f"SELECT {', '.join(RULE_KEYS)} FROM block_rules {where} ORDER BY id DESC LIMIT 1", params)
        row = cur.fetchone()
    return {k: float(v) for k, v in zip(RULE_KEYS, row)} if row else None


def insert_block_rules_row(rules: dict, source: str, changed: bool, n_events: int | None, note: str | None) -> int:
    """Appends a block_rules row (history is kept; the newest row is the active one). Returns its id."""
    from block_rules import RULE_KEYS

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO block_rules (source, changed, n_events, note, {', '.join(RULE_KEYS)}) "
            f"VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(RULE_KEYS))}) RETURNING id",
            [source, changed, n_events, note] + [float(rules[k]) for k in RULE_KEYS],
        )
        return cur.fetchone()[0]


def get_broker_b_blocked_touches(since: datetime) -> list[dict]:
    """Broker B touches that were blocked by one of the four BRA-tunable filters (DXY / RSI / ADX /
    volatility) since `since`, oldest first, for the block rules analysis. Timing blocks and
    "touch too old" notices are left out: no threshold here would have changed them."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rule_name, trigger_price, reasons, touch_ts FROM broker_b_blocked "
            "WHERE touch_ts IS NOT NULL AND touch_ts >= %s AND (reasons ILIKE '%%DXY%%' OR reasons ILIKE '%%RSI(14)%%' "
            "OR reasons ILIKE '%%ADX(14)%%' OR reasons ILIKE '%%volatility%%') ORDER BY touch_ts",
            (since,),
        )
        rows = cur.fetchall()
    return [{"rule_name": r[0], "trigger_price": float(r[1]), "reasons": r[2], "touch_ts": r[3]} for r in rows]


def get_readings_since(name: str, since: datetime) -> list[tuple[datetime, float]]:
    """(ts, price) rows for `name` at or after `since`, oldest first."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ts, price FROM readings WHERE name = %s AND ts >= %s ORDER BY ts", (name, since))
        return [(ts, float(price)) for ts, price in cur.fetchall()]


def get_stop_loss_override() -> float | None:
    """The stop-loss ($ per oz) last set by the Telegram "make SL 15" command (telegram_webhook/), or
    None if it was never set. Both brokers' exits use it in place of broker.STOP_LOSS_THRESHOLD."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('stop_loss_setting')")
        if cur.fetchone()[0] is None:
            return None
        cur.execute("SELECT stop_loss FROM stop_loss_setting WHERE id = 1")
        row = cur.fetchone()
    return float(row[0]) if row else None


def get_last_close_ts_b() -> datetime | None:
    """When Broker B's most recently closed trade closed -- the entry scan ignores candles at or
    before it, so a touch that happened while a previous position was still open (or the very touch
    that opened it) can never open a new, back-dated trade once Broker B is flat again."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(close_ts) FROM broker_b_trades")
        return cur.fetchone()[0]


def record_broker_b_blocked_if_new(
    ta_forecast_id: int, rule_name: str, trigger_price: float, reasons: str, touch_ts: datetime | None = None
) -> bool:
    """Records one Broker B blocked-entry notice and returns True if it's newly recorded (i.e. the
    caller should send a Telegram message), False if this exact (forecast, rule, reasons) combination
    was already recorded -- the dedup that stops a level sitting past its trigger for hours (outside
    trading hours, or DXY/RSI still against it) from sending the same notice every 5-minute poll. See
    broker_b.py's _notify_timing_block()/_notify_gate_block().

    `touch_ts`, when given, is when the blocked touch happened; it's stored (kept at the latest value
    even when the notice itself is a duplicate) so get_last_blocked_touch_ts_b() can stop a later poll
    from filling that same touch once the block clears."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO broker_b_blocked (ta_forecast_id, rule_name, trigger_price, reasons, touch_ts) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (ta_forecast_id, rule_name, reasons) DO NOTHING "
            "RETURNING id",
            (ta_forecast_id, rule_name, trigger_price, reasons, touch_ts),
        )
        inserted = cur.fetchone() is not None
        if not inserted and touch_ts is not None:
            cur.execute(
                "UPDATE broker_b_blocked SET touch_ts = GREATEST(touch_ts, %s) "
                "WHERE ta_forecast_id = %s AND rule_name = %s AND reasons = %s",
                (touch_ts, ta_forecast_id, rule_name, reasons),
            )
        return inserted


def get_last_blocked_touch_ts_b(ta_forecast_id: int) -> dict[str, datetime]:
    """{rule_name: latest blocked touch time} for this forecast row -- Broker B ignores any candle at or
    before it for that rule, so a touch that a filter (DXY/RSI/trading hours) blocked is never filled
    later, at a stale price and time, just because the filter cleared a few polls afterward. Rules
    with no recorded blocked touch are absent."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rule_name, MAX(touch_ts) FROM broker_b_blocked "
            "WHERE ta_forecast_id = %s AND touch_ts IS NOT NULL GROUP BY rule_name",
            (ta_forecast_id,),
        )
        return {rule: ts for rule, ts in cur.fetchall()}


def get_broker_b_blocked() -> list[dict]:
    """Every recorded Broker B blocked-entry notice, oldest first -- for ad hoc querying/analysis
    (e.g. by the Broker subagent); not used by broker_b.py's own trading logic."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, ta_forecast_id, rule_name, trigger_price, reasons, detected_ts "
            "FROM broker_b_blocked ORDER BY detected_ts"
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "ta_forecast_id": r[1],
            "rule_name": r[2],
            "trigger_price": r[3],
            "reasons": r[4],
            "detected_ts": r[5],
        }
        for r in rows
    ]


def record_broker_a_blocked_if_new(
    rule_name: str, price: float, reasons: str, since_ts: datetime, signal_ts: datetime | None = None
) -> bool:
    """Records one Broker A blocked-entry notice and returns True if it's newly recorded (i.e. the
    caller should send a Telegram message), False if this exact (rule_name, reasons) combination was
    already recorded since `since_ts` -- the dedup that stops the same ongoing block (RSI still
    exhausted, DXY still unconfirmed) from sending a new notice every 5-minute poll while it persists.
    `since_ts` is broker.py's own entry floor (the last trade's open time or the last blocked signal,
    whichever is later, or the epoch if neither exists) -- there's no forecast row to scope by here the
    way broker_b_blocked has, so that floor plays the role instead: it advances the moment a trade
    opens or a signal is blocked, which lets the same (rule_name, reasons) pair notify again on a
    genuinely later, separate occurrence. See broker.py's _notify_blocked().

    `signal_ts`, when given, is the newest alert behind the blocked signal; it's stored (kept at the
    latest value even when the notice itself is a duplicate) so get_last_blocked_signal_ts_a() can stop
    a later poll from acting on the same alerts once the block clears."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO broker_a_blocked (rule_name, price, reasons, since_ts, signal_ts) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (rule_name, reasons, since_ts) DO NOTHING "
            "RETURNING id",
            (rule_name, price, reasons, since_ts, signal_ts),
        )
        inserted = cur.fetchone() is not None
        if not inserted and signal_ts is not None:
            cur.execute(
                "UPDATE broker_a_blocked SET signal_ts = GREATEST(signal_ts, %s) "
                "WHERE rule_name = %s AND reasons = %s AND since_ts = %s",
                (signal_ts, rule_name, reasons, since_ts),
            )
        return inserted


def get_last_blocked_signal_ts_a() -> datetime | None:
    """Newest alert timestamp behind any Broker A signal that a filter (trading hours, TA bias, RSI,
    DXY) blocked, or None if none was ever recorded. broker.py treats every alert at or before it as
    consumed, so a blocked signal is a missed signal -- never opened a poll or two later, once the
    block clears, off the same still-fresh alerts."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(signal_ts) FROM broker_a_blocked")
        row = cur.fetchone()
    return row[0] if row else None


def get_trading_override() -> tuple[str | None, datetime | None]:
    """(mode, set_ts) of the Telegram "stop trading"/"start trading" override, or (None, None). Returns
    (None, None) if the table doesn't exist yet (the Worker creates it on first command)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('trading_override')")
        if cur.fetchone()[0] is None:
            return None, None
        cur.execute("SELECT mode, set_ts FROM trading_override WHERE id = 1")
        row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def get_active_trading_pauses(now: datetime) -> list[tuple[datetime, datetime]]:
    """(start_ts, end_ts) of every scheduled Telegram pause covering `now`."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('trading_pauses')")
        if cur.fetchone()[0] is None:
            return []
        cur.execute(
            "SELECT start_ts, end_ts FROM trading_pauses WHERE start_ts <= %s AND end_ts > %s",
            (now, now),
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def get_broker_a_blocked() -> list[dict]:
    """Every recorded Broker A blocked-entry notice, oldest first -- for ad hoc querying/analysis
    (e.g. by the Broker subagent); not used by broker.py's own trading logic."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, rule_name, price, reasons, since_ts, detected_ts "
            "FROM broker_a_blocked ORDER BY detected_ts"
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "rule_name": r[1],
            "price": r[2],
            "reasons": r[3],
            "since_ts": r[4],
            "detected_ts": r[5],
        }
        for r in rows
    ]


def get_all_trades_b() -> list[dict]:
    """Every Broker B trade, oldest first -- for ad hoc querying/analysis (e.g. by the Broker
    subagent); not used by broker_b.py's own trading logic, which only needs the single open trade
    plus the per-forecast dedup check above."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT rule_name, trade_type, entry_price, open_ts, triggering_alerts, ta_forecast_id, "
            "exit_price, close_ts, pnl, status FROM broker_b_trades ORDER BY open_ts"
        )
        rows = cur.fetchall()
    return [
        {
            "rule_name": r[0],
            "trade_type": r[1],
            "entry_price": r[2],
            "open_ts": r[3],
            "triggering_alerts": r[4],
            "ta_forecast_id": r[5],
            "exit_price": r[6],
            "close_ts": r[7],
            "pnl": r[8],
            "status": r[9],
        }
        for r in rows
    ]
