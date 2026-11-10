"""Side-by-side price ladder: Broker B / Broker F's forecast levels on the left, Broker R's short-range levels on the right,
drawn on ONE shared price axis (10 Nov 2026, user request). Each side keeps its own analysis price as a hairline across its
half, so the two starting prices and the two sets of levels can be read against the same prices. Reuses the colors and
zone formatting of ta_forecast_job.render_diagram_svg()."""

import math
from html import escape

from ta_forecast_job import (
    DIAGRAM_COLOR_INK, DIAGRAM_COLOR_LIVE, DIAGRAM_COLOR_MUTED, DIAGRAM_COLOR_PRICE, DIAGRAM_COLOR_RESISTANCE,
    DIAGRAM_COLOR_RULE, DIAGRAM_COLOR_SUPPORT, DIAGRAM_MAX_ZONES_PER_SIDE, DIAGRAM_MIN_LABEL_GAP, DIAGRAM_PLOT_HEIGHT,
    DIAGRAM_PRICE_DOT_RADIUS, DIAGRAM_TOP, _fmt_zone,
)

WIDTH = 560
LEFT_LABEL_X = 100      # left labels are right-aligned to this x
LEFT_BAND_X, BAND_WIDTH = 106, 130
AXIS_X = 260            # price tick labels, centered here
RIGHT_BAND_X = 284
RIGHT_LABEL_X = RIGHT_BAND_X + BAND_WIDTH + 6
LEGEND_HEIGHT = 36
MONO = "IBM Plex Mono, ui-monospace, monospace"


def _marks(results: list[bool]) -> str:
    out = []
    for i, won in enumerate(results):
        dx = ' dx="4"' if i == 0 else ""
        color = DIAGRAM_COLOR_SUPPORT if won else DIAGRAM_COLOR_RESISTANCE
        out.append(f'<tspan font-weight="700" fill="{color}"{dx}>{"✓" if won else "✗"}</tspan>')
    return "".join(out)


