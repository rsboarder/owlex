# Agreement-Judge Shadow Replay — GLM-5.2 vs gpt-5.5

**Model**: `glm-5.2` via Z.ai (Anthropic-compatible API)
**Generated**: 2026-06-17 21:05:37
**Threshold for R2-needed**: agreement_score < 3.5

## Volume

| Metric | Count |
|---|---|
| Total councils replayed | 67 |
| Successful glm calls (parsed) | 64 |
| Failed glm calls / parse errors | 0 |
| Original judge had failed (fallback to overlap) | 11 |
| **GLM saves** (original failed, glm produced score) | 11 |

## Score correlation

| Metric | Value |
|---|---|
| Pearson correlation (raw 1-5 scores) | 0.469 |
| Mean original score | 3.47 |
| Mean glm score | 4.06 |

## R2-needed decision agreement (binary, threshold=3.5)

| Metric | Value |
|---|---|
| Cohen's kappa | 0.292 |
| Agreement % | 68.8% |

Confusion matrix (rows = orig judge, cols = glm):

|              | glm says R2 NOT needed | glm says R2 needed |
|---|---|---|
| orig R2 NOT needed | TN=35 | FP=3 |
| orig R2 needed     | FN=17 | TP=9 |

## Interpretation guide

| Kappa | Verdict |
|---|---|
| > 0.8 | GLM can REPLACE current judge (high agreement) |
| 0.6–0.8 | GLM can ENSEMBLE with current judge (cross-validation) |
| 0.4–0.6 | GLM is too divergent — not safe to substitute |
| < 0.4 | GLM diverges fundamentally — different judgment criteria |

Pearson correlation interpretation: > 0.7 strong, 0.4–0.7 moderate, < 0.4 weak.

## GLM-saves potential

Of 11 councils where the original judge failed, glm produced a valid score in 11.
If kappa is high, glm could serve as fallback when codex judge fails.
