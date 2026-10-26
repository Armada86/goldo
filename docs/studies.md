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

34. **26 Oct, +$20 profit lock** (103 closed Broker A/B trades, 45 days, 98 on mid prices; one-off script, not in the repo). Question: once a
    trade is $20 in profit, does trailing $5 (instead of the live $10) behind the peak pay? Baseline +$101.72. Trigger/trail: $12/$5 -$43.87,
    $15/$5 -$28.22, $18/$5 -$3.79, **$20/$5 +$7.67**, $22/$5 +$9.39, $25/$5 +$6.50, $30/$5 +$15.00, $35/$5 +$5.00; with a $3 trail
    $25 +$17.88. At $20/$5 nine trades change: six gain, three lose (B #1 -$14.44, A #4 -$3.03, B #101 -$0.92). Noisy, so not conclusive.
    Decision (user): live for every broker at $20/$5 (`config.PROFIT_LOCK_*`, `broker._exit_stop_offset()`); see Open / to repeat.

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

## 17 Oct 2026, weekly TA and fade-level study
25. **Weekly TA and fade study** (12-16 Oct): 17 forecast rows, 18 Broker B fade trades, 20 first level touches tested
    against 1-minute Twelve Data bars (mid prices).
    - Fades: 12 of 18 won, +$27.44 (sells 6/7 +$38.61, buys 6/11 -$11.18). Four -$15 stop-outs cost -$60.
    - 13 of 20 fade levels held at first touch (sells 6/9, buys 7/11).
    - Wednesday 14 Oct ($99 range): after the 10:00 run the levels sat $18 above and $28 below price, so there were zero
      fade trades after 8:11 AM. The generator only uses structural levels and has none near a new low.
    - Fade-buys at bias score -4 or lower held 2 of 5 and traded -$24.78; other buys held 5 of 6.
26. **BRA and SLA dry-runs** (read-only). Both said keep the current settings: BRA +$175.65 (100 events), SLA +$181.96
    (86 trades, 66 fades), the best setting being the live one (stop -$20, trail from +$3 at $10).
27. **Custom what-if on the 66 fades** (45-day total, baseline +$191.6; last week, baseline +$10.7 in the replay):
    - Skip fade-buys when bias <= -4: +$203.0 / +$36.0. Weak: the cutoff is fragile (-3 gives +$161.4, -5 gives +$188.0).
    - Wider stop (cap 20) or `max(15, 2.5 x ATR)`: +$223.1 / +$220.3. Not supported: the whole gain is trade B #85
      (without it +$2.7 and -$0.1).
    - Skip tight-gap fades, remove the lock, or use the next level as the lock: +$71 to +$135. The lock is worth about
      $120 over 45 days; keep it.
    - Caveat: the replay scored last week at +$10.7 against +$27.4 live (one position at a time, static lock level, mid
      prices for older trades).
    - Not testable from stored trades: level-generation fixes (intraday levels, automatic re-runs when price leaves the
      ladder, grading levels by touch).

28. **Level-gap fix: simulated re-run with extra intraday levels** (5-16 Oct, 1-minute bars, the real zone code; the sim
    reproduced all 32 published level sets exactly and scored the published levels at 45 trades, +$61 against +$112 live
    with the entry filters). The idea: add 1h swings, shorter 4h swings, session high/low and $25 round numbers to the
    level pool, and re-run the TA when gold is more than 2x ATR(15-min) from both fade levels.
    - Extra levels at the scheduled runs: noisy and inconsistent (all four +$25, single types -$4 to +$148, last week -$22).
    - Re-run only, no new levels: +$50. It cannot help: nothing nearby to pick.
    - Re-run with extra levels, scheduled runs left as published: +$47 to +$100 across 15 settings. The 12 that skip
      round-25 all beat the baseline by +$4 to +$39 (average about +$21). Wednesday 14 Oct itself improved by only $17-21.
    - Caveats: small sample, no entry filters, mid prices, and re-runs often picked identical levels (four in a row on
      Tue 13 Oct), which live would spam Telegram and re-arm each level's trade budget.
    - Decision: not built. Shadow logging added instead (study 29).
29. **TA re-run shadow log** (started 17 Oct 2026, `ta_rerun_shadow.py`, see `CLAUDE.md`). Log only: when gold is more
    than 2x ATR(15-min) from both fade levels, the poll records the re-run it would have started and the levels it would
    have produced (1h swings and session high/low added). It runs nothing and sends nothing. Read it with
    `python ta_rerun_shadow.py report 7`. Compare the real events with study 28: how often it fires, how often the
    levels would actually change, and whether price then traded at the would-be levels.
30. **Flip study: reverse a fade through the breakout level** (21 Oct 2026, one-off script, not in the repo). Question: when a
    fade (TA-Zone-sell/buy) is wrong and price keeps going through the scenario's `stop` (= the opposite breakout trigger,
    median $10 from the entry), would flipping to the other side make money? Replayed 99 fade trades from Broker B and
    Broker F (24 Sep-21 Oct) on Twelve Data 1-minute mid prices, $0.60 spread per simulated trade, current stops ($20
    initial, capped at $15 for fades; 3/10 trail). Simulated baseline +$151.82 (recorded +$93.57).
    - **Stop and reverse** (close the fade at the level, open the opposite there): worse in all 24 combinations of flip
      stop (8/10/15/20) and trail, with a $0 or $2 confirmation buffer: $196-$354 below baseline. With a $4 buffer
      $108 worse (9 flips, 3 wins); with $6 nothing flips.
    - Why: 23 of 99 fades crossed the level while open. 15 lost (-$214) but 8 recovered and won (+$95), net -$119 left
      alone; closing them at the level makes it -$251, and the flips lose on top.
    - **Flip after the fade's own loss** (fade runs its normal exit, flip at that price): 26 flips after a loss over $5,
      7-14 wins, added P/L -$21 to -$127 in every combination; after a loss over $10, 15 flips, -$35 to -$104.
    - Caveats: small sample, mid prices, each trade scored on its own (no one-position-at-a-time overlap), entry filters
      (RSI, ADX, spike gate) not applied to the flips.
    - Decision: not built. Consistent with Broker F's live breakouts (5 trades, -$10.22): consider turning the breakout
      rules off, to be re-checked with more trades.

