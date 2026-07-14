# Grok-as-7th-Seat — Structural R1 Quality (Shadow)

**Model**: `grok-4.5` (effort=low) via Grok CLI
**Generated**: 2026-07-14 21:34:00
**Councils**: 39

## Latency (seconds)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| gemini | 7 | 200.2 | 209.2 | 314.3 |
| claudeor | 7 | 117.2 | 113.0 | 169.8 |
| opencode | 32 | 111.2 | 91.2 | 570.1 |
| codex | 39 | 88.2 | 84.7 | 157.6 |
| aichat | 32 | 81.5 | 66.5 | 200.0 |
| **grok** | 39 | 80.9 | 78.3 | 182.2 |

## Response length (chars)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **grok** | 39 | 12542.4 | 12412.0 | 21052 |
| aichat | 32 | 6406.8 | 5595.0 | 25346 |
| opencode | 32 | 6127.8 | 5707.0 | 20861 |
| codex | 39 | 5693.0 | 5480.0 | 11656 |
| claudeor | 7 | 5426.1 | 4779.0 | 9035 |
| gemini | 7 | 4535.3 | 4664.0 | 5308 |

## Code blocks (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| aichat | 32 | 3.9 | 1.0 | 68 |
| codex | 39 | 2.5 | 1.0 | 20 |
| opencode | 32 | 2.2 | 1.0 | 10 |
| **grok** | 39 | 1.6 | 0.0 | 8 |
| claudeor | 7 | 1.3 | 2.0 | 2 |
| gemini | 7 | 0.4 | 0.0 | 2 |

## File references (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| claudeor | 7 | 18.4 | 17.0 | 54 |
| codex | 39 | 8.1 | 3.0 | 44 |
| opencode | 32 | 6.2 | 2.0 | 41 |
| gemini | 7 | 5.4 | 4.0 | 15 |
| **grok** | 39 | 5.1 | 2.0 | 31 |
| aichat | 32 | 4.6 | 1.0 | 22 |

## Bullet points (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **grok** | 39 | 26.8 | 25.0 | 58 |
| aichat | 32 | 23.9 | 12.0 | 227 |
| opencode | 32 | 15.0 | 8.0 | 74 |
| codex | 39 | 14.9 | 6.0 | 150 |
| gemini | 7 | 9.6 | 7.0 | 18 |
| claudeor | 7 | 3.9 | 3.0 | 10 |

## Markdown headings (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **grok** | 39 | 15.4 | 16.0 | 29 |
| gemini | 7 | 5.1 | 6.0 | 9 |
| aichat | 32 | 3.5 | 0.0 | 84 |
| codex | 39 | 0.3 | 0.0 | 5 |
| opencode | 32 | 0.2 | 0.0 | 5 |
| claudeor | 7 | 0.0 | 0.0 | 0 |

## Interpretation notes

- **Length** alone is not quality, but order-of-magnitude shorter = likely shallower analysis.
- **Code blocks + file refs** = groundedness proxies. Coding-strong seats reference real paths.
- **Headings + bullets** = structure proxy. Too few = stream-of-consciousness; too many = formatting noise.
- **Median (not mean) is the right central-tendency** for response length — distributions are heavy-tailed.

Next step if Grok looks comparable: full quality experiment with cross-judge blind rating (Phase C).
If Grok is order-of-magnitude shorter / fewer code blocks → likely weak seat, stop here.