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

- **Result (31 Oct 2026, combined review).** 14 shadow events in total. Four would-be levels $10 or more from price were later traded,
  and all four lost (-$31.97) with the live fade exit rule. Decision: real mid-day re-run **not built**; extra intraday levels stay out.

