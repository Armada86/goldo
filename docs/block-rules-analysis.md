# Block rules analysis (BRA)

Tunes Broker B's four entry filters from history and applies the result. Runs by itself every weekday at 6:30 AM ET (cron-job.org) and can also be triggered from Telegram with
`start block rules analysis` or `start BRA` (Worker -> `.github/workflows/block_rules_analysis.yml` ->
`block_rules_analysis_job.py`). Code: `block_rules_analysis.py` (pure analysis), `block_rules.py` (the values).

## The `block_rules` table

Append-only: every row is a full set of the eight values, and the **newest row is the active one**, so the table is also
the history of every change. `broker_b.py` reads it once per poll. `config.py` stays as the fallback for any value the
table can't supply (table missing or empty, database unreachable), so Broker B never stops trading over it.

| Column | Meaning | Seeded from `config.py` (4 Oct 2026) |
|---|---|---|
| `dxy_threshold` | skip a Buy if DXY rose / a Sell if it fell by at least this over 15 min | 0.0649 |
| `adx_trending` | fades blocked at ADX >= this while ADX is still rising (vs the previous 15-min candle) | 25 |
| `adx_chop` | breakouts blocked at ADX < this | 20 |
| `rsi_overbought` / `rsi_oversold` | breakout buy blocked at RSI >= / breakout sell at RSI <= | 70 / 30 |
| `fade_rsi_overbought` / `fade_rsi_oversold` | fade sell blocked at RSI >= / fade buy at RSI <= | 68 / 32 |
| `atr_max` | everything blocked when 15-min ATR(14) >= this ($) | 12 |
| `set_ts`, `source`, `changed`, `n_events`, `note` | when, who (`seed` or `BRA`), whether the run changed anything, how many events, a one-line note | |

`99` (or `0` / `101` for the lower-bound rules) means the block is off. The first row (`source = seed`) holds the values
that were in `config.py` when the table was created. The dashboard's "Broker B entry rules" block shows the rules in
force when each forecast run was made.

## What a run does

1. **Events:** every Broker B touch we know of in the last 45 days: the closed trades, plus the touches blocked by
   DXY / ADX / RSI / ATR (`broker_b_blocked`; timing blocks and too-old touches are left out because no value here would
   change them).
2. **Conditions:** for each event, RSI/ADX/ATR(14) on 15-min candles at that moment, whether ADX was rising (above the previous
   candle's, `Event.adx_rising`, used by the fade gate) and DXY's 15-min move.
3. **Replay:** each event is replayed from its entry (a blocked touch from the level price at the recorded touch time)
   with the live exit rule and the stop / trailing settings currently in force, using the same price data and replay as
   `start SLA`.
4. **Scoring:** a candidate set of values runs the real gate functions from `broker_b.py` on every event; events that
   pass are taken, one position at a time like Broker B, the rest are blocked.
5. **Search:** each of the eight values is swept over its own grid with the others held; each grid point is scored with
   its neighbours (a plateau beats a lucky spike). The best change is applied only if it beats the current total by
   `max($10, 10%)` **and** still wins without the single event that gained the most; then it repeats from the new
   values until nothing qualifies.
   Guards: a rule moves **at most one grid step per run** (so a block can't be switched off, or jump across its range, on one
   run's evidence), and moving a rule to its off value needs **twice** that margin and the margin without its best event.
   (Added after the first run switched both ADX blocks off from 40 events; the ADX values were restored by hand, table row 3.)
6. **Result:** a new `block_rules` row is written (changed or not) and the report goes to Telegram. Under 12 events
   nothing changes.

## Gate changes

- **6 Oct 2026, RSI never waived:** `broker_b._rsi_confirms()` no longer skips its block at ADX >= `adx_trending`.
- **6 Oct 2026, ADX fade block only while rising:** `_adx_confirms()` blocks a fade at ADX >= `adx_trending` only if ADX is above the
  previous 15-min candle's; a strong but fading ADX lets it through. A +DI/-DI direction version was tried and dropped the same day
  (a fade enters after price ran into the level, so it nearly always read as against the trend). `block_rules` rows 1-5 were scored
  before this, so the next run will read the `adx_trending` cutoff differently.

## Limits

- Small samples: a few dozen events at most for now. Treat early changes as provisional and re-run after more trading.
- Only touches that were recorded can be judged; a value that would let in a touch no filter ever saw is invisible.
- Blocked touches are replayed from an approximate entry time (the stored time is when the poll saw the bar).
- The stop settings are held fixed; run `start SLA` separately. The two interact (a wider stop makes the ATR block less
  necessary), so avoid running both back to back and changing both.
- Broker A is not affected: it keeps its own ADX / RSI constants in `config.py`.
