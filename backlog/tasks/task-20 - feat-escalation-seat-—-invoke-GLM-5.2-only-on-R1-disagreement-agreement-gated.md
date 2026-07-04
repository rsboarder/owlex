---
id: TASK-20
title: >-
  feat: escalation seat — invoke GLM-5.2 only on R1 disagreement
  (agreement-gated)
status: Done
assignee: []
created_date: '2026-06-17 20:47'
updated_date: '2026-06-17 21:34'
labels: []
dependencies:
  - TASK-19
priority: medium
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The principled way to use a "competitive but slow" seat: invoke GLM-5.2 ONLY when the R1 seats disagree, so we pay its latency (median ~163s) only when an extra opinion is actually worth it. Based on the 2026-06-18 GLM-5.2 eval (docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md), which found GLM quality-competitive as a seat but the slowest.

Hook point (already exists): owlex auto-mode already gates the R2 cross-critique round on agreement — see council.py `_resolve_deliberation` / `_auto_deliberation` (~lines 193-317): when `agreement < AUTO_DELIBERATION_THRESHOLD` it logs "R1 disagreement detected" and triggers R2; otherwise it skips R2. Extend exactly this gate: when agreement is below threshold AND an escalation flag is enabled, spawn GLM-5.2 (via the opencode runner + Z.ai, `--variant high`, per TASK-19's wiring) as ONE additional opinion that feeds R2 / the final outcome. When agreement is high (consensus) or the flag is off, GLM is NOT invoked.

This is NEW logic — owlex has no conditional/extra-seat invocation today (seats are binary in ALL_SEATS). Off by default (flag-gated). Keep all GLM config isolated to opencode + ~/.owlex/glm_token; do NOT touch ~/.claude or global ANTHROPIC_* env.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 When auto-mode R1 agreement < threshold (disagreement) AND the escalation flag is enabled, GLM-5.2 is invoked exactly once via the opencode/Z.ai runner (--variant high) as an additional opinion feeding R2 / the outcome.
- [ ] #2 When R1 agreement is high (consensus) OR the escalation flag is off, GLM is NOT invoked (zero added latency on consensus councils).
- [ ] #3 Reuses TASK-19's opencode-GLM runner wiring (per-call fresh XDG_DATA_HOME, plain-text stdout parse, --variant high).
- [ ] #4 No changes to ~/.claude/settings.json or to global ANTHROPIC_* environment variables.
- [ ] #5 `python -m pytest tests/ -q` remains green.
- [ ] #6 The task notes / PR link the eval doc: docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (conservative scope): GlmEscalationConfig in config.py; CouncilResponse.glm_escalation_response field in models.py (CouncilRound untouched — preserves blind-rating/Agent-enum invariant); _invoke_glm_escalation() in council.py (reuses TASK-19 opencode-GLM runner via run_agent, try/except non-fatal), gated in deliberate() on `disagreement AND config.glm_escalation.enabled`; tests/test_glm_escalation.py. Code-reviewed: approved, low risk, 0 P1/P2; both core-flow blockers (flag-off = byte-for-byte unchanged; escalation failure non-fatal) confirmed; no new EnginePort method; recursion fence intact. Fixed a P3 'config that lies': removed dead OWLEX_GLM_ESCALATION_VARIANT, consolidated reasoning-effort on OWLEX_GLM_OC_VARIANT (shared with the seat). 402 tests green. R2-prompt-injection of the escalation opinion explicitly DEFERRED as a follow-up. Uncommitted in working tree. Enable: OWLEX_GLM_ESCALATION_ENABLED=1 + ~/.owlex/glm_token.
<!-- SECTION:NOTES:END -->
