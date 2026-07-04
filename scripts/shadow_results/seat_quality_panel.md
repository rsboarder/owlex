# Panel Quality Blind-Rate — GLM-5.2 (opencode) vs real seats

**Generated**: 2026-06-18 00:26:55
**Councils**: 13 · raters: gpt5.5, claude, gemini (all blind, identical anonymized layout)

## GLM-5.2 per rater

| Rater | n | Accept (+1) | Top-1 | Mean rank | GLM ground/help/corr | Incumbents ground/help/corr |
|---|---|---|---|---|---|---|
| gpt5.5 | 13 | 8/13 = 62% | 0/13 = 0% | 3.46 | 3.31/3.38/3.15 | 4.05/4.18/4.08 |
| claude | 12 | 11/12 = 92% | 6/12 = 50% | 2.08 | 4.42/4.25/4.75 | 4.28/4.42/4.42 |
| gemini | 13 | 13/13 = 100% | 4/13 = 31% | 2.23 | 4.69/4.62/4.77 | 4.46/4.18/4.49 |

## Consensus

- GLM ranked in the **bottom half by ALL 3 raters**: 3/12 councils.

## Reading

If all three raters put GLM near-bottom (low accept, ~0% top-1, mean rank toward N, dims
below incumbents), the weak-seat verdict is robust and not a gpt-5.5 artifact. If raters
diverge (e.g. gemini rates GLM high while gpt-5.5 rates it low), the original signal was
rater taste, not a real quality gap — investigate further before rejecting.