31. **Re-entry after an ADX/ATR-blocked fade touch** (22 Oct 2026, one-off script, not in the repo). Question: when a fade
    level (TA-Zone-buy/sell) is touched but blocked by the ADX or ATR gate, and price then goes through the level and
    comes back, should we enter on the way back? Replayed all 259 fade-level touches of 22 Sep-22 Oct (every TA run, both
    fade levels) on Twelve Data 1-minute mid prices. Gates rebuilt from 15-min candles at the touch (current thresholds:
    ATR >= 12, ADX >= 25 and rising, fade RSI 68/32, spike gate); live fade exit rule ($20 stop capped at 15, 3/10 trail,
    near-level 10/5 trail, lock at the opposite level), $0.60 spread, close at 5 PM ET. 24 touches were ADX/ATR-blocked
    (12 buys, 12 sells), 74 blocked by other gates, 161 clear.
    - **Reclaim** (dip of $0/$2/$5 beyond the level, then back through level +$0/$2/$4) and **bounce** (+$3/$5 off the
      low): best +$80.69 (18 trades, 78% wins; dip >= $0, back through level +$4), bounce +$78.37 (24 trades). Variants that
      need a real dip leave only 2-9 trades (dip >= $2 +$2: 9 trades, +$57.11; dip >= $5 +$2: 2 trades, +$20.86).
    - The same rules on touches blocked by RSI/spike/DXY mostly lost (-$10 to -$97), so there is no general
      "buy the reversal" edge.
    - **The block itself:** simply fading the 24 ADX/ATR-blocked touches at the level would have made +$131 (sells 9 of 12,
      +$86; buys 8 of 12, +$45), more than any re-entry variant and a win rate like unblocked touches (clear: 161
      touches, +$189).
    - Caveats: 24 events in one month, each trade scored alone (no one-position-at-a-time), mid prices, ADX/ATR rebuilt
      from 1-minute bars, touches blocked by ADX/ATR may also be blocked by another gate, `block_rules` thresholds
      changed during the month but current ones were used throughout.
    - Decision: not built. The finding to re-check is that the ADX/ATR fade block may cost money, not the reclaim entry.

