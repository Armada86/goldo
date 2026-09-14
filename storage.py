"""Postgres time-series store for polled prices (shared by the cloud poll job and dashboard)."""

import os
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")


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
