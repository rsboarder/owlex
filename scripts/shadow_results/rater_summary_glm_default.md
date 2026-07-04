# Blind-Rater Shadow Replay — GLM-5.2 vs existing claude_blind

**Model**: `glm-5.2` via Z.ai (Anthropic-compatible API)
**Generated**: 2026-06-17 20:13:07

## Volume

| Metric | Count |
|---|---|
| Councils replayed | 47 |
| Total per-agent comparisons | 133 |

## Score agreement (-1 / +1)

| Metric | Value |
|---|---|
| Exact match | 117/133 = 88.0% |
| Disagreements (flips) | 16 |

## Dimension correlation (Spearman ρ)

| Dimension | ρ | N pairs |
|---|---|---|
| groundedness | 0.126 | 133 |
| helpfulness  | 0.289 | 103 |
| correctness  | 0.230 | 103 |

## Top-1 winner agreement (who got the best rating in each council)

| Metric | Value |
|---|---|
| Same winner | 32/46 = 69.6% |

## Interpretation guide

| Spearman ρ | Verdict |
|---|---|
| > 0.7 | GLM is a strong independent rater — high correlation with existing rater |
| 0.4–0.7 | Moderate correlation — useful as a secondary signal, not replacement |
| < 0.4 | Diverges — different judgment criteria, low ensemble value |

Top-1 winner agreement >70% means GLM would pick the same "best" answer most of the time —
useful as a sanity check on the existing rater.

Note: the existing rater is Claude orchestrator (varies per session — different sessions used
different Claude versions). This compares two independent judges, not "ground truth vs candidate."
