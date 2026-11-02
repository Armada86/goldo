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

### 27. Fade and breakout exits
*In plain words:* How should a trade be closed? Tests on the stop-loss size, the lock at the opposite level, reversing into the other
side after a failed fade, whether to run the breakout rules at all, and, on the winning side, whether to tighten the trail once a trade
is $20 in profit (now live). So far the lock is worth keeping, wider stops and flips do not pay, the breakout rules look bad, and the
+$20 lock is a small gain on a noisy sample.
- **Baseline: formerly 25 (results) and 26.** Fades 12 of 18 won, +$27.44 (sells 6/7 +$38.61, buys 6/11 -$11.18); four -$15 stop-outs
  cost -$60. BRA and SLA dry-runs (read-only) both said keep the current settings: BRA +$175.65 (100 events), SLA +$181.96 (86 trades, 66
  fades), the best setting being the live one (stop -$20, trail from +$3 at $10).
- **Formerly 27, custom what-if on the 66 fades** (45-day total, baseline +$191.6; last week, baseline +$10.7 in the replay):
  - Wider stop (cap 20) or `max(15, 2.5 x ATR)`: +$223.1 / +$220.3. Not supported: the whole gain is trade B #85 (without it +$2.7 and -$0.1).
  - Skip tight-gap fades, remove the lock, or use the next level as the lock: +$71 to +$135. The lock is worth about $120 over 45 days; keep it.
  - Caveat: the replay scored last week at +$10.7 against +$27.4 live (one position at a time, static lock level, mid prices for older trades).
- **Formerly 30, flip study: reverse a fade through the breakout level** (21 Oct 2026, one-off script). Replayed 99 fade trades from Broker B
  and Broker F (24 Sep-21 Oct) on Twelve Data 1-minute mid prices, $0.60 spread per trade, current stops. Simulated baseline +$151.82
  (recorded +$93.57).
  - Stop and reverse (close the fade at the level, open the opposite there): worse in all 24 combinations, $196-$354 below baseline. With a
    $4 buffer $108 worse (9 flips, 3 wins); with $6 nothing flips.
  - Why: 23 of 99 fades crossed the level while open. 15 lost (-$214) but 8 recovered and won (+$95), net -$119 left alone; closing them at
    the level makes it -$251, and the flips lose on top.
  - Flip after the fade's own loss: 26 flips after a loss over $5, 7-14 wins, added P/L -$21 to -$127 in every combination; after a loss over $10, 15 flips, -$35 to -$104.
  - Caveats: small sample, mid prices, each trade scored alone, entry filters not applied to the flips.
  - Decision: not built. Consistent with Broker F's live breakouts (5 trades, -$10.22): consider turning the breakout rules off. This is
    the only place the breakout rules (`TA-Breakout-*`) are questioned.
