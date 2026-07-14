# Grok-as-7th-Seat — Structural R1 Quality (Shadow)

**Model**: `grok-build` (effort=low) via Grok CLI
**Generated**: 2026-05-28 23:16:54
**Councils**: 13

## Latency (seconds)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| gemini | 4 | 220.2 | 213.6 | 314.3 |
| **grok** | 13 | 157.5 | 140.7 | 279.7 |
| claudeor | 4 | 116.0 | 139.6 | 146.0 |
| codex | 13 | 111.2 | 125.4 | 155.3 |
| opencode | 9 | 110.2 | 111.2 | 200.3 |
| aichat | 9 | 94.8 | 78.3 | 184.4 |

## Response length (chars)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **grok** | 13 | 11398.6 | 11718.0 | 14830 |
| opencode | 9 | 6397.6 | 5776.0 | 11579 |
| codex | 13 | 6110.1 | 5792.0 | 10190 |
| claudeor | 4 | 5715.8 | 4779.0 | 9035 |
| aichat | 9 | 5645.7 | 5580.0 | 7234 |
| gemini | 4 | 4694.5 | 4741.0 | 5018 |

## Code blocks (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| opencode | 9 | 1.9 | 1.0 | 7 |
| codex | 13 | 1.5 | 1.0 | 6 |
| aichat | 9 | 1.4 | 0.0 | 6 |
| claudeor | 4 | 1.2 | 2.0 | 2 |
| **grok** | 13 | 0.8 | 0.0 | 7 |
| gemini | 4 | 0.8 | 1.0 | 2 |

## File references (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| claudeor | 4 | 22.2 | 29.0 | 54 |
| codex | 13 | 15.2 | 12.0 | 44 |
| opencode | 9 | 12.7 | 14.0 | 41 |
| **grok** | 13 | 10.6 | 5.0 | 29 |
| aichat | 9 | 8.4 | 12.0 | 22 |
| gemini | 4 | 5.2 | 5.0 | 15 |

## Bullet points (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **grok** | 13 | 21.8 | 20.0 | 36 |
| opencode | 9 | 16.2 | 6.0 | 50 |
| aichat | 9 | 16.1 | 6.0 | 51 |
| codex | 13 | 12.8 | 5.0 | 51 |
| gemini | 4 | 8.8 | 7.0 | 16 |
| claudeor | 4 | 3.5 | 3.0 | 8 |

## Markdown headings (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| gemini | 4 | 6.2 | 6.0 | 9 |
| **grok** | 13 | 4.9 | 6.0 | 9 |
| opencode | 9 | 0.6 | 0.0 | 5 |
| codex | 13 | 0.4 | 0.0 | 5 |
| aichat | 9 | 0.0 | 0.0 | 0 |
| claudeor | 4 | 0.0 | 0.0 | 0 |

## Interpretation notes

- **Length** alone is not quality, but order-of-magnitude shorter = likely shallower analysis.
- **Code blocks + file refs** = groundedness proxies. Coding-strong seats reference real paths.
- **Headings + bullets** = structure proxy. Too few = stream-of-consciousness; too many = formatting noise.
- **Median (not mean) is the right central-tendency** for response length — distributions are heavy-tailed.

Next step if Grok looks comparable: full quality experiment with cross-judge blind rating (Phase C).
If Grok is order-of-magnitude shorter / fewer code blocks → likely weak seat, stop here.