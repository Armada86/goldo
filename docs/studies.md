# Studies and tests log

A running list of the studies, backtests and tests run on this project, with what each found and what changed.
Compiled 17 Oct 2026 from `CLAUDE.md`, the other docs and the git log, so a study that was never written down is
missing. Add new ones at the bottom of their section (or under "Open / to repeat").

## Threshold and alert calibration
1. **Intrahour-swing frequency test** (`frequency_test.py`, `docs/frequency-test-thresholds.md`). Replaced the original
   event-rate target/tolerance search with the average companion swing over 30 days; reruns every weekday morning.
2. **Co-flagging study** (same doc). How often the indicators move together, for the seven Broker A indicators and all
   eight. Led to `Consensus6of8` becoming `Consensus5of7` (US10Y dropped).
3. **Data-source comparisons** (`docs/data-sources.md`). Why spot gold comes from Twelve Data, why DXY and US10Y stay on
   yfinance 5-minute bars, FRED versus FMP, the FMP history cap.

## Broker A and B entry filters (each came from a live loss)
4. **25 Sep, Broker A sell loss.** Added the RSI filter and the strict DXY confirmation.
5. **25 Sep, Broker B double loss** (9 pm ET). Added trading hours, the DXY check, and the RSI check on breakouts.
6. **25-30 Sep, phantom and stale-touch fixes.** Retreat-before-touch rule, no retroactive fills for a new forecast,
   blocked touches consumed in full, 7-minute touch-age limit.
7. **29 Sep, Twelve Data versus FOREX.com mismatch** (an $11 gap on a take-profit). Candle scans moved to FOREX.com bid/ask.
8. **1 Oct, ADX filters.** Textbook cutoffs, still to be calibrated.
9. **2 Oct end-of-day review** (5 trades, 1 win). Added the ATR cap and fade RSI gates on 3 Oct.
10. **6 Oct fixes.** Removed the ADX waiver after B #45; tried and dropped an ADX direction version (+DI/-DI); DXY gate
    needs 3 consecutive adverse polls.
11. **8-9 Oct, spike gate.** Replay of 35 trades: +$25.75 to +$76.89. Fade multiple raised from 2.5 to 3.0 after a 2.59x
    support fade bounced.

## Exit-rule studies
12. **2 Oct, trailing stop backtest** (28 trades). Trailing stop +$42.46 against +$40 for fixed $10/$10; tighter trails and
    early breakeven locks lost money.
