"""Automatic threshold search used by frequency_check_job.py to re-tune an
off-target intrahour-swing threshold every weekday morning, with no human in the loop.

Given a (timestamps, prices) series for one indicator and one window size,
finds a threshold that lands the rising-edge event count (frequency_test.
find_events) within [target_lo, target_hi]. Seeded from the current
threshold and expanded outward into a bracket, then binary-searched.

Mirrors the manual tuning convention documented in CLAUDE.md's "Standing
frequency test workflow": the threshold-vs-event-count curve is non-monotonic (occasional
plateaus/cliffs), so among in-band candidates this always keeps pushing
toward the higher-threshold/post-peak side rather than stopping at the first
match.
"""

from frequency_test import find_events


def search_threshold(
    timestamps: list,
    prices: list[float],
    window_minutes: int,
    target_lo: int,
    target_hi: int,
    seed: float,
    max_iterations: int = 50,
) -> tuple[float, int]:
    def count_at(threshold: float) -> int:
        return len(find_events(timestamps, prices, window_minutes, threshold))

    lo, hi = seed * 1e-3, seed

    # Expand the bracket outward until it straddles the target band: hi
    # should give too few events (threshold too strict), lo too many.
    for _ in range(30):
        if count_at(hi) <= target_hi:
            break
        hi *= 1.5

    for _ in range(30):
        if count_at(lo) >= target_lo:
            break
        lo /= 1.5

    if count_at(lo) < target_lo:
        # Even a near-zero threshold can't reach the target rate (the series
        # is too quiet over this lookback) -- nothing better to offer.
        return lo, count_at(lo)

    best = None
    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        n = count_at(mid)
        if target_lo <= n <= target_hi:
            best = (mid, n)
            lo = mid  # keep pushing toward the higher-threshold/post-peak side
        elif n > target_hi:
            lo = mid
        else:
            hi = mid
        if hi - lo < seed * 1e-5:
            break

    if best is not None:
        return best
    mid = (lo + hi) / 2
    return mid, count_at(mid)