- **Formerly 32, stop at the invalidation level** (the fade scenario's own `stop`, $7-16 from the entry, median about $10): worse than the
  $15 cap in every variant, -$38 to -$107, because gold often dips through the far side and reverses. Saves $8 on B #100 but costs far
  more across the other 75. Not built.
- **Formerly 34, +$20 profit lock** (26 Oct 2026; 103 closed Broker A/B trades, 45 days, 98 on mid prices; one-off script). Once a trade
  is $20 in profit, does trailing $5 (instead of the live $10) behind the peak pay? Baseline +$101.72. Trigger/trail: $12/$5 -$43.87,
  $15/$5 -$28.22, $18/$5 -$3.79, **$20/$5 +$7.67**, $22/$5 +$9.39, $25/$5 +$6.50, $30/$5 +$15.00, $35/$5 +$5.00; with a $3 trail $25
  +$17.88. At $20/$5 nine trades change: six gain, three lose (B #1 -$14.44, A #4 -$3.03, B #101 -$0.92). Noisy, so not conclusive.
  Decision (user): live for every broker at $20/$5 (`config.PROFIT_LOCK_*`, `broker._exit_stop_offset()`). Continues the trailing-stop
  work of studies 12-15.
- **New TA run against an open losing trade** (29 Oct 2026, one-off script). When a trade is open and losing at the moment a new TA run
  lands and its bias score points the other way (a Buy with a negative score, a Sell with a positive one), should the bot close the
  trade and open the opposite one, close it and stay flat, tighten the stop, or carry on? Replayed on Twelve Data 1-minute mid prices with
  the live exit rule (stop 20, trail from +3 at 10, fade cap, lock, +$20 lock) and $0.60 spread per trade. All 144 closed Broker B (102)
  and Broker F live (42) trades; 34 were open when a TA run landed, 25 of those were losing, 13 of those against the new bias
  (6 B, 7 F; only 8 independent events, since B and F mostly took the same trade).
  - **Opposite bias plus a loss is a bad sign, but not a reason to act.** Those 13 recorded -$86.08 (5 wins); the 12 losing trades with
    agreeing or neutral bias recorded +$44.52 (6 wins). Four of the 13 were -$15 fade stop-outs and two -$20 breakout stop-outs.
  - **Per event (B and F merged): carry on -$30.33, close at the TA price -$45.72, stop and reverse -$36.48, tighten the stop to $3 / $5
    behind the TA price -$60.98 / -$44.14.** Nothing beats carrying on.
  - The flip's whole edge is one event, the 23 Oct breakout buy (TA2 turned bearish, gold then fell $28: flip +$20.8 per broker). Without
    it: carry on -$56.30, close -$58.81, flip -$84.25, tighten -$86.27 / -$77.43.
  - Why: leaving early does save money on the trades that go on to the -$15 stop (B #85, #107, F #15, #40: $5-10 each) but gives up the
    ones that recover (F #36 went from -$5.87 to +$16.49). Roughly a wash before the flip's extra spread and its own losses. Same
    answer as the breakout-level flip above: a reversal costs more than it earns.
  - Caveats: 8 events, mid prices, each trade scored alone (no one-position rule), the replay misses recorded P/L (B #82 +$7 vs +$22 real),
    bias score is only one reading of "the TA asks for the opposite".
  - Decision: not built. Keep the trade; recheck once there are about 20 independent events (add to the 31 Oct review's data set).

- **Result (31 Oct 2026, combined review).**
  - Breakout rules: about break-even, not losing: +$24.58 since 8 Oct, Broker F live +$27.51 on 10 trades, resting on 3-4 winners. Kept on.
  - +$20 profit lock: worth about +$12 over no lock, but only 4 trades since 26 Oct reached $20. Left at $20/$5.
  - BRA and SLA dry-runs: both keep the current settings. The last 19 Broker B fades lost about $91.
  - New TA run against an open losing trade: 10 independent events (aim was 20). Carry on still beats close and flip; without the 23 Oct
    event flip is -$105 against -$23 for carry on. Not built; recheck at about 20 events.

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

## Study 35 (2 Nov 2026): stops and trails against ATR, ADX and RSI (Broker F #48)
35. **Should the trail adapt to volatility and trend?** Prompted by Broker F #48 (TA-Zone-buy, bought 3899.51 at 12:03 UTC): best price only +$7.1,
    so the $3 / $10 trail moved the stop from -$20 to -$2.87 and it closed at 3896.64 (-$2.87) at 13:01; gold then ran to 3950 (+$53 from
    entry) by 13:52. The question: should a high-volatility or trending market get a wider trail?
  - *Data and engine:* 136 Broker B (88) and Broker F (48) trades with ATR/ADX/RSI in `entry_context`, 56,221 Twelve Data 1-minute mid bars (24 Sep - 2 Nov),
    the SLA replay (`stop_loss_analysis.py`, fade rules and +$20/$5 lock kept) with per-trade stop/activation/distance. Baseline (stop $20, trail 3/10): +$51.7
    (B +$10.5, F +$41.2; F recorded +$39.4). One position at a time per broker. Replay totals are not recorded P/L; B and F trade the same signals, so
    they are not independent samples.
  - *What #48 was:* ATR(14, 15m) 5.1 and ADX 14 at entry (4.9 and 12.7 at exit), a quiet range of 3896-3907 for 45 minutes. The volatility came after the
    stop-out, so no rule keyed on high ATR/ADX could have changed it.
  - *Widen when volatility or trend is high (the original idea): rejected.* ATR >= 6 -> distance $20: -$32.7; ATR >= 8: +$7.3; ADX >= 20 / 25 / 30 with a wider
    trail or later activation: -$29 to +$36, all below baseline. Trail or +$20 lock distance scaled by the live 15-minute high-low range: -$6 to +$6 (noise).
    ATR-scaled activation/distance grids (0.5-2x / 1.5-4x ATR) mostly below baseline; fixed wider trails jump around (distance 12 +$92, 15 +$56, 20 +$91).
  - *Quiet-market rule (candidate):* when entry ATR < $6 **and** ADX < 20, activate the trail at +$10 instead of +$3 (distance stays $10). +$178.4 vs +$51.7
    (B +$83.2, F +$95.2; 8 trades better, 2 worse; first half +$119, second half +$59 vs baseline +$106 / -$54; +$51 without the 3 best improvements).
    #48 closes +$24.3 instead of -$2.2 (it is still stopped by a $10 pullback inside the spike; not the full +$53). Fragile: ATR < 5 changes nothing, < 5.5 +$135,
    < 6 +$168, < 7 +$141; ADX < 20 alone +$125; ATR < 6 alone +$168. About 70 variants were tried on 136 trades, so some of this is a lucky fit.
  - *Looser trail after +$20 (candidate):* once best profit passes +T, trail D behind the best price instead of the $5 lock, the stop never moving back.
    T = 15 or 20 with D = $12 / $15 / $20: +$103 / +$134 / +$119 (D = 5 +$52, 8 +$47, 10 +$47); with the quiet-market rule +$230 / +$260 / +$245
    (base +$178). 8 trades better, 0-1 worse, but +$7 to +$10 without the 3 best, a step between D = 10 and 12 rather than a slope, and the second half
    stays negative (-$6.5 at best) on the live entry rule. It does not help #48 (the earlier $10 trail is already the stop in force; helping it would need a trail that
    replaces the earlier stop, i.e. can move back, not tested).
  - *RSI:* one quick test (RSI 40-60 -> 3/15, +$81); not followed up.
  - *Changed:* no stop, trail or entry rule. The log-only flag `shadow_quiet_market` (ATR < 6 and ADX < 20 at entry) was added to every Broker B and Broker F
    trade's `entry_context` on 2 Nov 2026 (`entry_context.shadow_quiet_market()`, `config.SHADOW_QUIET_MAX_ATR` / `_ADX`); it blocks and changes nothing.
  - *Next:* judge both candidates on trades opened since 2 Nov at the 28 Nov review (see "Open / to repeat").

## Open / to repeat
**Monthly combined review, every last Saturday of the month (set 2 Nov 2026). Next: Saturday 28 Nov 2026.** Studies 25, 26, 27, 28 and 35 (everything from 25 up) run
together as **one study with one result**, on one shared data set and one replay engine, as on 31 Oct. They are not scheduled or reported separately. Studies 1-24 are not part of it.
New studies from 36 up join this review the same way. Items to cover each time (the first review's list):
1. Study 26: the effect of `spike_fade` 2.0 on trades since 31 Oct (blocked fades and what they would have done).
2. Study 26: `shadow_bias_adx`: about 20 flagged fades? Then flagged vs unflagged; clearly losing = real block, else remove the flag.
3. Study 25: a new TA run against an open losing trade: add events until about 20 independent ones. Also the TA re-run shadow log (`ta_rerun_shadow`).
4. Study 27: breakout rules and the +$20 lock, rechecked with the new trades (both rest on a handful of winners).
5. Study 28: the lean on 'live' `ta_bias_grades` rows (about 3 weeks of runs by 28 Nov).
6. Study 35: the quiet-market rule (ATR < 6 and ADX < 20 -> trail activation +$10) and the looser trail after +$20 (D $12-15), on trades opened since 2 Nov; the
   `shadow_quiet_market` flag in `entry_context` marks them. Replay those trades, not the 136 of study 35.

**Broker B and Broker F trade the same levels: count them once (rule set 2 Nov 2026).** Both engines run the same rules on the same forecast row, so a level that
both traded is one trade, not two. Never add the profit or loss of both. Method (checked on 2 Nov data: 39 of Broker F's 48 live trades pair with a Broker B trade):
- A pair = same `ta_forecast_id`, same `rule_name`, same direction, opened within 30 minutes of each other, matched one to one (nearest open first; a level can trade
  twice per forecast, so match in order). In the 2 Nov data the matched opens differ by a median of 0.6 minutes (up to about 26), because Broker B acts on a 5-minute poll.
- A pair counts as **one trade, using Broker F's record** (the real demo fill and exit). Small differences in price or P&L between the two are Broker F lag or Broker B poll lag
  and are ignored, not added. A trade only one engine took counts as one trade of its own.
- Replays (stop, trail, filter and lock tests) use one path per level anyway; the pair rule matters for the **recorded** totals, trade counts and win rates, and for the
  sample sizes quoted in the result.
- Report how many pairs and how many unmatched trades there were, and list the unmatched ones (a level both traded more than 30 minutes apart would be counted twice).
- Caution: on the 39 matched pairs of 2 Nov, Broker F's recorded P&L sums to +$0.4 and Broker B's to -$43.7, so the two engines do not realise the same result on the same
  levels (different exits and poll lag). The combined study uses one record per pair, so state which one was used.

Results go into this file on a branch and PR, as for 31 Oct. The review changes no rule or setting by itself; any change needs the user's approval.
