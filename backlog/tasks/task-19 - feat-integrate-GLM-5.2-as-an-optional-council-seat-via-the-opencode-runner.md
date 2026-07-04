---
id: TASK-19
title: 'feat: integrate GLM-5.2 as an optional council seat via the opencode runner'
status: Done
assignee: []
created_date: '2026-06-17 20:34'
updated_date: '2026-06-17 21:07'
labels: []
dependencies: []
priority: high
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add GLM-5.2 (Z.ai) as an OPTIONAL, off-by-default R1 council seat, run through the opencode harness. Based on the 2026-06-18 shadow evaluation: docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md

Why opencode + why optional:
- As a SEAT, GLM-5.2 is quality-competitive. A 3-rater blind panel (gpt-5.5 / claude / gemini) ranked GLM's harnessed R1 answers mean ~2.1-2.2 of 4 by claude & gemini, with dimension scores at or above the incumbent seats; the earlier "weak seat" reading was an artifact of a biased structural proxy (fenced code blocks) plus a single competitor-model rater (gpt-5.5).
- It MUST run through a model-agnostic harness (opencode), NOT the `claude` CLI (avoids a Claude-tuned-harness penalty for a third-party model) and NOT bare-API (no tools -> cannot ground in code).
- The only rater-independent downside is latency: GLM was the slowest seat (median 163s, 2/15 timeouts at reasoning=max). Mitigate with opencode `--variant high` (about half the tokens/latency, "sacrifices only a few points"). Hence integrate OPT-IN / off by default so it does not add latency to every council unless explicitly enabled.

Setup already in place (reusable):
- opencode 1.17.7 at ~/.opencode/bin/opencode.
- Z.ai GLM-5.2 provider configured in ~/.config/opencode/opencode.json (OpenAI-compatible endpoint https://api.z.ai/api/coding/paas/v4, apiKey as env-ref GLM_TOKEN).
- API token at ~/.owlex/glm_token. Model id: zai/glm-5.2. Reasoning via `--variant high|max`. A fresh XDG_DATA_HOME MUST be set per invocation (a stale opencode DB crashes v1.17.7). Output is plain text on stdout; do NOT use `--format json` (it hangs on this provider).
- Reference implementation of the working headless invocation: scripts/shadow_glm_seat_opencode.py.

Implementation sketch:
- owlex already has an opencode runner (owlex/agents/opencode.py). Wire a GLM seat that uses the opencode runner pointed at the Z.ai model (zai/glm-5.2, --variant high), injecting GLM_TOKEN + a per-call XDG_DATA_HOME. Likely via COUNCIL_SUBSTITUTION_MODELS or a new opt-in seat entry. Off by default.
- Keep all GLM config isolated to opencode + ~/.owlex/glm_token. Do NOT modify ~/.claude/settings.json or set a global ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN (that would break the user's normal Claude Code).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GLM-5.2 is activated via COUNCIL_SUBSTITUTION_MODELS (repointing an existing seat slot to the opencode runner + model zai/glm-5.2) — NOT by adding an 8th always-on seat. Off by default: when the env var is unset, GLM never runs. When set, GLM runs on every council in place of the substituted donor seat, so net seat count and wall-clock latency do not grow.
- [ ] #2 The seat uses `--variant high` to bound latency, sets a fresh XDG_DATA_HOME per call, and parses plain-text stdout (not --format json).
- [ ] #3 Enabling or disabling the GLM seat does not affect or break the existing seats.
- [ ] #4 No changes are made to ~/.claude/settings.json or to global ANTHROPIC_* environment variables.
- [ ] #5 `python -m pytest tests/ -q` remains green after the change.
- [ ] #6 The task notes / PR link the eval doc: docs/solutions/architecture/glm-5.2-2026-06-shadow-eval.md
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Out of scope here (candidate for a separate task): GLM-5.2 as an ensemble blind-rater — a higher-value, non-opencode, direct-Z.ai-API path; the rater shadow showed 88-94% binary agreement with no per-agent bias.

Activation semantics (verified against council.py:116 — seats are binary: in ALL_SEATS = runs on every council R1/R2, or excluded = never; there is no per-question conditional seat). So 'opt-in' here = substitution (config-only, no seat-count growth), which replaces a slower/redundant donor seat with GLM. Pick the donor carefully: GLM is competitive on quality but the slowest seat (median 163s), so prefer substituting an already-slow/redundant slot (e.g. the opencode or aichat slot) rather than a fast one (codex 52s). A richer, higher-value activation — invoking GLM ONLY when R1 seats disagree — is tracked as a separate escalation task (see OPERATION 2).

Implemented in owlex/agents/opencode.py (GLM branch in build_exec_command/build_resume_command + helpers) + tests/test_opencode_glm_seat.py. Code-reviewed: approved, low risk, 0 P1/P2, 3 minor P3s (redundant zai/ startswith clause; an over-engineered test; per-call tmpdir not cleaned up — minor leak on long-lived server). 383 tests green. Uncommitted in working tree pending commit. Enable: COUNCIL_SUBSTITUTION_MODELS=<donor-seat>:opencode:zai/glm-5.2 (+ ~/.owlex/glm_token).
<!-- SECTION:NOTES:END -->