32. **Bias/ADX fade filter, shadow only** (23 Oct 2026, replay one-off script, not in the repo; shadow flag is in the code). Question
    (from Broker F #32 / Broker B #100, a TA-Zone-buy at 4050 into a -6 bias and ADX 30, stopped at -$15): should fades against
    the TA bias with high ADX be skipped, or stopped at the forecast's invalidation level? Replayed 76 Broker B fades (24 Sep-23 Oct)
    on Twelve Data 1-minute mid prices with today's exit rule ($20 stop capped at 15, 3/10 trail, lock); replay baseline +$179.11
    vs recorded +$73.28 (older trades ran under older stops), so only compare variants with each other.
    - **Filter** (fade against the bias by >= N points and ADX >= M; 62 fades have the stored bias and ADX): |bias| >= 6 and
      ADX >= 30 blocks 2 (1 win) +$14; |bias| >= 4 and ADX >= 25 blocks 5 (1 win) +$30; |bias| >= 3 and ADX >= 30 blocks 3 +$29;
      |bias| >= 3 with no ADX condition blocks 15 (8 wins) +$25, so the ADX part does the work.
    - **Stop at the invalidation level** (the fade scenario's own `stop`, $7-16 from the entry, median about $10): worse than
      the $15 cap in every variant, -$38 to -$107 (distance + $0 to + $5), because gold often dips through the far side and
      reverses. Saves $8 on B #100 but costs far more across the other 75.
    - Caveats: the filter blocks only 3-5 trades (about the sample the idea came from), mid prices, each trade scored alone.
    - Decision: invalidation stop not built. Filter built as **shadow only** (`config.SHADOW_BIAS_ADX_MIN_BIAS` 4,
      `SHADOW_BIAS_ADX_MIN_ADX` 25; `entry_context.shadow_bias_adx()`): every Broker B and Broker F fade gets
      `shadow_bias_adx` true/false in its `entry_context` (missing bias or ADX = no key), nothing is blocked and nothing is sent.
      Compare with `SELECT entry_context->>'shadow_bias_adx' AS flag, count(*), sum(pnl) FROM broker_b_trades WHERE
      rule_name LIKE 'TA-Zone-%' AND entry_context ? 'shadow_bias_adx' GROUP BY 1` (and `forex_b_trades`, `mode = 'live'`).

33. **TA re-run shadow log review** (24 Oct 2026, first week of study 29's log: 19-23 Oct, read straight from `ta_rerun_shadow`;
    gold outcomes from stored 5-minute `readings` until 5 PM ET, a level counted as touched within $0.50). Study numbered 33
    because 32 was taken by the bias/ADX shadow flag.
    - **Events:** 5 in 5 trading days (Mon 19 Oct 09:51 and 13:11 ET, Wed 21 Oct 15:02 and 16:02, Fri 23 Oct 11:01), about one a
      day, so the 3-a-day cap never bit. All 5 had `differs` (moved at least $3), as expected from adding 1h swings and session
      high/low. The old levels were $12-50 from price (resistance $19-50, support $12-24).
    - **Would-be levels vs price:** in events 1, 2, 3 and 5 at least one would-be level sat on or within about $4 of the current
      price (often a "1h swing low" or "session low" labeled as resistance), so "gold later touched it" is nearly automatic and
      says nothing about whether a fade there wins. The levels that were $15-20 away were not reached in events 1, 2 and 5 (the
      resistance), and event 3's was at price.
    - Caveats: 5 events, 5-minute readings can miss short touches, no P/L attached to any event, 3 of 5 fell in a slide from
      about 4150 to 4000.
    - Decision: real re-run **not built**; extra intraday levels in the scheduled runs stay out (study 28). Next step if wanted:
      replay these events with the live fade exit rule to see whether trades at the would-be levels win.

## Open / to repeat
Ordered by study number. Everything below is due on about **31 Oct 2026**; one Telegram reminder that day (9:00 AM ET) covers all of it.
(The 24 Oct review of the re-run shadow log is done: study 33.)

- **Studies 25-27 (weekly TA / fade study, BRA and SLA dry-runs, 66-fade what-if)**: re-run with about two more weeks of trades
  before changing the bias gate, the stops or the levels.
- **Studies 29 and 33 (TA re-run shadow log)**: run `python ta_rerun_shadow.py report 14` (or query `ta_rerun_shadow` over the Neon
  HTTPS endpoint from a cloud session, port 5432 is blocked there), add the new events to the 5 in study 33, and check whether any
  would-be level was $10+ from price and later traded, ideally by replaying the events with the live fade exit rule. Only then
  reconsider a real re-run (narrow version: scheduled runs unchanged, re-run only with 1h swings and session high/low, 60-minute
  cooldown, at most 3 a day, only when the levels change).
- **Study 30 (flip study)**: with the extra week of Broker B and Broker F trades, check whether the breakout rules (`TA-Breakout-*`)
  still lose (Broker F live: 5 trades, -$10.22 at the time of study 30) and decide whether to turn them off.
- **Study 31 (re-entry after an ADX/ATR-blocked fade touch)**: re-run the replay of blocked fade touches with the extra week of data
  (and the `broker_b_blocked` ADX/ATR notices), and check whether fading them would still beat the block. Only then consider relaxing
  the ADX/ATR fade block (`start BRA` tunes it) or building the reclaim entry.
- **Study 32 (bias/ADX shadow flag)**: count flagged and unflagged fades (SQL in study 32) in `broker_b_trades` and live
  `forex_b_trades`. The replay found 5 such trades in a month, so expect only a few flagged by then; if there are fewer than about
  20, keep collecting. If the flagged ones clearly lose, make it a real block (a rule in `broker_b.evaluate_gates()` shared with
  Broker F, ideally a `block_rules` value so `start BRA` can tune it); if not, remove the flag.
- **Study 34 (+$20 profit lock)**: with the extra week of closed trades, list every trade whose peak reached $20 and compare its real
  exit with what the old $10 trail would have given (replay with `config.PROFIT_LOCK_ACTIVATION` set very high), at other triggers
  ($22-30) and a $3 trail. Keep, retune or remove the lock.
- **Range / ATR spike rule (22 Oct review of the $10 spike ceiling; no study number yet)**: 91 Broker B trades 22 Sep-22 Oct plus
  Broker F's 30 live trades; one-off script, not in the repo. The flat ceiling is on a plateau: blocking at $8-15 gains $77-99 for
  Broker B, best $9 (+$99), $10 +$77, $13 +$82; fades alone prefer $13-15 (+$78 vs +$47 at $10); breakouts lose (-$67) at any range;
  all of the $10 ceiling's gain came in the second half of the month. The 15-min range divided by ATR(14) separates trades better
  (B+F with ATR: up to 1.0x +$13 over 34 trades, 1.0-1.5x +$119 over 35, 1.5x or more -$131 over 34), while the live multiples
  (fade 3.0x, breakout 2.0x) almost never fire. Found in the same sample, so untested. With the extra week: check whether a range /
  ATR cutoff near 1.5x (fades, and breakouts if they stay on) still separates winners from losers, how it compares with the $10
  ceiling (and $13 for fades) on out-of-sample trades, and whether to lower `spike_fade` (`start BRA` tunes it via `block_rules`)
  instead of keeping a flat dollar ceiling.
