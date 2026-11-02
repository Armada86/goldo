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

