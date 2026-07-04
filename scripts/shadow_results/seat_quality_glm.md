# Seat Quality Blind-Rate — GLM-5.2 (opencode/max) vs real seats

**Rater**: `gpt-5.5` via codex CLI (the owlex agreement judge's model), blind.
**Generated**: 2026-06-18 00:02:08
**Councils rated**: 13  (mean 4.0 candidates/council, incl. GLM)

GLM-5.2's harnessed R1 answer was dropped in anonymously alongside the real seats'
answers; the rater never knew which was which.

## Headline

| Metric | GLM-5.2 (opencode) | Incumbent seats (pooled) |
|---|---|---|
| Accept rate (+1) | 9/13 = 69% | 35/39 = 90% |
| **Top-1 (best in council)** | 0/13 = 0% | (chance ≈ 25%) |
| Mean rank (1=best) | 3.54 of 4.0 | — |

## Dimension means (1–5)

| Dimension | GLM-5.2 | Incumbents |
|---|---|---|
| groundedness | 3.15 | 3.95 |
| helpfulness  | 3.54 | 4.15 |
| correctness  | 3.23 | 4.03 |

## Reading

This bypasses the structural proxies. If GLM's accept rate and dimension means are
**at or above** the incumbent pool and top-1 rate beats chance, GLM-as-seat is a
quality-competitive seat (the "code-light" structural finding was a proxy artifact).
If GLM lands clearly below the pool, the structural concern reflected a real quality gap.