def render_compare_svg(
    left: dict, right: dict, live_price: float | None = None,
    left_title: str = "Broker B / F", right_title: str = "Broker R", tick_step: int | None = None,
) -> str:
    """`left` / `right`: {"price", "resistances", "supports", "scenarios", "outcomes"} (outcomes = {scenario: {"results": [bool]}},
    same shape as render_diagram_svg's scenario_outcomes). The left side's breakout stop lines are drawn dashed; Broker R has none."""
    sides = []
    for data, is_left in ((left, True), (right, False)):
        sides.append((
            data, is_left,
            data["resistances"][:DIAGRAM_MAX_ZONES_PER_SIDE], data["supports"][:DIAGRAM_MAX_ZONES_PER_SIDE],
        ))

    values = [live_price] if live_price is not None else []
    stops_by_side = []
    for data, _is_left, res, sup in sides:
        values.append(data["price"])
        values += [v for z in res + sup for v in (z["low"], z["high"])]
        sc = {s["name"]: s for s in data["scenarios"]}
        stops = [s for s in (sc.get("sell_resistance", {}).get("stop"), sc.get("buy_support", {}).get("stop")) if s is not None]
        stops_by_side.append(stops)
        values += stops
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.1, 5.0)
    lo, hi = lo - pad, hi + pad
    span = hi - lo

    def y_of(p: float) -> float:
        return DIAGRAM_TOP + (hi - p) / span * DIAGRAM_PLOT_HEIGHT

    step = tick_step or (100 if span > 500 else 50 if span > 200 else 25 if span > 80 else 10 if span > 40 else 5)
    ticks, t = [], math.ceil(lo / step) * step
    while t <= hi:
        ticks.append(t)
        t += step

    parts: list[str] = []
    # Faint full-width gridlines + the shared axis labels in the middle.
    for tk in ticks:
        y = y_of(tk)
        parts.append(
            f'<line x1="{LEFT_BAND_X}" x2="{RIGHT_BAND_X + BAND_WIDTH}" y1="{y:.1f}" y2="{y:.1f}" stroke="{DIAGRAM_COLOR_RULE}" '
            f'stroke-width="0.5" stroke-dasharray="2 3"/>'
            f'<text x="{AXIS_X}" y="{y + 3:.1f}" font-size="9" fill="{DIAGRAM_COLOR_MUTED}" text-anchor="middle">{tk:,.0f}</text>'
        )

    for (data, is_left, res, sup), stops in zip(sides, stops_by_side):
        band_x = LEFT_BAND_X if is_left else RIGHT_BAND_X
        label_x = LEFT_LABEL_X if is_left else RIGHT_LABEL_X
        anchor = "end" if is_left else "start"
        outcomes = data.get("outcomes") or {}
        title = left_title if is_left else right_title
        parts.append(
            f'<text x="{band_x + BAND_WIDTH / 2}" y="14" font-size="11" font-weight="700" fill="{DIAGRAM_COLOR_INK}" '
            f'text-anchor="middle">{escape(title)}</text>'
        )

        rows = []  # (true_y, sort_price, color, label_svg_fn)
        for zone, is_res in [(z, True) for z in res] + [(z, False) for z in sup]:
            color = DIAGRAM_COLOR_RESISTANCE if is_res else DIAGRAM_COLOR_SUPPORT
            y_top, y_bot = y_of(zone["high"]), y_of(zone["low"])
            parts.append(
                f'<rect x="{band_x}" y="{y_top:.1f}" width="{BAND_WIDTH}" height="{max(3.0, y_bot - y_top):.1f}" rx="2" '
                f'fill="{color}" fill-opacity="0.22" stroke="{color}" stroke-width="1"/>'
            )
            name = "sell_resistance" if is_res and zone is res[0] else "buy_support" if not is_res and zone is sup[0] else None
            marks = _marks(outcomes.get(name, {}).get("results", [])) if name else ""
            tip = escape(", ".join(zone.get("labels", [])))
            rows.append((
                (y_top + y_bot) / 2, zone["low"], color,
                lambda ly, z=zone, c=color, m=marks, tp=tip: (
                    f'<text x="{label_x}" y="{ly:.1f}" font-size="9.5" text-anchor="{anchor}" fill="{DIAGRAM_COLOR_INK}">'
                    f'<tspan font-family="{MONO}" font-weight="600" fill="{c}">{_fmt_zone(z)}</tspan>{m}<title>{tp}</title></text>'
                ),
            ))
        price = data["price"]
        y = y_of(price)
        parts.append(
            f'<line x1="{band_x}" x2="{band_x + BAND_WIDTH}" y1="{y:.1f}" y2="{y:.1f}" stroke="{DIAGRAM_COLOR_PRICE}" stroke-width="1.5"/>'
        )
        rows.append((
            y, price, DIAGRAM_COLOR_PRICE,
            lambda ly, p=price: (
                f'<text x="{label_x}" y="{ly:.1f}" font-size="9.5" text-anchor="{anchor}" fill="{DIAGRAM_COLOR_INK}">'
                f'<tspan font-family="{MONO}" font-weight="700" fill="{DIAGRAM_COLOR_PRICE}">{p:,.2f}</tspan> start</text>'
            ),
        ))
        # Breakout/breakdown stop lines (Broker B / F only; R has none).
        sc = {s["name"]: s for s in data["scenarios"]}
        for stop, bname in ((sc.get("sell_resistance", {}).get("stop"), "bull_breakout"), (sc.get("buy_support", {}).get("stop"), "bear_breakdown")):
            if stop is None:
                continue
            ys = y_of(stop)
            parts.append(
                f'<line x1="{band_x}" x2="{band_x + BAND_WIDTH}" y1="{ys:.1f}" y2="{ys:.1f}" stroke="{DIAGRAM_COLOR_MUTED}" '
                f'stroke-width="1" stroke-dasharray="4 3"/>'
                f'<text x="{band_x + 2}" y="{ys - 2:.1f}" font-size="7.5" fill="{DIAGRAM_COLOR_MUTED}">{stop:,.0f}</text>'
            )
            marks = _marks(outcomes.get(bname, {}).get("results", []))
            if marks:
                parts.append(f'<text x="{band_x + BAND_WIDTH - 3}" y="{ys - 2.5:.1f}" font-size="10" text-anchor="end">{marks}</text>')

        # Label pass, top to bottom, with the usual anti-collision nudge (per side) and a dashed leader when a label moved.
        rows.sort(key=lambda r: -r[1])
        prev = None
        edge_x = band_x if is_left else band_x + BAND_WIDTH
        for true_y, _sp, color, fn in rows:
            natural = true_y + 3.3
            ly = natural if prev is None or natural - prev >= DIAGRAM_MIN_LABEL_GAP else prev + DIAGRAM_MIN_LABEL_GAP
            prev = ly
            if ly != natural:
                lx = label_x + 4 if is_left else label_x - 4
                parts.append(
                    f'<circle cx="{edge_x}" cy="{true_y:.1f}" r="1.6" fill="{color}"/>'
                    f'<line x1="{edge_x + (-2 if is_left else 2)}" x2="{lx}" y1="{true_y:.1f}" y2="{ly - 3:.1f}" stroke="{color}" '
                    f'stroke-width="0.75" stroke-opacity="0.6" stroke-dasharray="1.5 1.5"/>'
                )
            parts.append(fn(ly))

    if live_price is not None:
        parts.append(
            f'<circle cx="{AXIS_X}" cy="{y_of(live_price):.1f}" r="{DIAGRAM_PRICE_DOT_RADIUS}" fill="{DIAGRAM_COLOR_LIVE}"/>'
        )

    height = DIAGRAM_TOP + DIAGRAM_PLOT_HEIGHT + LEGEND_HEIGHT
    ly = height - 22
    live_legend = (
        f'<circle cx="326" cy="{ly + 5}" r="{DIAGRAM_PRICE_DOT_RADIUS}" fill="{DIAGRAM_COLOR_LIVE}"/><text x="336" y="{ly + 9}">Live</text>'
        if live_price is not None else ""
    )
    legend = (
        f'<g font-size="9" fill="{DIAGRAM_COLOR_MUTED}">'
        f'<rect x="106" y="{ly}" width="10" height="10" fill="{DIAGRAM_COLOR_RESISTANCE}" fill-opacity="0.5"/>'
        f'<text x="120" y="{ly + 9}">Resistance</text>'
        f'<rect x="184" y="{ly}" width="10" height="10" fill="{DIAGRAM_COLOR_SUPPORT}" fill-opacity="0.5"/>'
        f'<text x="198" y="{ly + 9}">Support</text>'
        f'<line x1="250" x2="264" y1="{ly + 5}" y2="{ly + 5}" stroke="{DIAGRAM_COLOR_PRICE}" stroke-width="1.5"/>'
        f'<text x="268" y="{ly + 9}">Start price</text>{live_legend}</g>'
    )
    return (
        f'<svg viewBox="0 0 {WIDTH} {height}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block" '
        f'font-family="IBM Plex Sans, Arial, sans-serif" role="img" aria-label="Broker B/F and Broker R levels on one price axis">'
        f'{"".join(parts)}{legend}</svg>'
    )
