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

## Open / to repeat
- **Review the re-run shadow log on about 24 Oct 2026 (one week of data).** Run `python ta_rerun_shadow.py report 7`,
  check how many events fired, how many had `differs`, and how price behaved around the would-be levels. Then decide
  whether to build a real re-run (narrow version: scheduled runs unchanged, re-run only with 1h swings and session
  high/low, 60-minute cooldown, at most 3 a day, only when the levels change).
- **Re-run studies 25-27 in about two weeks (around 31 Oct 2026)** with more trades, before changing the bias gate, the
  stops or the levels.
