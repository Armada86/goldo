---
name: frequency-test
description: Run the interactive intrahour-swing threshold frequency test — backtest the companion-swing average for each of GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y against gold spot's own $5/$10/$15 moves over the trailing 30 days, and propose the freshly computed thresholds. Use when a user asks to run a frequency test, check alert threshold tuning, or review what each indicator does when gold itself swings $5/$10/$15 in 5/10/15 minutes. Never edits intrahour_swing_thresholds.json or commits without explicit user approval — this is the human-approved path, distinct from the automatic weekday-morning frequency_check_job.py.
---

# Frequency test workflow

This is the **interactive, human-approved** frequency test. The automatic weekday-morning version is
`frequency_check_job.py` + `.github/workflows/frequency_check.yml`; it runs the same study but skips the
approval step. Don't conflate the two.

## Steps

1. Run `python frequency_test.py`. Report each of the twenty-four indicator/window combinations
   (GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y × 15/10/5 min) with its freshly computed average and its sample
   size (`n_used`/`n_total`: how many of gold's events had enough data on that indicator to measure).
   Show the old threshold from `intrahour_swing_thresholds.json` next to each new value.

2. **Stop here.** Do not edit `intrahour_swing_thresholds.json` and do not commit anything. Report the
   findings and wait for the user to explicitly approve the new values.

3. Only after approval: update `intrahour_swing_thresholds.json`, and the numbers quoted in
   `docs/market.md` if they appear there (see CLAUDE.md's "indicator change" workflow). Changes to
   `config.py` itself (windows, gold swing sizes, common session) are a separate ask.

## Where the details are (don't restate them here)

- Methodology: `frequency_test.py` docstring and `docs/frequency-test-thresholds.md`
- Data sources and why dxy/us10y use coarser bars: `docs/data-sources.md`
- Settings: `config.py` (`INTRAHOUR_SWING_WINDOWS_MINUTES`, `GOLD_SWING_THRESHOLDS`,
  `FREQUENCY_TEST_LOOKBACK_DAYS`, `COMMON_SESSION_START_ET`/`_END_ET`)
- Per-indicator background: `docs/technical-analyst-<name>-log.md`
