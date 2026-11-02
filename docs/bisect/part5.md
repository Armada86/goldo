## Study 28 (29 Oct 2026): did the TA bias predict direction?
28. **Forecast bias accuracy** (`ta_forecast_job.py`). Scored every stored forecast's bias against gold's move after it, then rebuilt the
    six-component score from Twelve Data history to test it on more than 82 samples.
  - Live (82 runs, 23 Sep - 29 Oct, gold fell about $300): hit rate 46% at +1h, 55% at +3h, 61% until the next run (always-short 55%),
    40% to 5pm ET (always-short 40%); day level 64% vs 60% for always-short. 79% of runs read Bearish or Neutral-to-bearish.
  - Rebuilt (447 decisions at 7/10/14 ET over 6.7 months, gold -$680): hit rate 49-50% at 1h, 3h and 5pm, correlation about 0.
    Price vs 1h EMA200, 4h EMA100 and daily SMA50 are bearish 63-77% of the time (slow filters, not a call); the fast three are coin flips.
    On 5 years of daily bars the slow filters do have an edge at about 20 days (SMA200 hit 61%, t=4.3) but flipped in the Apr-Oct selloff.
  - Scenarios (150 resolved, target 1 before stop): fades won 53%, breakouts 33% (bull breakout 25%); fades against the bias won 62%,
    with the bias 48%. The bias does not help pick a scenario.
  - Mean-reversion candidates (RSI(1h) extremes, stretch from EMA20 in ATR, fading the prior day) hit 56-59% at t about 2.5, but about 40
    tests were run, so these are hypotheses only.
  - Changed (29 Oct 2026): the forecast now also stores and prints `regime` (slow three, -3..+3) and `lean` (fast three, "No edge"
    unless their score is at least 2 either way; about half of runs); PRIMARY follows the lean (none on "No edge"); breakouts are graded
    triggered only after a 15-min close beyond the trigger (a wick-only touch is graded "not triggered (wick through ...)"). `bias` /
    `bias_score` are unchanged (dashboard, study 26's shadow flag). Broker B / Broker F entries are not changed.
  - Grading (built 29 Oct 2026, `ta_bias_grading.py`, table `ta_bias_grades`): every forecast's combined bias, regime and lean are
    scored against gold's move at +1h, +3h, until the next run and until 5pm ET (15-min Twelve Data candles; moves under $2 are flat;
    shown next to the always-short baseline). New forecasts are graded by each TA run (plus one REVIEW line); the 82 older ones are
    backfilled by the `ta_bias_grading.yml` workflow (`python ta_bias_grading.py backfill` / `report`). Rows are `source` 'live'
    (regime/lean written with the forecast, the only out-of-sample test of the lean) or 'backfill' (recomputed, designed on that
    period) and the report keeps them apart. First look at the backfill: lean 62% at +3h and 60% to the next run (n=39-40), 34% at
    +1h (n=32); combined bias 47-53%; baseline short 41-58%. Too few to trust.
  - Not done yet: shadow-logging a mean-reversion lean. Judge the lean on 'live' rows once there are about 3 weeks of runs (around 19 Nov 2026).

## Combined result of the 31 Oct 2026 review (studies 25-27)
*Data:* Broker B 106 trades (to 30 Oct), Broker F live 47, Broker A 3 (155 of 156 replayable; B #25 has no prices); 84 forecasts, 166 blocked
rows, 14 shadow events; 54,063 Twelve Data 1-minute bars plus 4,000 FOREX.com bid/ask bars per side (28-30 Oct). *Engine check:* study 30
(+$151.82), study 32 (+$179.11) and the 29 Oct opposed-bias numbers (-$86.08 / +$44.52) reproduced exactly; study 34 baseline +$106.9 vs
+$101.72 and the 66-fade baseline +$199.37 vs +$191.6; study 31's +$131 not reproduced (+$62.75). Replay totals are not recorded P/L.
*Package test:* `spike_fade` 2.0 + blocking the flagged fades + a $25/$3 lock scored +$123 alone and +$78 one position at a time, with no
overlap between the parts. In-sample on 5 weeks, so expect much less. Adding the ADX-unblock (+$47 alone, -$4 with the others), breakouts
off, or extra levels cancels the gain.
Ranked suggestions, and what was done (user approved all four on 31 Oct 2026):
1. Lower `spike_fade` 3.0 -> 2.0 (+$39-52, medium-low confidence). **Done.**
2. Keep the bias/ADX flag shadow-only; decide at about 20 flagged fades. **Done (no change).**
3. Leave the profit lock at $20/$5. **Done (no change).**
4. Leave alone: ADX/ATR block, breakout rules, stop and trail settings, carry on when a TA run opposes a losing trade. **Done (no change).**

