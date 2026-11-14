#!/usr/bin/env python3
"""Forecast Polymarket Yes prices, 240 minutes ahead, 5 quantiles.

Second vocaine entry for polymarket-faa-attribution-02, evolved from
the first vocaine entry's fully empirical design.  What changed and
why (all tuned on the six practice cases with a local scorer that
reproduces the house baseline's published 0.363589 exactly):

  * Short anchors only.  The first entry interpolated empirical
    quantiles of h-minute changes out to h = 240; the realised 2-4 h
    dispersion of these step prices badly overstates the next 4 h
    (they are mean-reverting, not diffusive).  This entry measures
    anchors at h in (1, 5, 15, 30) only and grows them in h**ALPHA
    space with ALPHA = 0.33, a sub-diffusive exponent.
  * Recency-blended windows.  Each anchor blends the quantiles from a
    720-minute window with those from a fresh 360-minute window
    (weight W_SHORT for the short anchors).  A market that has gone
    dead lately therefore collapses to its fresh, near-zero quantiles
    -- an effective dead-market freeze -- while a market that just
    woke up widens at once.
  * Robust median anchoring.  The centre starts from the last price
    pulled 30% toward the median of the last 3 prices (denoising a
    one-tick spike at the checkpoint), then (a) decays slowly toward
    zero, (b) reverts 8% of the last hour's move, ramping in over the
    first hour: overshoots in these markets partially unwind.
  * Recalibrated shape.  A global SPREAD_SCALE, a slightly wider
    downside (DN_SCALE, long-shots fall faster than they rise), a
    half-width floor that grows like h**ALPHA, and a hard cap on the
    offset from the median (JUMP_CAP) so one freak historical jump
    cannot blow up every row.
  * Short history (< 30 prices) falls back to the house baseline's
    shape (sigma 0.0025 per sqrt-minute, flat median).

Stdlib only, deterministic, no network.  Reads every *.in in
$MAKEFUN_INPUT_DIR (default /input), writes /output/<id>.out
(default $MAKEFUN_OUTPUT_DIR).
"""

import json
import math
import os
import sys

QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)
Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722)
HORIZON = 240
ANCHORS = (1, 5, 15, 30)
ANCHOR_MIN = 20           # min samples for an anchor horizon

# --- tuned parameters (practice cases, local scorer) ---
WINDOW_LONG = 720         # slow window for anchor quantiles
WINDOW_SHORT = 360        # fresh window for anchor quantiles
W_SHORT = 0.6             # short-window weight for anchors <= 15
W_SHORT_LONG = 0.45       # short-window weight for the 30 anchor
ALPHA = 0.33              # growth exponent in h**ALPHA space
SPREAD_SCALE = 0.65       # global scale on empirical spread
DN_SCALE = 1.08           # extra width on the two lower quantiles
UP_SCALE = 0.95           # extra width on the two upper quantiles
FLOOR_SIGMA = 0.0004      # floor half-width per h**ALPHA unit
FALLBACK_SIGMA = 0.0025   # per sqrt-minute when history too short
JUMP_CAP = 0.06           # max |offset| of any quantile from median
CENTRE_PULL = 0.3         # pull of centre toward median of last 3
DECAY = 0.00015           # centre decay toward 0, per minute
REVERT = 0.08             # fraction of last hour's move reverted
REVERT_LOOK = 60          # minutes over which the reverted move is read
REVERT_TAU = 60.0         # minutes over which reversion ramps in


