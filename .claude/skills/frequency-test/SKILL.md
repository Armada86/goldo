---
name: frequency-test
description: Run the interactive intrahour-swing threshold frequency test — backtest current INTRAHOUR_SWING_ALERT_THRESHOLD values against live yfinance history, report event counts per indicator/window, and propose new thresholds for any off-target combination. Use when a user asks to run a frequency test, check alert threshold tuning, or review how often GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y intrahour-swing alerts would have fired. Never edits intrahour_swing_thresholds.json or commits without explicit user approval — this is the human-approved path, distinct from the automatic nightly frequency_check_job.py.
---

# Frequency test workflow

This is the **interactive, human-approved** frequency test — for the fully automatic nightly
version, see `frequency_check_job.py` and `.github/workflows/frequency_check.yml` instead. Do not
conflate the two.

## Steps

1. Run `python frequency_test.py` against the *current* `INTRAHOUR_SWING_ALERT_THRESHOLD` values
   (from `intrahour_swing_thresholds.json`). Report each of the twenty-four indicator/window
   combinations (GLD/IAU/GLDM/GDX/GDXJ/RING/DXY/US10Y × 15/10/5 min) and its actual rising-edge event
   count over the last `FREQUENCY_TEST_LOOKBACK_DAYS` days (60 — the max 5-min-resolution history
   yfinance serves for intraday bars).

2. For any combination outside `FREQUENCY_TEST_TARGET +/- FREQUENCY_TEST_TOLERANCE` (60 +/- 4
   events), search for a new threshold that lands within target. The threshold-vs-event-count curve
   is non-monotonic (it rises to a peak, then falls as the threshold keeps increasing) — pick the
   higher-threshold, post-peak side, not the first in-band match. Propose the new threshold and its
   resulting event count.

3. **Stop here.** Do not edit `intrahour_swing_thresholds.json` and do not commit anything. Report
   findings and wait for the user to explicitly approve each proposed threshold before making any
   change.

## Reference

- Config: `config.py` (`INTRAHOUR_SWING_WINDOWS_MINUTES`, `FREQUENCY_TEST_TARGET`,
  `FREQUENCY_TEST_TOLERANCE`, `FREQUENCY_TEST_LOOKBACK_DAYS`)
- Live thresholds: `intrahour_swing_thresholds.json`
- Indicator description/usage: `docs/technical-analyst-gld-log.md`, `docs/technical-analyst-iau-log.md`,
  `docs/technical-analyst-gldm-log.md`, `docs/technical-analyst-gdx-log.md`,
  `docs/technical-analyst-gdxj-log.md`, `docs/technical-analyst-ring-log.md`,
  `docs/technical-analyst-dxy-log.md`, `docs/technical-analyst-us10y-log.md`
