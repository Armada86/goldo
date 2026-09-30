"""Context snapshot recorded on every Broker A / Broker B trade at entry (30 Sep 2026).

Why: a trade row only said *what* happened (rule, price, result). Reviewing which rules and conditions
actually work -- RSI stretched or not, DXY confirming or not, time of day, how wide the spread and recent
range were, how close the next levels are -- meant re-deriving all of it after the fact from data that may
no longer be reachable. So each trade now carries an `entry_context` JSONB (`trades.entry_context` /
`broker_b_trades.entry_context`) captured at the moment it opened. Purely descriptive: nothing here gates,
changes, or delays a trade, and every piece is best-effort -- a failure to fetch one field just leaves it
out (or null), never raises into the trading path.

Fields (all optional): hour_et, weekday, rsi14, adx14, atr14 (ADX/ATR on the same 15-min candles as RSI,
logged first so their thresholds can be judged against real trade outcomes before anything gates on them),
dxy_change_15m, dxy_threshold_15m, bias_score, bias,
forecast_id, session, spread (ask - bid close), range_15m_bid / range_15m_ask (last 15 one-minute bars),
plus whatever the calling broker adds via `extra` (Broker A: the flagging indicators; Broker B: scenario,
trigger price, minutes between the touch and the poll that acted on it, distance to the next resistance /
support zone, zone width).
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import INTRAHOUR_SWING_ALERT_THRESHOLD, RSI_PERIOD

log = logging.getLogger("gold-monitor")

DISPLAY_TZ = ZoneInfo("America/New_York")
DXY_WINDOW_MINUTES = 15
BAR_WINDOW_MINUTES = 15


_UNSET = object()  # "caller didn't supply this" -- distinct from None ("caller tried and it failed")


def _num(v, digits: int = 4):
    try:
        return round(float(v), digits)
    except Exception:
        return None


def build_entry_context(
    now: datetime,
    *,
    rsi_value=_UNSET,
    dxy_readings=_UNSET,
    forecast=_UNSET,
    extra: dict | None = None,
) -> dict:
    """Never raises. `rsi_value`/`dxy_readings`/`forecast` are the caller's already-fetched values when it
    has them (avoids repeating a DB/API call); pass None to say "I tried and it failed" (recorded as
    missing, not retried here, so a flaky API can't delay the trade being written). Anything not supplied
    at all is fetched here, best-effort."""
    ctx: dict = {}
    try:
        local = now.astimezone(DISPLAY_TZ)
        ctx["hour_et"] = local.hour
        ctx["minute_et"] = local.minute
        ctx["weekday"] = local.strftime("%a")
    except Exception:
        pass

    try:
        if rsi_value is _UNSET:
            from data_fetcher import compute_rsi, fetch_gold_candles

            rsi_value = compute_rsi(fetch_gold_candles()["close"], period=RSI_PERIOD).dropna().iloc[-1]
        if rsi_value is not None:
            ctx["rsi14"] = _num(rsi_value, 2)
    except Exception as e:
        log.info("entry_context: RSI unavailable (%s)", e)

    try:
        from data_fetcher import compute_adx, compute_atr, fetch_gold_candles

        candles = fetch_gold_candles()  # 15-min, same series RSI uses
        ctx["adx14"] = _num(compute_adx(candles, period=RSI_PERIOD).dropna().iloc[-1], 2)
        ctx["atr14"] = _num(compute_atr(candles, period=RSI_PERIOD).dropna().iloc[-1], 2)
    except Exception as e:
        log.info("entry_context: ADX/ATR unavailable (%s)", e)

    try:
        if dxy_readings is _UNSET:
            from storage import get_recent_readings

            dxy_readings = get_recent_readings("dxy", DXY_WINDOW_MINUTES)
        if dxy_readings and len(dxy_readings) >= 2:
            ctx["dxy_change_15m"] = _num(dxy_readings[-1][1] - dxy_readings[0][1])
        ctx["dxy_threshold_15m"] = _num(INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][DXY_WINDOW_MINUTES])
    except Exception as e:
        log.info("entry_context: DXY unavailable (%s)", e)

    try:
        if forecast is _UNSET:
            from storage import get_latest_ta_forecast

            forecast = get_latest_ta_forecast()
        if forecast and forecast.get("levels"):
            levels = forecast["levels"]
            ctx["bias_score"] = _num(levels.get("bias_score"), 1)
            ctx["bias"] = levels.get("bias")
            ctx["session"] = levels.get("session")
            ctx["forecast_id"] = forecast.get("id")
    except Exception as e:
        log.info("entry_context: forecast unavailable (%s)", e)

    try:
        from price_bars import fetch_gold_bars

        bid = fetch_gold_bars(BAR_WINDOW_MINUTES, "bid")
        ask = fetch_gold_bars(BAR_WINDOW_MINUTES, "ask")
        ctx["spread"] = _num(ask["close"].iloc[-1] - bid["close"].iloc[-1], 3)
        ctx["range_15m_bid"] = _num(bid["high"].max() - bid["low"].min(), 2)
        ctx["range_15m_ask"] = _num(ask["high"].max() - ask["low"].min(), 2)
    except Exception as e:
        log.info("entry_context: bars unavailable (%s)", e)

    if extra:
        ctx.update({k: v for k, v in extra.items() if v is not None})
    return ctx


def level_distances(entry_price: float, forecast: dict | None) -> dict:
    """Distance from `entry_price` to the nearest forecast resistance zone above and support zone below
    (near edges), for Broker B's context."""
    out: dict = {}
    try:
        levels = (forecast or {}).get("levels") or {}
        res = [z for z in levels.get("resistances", []) if z["low"] > entry_price]
        sup = [z for z in levels.get("supports", []) if z["high"] < entry_price]
        if res:
            out["dist_to_resistance"] = _num(min(z["low"] for z in res) - entry_price, 2)
        if sup:
            out["dist_to_support"] = _num(entry_price - max(z["high"] for z in sup), 2)
    except Exception:
        pass
    return out
