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
    """Every trade, oldest first -- the source of truth docs/trades.md's table is regenerated from."""
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
