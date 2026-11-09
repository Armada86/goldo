# Studies 25 and up

The detailed write-ups of studies 25 and up (studies 1-24 are summarised in `docs/studies.md`, which also holds the "Open / to repeat" list
and the monthly combined review method). Split out of `docs/studies.md` on 2 Nov 2026 because the single file was too large for the GitHub mobile app to render.
New studies (36 and up) go here.

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

- **Result (31 Oct 2026, combined review).** 14 shadow events in total. Four would-be levels $10 or more from price were later traded,
  and all four lost (-$31.97) with the live fade exit rule. Decision: real mid-day re-run **not built**; extra intraday levels stay out.

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
- **Fade spike cutoff, proposal (4 Nov 2026, one-off scripts, not in the repo; NOT applied, needs approval).** Trigger: the user asked whether a fade that
  spikes into its level and bounces should be blocked at all. Data set: Broker F's 43 live fades with `range_15m` and ATR logged (12 Oct-3 Nov) plus the 9 fade
  touches that ONLY the spike gate blocked (`broker_b_blocked`, replayed from the trigger price with the live exit rule on FOREX.com bid/ask, else Twelve Data mid;
  3 of 38 spike-blocked touches could not be replayed). 52 events, +$74.21 if nothing is blocked. "Gain" = minus the P/L of what a setting would have blocked.
  - Blocked touches alone (all rules, 35 replayed): +$54.98; spike-only 19 touches +$33.42 (8 wins +$89.54, 6 losses -$56.12, 5 no trade); spike-only fades 9 touches
    +$27.67 (5 wins, 2 losses). By range: touches at $20 or more net -$34.94 (7 trades), so the gate earned its keep there; $7-20 netted about +$90, so it cost money there.
  - Ceiling only (fades): $10 (live) +$13.27 (15 blocked, 7 wins); $11 -$5.01; $12-13 -$7.99; $14 +$15.66; $15 +$9.43; **$16-18 +$17.68 (3 blocked: 1 win, 2 losses)**; $20 +$2.67;
    $22-25 +$15.00 (1 event). Noisy: not a smooth plateau, only $14-18 is positive throughout.
  - ATR multiple only (fades): 1.5x -$8.79; 1.75x +$22.02 (7 blocked); **2.0x (live) +$6.99**; 2.5x +$2.67; 3.0x -$12.33.
  - Combined (ceiling OR multiple), best cells: ceiling $16-18 with the multiple at 2.5x or higher/off +$17.68; ceiling $14 + 2.0x +$13.77; the live pair ($10 + 2.0x) +$13.27;
    ceiling $16-18 + live 2.0x +$6.99 (the 2.0x multiple blocks winners the ceiling alone would not).
  - Reading: the live gate is positive on this set (+$13.27), so the earlier statement that it "costs money" only holds for the blocked touches alone. Differences between the
    best settings and the live one are $4-5 on 3 events, well inside noise.
  - **Proposal (for the 28 Nov review):** fades only, replace the flat $10 ceiling with **$16** (`SPIKE_RANGE_MAX_DOLLARS` for fades; breakouts keep $10) and set `spike_fade`
    back to **2.5-3.0** (the 2.0x multiple is the part that costs most). Expected gain over live: about +$4 on this set, i.e. no proven edge; the case is that it blocks only
    ranges of $16 and more (the extreme-range losers: -$15 on 22 Oct, 28 Oct, 2 Nov) while freeing $10-16 fades, which won more than they lost. Applied: `config.SPIKE_FADE_RANGE_MAX_DOLLARS` = 16.0,
    `SPIKE_FADE_ATR_MULTIPLE` = 3.0 and a `block_rules` row (source `manual`) with `spike_fade` 3.0. The 28 Nov review confirms or reverts it on the trades since 4 Nov.
  - Check at the review: (a) fades opened since 4 Nov with range $10-16 (would the live gate have blocked them? what did they make); (b) blocked fade touches since 4 Nov with
    range $10-16 replayed the same way; (c) whether $14-18 still beats $10, using the same method and Broker F trades only.

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

