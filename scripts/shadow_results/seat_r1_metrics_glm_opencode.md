# GLM-5.2-as-Seat (HARNESSED via opencode) — Structural R1 Quality (Shadow)

**Candidate**: `glm_oc` = zai/glm-5.2 via opencode (read-only, --variant max)
**Note**: distinct from the incumbent `opencode` seat (that's a different model in the same harness).
**Generated**: 2026-06-17 23:45:57
**Councils**: 13

## Latency (seconds)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **glm_oc** | 13 | 173.4 | 162.9 | 310.7 |
| opencode | 13 | 123.6 | 85.9 | 570.1 |
| aichat | 13 | 69.2 | 63.3 | 128.1 |
| codex | 13 | 68.7 | 51.6 | 129.3 |

## Response length (chars)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **glm_oc** | 13 | 8903.6 | 8442.0 | 14841 |
| aichat | 13 | 6762.2 | 6034.0 | 16076 |
| opencode | 13 | 6038.5 | 5707.0 | 10146 |
| codex | 13 | 5877.1 | 5500.0 | 10571 |

## Code blocks (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| codex | 13 | 3.5 | 2.0 | 20 |
| opencode | 13 | 2.2 | 0.0 | 10 |
| aichat | 13 | 1.8 | 0.0 | 10 |
| **glm_oc** | 13 | 0.4 | 0.0 | 4 |

## File references (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| aichat | 13 | 2.6 | 0.0 | 12 |
| opencode | 13 | 2.6 | 1.0 | 10 |
| codex | 13 | 1.2 | 0.0 | 7 |
| **glm_oc** | 13 | 0.6 | 0.0 | 2 |

## Bullet points (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| aichat | 13 | 21.8 | 12.0 | 101 |
| opencode | 13 | 16.9 | 11.0 | 66 |
| **glm_oc** | 13 | 16.4 | 13.0 | 56 |
| codex | 13 | 12.0 | 10.0 | 30 |

## Markdown headings (count)

| Agent | n | mean | median | max |
|---|---|---|---|---|
| **glm_oc** | 13 | 2.5 | 1.0 | 8 |
| aichat | 13 | 1.8 | 0.0 | 24 |
| codex | 13 | 0.5 | 0.0 | 5 |
| opencode | 13 | 0.2 | 0.0 | 2 |

## Interpretation notes

- **Length** alone is not quality, but order-of-magnitude shorter = likely shallower analysis.
- **Code blocks + file refs** = groundedness proxies. Coding-strong seats reference real paths.
- **Headings + bullets** = structure proxy. Too few = stream-of-consciousness; too many = formatting noise.
- **Median (not mean) is the right central-tendency** for response length — distributions are heavy-tailed.

Next step if GLM looks comparable: full quality experiment with cross-judge blind rating (Phase C).
If GLM is order-of-magnitude shorter / fewer code blocks → likely weak seat, stop here.