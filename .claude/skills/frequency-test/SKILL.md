---
name: frequency-test
description: Run the interactive intrahour-swing threshold frequency test — backtest the companion-swing average for each of GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y against gold spot's own $5/$10/$15 moves over the trailing 30 days, and propose the freshly computed thresholds. Use when a user asks to run a frequency test, check alert threshold tuning, or review what each indicator does when gold itself swings $5/$10/$15 in 5/10/15 minutes. Never edits intrahour_swing_thresholds.json or commits without explicit user approval — this is the human-approved path, distinct from the automatic weekday-morning frequency_check_job.py.
---

# Frequency test workflow

This is the **interactive, human-approved** frequency test — for the fully automatic weekday-morning
version, see `frequency_check_job.py` and `.github/workflows/frequency_check.yml` instead. Do not
conflate the two; both run the same study, only the automatic one skips the approval step.

## Methodology

Not a target-event-rate search. `python frequency_test.py`: finds every moment gold spot itself swung
`GOLD_SWING_THRESHOLDS[window]` (a fixed $5/$10/$15 for the 5/10/15-min windows) within that trailing
window — rising-edge deduped, so a sustained move counts once — restricted to
`COMMON_SESSION_START_ET`-`COMMON_SESSION_END_ET` (9:30am-2:55pm ET, weekdays), the trading hours
shared by all eight intrahour-swing indicators. At each of those moments, it measures each indicator's
own high-low swing over that identical window and averages it across every such moment. **That average
is the indicator's threshold** — there's no search, no target/tolerance band, just a direct, rolling
30-day (`FREQUENCY_TEST_LOOKBACK_DAYS`) average.

Data sources: gold spot and the six gold ETFs (gld/iau/gldm/gdx/gdxj/ring) come from Twelve Data at
true 1-minute resolution (yfinance's 1-minute bars are capped at ~7-8 days, too short for this
lookback); dxy/us10y stay on yfinance's 5-minute bars, since no source evaluated has a genuine Dollar
Index or intraday Treasury-yield instrument at any accessible plan tier — see `docs/data-sources.md`.

## Steps

1. Run `python frequency_test.py`. Report each of the twenty-four indicator/window combinations
   (GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y × 15/10/5 min) and its freshly computed average, alongside its
   sample size (`n_used`/`n_total` — how many of gold's events fell in the common session with enough
   data on that indicator to measure a swing at all).

2. **Stop here.** Do not edit `intrahour_swing_thresholds.json` and do not commit anything. Report
   findings and wait for the user to explicitly approve the new values before making any change.

## Reference

- Config: `config.py` (`INTRAHOUR_SWING_WINDOWS_MINUTES`, `GOLD_SWING_THRESHOLDS`,
  `FREQUENCY_TEST_LOOKBACK_DAYS`, `COMMON_SESSION_START_ET`, `COMMON_SESSION_END_ET`)
- Live thresholds: `intrahour_swing_thresholds.json`
- Full methodology writeup: `docs/frequency-test-thresholds.md`, `docs/data-sources.md`
- Indicator description/usage: `docs/technical-analyst-gld-log.md`, `docs/technical-analyst-iau-log.md`,
  `docs/technical-analyst-gldm-log.md`, `docs/technical-analyst-gdx-log.md`,
  `docs/technical-analyst-gdxj-log.md`, `docs/technical-analyst-ring-log.md`,
  `docs/technical-analyst-dxy-log.md`, `docs/technical-analyst-us10y-log.md`
