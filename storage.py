"""Postgres time-series store for polled prices (shared by the cloud poll job and dashboard)."""

import os
from datetime import datetime, timezone

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
