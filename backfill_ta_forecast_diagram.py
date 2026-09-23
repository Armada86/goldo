"""One-time backfill: renders diagram_svg for any ta_forecasts row that predates that column (every
row generated before the price-ladder diagram feature shipped). No API calls needed -- everything the
diagram needs (price, resistances, supports, scenarios) is already stored in that row's `levels` JSONB.
Run once (`python backfill_ta_forecast_diagram.py`, with DATABASE_URL set) -- init_db() below adds the
column if it's missing. Safe to re-run: it only touches rows still missing a diagram.

`--force` instead regenerates every row's diagram from its stored `levels`, including ones that already
have one -- not part of the normal backfill, but handy the same way after a render_diagram_svg() layout
change (e.g. the 2026-09-23 price-badge -> price-line change), to refresh already-generated diagrams
without waiting for their next scheduled forecast.
"""

import sys

from storage import get_connection, init_db
from ta_forecast_job import render_diagram_svg


def main(force: bool) -> None:
    init_db()
    with get_connection() as conn, conn.cursor() as cur:
        where = "" if force else "WHERE diagram_svg IS NULL"
        cur.execute(f"SELECT id, levels FROM ta_forecasts {where} ORDER BY ts")
        rows = cur.fetchall()
        if not rows:
            print("Every ta_forecasts row already has a diagram -- nothing to backfill.")
            return
        for row_id, levels in rows:
            svg = render_diagram_svg(
                levels["price"], levels["resistances"], levels["supports"], levels["scenarios"]
            )
            cur.execute("UPDATE ta_forecasts SET diagram_svg = %s WHERE id = %s", (svg, row_id))
            print(f"{'Regenerated' if force else 'Backfilled'} diagram for ta_forecasts row {row_id}")
    print(f"Done -- {len(rows)} row(s) updated.")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
