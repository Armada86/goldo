# Block rules analysis (BRA)

Tunes the five entry filters (DXY, ADX, RSI, ATR, spike) that Broker B and Broker F share, from history, and applies the result. It scores on Broker F's live trades (since 4 Nov 2026). Runs by itself every weekday at 6:30 AM ET (cron-job.org) and can also be triggered from Telegram with
`start block rules analysis` or `start BRA` (Worker -> `.github/workflows/block_rules_analysis.yml` ->
`block_rules_analysis_job.py`). Code: `block_rules_analysis.py` (pure analysis), `block_rules.py` (the values).

## The `block_rules` table

Append-only: every row is a full set of the twelve values, and the **newest row is the active one**, so the table is also
the history of every change. `broker_b.py` reads it once per poll. `config.py` stays as the fallback for any value the
table can't supply (table missing or empty, database unreachable), so Broker B never stops trading over it.

| Column | Meaning | Seeded from `config.py` (4 Oct 2026) |
|---|---|---|
| `dxy_threshold` | skip a Buy if DXY rose / a Sell if it fell by at least this over 15 min, on 3 polls in a row | 0.0649 |
| `adx_trending` | fades blocked at ADX >= this while ADX is still rising (vs the previous 15-min candle) | 25 |
| `adx_chop` | breakouts blocked at ADX < this; never below `config.ADX_CHOP_FLOOR` (20): BRA's grid is 20/22/25 and `get_block_rules()` clamps the live value (6 Oct 2026, after B #48 passed at ADX 18.54 with the cutoff at 18) | 20 |
| `rsi_overbought` / `rsi_oversold` | breakout buy blocked at RSI >= / breakout sell at RSI <= | 70 / 30 |
| `fade_rsi_overbought` / `fade_rsi_oversold` | fade sell blocked at RSI >= / fade buy at RSI <= | 68 / 32 |
| `atr_max` | everything blocked when 15-min ATR(14) >= this ($) | 12 |
| `spike_fade_ceiling` / `spike_breakout_ceiling` | **4 Nov 2026:** the dollar ceiling on the same 15-minute range, one per family (fades default $16, breakouts $10, `config.SPIKE_FADE_RANGE_MAX_DOLLARS` / `SPIKE_RANGE_MAX_DOLLARS`; nullable columns, older rows fall back to those). BRA grids: fade ceiling $10-25 or off, breakout ceiling $8-25 or off. BRA tunes each ceiling **together with its family's ATR multiple** (see step 5). |
| `spike_fade` / `spike_breakout` | spike gate (8 Oct 2026): fades / breakouts blocked when the entry-side high-low range of the last 15 one-minute bars is >= this x ATR(14). Fades get the looser multiple (they enter after a run into the level and bet on the reversal). Added later than the other columns: older rows are NULL, which the dashboard shows as off for those times; `init_db()` appended a row with the defaults when the gate went in. BRA's grids: fade 1.75-3.5 or off, breakout 1.25-3.0 or off. The event's range is measured from the same 1-minute bars the replay already fetches (FOREX.com bid/ask, else Twelve Data mid), ending at the event time; the live gate measures it at the poll, a few minutes later at most. Unknown range fails open. **31 Oct 2026: the `spike_fade` default went 3.0 -> 2.0** (studies review; `block_rules` row id 26, source `manual`). **9 Oct 2026: it had gone 2.5 -> 3.0** (a 2.59x support fade that bounced +$13 was blocked; a `block_rules` row with source `manual` set the live value, and BRA is free to move it from there). Since 9 Oct 2026 BRA's replay models the fade exit rule (hard stop capped at $15, no ordinary trail before a near opposite level (within $15), then the lock), like the live engine. | 2.0 / 2.0 |
| `set_ts`, `source`, `changed`, `n_events`, `note` | when, who (`seed` or `BRA`), whether the run changed anything, how many events, a one-line note | |

