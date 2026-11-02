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
