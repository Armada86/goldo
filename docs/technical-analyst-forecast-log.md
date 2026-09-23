# XAU/USD technical forecast (`ta_forecast_job.py` / `ta_forecasts` table)

This doc explains the generated XAU/USD technical forecast: what style of analysis it copies, how each
part is computed, where it's stored, and what it doesn't do yet. Like the other
`docs/technical-analyst-*-log.md` files, it is a description/methodology reference, not a findings log.

## Where it lives

- **Generator:** `ta_forecast_job.py`, a one-shot job. `python ta_forecast_job.py` generates a
  forecast, saves it, and then sends the same text to Telegram with the 🟡 XAU/USD prefix, split into
  several messages only if it exceeds Telegram's length limit. `--dry-run` generates and prints only, with no DB read or write, so the
  read-only `technical-analyst` subagent can run it too.
- **Schedule:** `.github/workflows/ta_forecast.yml` runs only when triggered through
  `workflow_dispatch`. Like every other job, it needs its own cron-job.org entry: weekdays (Mon-Fri)
  at 7:00am America/New_York.
- **Table:** `ta_forecasts` in Neon, created by `storage.init_db()`:

| column | type | meaning |
|---|---|---|
| `id` | SERIAL | row id |
| `forecast_date` | DATE | the America/New_York date of the run |
| `ts` | TIMESTAMPTZ | exact run time (UTC) |
| `analysis` | TEXT | the human-readable forecast (format below) |
| `levels` | JSONB | the same forecast as numbers: price, bias/score, indicators, resistance/support zones, the four scenarios (entry/trigger, stop, target zones), and the review of the previous row |

`storage.insert_ta_forecast()`/`get_latest_ta_forecast()` are the write and read paths. The `levels`
column is what makes the review section possible, because the next run reads the previous plan back
from it and grades it.

## Reference analyses it was modelled on (collected 2026-09-23)

Two third-party analyses the user pasted in, both checked against Twelve Data candles.

**1. Indicator snapshot (aggregator style, written ~Sept 21-22).** "Consolidating $4,300-$4,400, trading
around $4,344. Resistance $4,393-$4,400 (supply zone $4,375-$4,500), pivot $4,358, support $4,333 then
sub-$4,300. Hourly price hovering in the EMA20/50/100 cluster, EMA200 overhead at $4,351. RSI(14)
49.4-53.6 neutral. MACD negative. Bias bearish-to-neutral unless a sustained break above $4,400."
- Checked: 1h EMA200 was $4,350.74 (an exact match), the $4,399 swing high was real, and MACD was
  negative. The bias was correct: price fell to about $4,295 the next day.
- Weak points: no timestamp or timeframe on any figure, an RSI given as a range (two sources merged),
  a supply zone that contradicts its own resistance, a pivot that couldn't be reproduced, no bearish
  targets or invalidation level, and no macro context.
- **Taken from it:** the snapshot section (the 1h EMA20/50/100/200 stack, RSI(14), MACD(12,26,9), a
  pivot, and one overall bias), with the timeframe stated on every figure.

**2. Conditional trading plan (a newsletter-style daily note, morning of Sept 23).** "5-week triangle;
range 4510-4235 since Aug 28. Resistance at the 4h 100-period MA plus a trend line from Aug 25, at
4360/4370: try shorts, stop above 4380. A sustained break above 4380 is a buy signal, targeting
4410/4415, then 4430/4435/4440; above 4444, 4460/4465. Shorts target 4340/4335, 4323, 4318/4315; a
break below targets 4302/4297. Longs need a stop below 4290; a break lower targets 4270." It also
reviewed the previous day's calls.
- Checked: the range since Aug 28 was right (4,510.6/4,514.1 high, 4,238.4 low). The 4h EMA100 was
  $4,364, inside their 4360/4370 zone. Yesterday's low (4,290) and the overnight high (4,370) matched.
  The short from 4370 reached every target down to about 4,295.
- Weak points: the "worked perfectly" track record is self-reported, some paragraphs are recycled
  word for word from the previous day, and the trend line can't be reproduced exactly.