`99` (or `0` / `101` for the lower-bound rules) means the block is off. The first row (`source = seed`) holds the values
that were in `config.py` when the table was created. The dashboard's "Broker B entry rules" block shows the rules in
force when each forecast run was made.

## What a run does

1. **Events:** every Broker B touch we know of in the last 45 days: the closed trades, plus the touches blocked by
   DXY / ADX / RSI / ATR / spike (`broker_b_blocked`; timing blocks and too-old touches are left out because no value here would
   change them).
2. **Conditions:** for each event, RSI/ADX/ATR(14) on 15-min candles at that moment, whether ADX was rising (above the previous
   candle's, `Event.adx_rising`, used by the fade gate) and DXY's 15-min move at each of the last three polls (`Event.dxy_changes`, for the 3-in-a-row DXY gate).
3. **Replay:** each event is replayed from its entry (a blocked touch from the level price at the recorded touch time)
   with the live exit rule and the stop / trailing settings currently in force, using the same price data and replay as
   `start SLA`.
4. **Scoring:** a candidate set of values runs the real gate functions from `broker_b.py` on every event; events that
   pass are taken, one position at a time like Broker B, the rest are blocked.
5. **Search:** each single rule is swept over its own grid with the others held; **the spike gate of each family is one 2-D search unit (ATR multiple x dollar ceiling), fades and breakouts separately, from 4 Nov 2026** (the two interact: a $16 ceiling scored +$7 with a 2.0x fade multiple and +$18 with 3.0x in study 26). Each grid point is scored with
   its neighbours (a plateau beats a lucky spike). The best change is applied only if it beats the current total by
   `max($10, 10%)` **and** still wins without the single event that gained the most; then it repeats from the new
   values until nothing qualifies.
   Guards: a rule moves **at most one grid step per run** (a spike pair: one step on each axis, so up to 8 neighbouring points) (so a block can't be switched off, or jump across its range, on one
   run's evidence), and moving a rule to its off value (a spike pair: both values off) needs **twice** that margin and the margin without its best event.
   (Added after the first run switched both ADX blocks off from 40 events; the ADX values were restored by hand, table row 3.)
6. **Result:** a new `block_rules` row is written (changed or not) and the report goes to Telegram. Under 12 events
   nothing changes.

## 4 Nov 2026: spike pairs per family, and Broker F as the source

- **Spike gate tuned as a pair, fades and breakouts separately.** The spike gate blocks when the last 15 one-minute bars' entry-side range reaches `spike_*` x ATR(14) or the dollar ceiling
  `spike_*_ceiling`, whichever comes first. The two interact (study 26, 52 fade events: a $16 ceiling scored +$6.99 with a 2.0x multiple and +$17.68 with 3.0x; the old $10 / 2.0x pair
  +$13.27), so `block_rules_analysis.UNITS` makes each family's multiple and ceiling one 2-D search unit: `("spike_fade", "spike_fade_ceiling")` and
  `("spike_breakout", "spike_breakout_ceiling")`. Every point of the grid product is scored with its neighbours (a 3x3 mean), a unit moves at most one step on each axis per run, the
  `max($10, 10%)` margin and the without-best-event test are unchanged, and moving a pair fully off (both values off) needs twice the margin. All other rules are still single 1-D units and
  produce the same values as before (checked on 171 live events: identical for every non-spike rule).
- **Grids:** `spike_fade` 1.75 / 2 / 2.25 / 2.5 / 3 / 3.5 / off; `spike_fade_ceiling` $10 / 12 / 14 / 16 / 18 / 20 / 25 / off; `spike_breakout` 1.25 to 3 or off; `spike_breakout_ceiling` $8 / 10 / 12 / 14 / 16 / 18 / 20 / 25 / off.
- **New columns:** `spike_fade_ceiling`, `spike_breakout_ceiling` (nullable, added by `init_db()`; defaults `config.SPIKE_FADE_RANGE_MAX_DOLLARS` $16 and `SPIKE_RANGE_MAX_DOLLARS` $10, used when a row has NULL). `get_block_rules(as_of)` shows a
  past run with the flat $10 from 21 Oct 2026 and off before that. `broker_b._spike_confirms()` reads the ceilings from the active rules and falls back to config.
- **Broker F is the source (user decision):** `storage.get_closed_trades_for_analysis()` reads `forex_b_trades` (mode `live`) like the monthly studies, instead of Broker B. Broker B and Broker F trade the same levels, so one
  source avoids double counting. The blocked touches still come from the shared `broker_b_blocked` table. Cost: a smaller sample (54 live trades from 12 Oct against 112 for Broker B), so changes will be rarer and noisier at first.
  Reports say "Broker F events" and label trades `F #n`.
- **First runs:** row 32 (on Broker B's trades, 171 events) moved the breakout pair 2.0x / $10 to 1.75x / $12 (+$25.60 replay, +$3.98 without its best event, B #16): a narrow pass, re-check at the 28 Nov review.
  Row 33 (Broker F, 113 events): reviewed, no change (+$118.65 either way).
- **Run history since the doc was last written:** rows 11-25 daily reviews, no change (72 to 151 events); row 26 (31 Oct, manual) `spike_fade` 3.0 -> 2.0; rows 27-29 reviews, no change; row 30 (4 Nov, manual) `spike_fade` 2.0 -> 3.0
  with the $16 fade ceiling in the code; row 31 (manual) stores the two ceilings (fades $16, breakouts $10); row 32 and 33 as above.

## Gate changes

- **6 Oct 2026, RSI never waived:** `broker_b._rsi_confirms()` no longer skips its block at ADX >= `adx_trending`.
- **6 Oct 2026, ADX fade block only while rising:** `_adx_confirms()` blocks a fade at ADX >= `adx_trending` only if ADX is above the
  previous 15-min candle's; a strong but fading ADX lets it through. A +DI/-DI direction version was tried and dropped the same day
  (a fade enters after price ran into the level, so it nearly always read as against the trend). `block_rules` rows 1-5 were scored
  before this, so the next run will read the `adx_trending` cutoff differently.

- **6 Oct 2026, breakout ADX floor:** BRA had lowered `adx_chop` from 20 to 18 (row 4) and B #48 bought a breakout at ADX 18.54 and lost the full
  stop. `config.ADX_CHOP_FLOOR` (20) now bounds it: `get_block_rules()` never returns a lower live value, and BRA's grid for `adx_chop` is 20 / 22 / 25
  (it can raise the cutoff, not lower it or switch it off). The next BRA run writes a row with 20. The dashboard still shows past runs' stored values.
- **6 Oct 2026, DXY gate needs 3 polls in a row:** `_dxy_confirms()` blocks only if DXY's trailing-15-min move cleared `dxy_threshold`
  in the adverse direction on each of the last three polls; fewer polls of history fails open. Earlier BRA rows judged DXY on a single poll.

- **6 Oct 2026, re-arm needs a real win (not a BRA rule):** `broker_b.REARM_MIN_WIN_PNL` ($3, was $5 on 6 Oct) -- a level re-arms only after a close of at least +$3; BRA's replay does
  not model level re-arming at all.

## Limits

- Broker F's live trades are the only trades read (from 12 Oct), so expect a hundred events or so, most of them blocked touches. The spike pair is scored on the total P/L of all events one position at a time, while study 26 scored a family on its own events, so the two can differ slightly.
- Small samples: a few dozen events at most for now. Treat early changes as provisional and re-run after more trading.
- Only touches that were recorded can be judged; a value that would let in a touch no filter ever saw is invisible.
- Blocked touches are replayed from an approximate entry time (the stored time is when the poll saw the bar).
- The stop settings are held fixed; run `start SLA` separately. The two interact (a wider stop makes the ATR block less
  necessary), so avoid running both back to back and changing both.
- Broker A is not affected: it keeps its own ADX / RSI constants in `config.py`.
