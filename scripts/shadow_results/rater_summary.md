# Blind-Rater Shadow Replay — grok-build vs existing claude_blind

**Model**: `grok-build` via Grok CLI
**Generated**: 2026-05-28 21:59:26

## Volume

| Metric | Count |
|---|---|
| Councils replayed | 57 |
| Total per-agent comparisons | 156 |

## Score agreement (-1 / +1)

| Metric | Value |
|---|---|
| Exact match | 120/156 = 76.9% |
| Disagreements (flips) | 36 |

## Dimension correlation (Spearman ρ)

| Dimension | ρ | N pairs |
|---|---|---|
| groundedness | 0.438 | 156 |
| helpfulness  | 0.225 | 150 |
| correctness  | 0.388 | 150 |

## Top-1 winner agreement (who got the best rating in each council)

| Metric | Value |
|---|---|
| Same winner | 33/56 = 58.9% |

## Interpretation guide

| Spearman ρ | Verdict |
|---|---|
| > 0.7 | Grok is a strong independent rater — high correlation with existing rater |
| 0.4–0.7 | Moderate correlation — useful as a secondary signal, not replacement |
| < 0.4 | Diverges — different judgment criteria, low ensemble value |

Top-1 winner agreement >70% means Grok would pick the same "best" answer most of the time —
useful as a sanity check on the existing rater.

Note: the existing rater is Claude orchestrator (varies per session — different sessions used
different Claude versions). This compares two independent judges, not "ground truth vs candidate."
