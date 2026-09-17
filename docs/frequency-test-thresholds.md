# Frequency test thresholds (15/10/5-min windows)

`config.INTRAHOUR_SWING_ALERT_THRESHOLD` used to hold a single trailing-60-minute threshold per
indicator. That single window has been dropped in favor of three independent, shorter windows —
15, 10, and 5 minutes (`config.INTRAHOUR_SWING_WINDOWS_MINUTES`) — each with its own threshold, so
`rules.check_intrahour_swing_alerts` can fire up to three alerts per indicator per poll (one per
window), each naming the indicator, the window, the threshold, the direction (up/down), and the swing
amount.

**These nine thresholds are no longer a fixed, hand-picked table — they're re-tuned automatically every
night.** The values below are the ones this backtest produced at the time this doc was written; the
live values always live in `intrahour_swing_thresholds.json`, not here. See "Nightly auto-tuning"
below for how and how often they actually move.

## Thresholds (as of this backtest)

| Indicator | 15-min threshold | 10-min threshold | 5-min threshold |
|---|---|---|---|
| GLD | $1.65 | $1.42 | $1.08 |
| DXY | 0.102 | 0.084 | 0.064 |
| US10Y | 0.0140 | 0.0123 | 0.0100 |

## How these were picked

Same method as the original 60-minute thresholds (`frequency_test.py`, see the 2026-09-16 entries in
`docs/technical-analyst-*-log.md`): for each indicator/window combination, search the
threshold-vs-event-count curve for a value landing within `FREQUENCY_TEST_TARGET +/-
FREQUENCY_TEST_TOLERANCE` rising-edge events — same ~1 event/day rate as the original 30±2/30-day
target, scaled to the new lookback (see below).

**Lookback**: the request was to backtest against the last 6 months, but yfinance caps 5-minute-bar
history at 60 days — a direct fetch for a longer range raises `$SYMBOL: 5m data not available ... range
must be within the last 60 days`, it doesn't just truncate silently. 60 days is therefore the actual
lookback (`config.FREQUENCY_TEST_LOOKBACK_DAYS`), and the target scales from 30±2 events/30 days to
60±4 events/60 days to keep the same underlying rate.

Event counts at the picked thresholds, over the last 60 days (2026-07-19 to 2026-09-17):

| Indicator | Window | Threshold | Events/60 days |
|---|---|---|---|
| GLD | 15 min | $1.65 | 62 |
| GLD | 10 min | $1.42 | 60 |
| GLD | 5 min | $1.08 | 62 |
| DXY | 15 min | 0.102 | 58 |
| DXY | 10 min | 0.084 | 61 |
| DXY | 5 min | 0.064 | 62 |
| US10Y | 15 min | 0.0140 | 56 |
| US10Y | 10 min | 0.0123 | 57 |
| US10Y | 5 min | 0.0100 | 58 |

All nine land inside the 56–64 target band. US10Y's 15-min threshold sits on a sharp cliff in the
curve — 0.0136–0.0139 all produced 80 events, then 0.0140 drops straight to 56 — so per the
non-monotonic-curve convention, the higher-threshold/post-peak side (0.0140) was picked; there's no
smoother candidate between 56 and 64 on that curve.

## Alerting

Every one of these nine threshold values is wired into `rules.check_intrahour_swing_alerts` and alerts
to Telegram — no window or indicator here is backtest-only. A poll can produce anywhere from zero to
nine of these alerts (three windows x three indicators) in a single cycle, each rising-edge deduped per
window so a sustained swing alerts once, not repeatedly for the rest of the window.

## Nightly auto-tuning

`frequency_check_job.py` runs this same backtest every night (8 PM ET, see CLAUDE.md's "Scheduling")
against the *live* thresholds in `intrahour_swing_thresholds.json`. For any of the nine
indicator/window combinations that has drifted outside `FREQUENCY_TEST_TARGET +/-
FREQUENCY_TEST_TOLERANCE` (60±4 events/60 days), it searches a new threshold itself
(`threshold_search.search_threshold`, same higher-threshold/post-peak-side convention as the manual
method above) and rewrites just that entry in the JSON file — combinations still on target are left
untouched. `.github/workflows/frequency_check.yml` then commits the file, opens a PR, and merges it
(`gh pr merge --squash`, no branch-protection bypass — a protected `main` requiring review will leave
the PR open for a human instead of forcing it through).

A Telegram message is sent every night either way, listing all nine combinations and marking each one
`unchanged` (with its threshold and current event count) or showing the change (old threshold/count ->
new threshold/count). Unlike the interactive workflow above, this path never asks for approval first —
that trade-off (nightly drift correction with no human gate, vs. a threshold that can go stale between
manual runs) was a deliberate choice; see CLAUDE.md's "Automatic (nightly, unattended)" workflow
section for the reasoning.
