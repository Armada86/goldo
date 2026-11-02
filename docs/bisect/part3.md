### 26. Fade entry filters
*In plain words:* Some fade entries lose because the market is trending hard against them. Which should we skip: fades against the TA
bias, fades in a strong trend (high ADX), fades in wild volatility (ATR) or right after a spike? So far the current ADX/ATR block may
cost money, and the bias idea is too thin to act on.
- **Formerly 25 and 27 (bias).** Fade-buys at bias score -4 or lower held 2 of 5 and traded -$24.78; other buys held 5 of 6 (old study 25).
  Skipping fade-buys at bias <= -4 gave +$203.0 / +$36.0 against a +$191.6 / +$10.7 baseline (45 days / last week): weak, since the
  cutoff is fragile (-3 gives +$161.4, -5 gives +$188.0) (old study 27).
- **Formerly 31, re-entry after an ADX/ATR-blocked fade touch** (22 Oct 2026, one-off script, not in the repo). Question: when a fade
  level is touched but blocked by the ADX or ATR gate, and price then goes through the level and comes back, should we enter on the
  way back? Replayed all 259 fade-level touches of 22 Sep-22 Oct (every TA run, both fade levels) on Twelve Data 1-minute mid prices.
  Gates rebuilt from 15-min candles at the touch (current thresholds: ATR >= 12, ADX >= 25 and rising, fade RSI 68/32, spike gate); live
  fade exit rule, $0.60 spread, close at 5 PM ET. 24 touches were ADX/ATR-blocked (12 buys, 12 sells), 74 blocked by other gates, 161 clear.
  - Reclaim (dip of $0/$2/$5 beyond the level, then back through level +$0/$2/$4) and bounce (+$3/$5 off the low): best +$80.69 (18
    trades, 78% wins); bounce +$78.37 (24 trades). Variants that need a real dip leave only 2-9 trades.
  - The same rules on touches blocked by RSI/spike/DXY mostly lost (-$10 to -$97): no general "buy the reversal" edge.
  - **The block itself:** simply fading the 24 ADX/ATR-blocked touches at the level would have made +$131 (sells 9 of 12, +$86; buys 8 of
    12, +$45), more than any re-entry variant, with a win rate like unblocked touches (clear: 161 touches, +$189).
  - Caveats: 24 events in one month, each trade scored alone, mid prices, ADX/ATR rebuilt from 1-minute bars, touches may also be blocked
    by another gate, `block_rules` thresholds changed during the month but current ones were used throughout.
  - Decision: re-entry not built. The finding to re-check is that the ADX/ATR fade block may cost money.
- **Formerly 32, bias/ADX flag, shadow only** (23 Oct 2026; shadow flag is in the code). Trigger: Broker F #32 / Broker B #100, a
  TA-Zone-buy at 4050 into a -6 bias and ADX 30, stopped at -$15. Replayed 76 Broker B fades (24 Sep-23 Oct) with today's exit rule;
  replay baseline +$179.11 vs recorded +$73.28 (older trades ran under older stops), so only compare variants with each other.
  - Filter (fade against the bias by >= N points and ADX >= M; 62 fades have the stored bias and ADX): |bias| >= 6 and ADX >= 30 blocks
    2 (1 win) +$14; |bias| >= 4 and ADX >= 25 blocks 5 (1 win) +$30; |bias| >= 3 and ADX >= 30 blocks 3 +$29; |bias| >= 3 with no ADX
    condition blocks 15 (8 wins) +$25, so the ADX part does the work. Only 3-5 trades are affected.
  - Decision: built as **shadow only** (`config.SHADOW_BIAS_ADX_MIN_BIAS` 4, `SHADOW_BIAS_ADX_MIN_ADX` 25; `entry_context.shadow_bias_adx()`):
    every Broker B and Broker F fade gets `shadow_bias_adx` true/false in its `entry_context` (missing bias or ADX = no key); nothing is
    blocked. Compare with `SELECT entry_context->>'shadow_bias_adx' AS flag, count(*), sum(pnl) FROM broker_b_trades WHERE rule_name LIKE
    'TA-Zone-%' AND entry_context ? 'shadow_bias_adx' GROUP BY 1` (and `forex_b_trades`, `mode = 'live'`).
- **Range / ATR spike rule** (22 Oct review of the $10 spike ceiling; 91 Broker B trades 22 Sep-22 Oct plus Broker F's 30 live trades;
  one-off script, not in the repo). The flat ceiling is on a plateau: blocking at $8-15 gains $77-99 for Broker B, best $9 (+$99), $10
  +$77, $13 +$82; fades alone prefer $13-15 (+$78 vs +$47 at $10); breakouts lose (-$67) at any range; all of the $10 ceiling's gain came in
  the second half of the month. The 15-min range divided by ATR(14) separates trades better (B+F with ATR: up to 1.0x +$13 over 34 trades,
  1.0-1.5x +$119 over 35, 1.5x or more -$131 over 34), while the live multiples (fade 3.0x, breakout 2.0x) almost never fire. Found in
  the same sample, so untested.

- **Result (31 Oct 2026, combined review).**
  - ADX/ATR block: kept. The 13 touches blocked since 22 Oct would all have lost (-$99.72). Study 31's +$131 was not reproduced (+$62.75 on
    23 touches), so only the direction matched.
  - `shadow_bias_adx`: only 2 live trades flagged (1 independent event; -$6.33 on B, +$14.75 on F). Kept shadow-only; decide at about 20
    flagged fades (next review 28 Nov 2026). In the old sample the rule would have blocked 9 trades (7 events, 5 lost, $26-58 saved), but
    its cutoffs were picked from those trades. A separate "against the TA bias" entry block is not built (same trades, no new information).
  - Range / ATR cutoff: works at about 2.0x, not 1.5x; the 1.5-2.0x band made about +$49 in replay. **Applied: `spike_fade` 3.0 -> 2.0**
    (9 trades in 6 events, +$39-52 expected, medium-low confidence; `block_rules` row id 26, source `manual`, `config.SPIKE_FADE_ATR_MULTIPLE`
    = 2.0). `spike_breakout` stays 2.0.