13. **5-7 Oct, re-arm rules.** Minimum win to re-arm a level set at $3 (after B #46/#47).
14. **9 Oct, fade exit replay** (60 trades). No trail +$62, plain trail +$148, lock plus ordinary trail +$187; a $15
    cutoff +$194.5.
15. **13 and 16 Oct, lock changes.** Dropped the floor at the level; added the near-lock trail ($5 once $10 in profit).
16. **12 Oct, exit-context backtest** (11 trades). Inconclusive for an RSI/ADX exit rule, so `exit_context` is now
    collected on every close for a later look.

## Tools these studies became
17. **SLA** (`stop_loss_analysis.py`). Parity with the live exit code checked on 4,000 random paths, 0 mismatches.
18. **BRA** (`block_rules_analysis.py`).
19. **`spike_gate_replay.py`.**
20. **End-of-day Broker review** (`broker_review.py`).

## Fundamental and TA research
21. **NFP** (12 releases), **ADP** (12 releases), **API crude oil** (52 weeks). ADP's 5-minute reaction followed the
    beat/miss direction 92% of the time, but that edge faded by +1h.
22. **Reference TA analyses** (collected 23 Sep, `technical-analyst-forecast-log.md`). Third-party analyses checked
    against Twelve Data to steer `ta_forecast_job.py`.
23. **Per-indicator logs** for GLD, IAU, GLDM, GDX, GDXJ, RING, DXY and US10Y (`docs/technical-analysts/`).

## Broker F and infrastructure tests
24. **FOREX.com demo checks.** The 22 Sep test buy, `forex_live_check.py` (attach, move, cancel, close), and
    `forex_watcher_sim.py` (two overlapping watchers on a scripted tick path).

## Studies 25-27 (12-26 Oct 2026): Broker B and F fades, levels, filters and exits
Ten separate studies (formerly numbered 25-34, plus an unnumbered range/ATR review) kept finding the same things from the same trades,
so on 26 Oct 2026 they were merged into three. Each merged study answers one question, is replayed once on one shared data set, and
gives one result. The old numbers are kept below as "formerly study N" so older notes and commits still make sense.

| New study | One question | Formerly |
|---|---|---|
| **25. Fade levels** | Are the fade levels often too far from price, and would extra levels or a mid-day re-run fix that? | 25 (levels part), 27 (last bullet), 28, 29, 33 |
| **26. Fade entry filters** | Which fades should be skipped (against the TA bias, high ADX/ATR, spikes)? | 25 (bias part), 27 (bias bullet), 31, 32 (filter), range/ATR review |
| **27. Fade and breakout exits** | How should a trade be closed: stop width, the lock, reversing, breakouts on or off, and the +$20 profit lock? | 25 (results), 26, 27 (stop, lock), 30, 32 (invalidation stop), 34 |

Studies 25-27 are not independent: a rule that skips fades (26) changes which trades exist to test exits on (27), and new levels (25)
change both. So the 31 Oct review (see "Open / to repeat") tests them together and reports one combined result.

### 25. Fade levels and the level-gap fix
*In plain words:* Sometimes price runs away and none of the fade levels is near it, so Broker B and F have nothing to trade for hours.
Can more levels, or re-running the forecast mid-day, fix that? So far: it might help a little, but the evidence is too noisy to build it.
- **Formerly 25 (levels).** Weekly TA and fade study, 12-16 Oct: 17 forecast rows, 18 Broker B fade trades, 20 first level touches tested
  against 1-minute Twelve Data bars (mid prices). 13 of 20 fade levels held at first touch (sells 6/9, buys 7/11). Wednesday 14 Oct
  ($99 range): after the 10:00 run the levels sat $18 above and $28 below price, so there were zero fade trades after 8:11 AM. The
  generator only uses structural levels and has none near a new low.
- **Formerly 28, simulated re-run with extra intraday levels** (5-16 Oct, 1-minute bars, the real zone code; the sim reproduced all 32
  published level sets exactly and scored the published levels at 45 trades, +$61 against +$112 live with the entry filters). Idea: add
  1h swings, shorter 4h swings, session high/low and $25 round numbers to the level pool, and re-run the TA when gold is more than 2x
  ATR(15-min) from both fade levels.
  - Extra levels at the scheduled runs: noisy and inconsistent (all four +$25, single types -$4 to +$148, last week -$22).
  - Re-run only, no new levels: +$50. It cannot help: nothing nearby to pick.
  - Re-run with extra levels, scheduled runs left as published: +$47 to +$100 across 15 settings. The 12 that skip round-25 all beat the
    baseline by +$4 to +$39 (average about +$21). Wednesday 14 Oct itself improved by only $17-21.
  - Caveats: small sample, no entry filters, mid prices, and re-runs often picked identical levels (four in a row on Tue 13 Oct), which
    live would spam Telegram and re-arm each level's trade budget.
  - Decision: not built. Shadow logging added instead.
- **Formerly 29, TA re-run shadow log** (started 17 Oct 2026, `ta_rerun_shadow.py`, see `CLAUDE.md`). Log only: when gold is more than 2x
  ATR(15-min) from both fade levels, the poll records the re-run it would have started and the levels it would have produced. Runs
  nothing and sends nothing. Read it with `python ta_rerun_shadow.py report 7`.
- **Formerly 33, first review of that log** (24 Oct 2026, 19-23 Oct, read from `ta_rerun_shadow`; gold outcomes from stored 5-minute
  `readings` until 5 PM ET, a level counted as touched within $0.50).
  - Events: 5 in 5 trading days (Mon 19 Oct 09:51 and 13:11 ET, Wed 21 Oct 15:02 and 16:02, Fri 23 Oct 11:01), about one a day, so
    the 3-a-day cap never bit. All 5 had `differs` (moved at least $3). The old levels were $12-50 from price (resistance $19-50,
    support $12-24).
  - Would-be levels vs price: in events 1, 2, 3 and 5 at least one would-be level sat on or within about $4 of the current price (often
    a "1h swing low" or "session low" labeled as resistance), so "gold later touched it" is nearly automatic and says nothing about
    whether a fade there wins. The levels that were $15-20 away were not reached in events 1, 2 and 5 (the resistance); event 3's was at price.
  - Caveats: 5 events, 5-minute readings can miss short touches, no P/L attached to any event, 3 of 5 fell in a slide from about 4150 to 4000.
  - Decision: real re-run **not built**; extra intraday levels in the scheduled runs stay out.
- **Formerly 27 (last bullet).** Level-generation fixes (intraday levels, automatic re-runs when price leaves the ladder, grading levels
  by touch) cannot be tested from stored trades alone; that is why 28 used a simulation.

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

## Open / to repeat
**One combined review, about 31 Oct 2026**, covers studies 25, 26 and 27 together (the scheduled routine "Goldo 31 Oct studies review").
It is a single piece of work with one report, not three:
1. **One shared data set**, fetched once: closed trades since 24 Sep from Broker B, Broker F (live) and Broker A, forecasts, blocked
   touches, the shadow log, 1-minute and 15-minute gold bars, alerts if needed.
2. **One shared replay engine** with the live exit rule (including the +$20 lock), reused for every question below.
3. **Study 25 (levels):** the new shadow-log events (`python ta_rerun_shadow.py report 14`); did any would-be level $10+ from price later
   trade? Recommend whether to build a real re-run.
4. **Study 26 (entry filters):** would fading the ADX/ATR-blocked touches still beat the block; flagged vs unflagged fades by
   `shadow_bias_adx` (fewer than about 20 flagged = keep collecting; if flagged clearly lose, real block, else remove the flag); does a
   15-min range / ATR cutoff near 1.5x still separate winners from losers vs the $10 ceiling (and $13 for fades); should `spike_fade` be lowered.
5. **Study 27 (exits):** do the breakout rules still lose (Broker F live was 5 trades, -$10.22); every trade since 26 Oct whose peak reached
   $20, real exit vs the old $10 trail; other triggers ($22-30) and a $3 trail; BRA and SLA dry-runs and the 66-fade baseline.
6. **Study 27 add-on (29 Oct 2026): new TA run against an open losing trade.** Re-run the carry on / close / flip / tighten comparison on every
   trade open when a TA run landed with the opposite bias (8 independent events so far; carry on won, flip's edge was the 23 Oct event alone).
   Report whether it now holds with the larger sample (aim for about 20 events). Also use the same trades to check the entry-side question
   from it: losing trades whose bias opposed them recorded -$86.08 (5 of 13 wins) against +$44.52 (6 of 12) for agreeing/neutral, so test a bias
   filter at entry together with the study 26 `shadow_bias_adx` flag, not as a separate answer.
7. **Combined result (the point of the review).** Because the three studies interact, the report must not be three separate answers:
   test the recommended package **together** in one replay (e.g. levels change + filter change + breakouts off + lock setting) against the
   live rules, and report the combined effect and each part's contribution, so gains that overlap are not counted twice. Then give one
   ranked list of suggestions that apply to all the studies at once: what to change, in what order, expected effect, confidence, what
   to leave alone and what to keep collecting data on. Results go into this file (a "Result" bullet under each of 25-27 and the
   combined summary) on a branch and PR, never merged automatically.