- **Taken from it:** the plan structure (fade a zone with a hard stop just beyond it, where that stop
  doubles as the breakout trigger the other way, plus a ladder of targets), levels written as $5-10
  zones ("4318/4315", which also absorbs a few dollars of feed difference), and a review of the
  previous plan. Our review is **computed from candles**, not self-reported.

## How each section is computed

All from Twelve Data XAU/USD candles (`data_fetcher.fetch_candles`, UTC): 1h (500 bars), 4h (300),
1day (120, filtered to finished weekday sessions, because Twelve Data's daily series includes
near-empty Sat/Sun bars plus today's still-forming bar), and 15min for the review.

- **Indicators:** 1h EMA20/50/100/200; 4h EMA100 and SMA100 (the second reference's "100-period MA on
  the four-hour chart"); daily SMA20/50; Wilder RSI(14) on 1h and 4h (`data_fetcher.compute_rsi`,
  the same one used for RSI alerts); 1h MACD(12,26,9); daily ATR(14).
- **Pivot:** classic floor pivot (P, R1/R2, S1/S2) from the previous finished UTC weekday session.
  Different sites cut the "day" differently (NY 5pm vs UTC), which is why reference 1's $4,358 pivot
  doesn't match ours. We state the session instead of hiding the choice.
- **Levels:** candidate levels come from the 1h EMA200, the 4h EMA100, daily SMA20/50, prior-day
  high/low, 20-day high/low, the pivots, 4h fractal swing highs/lows (a bar beating the 5 bars on each
  side, over about the last 30 trading days), and $50 round numbers. Candidates within $6 merge into
  one zone that keeps all its source labels. Walking outward from price, zones are kept at least
  0.15 x daily ATR apart; when two are closer than that, the heavier one wins (20-day high/low = 3;
  prior-day high/low, the moving averages and pivot P = 2; everything else = 1). Up to 4 zones are
  listed on each side.
- **Bias:** a score from -6 to +6, one point each for price vs 1h EMA200, price vs 4h EMA100, price vs
  daily SMA50, 1h EMA20 vs EMA50, 1h MACD histogram sign, and 1h RSI (>55 bullish, <45 bearish). A
  score of >= 3 is Bullish, 1..2 Neutral-to-bullish, 0 Neutral, -1..-2 Neutral-to-bearish, and <= -3
  Bearish.
- **Plan:** four mirrored scenarios, with stop buffer = max($5, 10% of daily ATR):
  - `sell_resistance`: sell the nearest resistance zone R1, stop at R1 top + buffer, targets S1-S3.
  - `bull_breakout`: a sustained break above that stop is a buy, stop back below R1, targets R2-R4.
  - `buy_support`: buy the nearest support zone S1, stop at S1 bottom - buffer, targets R1-R3.
  - `bear_breakdown`: a break below that stop is a sell, stop back above S1, targets S2-S4.

  A negative bias marks `sell_resistance` as PRIMARY and a positive one marks `buy_support`; at
  score 0, neither is marked.
- **Context:** DXY and US10Y daily change from yfinance, flagged as a headwind or tailwind for gold
  (both move inversely to gold).
- **Review:** 15-min candles since the previous row's `ts`. For each previous scenario, the review
  reports whether the entry/trigger was reached, and then whether the stop or each target in order
  came first. When a single 15-min bar spans both the stop and the next target, it's reported as
  ambiguous rather than guessed.

## Known gaps (v1, to work on later)

- No trend-line or chart-pattern (triangle/channel) detection, which the second reference relies on.
- No calendar awareness: it doesn't flag an NFP/CPI/FOMC day, even though those invalidate intraday
  levels.
- The plan fades the nearest zones even when price is already sitting on one, so R1/S1 can be only a
  few dollars from price.
- The bias weights and level weights are hand-picked, not backtested. The `levels` JSONB history is
  what a future backtest of hit rate per scenario would use.

## Adding a new reference analysis

The user periodically pastes third-party analyses they've chosen; see CLAUDE.md's "Standing
reference-analysis workflow". Each one gets checked against candles, then added above under
"Reference analyses" with its date, what checked out, its weak points, and what (if anything) was
taken from it.
