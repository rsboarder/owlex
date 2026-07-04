# GLM-as-7th-Seat — Structural R1 Quality (Shadow)

**Model**: `glm-5.2` (reasoning=default) via Z.ai (Anthropic-compatible API)
**Generated**: 2026-06-17 20:25:27
**Councils**: 15

## Latency (seconds)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| opencode | 15 | 128.5 | 91.2 | 570.1 |
| aichat | 15 | 81.1 | 65.7 | 200.0 |
| codex | 15 | 73.2 | 59.7 | 145.3 |
| **glm** | 15 | 48.9 | 47.1 | 90.1 |

## Response length (chars)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| aichat | 15 | 7702.7 | 6034.0 | 25346 |
| opencode | 15 | 6811.1 | 5707.0 | 20861 |
| **glm** | 15 | 6175.0 | 6246.0 | 8476 |
| codex | 15 | 6044.5 | 5500.0 | 11656 |

## Code blocks (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| aichat | 15 | 6.2 | 0.0 | 68 |
| codex | 15 | 3.1 | 1.0 | 20 |
| opencode | 15 | 1.9 | 0.0 | 10 |
| **glm** | 15 | 0.7 | 0.0 | 5 |

## File references (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| opencode | 15 | 3.7 | 1.0 | 13 |
| aichat | 15 | 3.5 | 0.0 | 12 |
| codex | 15 | 2.4 | 0.0 | 16 |
| **glm** | 15 | 2.3 | 2.0 | 7 |

## Bullet points (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| aichat | 15 | 34.3 | 12.0 | 227 |
| codex | 15 | 20.7 | 10.0 | 150 |
| opencode | 15 | 15.2 | 9.0 | 66 |
| **glm** | 15 | 12.9 | 9.0 | 64 |

## Markdown headings (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| aichat | 15 | 7.2 | 0.0 | 84 |
| **glm** | 15 | 5.9 | 6.0 | 8 |
| codex | 15 | 0.4 | 0.0 | 5 |
| opencode | 15 | 0.1 | 0.0 | 2 |

## Interpretation notes

- **Length** alone is not quality, but order-of-magnitude shorter = likely shallower analysis.
- **Code blocks + file refs** = groundedness proxies. Coding-strong seats reference real paths.
- **Headings + bullets** = structure proxy. Too few = stream-of-consciousness; too many = formatting noise.
- **Median (not mean) is the right central-tendency** for response length — distributions are heavy-tailed.

Next step if GLM looks comparable: full quality experiment with cross-judge blind rating (Phase C).
If GLM is order-of-magnitude shorter / fewer code blocks → likely weak seat, stop here.