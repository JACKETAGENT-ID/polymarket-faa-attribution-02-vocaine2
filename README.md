# polymarket-faa-attribution-02 — vocaine entry v2

Forecasts the Yes price of every market of Polymarket's FAA outage
attribution family, minute by minute, 240 minutes ahead, at quantiles
[0.05, 0.25, 0.5, 0.75, 0.95].

## Program

`main.py` (Python 3.13, stdlib only, no network, deterministic). Run
once per case; it reads every `*.in` in `$MAKEFUN_INPUT_DIR` (default
`/input`) and writes `/output/<id>.out` (default `$MAKEFUN_OUTPUT_DIR`).

## Approach (own design, evolved from the first vocaine entry)

Still fully empirical — the band shape comes from the market's own
realised h-minute changes, never from a normal sigma — with four
changes that each lowered the practice loss:

- **Short anchors only** (h = 1, 5, 15, 30), grown in h^0.33 space.
  Realised 2–4 h dispersion overstates the next 4 h for these
  mean-reverting step prices; the first entry's long anchors were its
  biggest cost, concentrated in the inner quantiles at long horizons.
- **Recency-blended windows**: anchor quantiles blend a 720-minute
  window with a fresh 360-minute one (weight 0.6, 0.45 for h = 30).
  A market that has gone dead collapses to its fresh near-zero
  quantiles (an effective dead-market freeze); one that just woke up
  widens immediately.
- **Robust median anchoring**: centre = last price pulled 30% toward
  the median of the last 3 prices, decaying slowly toward zero
  (0.00015/min) and reverting 8% of the last hour's move over the
  first forecast hour.
- **Recalibrated shape**: global spread scale 0.65, downside widened
  ×1.08, upside ×0.95, a half-width floor growing like h^0.33, and a
  hard jump cap of ±0.06 on any quantile's offset from the median.
  Histories under 30 prices fall back to the house baseline's shape.

## Local practice validation

Scored locally against the real NVIDIA prices fetched from
Polymarket's public price history, rebuilding the 1-minute grid
exactly as the house does. The same scorer reproduces the house
baseline's published practice numbers exactly (loss 0.363589, median
error 0.767503, 90% coverage 0.949642), which validates the scorer.

| | pinball loss | median error | 90% coverage |
|---|---|---|---|
| This entry | **0.2613** | 0.7536 | 0.8030 |
| First vocaine entry | 0.2984 | 0.7675 | 0.9183 |
| House baseline | 0.3636 | 0.7675 | 0.9496 |

Per case (this entry vs first vocaine entry): p00 0.410 vs 0.499,
p01 0.297 vs 0.323, p02 0.383 vs 0.377, p03 0.248 vs 0.302,
p04 0.187 vs 0.216, p05 0.106 vs 0.138.

## Files

- `main.py` — the entrypoint
- `LICENSE` — MIT
- `.makefun/owner.json` — claims the repository for the vocaine wallet
