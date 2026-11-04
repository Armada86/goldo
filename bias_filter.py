"""Telegram "start bias" / "stop bias" / "bias status": while the mode is on, the automatic brokers (A, B, F, K) may only open
trades in the direction of the latest TA forecast's combined bias (`levels.bias_score`, the same number the forecast prints as
"Bias"): score > 0 (Bullish, Neutral-to-bullish) -> Buy only; score < 0 (Bearish, Neutral-to-bearish) -> Sell only; score 0
(Neutral) -> no direction, so no new entry at all. It gates fresh entries only: an open trade is never closed or touched, and the
Telegram "buy/sell broker X" commands are the user's own explicit orders, so they are not gated.

The switch lives in the one-row `bias_setting` table (written by the Worker, read here each poll), stays on until "stop bias" (it
does not expire at 5pm), and fails open: a DB error, no forecast, or a forecast without a bias score means "no block".
"""

import logging

from storage import get_bias_mode, get_latest_ta_forecast_levels

log = logging.getLogger(__name__)


def allowed_direction(levels: dict | None) -> tuple[str | None, str, int | None]:
    """(allowed trade type or None, bias label, bias score) for a forecast's `levels`. The trade type is None for a Neutral (0)
    score; (None, "unknown", None) when the forecast has no readable score."""
    if not levels or levels.get("bias_score") is None:
        return None, "unknown", None
    score = int(levels["bias_score"])
    label = str(levels.get("bias") or "?")
    if score > 0:
        return "Buy", label, score
    if score < 0:
        return "Sell", label, score
    return None, label, score


def bias_confirms(trade_type: str) -> tuple[bool, str | None, str | None]:
    """(ok, dedup_category, detail) for a fresh entry of `trade_type` ("Buy"/"Sell"), shaped like the brokers' other gates. ok is True
    when the mode is off or anything can't be read. The category has no poll-varying numbers in it (Telegram blocked-notice dedup)."""
    try:
        active, _set_ts = get_bias_mode()
        if not active:
            return True, None, None
        forecast = get_latest_ta_forecast_levels()
    except Exception:
        log.exception("bias filter: could not read the bias mode or the forecast -- not blocking")
        return True, None, None
    if forecast is None:
        return True, None, None
    direction, label, score = allowed_direction(forecast.get("levels"))
    if score is None:
        return True, None, None
    if direction == trade_type:
        return True, None, None
    if direction is None:
        return False, "bias mode on: forecast bias is Neutral, no direction allowed", f"bias mode on: forecast bias is {label} (score {score}), no direction allowed"
    return (
        False,
        f"bias mode on: forecast bias allows {direction} only",
        f"bias mode on: forecast bias is {label} (score {score:+d}), {direction} only",
    )
