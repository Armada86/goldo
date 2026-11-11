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
18. **BRA** (`block_rules_analysis.py`). From 4 Nov 2026 it also tunes the spike gate per family (fades and breakouts separately), each as a pair: ATR multiple x dollar ceiling, Broker B data only.
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

## Studies 25 and up
Studies 25 and up (Broker B and F fades, levels, filters, exits, the TA bias study, the quiet-market study, Broker K, the DXY block study, the Broker R +$7/-$4 shadow exit, plus the 31 Oct review result) are in `docs/studies-25-plus.md`.

## Open / to repeat
**Monthly combined review, every last Saturday of the month (set 2 Nov 2026). Next: Saturday 28 Nov 2026.** Studies 25, 26, 27, 28, 35, 36, 37, 38, 39, 40, 41 and 42 (everything from 25 up) run
together as **one study with one result**, on one shared data set and one replay engine, as on 31 Oct. They are not scheduled or reported separately. Studies 1-24 are not part of it.
New studies from 38 up join this review the same way. Items to cover each time (the first review's list):
1. Study 26: the effect of `spike_fade` 2.0 on trades since 31 Oct (blocked fades and what they would have done).
   Also the fade spike cutoff APPLIED 4 Nov (fade ceiling $10 -> $16, `spike_fade` 2.0 -> 3.0; breakouts unchanged) -- confirm or revert: replay fades with range $10-16 since 4 Nov, method in `docs/studies-25-plus.md` study 26.
2. Study 26: `shadow_bias_adx`: about 20 flagged fades? Then flagged vs unflagged; clearly losing = real block, else remove the flag.
3. Study 25: a new TA run against an open losing trade: add events until about 20 independent ones. Also the TA re-run shadow log (`ta_rerun_shadow`).
4. Study 27: breakout rules and the +$20 lock, rechecked with the new trades (both rest on a handful of winners).
5. Study 28: the lean on 'live' `ta_bias_grades` rows (about 3 weeks of runs by 28 Nov).
6. Study 35: the quiet-market rule (ATR < 6 and ADX < 20 -> trail activation +$10) and the looser trail after +$20 (D $12-15), on trades opened since 2 Nov; the
   `shadow_quiet_market` flag in `entry_context` marks them. Replay those trades, not the 136 of study 35.
7. Study 36: Broker K's settings (`python broker_k_study.py --fetch`, then `python broker_k_study.py`; method and the 3 Nov result in `docs/studies-25-plus.md`). Rerun on
   fresh 1-minute bars and the new live `broker_k_trades`: (a) the current rule's out-of-sample total since 22 Oct and its t-statistic; (b) the live record against the replay of
   the same days (does the engine still match?); (c) only the few ideas study 36 left open (ADX minimum 25-30, time stop 90, stop-loss 10, RSI cap, the cooldown and entry-hour
   findings) judged on the new weeks, not re-searched across the whole grid; (d) the live fill spread, since the result flips sign at a $1.20 spread, and how the two protections added on 3 Nov (spread cap $1.50, 2-stop-loss pause 30 min) behaved. Broker K is its own
   engine with its own table, so the Broker-F-only rule below does not apply to it.
8. Study 37: the DXY entry block (`broker_b_blocked` rows whose reason is DXY only). Replay the blocks since 3 Nov with the live exit rule, one at a time, minus the
   ~$0.68 spread, and add them to the study 37 result (+$19.1 over 4 touches before spread). Nothing is logged by a flag; the blocked table is the record.
9. Study 38: the Fibonacci levels (live in `ta_forecast_job.py` from the first TA run after PR #283 merged). Method in `docs/studies-25-plus.md`: how often a Fib-labelled zone was a fade
   entry level or a target, and Broker F's trades at Fib-labelled levels against the others (win rate, P/L, with the live exit rule). Keep, change the lookback, or remove.
    Also (added 9 Nov): the gap between the sell and buy fade entries per forecast, with and without the Fib candidates (TA2 of 9 Nov had them only $1.78 apart because a lone Fib 61.8% was the
    sell entry); decide on a minimum gap or keeping a lone Fib out of the fade-entry role. Details in study 38.
10. Study 39: `shadow_stretched` (log only, from 4 Nov 2026). Method in `docs/studies-25-plus.md`: flagged against unflagged Broker F trades, the raw `bb20_pos_1h` / `ext_sma20_1h_pct`
    values to pick better cutoffs, and whether the flag adds anything to the RSI/ADX blocks. Real block only if clearly losing on enough flagged trades; otherwise remove the flag.
11. Study 40: the TA levels against the next 2 hours (`python ta_levels_study.py --fetch`, then `python ta_levels_study.py`; method and the 5 Nov result in `docs/studies-25-plus.md`). Rerun on all
    forecasts since 23 Sep and compare with the 5 Nov table: do levels still beat the shifted-zones chance baseline (turns within $5 of a zone 45% vs 28%), is the nearest zone still the only one that
    matters, do the source categories keep their order (4h swing / pivot bounce best, prior-day / 20-day range worst), and do the first Fibonacci-labelled zones (study 38) bounce more or less than the
    rest. Observation only; reads no trades, so the Broker-F-only rule does not apply.
12. Study 42: the Broker F daily reviews (saved in `broker_daily_reviews`, per the user 11 Nov 2026; `broker_f_daily_reviews` is also checked in case some are there). Each review's fixes section says no decision can be made on one trade, so read all of them together:
    `SELECT review_date, trade_count, total_pnl, analysis, trade_reviews FROM broker_daily_reviews ORDER BY review_date` (plus the same on `broker_f_daily_reviews`). Method and the questions to answer are in `docs/studies-25-plus.md`
    study 42. Observation only; the reviews are Broker F's own, so the Broker-F-only rule is met.

**Broker A is excluded from every study (user rule, 3 Nov 2026).** No study, replay, analysis or recorded total reads Broker A's trades, signals or blocked entries
(`trades`, `broker_a_blocked`), and nothing Broker A did feeds any setting. Broker B and Broker F run the same rules and are studied as below; a Broker K study uses Broker K's own
trades only. `get_closed_trades_for_analysis()` (SLA, BRA) now reads Broker B only.

**Studies use Broker F only (rule set 2 Nov 2026, replaces the pair-matching rule of the same day).** Broker B and Broker F trade the same levels, so using both would count a
level twice. From now on every study, replay and recorded total (the monthly combined review and any ad hoc study) uses **Broker F's trades only**
(`forex_b_trades`, `mode = 'live'`; the real demo fills and exits). Broker B's `broker_b_trades` is not read for studies, and no pairing or de-duplication between the two is
needed. Consequences to state in each result:
- Sample sizes are Broker F's alone (it has traded only since 10 Oct 2026, live since 12 Oct), so they are smaller than the Broker B + F figures quoted in studies 25-35.
- Touches blocked by the entry filters (`broker_b_blocked`) are shared bookkeeping and may still be used for what-if replays of the same levels; closed trades come from Broker F.
- Earlier results (studies 1-35) are not redone; they stay as written, with their own data source noted.

Results go into `docs/studies-25-plus.md` on a branch and PR, as for 31 Oct. The review changes no rule or setting by itself; any change needs the user's approval.
11. Study 41: Broker R's +$7/-$4 shadow exit (log only, from 6 Nov 2026). `python broker_r_tp_sl_study.py --shadow` (live +5/-5 against the logged shadow on the same entries, paired t-stat, by day, overlap count), then the
    grid replay (`--bidask` and the default mid mode) on all closed Broker R trades. Broker R has its own table, so the Broker-F-only rule does not apply. Switch only if the shadow is ahead with t about 2 or more on 100+ trades.