def quantile(sorted_vals, level):
    """Linear-interpolation quantile of an already sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    pos = level * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def anchor_quantiles(prices, window):
    """Empirical quantiles of the h-minute changes, per anchor h."""
    recent = prices[-window:]
    n = len(recent)
    out = {}
    for h in ANCHORS:
        if n - h < ANCHOR_MIN:
            out[h] = None
            continue
        diffs = sorted(recent[i + h] - recent[i] for i in range(n - h))
        out[h] = [quantile(diffs, q) for q in QUANTILES]
    return out


def blended_anchors(prices):
    """Blend long- and short-window anchor quantiles per anchor."""
    a_long = anchor_quantiles(prices, WINDOW_LONG)
    a_short = anchor_quantiles(prices, WINDOW_SHORT)
    out = {}
    for h in ANCHORS:
        if a_long[h] is not None and a_short[h] is not None:
            w = W_SHORT if h <= 15 else W_SHORT_LONG
            out[h] = [a_long[h][i] * (1.0 - w) + a_short[h][i] * w
                      for i in range(5)]
        elif a_short[h] is not None:
            out[h] = a_short[h]
        else:
            out[h] = a_long[h]
    return out


def interp_quantiles(anchors, h):
    """Interpolate/extrapolate the anchor quantile vectors in
    h**ALPHA space; beyond the largest anchor the vector grows by
    (h / a_last)**ALPHA."""
    have = [(a, anchors[a]) for a in ANCHORS if anchors[a] is not None]
    if not have:
        return None
    if h <= have[0][0]:
        return list(have[0][1])
    if h >= have[-1][0]:
        a_last, q_last = have[-1]
        s = (h / a_last) ** ALPHA
        return [v * s for v in q_last]
    sh = h ** ALPHA
    for (a0, q0), (a1, q1) in zip(have, have[1:]):
        if a0 <= h <= a1:
            s0, s1 = a0 ** ALPHA, a1 ** ALPHA
            w = (sh - s0) / (s1 - s0) if s1 > s0 else 0.0
            return [q0[i] * (1.0 - w) + q1[i] * w for i in range(5)]
    return list(have[-1][1])


def _clamp_row(row):
    row = [min(1.0, max(0.0, v)) for v in row]
    row.sort()
    return [round(v, 6) for v in row]


def forecast_market(prices):
    """Return 240 rows of 5 quantiles for one market."""
    if not prices:
        prices = [0.5]
    x = float(prices[-1])
    n = len(prices)
    rows = []

    # Short history: not enough to learn anything -> baseline shape.
    if n < 30:
        for h in range(1, HORIZON + 1):
            s = FALLBACK_SIGMA * math.sqrt(h)
            rows.append(_clamp_row([x + Z[i] * s for i in range(5)]))
        return rows

    # Robust centre: last price pulled toward the median of last 3.
    tail = sorted(float(p) for p in prices[-3:])
    centre0 = x + CENTRE_PULL * (tail[1] - x)
    look = min(REVERT_LOOK, n - 1)
    recent_move = x - float(prices[-1 - look]) if look > 0 else 0.0

    anchors = blended_anchors([float(p) for p in prices])
    for h in range(1, HORIZON + 1):
        med = centre0 * (1.0 - DECAY * h)
        med -= REVERT * recent_move * min(1.0, h / REVERT_TAU)
        med = min(1.0, max(0.0, med))
        q = interp_quantiles(anchors, h)
        if q is None:
            spread = [Z[i] * FALLBACK_SIGMA * math.sqrt(h) for i in range(5)]
        else:
            spread = []
            for i in range(5):
                mult = SPREAD_SCALE
                if i < 2:
                    mult *= DN_SCALE
                elif i > 2:
                    mult *= UP_SCALE
                spread.append((q[i] - q[2]) * mult)
        row = [med + spread[i] for i in range(5)]
        # Floor on the 90% half-width, growing like h**ALPHA.
        half = (row[4] - row[0]) / 2.0
        floor_half = Z[4] * FLOOR_SIGMA * (h ** ALPHA)
        if half < floor_half:
            if half > 0:
                row = [med + (v - med) * (floor_half / half) for v in row]
            else:
                row = [med - floor_half, med - floor_half * 0.41, med,
                       med + floor_half * 0.41, med + floor_half]
        # Cap any quantile's offset from the median.
        row = [min(med + JUMP_CAP, max(med - JUMP_CAP, v)) for v in row]
        rows.append(_clamp_row(row))
    return rows


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)

    names = sorted(f for f in os.listdir(in_dir) if f.endswith(".in"))
    for name in names:
        case_id = name[:-3]
        with open(os.path.join(in_dir, name), "r", encoding="utf-8") as fh:
            case = json.load(fh)
        forecasts = []
        for m in case["markets"]:
            prices = [float(p) for p in m["prices"]]
            forecasts.append({"id": m["id"], "q": forecast_market(prices)})
        out = {"forecasts": forecasts}
        with open(os.path.join(out_dir, case_id + ".out"), "w",
                  encoding="utf-8") as fh:
            json.dump(out, fh, separators=(",", ":"), sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
