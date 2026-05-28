# Agreement-Judge Shadow Replay — grok-build vs gpt-5.5

**Model**: `grok-build` via Grok CLI
**Generated**: 2026-05-28 20:43:58
**Threshold for R2-needed**: agreement_score < 3.5

## Volume

| Metric | Count |
|---|---|
| Total councils replayed | 62 |
| Successful grok calls (parsed) | 62 |
| Failed grok calls / parse errors | 0 |
| Original judge had failed (fallback to overlap) | 20 |
| **Grok saves** (original failed, grok produced score) | 20 |

## Score correlation

| Metric | Value |
|---|---|
| Pearson correlation (raw 1-5 scores) | 0.491 |
| Mean original score | 3.09 |
| Mean grok score | 3.26 |

## R2-needed decision agreement (binary, threshold=3.5)

| Metric | Value |
|---|---|
| Cohen's kappa | 0.510 |
| Agreement % | 75.8% |

Confusion matrix (rows = orig judge, cols = grok):

|              | grok says R2 NOT needed | grok says R2 needed |
|---|---|---|
| orig R2 NOT needed | TN=20 | FP=8 |
| orig R2 needed     | FN=7 | TP=27 |

## Interpretation guide

| Kappa | Verdict |
|---|---|
| > 0.8 | Grok can REPLACE current judge (high agreement) |
| 0.6–0.8 | Grok can ENSEMBLE with current judge (cross-validation) |
| 0.4–0.6 | Grok is too divergent — not safe to substitute |
| < 0.4 | Grok diverges fundamentally — different judgment criteria |

Pearson correlation interpretation: > 0.7 strong, 0.4–0.7 moderate, < 0.4 weak.

## Grok-saves potential

Of 20 councils where the original judge failed, grok produced a valid score in 20.
If kappa is high, grok could serve as fallback when codex judge fails.
