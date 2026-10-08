"""Broker B's entry-filter thresholds (the "block rules"): DXY, ADX, RSI, ATR and the spike gate.

The values live in the Postgres `block_rules` table (append-only; the newest row is the active one) so the block
rules analysis (block_rules_analysis_job.py, Telegram "start BRA") can change them without a code commit.
config.py holds the defaults the table was first seeded from, and stays the fallback for any value the table
can't supply (table missing or empty, database unreachable): broker_b.py behaves exactly as before in that case.

    DXY   dxy_threshold          skip a Buy if DXY rose / a Sell if it fell by at least this over 15 min, on 3 polls in a row
    ADX   adx_trending           fades (TA-Zone-*) blocked at ADX >= this while ADX is still rising
          adx_chop               breakouts (TA-Breakout-*) blocked at ADX < this (never below config.ADX_CHOP_FLOOR, 20)
    RSI   rsi_overbought         TA-Breakout-buy blocked at RSI >= this
          rsi_oversold           TA-Breakout-sell blocked at RSI <= this
          fade_rsi_overbought    TA-Zone-sell blocked at RSI >= this
          fade_rsi_oversold      TA-Zone-buy blocked at RSI <= this
    ATR   atr_max                every rule blocked when 15-min ATR(14) >= this ($)
    Spike spike_fade             fades (TA-Zone-*) blocked when the last 15 one-minute bars' entry-side range >= this x ATR(14)
          spike_breakout         breakouts (TA-Breakout-*) blocked at this multiple (tighter: they follow the move, fades want it)

A value of 99 / 0 / 101 (see OFF_VALUES below) effectively switches a block off."""

import logging

from config import (
    ADX_CHOP_FLOOR,
    ADX_CHOP_THRESHOLD,
    ADX_TRENDING_THRESHOLD,
    ATR_HIGH_VOLATILITY_THRESHOLD,
    FADE_RSI_OVERBOUGHT_THRESHOLD,
    FADE_RSI_OVERSOLD_THRESHOLD,
    INTRAHOUR_SWING_ALERT_THRESHOLD,
    RSI_OVERBOUGHT_THRESHOLD,
    RSI_OVERSOLD_THRESHOLD,
    SPIKE_BREAKOUT_ATR_MULTIPLE,
    SPIKE_FADE_ATR_MULTIPLE,
)

log = logging.getLogger(__name__)

DXY_CONFIRM_WINDOW_MINUTES = 15  # the swing window whose threshold the DXY gate reuses by default

RULE_KEYS = (
    "dxy_threshold",
    "adx_trending",
    "adx_chop",
    "rsi_overbought",
    "rsi_oversold",
    "fade_rsi_overbought",
    "fade_rsi_oversold",
    "atr_max",
    "spike_fade",
    "spike_breakout",
)


SPIKE_KEYS = ("spike_fade", "spike_breakout")  # added 8 Oct 2026: older block_rules rows have NULL here

# Values that switch a block off, per rule.
OFF_VALUES = {
    "dxy_threshold": 99.0,
    "adx_trending": 99.0,
    "adx_chop": 0.0,
    "rsi_overbought": 101.0,
    "rsi_oversold": 0.0,
    "fade_rsi_overbought": 101.0,
    "fade_rsi_oversold": 0.0,
    "atr_max": 99.0,
    "spike_fade": 99.0,
    "spike_breakout": 99.0,
}


def default_rules() -> dict[str, float]:
    """The rules as configured in config.py / intrahour_swing_thresholds.json (the fallback)."""
    return {
        "dxy_threshold": float(INTRAHOUR_SWING_ALERT_THRESHOLD["dxy"][DXY_CONFIRM_WINDOW_MINUTES]),
        "adx_trending": float(ADX_TRENDING_THRESHOLD),
        "adx_chop": float(ADX_CHOP_THRESHOLD),
        "rsi_overbought": float(RSI_OVERBOUGHT_THRESHOLD),
        "rsi_oversold": float(RSI_OVERSOLD_THRESHOLD),
        "fade_rsi_overbought": float(FADE_RSI_OVERBOUGHT_THRESHOLD),
        "fade_rsi_oversold": float(FADE_RSI_OVERSOLD_THRESHOLD),
        "atr_max": float(ATR_HIGH_VOLATILITY_THRESHOLD),
        "spike_fade": float(SPIKE_FADE_ATR_MULTIPLE),
        "spike_breakout": float(SPIKE_BREAKOUT_ATR_MULTIPLE),
    }


def get_block_rules(as_of=None) -> dict[str, float]:
    """The active rules: the newest block_rules row (or, with `as_of`, the newest one set at or before that time --
    what the dashboard shows for a past forecast run), with config.py's value for anything the table can't supply
    (so a missing table or a database hiccup never stops Broker B from trading)."""
    rules = default_rules()
    try:
        from storage import get_block_rules_row

        row = get_block_rules_row(as_of)
    except Exception as e:
        log.warning("block_rules table unreadable (%s); using config.py values", e)
        return rules
    if row:
        rules.update({k: v for k, v in row.items() if k in RULE_KEYS and v is not None})
        if as_of is not None:  # a row from before the spike gate existed had it off, whatever the config default says now
            for k in SPIKE_KEYS:
                if row.get(k) is None:
                    rules[k] = OFF_VALUES[k]
    if as_of is None:  # live gate only: a past run's display shows what was really in force then
        rules["adx_chop"] = max(rules["adx_chop"], float(ADX_CHOP_FLOOR))
    return rules