## Study 36 (3 Nov 2026): Broker K's settings (RSI, ADX, ATR, take-profit, stop-loss, time stop)
36. **How has Broker K performed with its current settings, and can they be improved?** Observation only: no setting was changed. Rerun at every monthly combined review
    (`python broker_k_study.py --fetch`, then `python broker_k_study.py`; the script prints every table below).
  - *Data and engine:* live `broker_k_trades` (15 trades from 22 Oct to 3 Nov, 14 closed: **-$4.68**; 2 take-profit, 3 stop-loss, 8 time stop, 1 closed by a pause; 4 winners) and
    82,063 Twelve Data 1-minute mid bars (7 Sep - 3 Nov, 3,160 simulated polls). Same replay engine as `broker_k_backtest.py` (poll every 5 minutes 7am-5pm ET, indicators from
    the last 96 15-minute candles incl. the forming one, fill the next minute on the ask/bid with an assumed spread, side-correct bar scan, stop wins a same-bar tie, one trade at
    a time), plus a trailing-stop and ATR-scaled-limit option. The settings were chosen on 9 Sep - 22 Oct, so polls from 22 Oct on are out of sample for the current rule only;
    every other variant below was looked at on all the data. Studies use Broker K's own table (it is its own engine, so the Broker-F-only rule does not apply).
  - *Engine check:* of the 10 replay trades from 22 Oct 19:55 UTC on that the live bot could also have taken, all 10 were also live trades within one minute, same side; replay
    P/L was $19.6 against $13.7 live (about $0.60 a trade better, the live median spread is $0.60 against the assumed $0.40). The replay missed live #10 and #11 and has no polls for
    the last two hours of data (live #13-15). So the replay is faithful but flattering.
  - *The current rule (ADX >= 35, ATR $3-12, RSI 50-70 buy / 30-50 sell, TP $12, SL $8, 60 min), replayed:* +$47.9 on 81 trades (avg +$0.59, win 48%), positive in 6 of 9
    weeks, +$11.9 without its best three trades, maximum drawdown $43. Out of sample (22 Oct on): +$16.4 on 9 trades. The live result so far is -$4.68 on 14.
    **It is not distinguishable from zero:** standard deviation $8.48 per trade, standard error $0.94, t = 0.63; bootstrap 90% range of the total -$79 to +$168; 27% of resamples
    are <= 0. At a $0.80 spread and a 2-minute fill delay it falls to +$16.6 (-$19.4 without the best three); at $1.20 it is -$28.1. With no entry filter at all it loses $563
    (spread and noise). So the edge, if any, is smaller than the spread uncertainty.
  - *Is it the RSI direction or the market?* Gold fell from 4,335 (13 Sep) to 3,897 (1 Nov), -10%. Same ADX/ATR trigger with the side forced: always Buy -$55.6 (n=106), always Sell
    **+$65.3** (n=105, 7 of 9 weeks, +$29 without the best three); RSI side flipped on purpose -$46.0. The replay's sells made +$37.4 and its buys +$10.4. So the RSI side clearly beats
    the wrong side, but a plain "sell when ADX is high" did as well as the whole rule in this falling market, and nothing here separates trend-following from a short bias. (Live is the
    reverse so far: buys +$17, sells -$14 over 7 and 6 trades, which is noise.)
  - *One setting at a time (everything else as current; differences under about $50 are noise at this sample size, the standard error of the total is about $76):*
    - ADX minimum: 25 +$154.8 (n=207), 30 +$95.8 (139), **35 +$47.9 (81)**, 40 +$84.8 (48), 45 +$16.1, 50 +$37.8. Average per trade is similar (+$0.6 to +$1.8), so a lower minimum
      mostly adds trades, and the 22 Oct study found 30 "much weaker"; the ranking flipped with 3 more weeks, so it is not stable. Requiring ADX rising is clearly worse (-$55.9,
      2 of 8 weeks).
    - ATR: the $3 minimum never binds (0, 2 and 4 give identical results; ADX >= 35 already means ATR above 4). Maximum 8 +$24.9, 10 +$38.5, **12 +$47.9**, 15 +$28.6, none +$30.4. Keep.
    - RSI: raising the buy-side cap to 75 or removing it gives +$61.6 / +$55.6 (out of sample +$41.9 / +$52.4 on 15 / 23 trades, in sample +$19.6 / +$3.3), so the 70 cap neither
      clearly helps nor hurts; a floor of 55 or 60 loses (-$8.4, +$1.2), i.e. the 50-55 band is where about $56 of the replay profit came from. +DI/-DI agreement: +$15.1 (worse).
    - Take-profit / stop-loss: TP 8 -$47.7, 10 -$4.2, **12 +$47.9**, 15 +$46.4, 18 +$6.4, 20 +$14.0, 25 +$2.0; SL 5 -$39.5, 6 -$38.5, **8 +$47.9**, 10 +$60.6, 12 +$2.1, 15 +$41.8.
      Tight limits ($5-6 stop, $8-10 target) clearly lose to the spread, as the 22 Oct study found; between $12/$8 and $15/$10 the numbers jump around (SL 10 +$61, SL 12 +$2, SL 15
      +$42), which is noise, not a slope.
    - Time stop: 20 min -$23.4, 30 +$38.5, 45 +$1.5, **60 +$47.9**, 90 +$52.8, 120 +$51.3 (out of sample +$23.6 / +$20.7). Longer is marginally better, not meaningfully.
    - Cooldown after a close: 0 (current) +$47.9, 15 min -$28.8, 30 -$41.4, 60 -$51.2: waiting after a trade made the replay worse (consecutive trades in a trend help),
      but see the 3 Nov cluster below.
    - Entry hours: no pattern (7h -$11.5, 8h +$36.0, 9h +$32.0, 10h -$14.0, 13h -$34.4, 15h +$15.3); a last-entry-hour cutoff of 16, 15, 14 or 13 gives +$47.7, +$32.4, +$27.9,
      +$62.3 (nothing consistent).
  - *Exit structure:* trailing stops (activation 4-10, distance 4-8, with TP 12, 20 or none) ranged from -$19 to +$32 against +$47.9 for the fixed exit, always below it. Limits scaled
    to ATR (TP 1-2x ATR, SL 0.7-1.5x) ranged from -$43 to +$16. Neither is an improvement.
  - *Live observations worth knowing (not replay):* (a) live #10 was opened at 16:55 ET on a Friday with a **$3.04 spread** (normal is $0.40-0.66), five minutes before the close, and was
    held across the weekend (closed at the time-stop level next session); (b) live #13, #14 and #15 on 3 Nov were three Buy entries in 16 minutes (14:10, 14:20, 14:26 UTC) into a
    reversal: -$8, -$8 and #15 still open when this was written, since the rule re-enters at the next poll after any close; (c) live #1 and #10 were closed retroactively next morning
    (905 and 3,785 minutes between open and the stored `exit_context`), which is by design; (d) live spread median $0.60.
  - *Variants evaluated in the whole run: 79* (about 40 distinct settings plus combinations), on 81 trades. The best cells (ADX 25 +$155, SL 10 +$61, RSI cap off) are what a random search
    on noise would also produce, and several flip sign between neighbouring values, so none is a finding.
  - *Conclusion and suggestions (nothing changed):*
    1. **Keep every setting.** No variant is both better and stable (in sample, out of sample, most weeks, and without its best trades). The current numbers are not shown to be wrong, and not shown to be right.
    2. The evidence problem is sample size, not the settings: to see a +$0.60 average through $8.5 of noise a standard error under $0.3 is needed, about 800 trades; Broker K makes about 2 a day, so that is most of a year.
       Judge it on the live record, a month at a time, and do not tune it until there are several hundred live trades.
    3. Two inexpensive protections that do not depend on the fitted numbers, **proposed; approved by the user and built the same day (3 Nov 2026), live in `broker_k.py`**: skip an entry when the live bid-ask spread is above a cap (for example $1.50, K already
       records `spread`; live #10 paid $3.04, and the replay shows the result flips sign above about $1.00 average), and optionally pause entries for 15-30 minutes after two consecutive stop-losses
       (only as a risk limit: the replay says a flat cooldown costs money, so test it on the new live trades before adopting it).
    4. Record true bid/ask per trade (already done via `spread`) and keep a K-specific note of regime: the replay window was a 10% decline, so re-check whether the buys and sells are
       separately positive once gold has gone sideways or up.
  - *Built (3 Nov 2026, user approved):* `config.K_MAX_SPREAD` $1.50 (no entry on a wider spread; unknown spread fails open) and `K_PAUSE_AFTER_STOPS` 2 / `K_PAUSE_MINUTES` 30 (no entry for 30 minutes after two stop-losses in a row; a Telegram note on the second stop-loss; the Worker's `broker status` shows the pause). Replay of the pause on the same bars: +$47.9 -> +$37.8 (77 trades, 20 signals skipped), i.e. it costs about $10 as a risk limit; the spread cap cannot be replayed (no recorded bid/ask), so judge it on the live `entry_context.spread` of skipped and taken signals. `broker_k_study.py` replays the unprotected rule, so compare live results with that baseline.
  - *Next:* at the 28 Nov review rerun `broker_k_study.py` on fresh bars and the new live trades and check, without re-searching the grid: the out-of-sample total and t-statistic of the current rule;
    the live-versus-replay match; whether sells and buys are both positive; how often the spread cap and the stop-streak pause fired (log lines `Broker K: ... skipped` / `stop-losses in a row`) and what the skipped signals would have done; and the handful of settings that came out marginally ahead here
    (ADX minimum 25-30, time stop 90, SL 10, RSI cap) on the new weeks only.

## Study 37 (3 Nov 2026): does the DXY entry block help? (shadow-flag item, log only)
37. **Did blocking on DXY save money, or is DXY unrelated to whether these entries work?** Every Broker B / Broker F entry that the DXY gate blocked was replayed as if it had been taken.
  - *Scope (user rule, 3 Nov 2026): Broker A is not part of any study.* A first version of this study also replayed Broker A's 53 DXY blocks (+$41.63 DXY-only, p 0.43); that was withdrawn
    the same day and is not used. Only the shared `broker_b_blocked` rows (Broker B and Broker F trade the same levels) are read here.
  - *Data and engine:* `broker_b_blocked`, 7 rows whose reasons mention DXY (4 with DXY as the only reason). 52,066 Twelve Data 1-minute mid bars (28 Sep - 3 Nov; FOREX.com bid/ask only
    for the last ~2.8 days), the SLA replay (`stop_loss_analysis.py`) with the live exit rule ($20 stop, trail 3/10, +$20/$5 lock, fade rules for `TA-Zone-*`), 1 oz. Entry at the level
    price at the stored touch time. Rows that also tripped ADX or the spike gate (3 of the 7) would have been blocked anyway and are shown separately. Scratch scripts, not in the repo.
  - *Result (positive = the block cost that much profit):* DXY-only, 4 touches: **+$19.11** (3 wins, 1 loss, p 0.32). All 7 DXY-flagged: +$34.93 (4 / 2, p 0.15). With the older $10 stop /
    7-7 trail: +$17.48 and +$36.30. Spread is not subtracted (about $0.68 per trade, median of stored `entry_context.spread`, i.e. about $2.7 over 4 trades).
  - *Reading:* the DXY blocks on Broker B/F did not avoid losses; the blocked touches won 3 of 4. Four trades are far too few to conclude anything either way, so DXY is neither shown to help nor
    to hurt here. Limits: mid prices, approximate entry times, repeat blocks of one level may not be logged.
  - *Shadow-flag item (nothing built, nothing changed):* the blocked rows already accumulate in `broker_b_blocked`, so no new logging is needed to repeat this replay. A true "flag, don't
    block" shadow (a `shadow_dxy_adverse` key in `entry_context`) would need the DXY gate turned log-only, which changes behaviour and needs the user's approval. Until then the replay of new
    blocks is the shadow record.
  - *Next:* at the 28 Nov review rerun the replay on DXY blocks since 3 Nov (Broker B/F only, one at a time, minus the spread) and add them to the 4 above. Suggested decision rule: with at
    least ~20 DXY-only blocks in total and a positive result after spread, propose loosening or dropping the DXY gate.

## Study 38 (4 Nov 2026): Fibonacci levels in the TA forecast (live, to be judged)
38. **Do the Fibonacci levels (38.2 / 50 / 61.8% of the latest major daily swing) make the forecast's levels better?** Source: reference analysis 5 in
    `docs/technical-analysts/technical-analyst-forecast-log.md` (a third-party report whose Fib levels coincided with the cloud top and SMA50). Approved and live from the first TA run after
    PR #283 merged; it changes which zones get picked, so Broker B and Broker F levels can differ from before.
  - *What was built:* `ta_forecast_job._fib_swing_and_levels()`: highest high and lowest low of the last 30 finished daily bars (`FIB_LOOKBACK_DAYS`), skipped under 2 x daily ATR,
    candidates "Fib 38.2%" / "Fib 50%" / "Fib 61.8%" with weight 2 each, swing saved in `levels.indicators.fib`. A 60-day lookback was tried first and picked the 25 Aug peak ($832
    swing, every level $120+ from price), so it was cut to 30 on one day's output, not a backtest.
  - *Dry run 4 Nov 2026:* fade entries and stops unchanged (sell 4077/4073, buy 4050); Fib 38.2% joined the 4077 zone as "nearby", Fib 50% merged with the Jul 30 swing high into 4120/4119,
    changing the targets.
  - *Next (28 Nov review):* from `ta_forecasts.levels` since the first Fib run, count zones whose labels contain "Fib", and how many were a fade entry or target. Then, from Broker F's live
    trades (`forex_b_trades`, joined on `ta_forecast_id` and the scenario's zone), compare trades at Fib-labelled levels with the rest (win rate, P/L, replayed with the live exit rule, spread
    subtracted); also check whether targets that include a Fib zone were reached more often (the existing grading in `levels.review`). Expect few trades; do not decide on fewer than about 20
    at Fib levels. Options: keep, change the lookback (20-60 days), or remove. Nothing is changed without the user's approval.
  - *Added 9 Nov 2026 (user request) -- do the Fib levels pull the two fade entries too close together?* Observed on TA2 of 9 Nov (forecast id 102, 11:00 ET): price 4122.11, `sell_resistance`
    entry 4122.21 was a lone `Fib 61.8%` zone (swing 4280.56 on 28 Sep to 3866.02 on 2 Nov), `buy_support` entry 4120.43 (top edge of the 4115.82-4120.43 zone), so the two fades were
    only **$1.78** apart (TA1 the same morning: $4.87). Without the Fib the next resistance was 4142.67, about $22 away. Cause: nothing compares the nearest resistance entry with the nearest
    support entry; the zone weights only settle conflicts between zones on the SAME side (`_pick_side()`), so lowering the Fib weight would not have changed it. For the 28 Nov review: (1) from
    `ta_forecasts.levels` since the first Fib run (4 Nov), list the gap between the `sell_resistance` and `buy_support` entry prices of every forecast, and mark the forecasts where a Fib-only
    zone (labels all start with "Fib") is one of the two entries; (2) compare that gap with the forecasts before 4 Nov and with the same forecasts re-picked without the Fib candidates;
    (3) for the trades at those close pairs (`forex_b_trades`, joined on `ta_forecast_id`) look at win rate and P/L with the live exit rule, spread subtracted, since the opposite-level profit lock sits
    only that gap away. Options to decide then (nothing changed now): a minimum gap between the two fade entries (about $8-10), keeping a lone Fib out of the fade-entry role, or leaving it.
  - *Switched to shadow 9 Nov 2026 (user request):* `ta_forecast_job.FIB_LIVE = False`. From the next TA run the Fibs no longer enter the zone candidates (so Broker B and Broker F levels are the pre-4-Nov kind again) and
    each forecast stores `levels.indicators.fib` (swing + the three levels, `live: false`) and `levels.fib_shadow` (`used` / `with_fib` sell and buy entry prices, their gap, `changed`). At the 28 Nov review: how
    often `changed` was true, the gap in those cases, and a replay of Broker F's trades at the levels the Fibs would have produced against the ones actually used. Forecasts 4-9 Nov were made with the Fibs live.

## Study 39 (4 Nov 2026): the stretched shadow flag (log only)
39. **Do entries taken when gold is already stretched (at the upper/lower Bollinger band, or far from its mean) do worse?** Source: the same reference analysis (price at the upper
    Bollinger band, +2.59% from SMA(20), "bull trap" warning). Related to the RSI/ADX exhaustion blocks (Broker B #45 bought a breakout at RSI 80, ADX 34), but an independent measure.
  - *Built (4 Nov 2026, user approved, log only):* `entry_context.shadow_stretched(trade_type, price)` adds `bb20_pos_1h`, `ext_sma20_1h_pct` and `shadow_stretched` to every Broker B and Broker F
    trade's `entry_context` at entry (one extra Twelve Data 1h call per entry; best effort, never raises; nothing is added when the candles can't be read). `bb20_pos_1h` = (price - SMA20) /
    (2 x stdev of the last 20 1h closes), so +1 is the upper Bollinger band (20, 2); `ext_sma20_1h_pct` = % from the 1h SMA(20). `shadow_stretched` is true for a Buy at
    `bb20_pos_1h >= SHADOW_STRETCHED_BB_POS` (1.0) or `ext >= SHADOW_STRETCHED_EXT_PCT` (2.0), and for a Sell at the mirror image. It blocks nothing, changes no stop or trail, sends nothing,
    and no gate, BRA, SLA or review reads it. Wired in `broker_b.check_broker_b_trades()` and `forex_watcher._try_open()`. Cutoffs are hand-picked (the report's numbers were on 5h candles), not calibrated.
  - *Next (28 Nov review):* on Broker F trades opened since 4 Nov: flagged against unflagged (win rate, average P/L, replayed with the live exit rule, spread subtracted), breakouts and fades
    separately; look at the raw values to see whether other cutoffs (for example 0.8 or 1.5 in `bb20_pos_1h`, 1% or 3% in `ext_sma20_1h_pct`) separate winners from losers, and whether flagged
    trades are mostly the ones RSI/ADX already block. Strong trends sit at the band for long, so a block could remove good breakouts; turn it into a real block only if flagged trades are clearly
    losing on about 20 or more of them, otherwise remove the flag. Nothing is changed without the user's approval.

## Study 40 (5 Nov 2026): how the TA levels performed against the next 2 hours
40. **How did the support/resistance zones of every stored TA forecast perform against what gold did 0-2 and 1-2 hours later, and how could the level selection improve?** Triggered by the
    5 Nov 7 AM run, whose buy-support entry (4111) sat $4.7 above the morning's real low (4116.3 at 07:40 ET, retested at 08:00). Observation only: nothing was changed. It reads no trades
    (only `ta_forecasts` and gold bars), so the Broker-F-only rule does not apply. Script: `ta_levels_study.py` (`--fetch` downloads about 65,000 one-minute Twelve Data bars, ~2 minutes).
  - *Data and method:* all 95 weekday forecasts, 23 Sep to 5 Nov 2026 (94 usable), each with 4 resistance and 4 support zones; Twelve Data mid bars (the FOREX.com quote is about $0.7
    wider, so edges would shift by roughly $1). A zone is REACHED when a 1-minute bar trades through its entry edge (a resistance's low edge, a support's high edge) inside the window; then BOUNCE = price retreats $8 before it runs
    $8 through, BREAK = the reverse, DRIFT = neither in 90 minutes. A TURNING POINT is the window's high or low, counted when price then reverses $8 within an hour; its distance is measured to
    the nearest zone of that side. The chance baseline shifts the same forecast's zones by a random $15-40 either way (200 draws). The hours 1-2 window only counts zones not already reached in
    hour 1.
  - *Do the levels beat chance? Yes, but loosely.* Over 0-2 h, 163 confirmed turns: median distance to the nearest zone $5.5 against $11.4 for shifted zones; within $5 of a zone 45% against 28%; within
    $10 82% against 47%. Only 7% of turns fell inside a zone (zones are narrow). Hours 1-2 (138 turns): median $6.3 against $11.1; within $5 40% against 28%; within $10 72% against 47%.
  - *Only the nearest zone per side matters inside 2 hours.* Rank 1 was reached in 57% of windows (51 bounce / 43 break / 14 drift, 47% bounce); rank 2 in 8% (10/4/1); rank 3 in 1%; rank 4 never. Zones
    within $15 of price were reached 68% of the time, $15-30 in 15%, $30-50 in 4%, beyond $50 never. In hours 1-2 only 18% of rank-1 zones were reached for the first time then (8/6/3).
  - *Near misses are common.* Of in-play zones (within $30) that were not reached in 0-2 h, 22% came within $5 (resistance 26%, support 17%), in line with the 5 Nov morning.
  - *Sources (in-play zones within $30, 0-2 h; bounce rate among reached, n reached in brackets):* 4h swing 58% (45), pivot 55% (38), moving average 46% (26), daily swing 43% (54), round number 41% (32),
    prior-day high/low 32% (22), 20-day high/low 25% (8). Hours 1-2 are too thin to rank (4h swing 60%, daily swing 60%, pivot 62%, round number 50%, prior-day 40%; 5-15 reached each).
    More source labels did not mean a better level: zones with 1 label bounced 62% (45 reached), 2 labels 42%, 3 labels 40%. By TA run: TA1 49%, TA2 47%, TA3 43%, TA4 43% (differences are within noise).
    Fibonacci zones exist only in the last 2-3 forecasts, so there is nothing to say yet (study 38). Note `LABEL_WEIGHTS` in `ta_forecast_job.py` (which zone survives when two candidates are too close
    to both be listed) gives the 20-day high/low weight 3 and prior-day high/low weight 2, the two sources that bounced worst here, while 4h swings carry no weight at all.
  - *Systematic offset to the entry edge (confirmed turns, + = turned beyond the edge):* resistances median -1.2 (58% turned short of the edge, hours 1-2: -1.8, 63%); supports median +0.5 (48% short;
    hours 1-2: -0.7, 55%). By source of the nearest zone (turns within $12, 0-2 h): 20-day range +5.6 (n 7), 4h swing +1.1 (55), daily swing +0.4 (71), prior-day +0.2 (22), round number -1.1 (33), moving
    average -1.3 (35), pivot -1.3 (51); the inter-quartile range is about $8-10 wide for all of them, so the median shifts ($1-3) are small against the spread. The zone midpoint is not a better
    level than the entry edge (median |offset| $6.1 against $5.8).
  - *What this means for selecting levels (proposals only; none applied):* (1) The levels carry real information but are only good to about $5 (the median turn is $5.5 from the nearest zone), so a miss of that size is normal and not a sign of a bad forecast. (2) Rank beyond the nearest zone per side is irrelevant for a 2-hour
    horizon, so the third and fourth zones are only useful as targets. (3) `LABEL_WEIGHTS` could be re-ranked (4h swing and pivot up; prior-day and 20-day range down), but the evidence is 8-54 reached zones per
    source, so re-check it on more forecasts at the 28 Nov review before touching it. (4) Stacking more labels in one zone should not be treated as extra strength. (5) Not established: the "nearby"
    candidates absorbed into each zone were closer to the turn more often (median distance $2.8 against $5.8), but that comparison is unreliable because it has many more candidate points than the
    four zones; it needs a matched chance baseline before it means anything.
  - *Limits:* six weeks, 94 forecasts and 45-60 reached rank-1 zones per side, so every per-source number is a sample of tens; mid prices; the $8 bounce and the $5 near-miss definition are choices (not
    tuned); the last few forecasts include Fibonacci zones, the earlier ones do not.
  - *Next (28 Nov review):* rerun `python ta_levels_study.py --fetch` and compare with this table; once there are about 15 more forecast days, decide on re-weighting sources from their reach and bounce
    rates alone (still a price study, no trades). Nothing is changed without the user's approval.

## Study 41 (6 Nov 2026): Broker R's take-profit / stop-loss, +$7/-$4 shadow exit (log only)
41. **Would a wider target and tighter stop (+$7 / -$4) have done better than Broker R's strict +$5 / -$5, on the same entries?** Triggered by `broker_r_tp_sl_study.py` (grid replay of
    Broker R's first 30 trades, 5-6 Nov 2026). Broker R is its own engine with its own table (`broker_r_trades`), so the Broker-F-only rule does not apply (same exception as Broker K, study 36);
    it is read by nothing else. Observation only: the live exit stays +$5 / -$5 until the user approves a change.
  - *Replay result (30 trades, 5-6 Nov):* FOREX.com bid/ask bars reproduce all 30 booked outcomes (+$30). +$7/-$4 replays at +$56 ($1.87/trade, 53% wins, 11 min average hold, $10.06 P&L per hour in position);
    the mid-price replay (cost $0.50 assumed) gives +$58.6. The whole region TP 7-10 x SL 3.5-5 made +$41 to +$62; SL -$3 or tighter and TP above $9 were worse (longer holds, more skipped entries, lower P&L per hour);
    +$3 targets are close to break-even once spread is paid. t about 1.7-2.0, both days wide-range, grid searched on the same trades: a hint only.
  - *Built (6 Nov 2026, user approved, log only):* `broker_r.resolve_shadow_exits()` (called from `main.poll_once()` after Broker R, own `try/except`) scores `config.R_SHADOW_TAKE_PROFIT` (7) /
    `R_SHADOW_STOP_LOSS` (4) on every Broker R trade, open or closed, that has no shadow yet, and stores it in `broker_r_trades.shadow_exit` (JSONB: reason `TP`/`SL`/`EOD`, exit price and time,
    pnl, held minutes). Same entry, same side-correct 1-minute bars and stop-wins-a-tie rule as the live exit (`scan_exit()` now takes the two limits as arguments, defaults unchanged); a shadow still
    open at 5 PM ET closes at that bar's close (`EOD`), resolved at the next poll; trades older than 48 h whose bars cannot be fetched stay unresolved. It trades nothing, sends no Telegram, and the live
    exit, the dashboard and every other job ignore it. Bars are only fetched when a shadow is unresolved (normally for the first ~20 minutes after an entry).
  - *Next (28 Nov review):* `python broker_r_tp_sl_study.py --shadow` for the forward test (live against shadow on the same entries, paired difference per trade and its t-stat, by day, plus the overlap count:
    shadow trades still open when the next real entry happened, which the one-position rule would have skipped). Then `python broker_r_tp_sl_study.py --bidask` and the mid replay on all closed trades
    to re-check the grid region (is 7/4 still inside the best region? is SL 3.5-4.5 still better than 5? did TP 8-9 take over?). Judge on out-of-sample trades since 6 Nov only; trades closed by `stop trading`
    (`exit_reason` Stop) are excluded. A change of `R_TAKE_PROFIT` / `R_STOP_LOSS` also needs `brokerRStatusLines()` in the Worker and the Broker R text in `CLAUDE.md` / `docs/broker-r.md` updated.
    Switch only if the shadow is ahead with a paired t of about 2 or more on 100 or more trades (fewer: keep logging); nothing is changed without the user's approval.
