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

