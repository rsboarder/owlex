---
id: TASK-14
title: Ship builtin edge-case-adversary role + test-spec team preset
status: Done
assignee: []
created_date: '2026-06-13 14:50'
updated_date: '2026-06-13 15:15'
labels:
  - enhancement
  - roles
  - council
dependencies: []
priority: high
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
owlex council already supports per-seat role framing via the roles/team params of council_ask, injected as a prompt prefix by inject_role_prefix in owlex/prompts.py. The default COUNCIL_SYSTEM_INSTRUCTION frames agents as read-only advisors doing analysis/review — the wrong framing for *generating* an exhaustive test specification. We want a first-class, reusable framing for test-spec / edge-case generation so users don't have to hand-roll ~/.owlex/roles.json.

Add to BUILTIN_ROLES (in owlex/roles.py) a role edge-case-adversary with round_1_prefix and round_2_prefix. round_1 frames the model as an adversarial TEST DESIGNER: given a user flow + its interface (NOT the implementation), enumerate inputs/states that BREAK it — boundaries, empty/max length, auth/permission edges, concurrent modification, network failure, malformed input — and output a STRUCTURED list of test scenarios with expected behavior; specify what the code MUST do, never describe what existing code does. round_2 keeps the sticky role, asks the model to merge peers' scenarios and flag any expected-behavior disagreement as a spec ambiguity.

Add to BUILTIN_TEAMS (in owlex/roles.py) a team test-spec assigning edge-case-adversary to the default seats (DEFAULT_AGENT_ORDER — currently codex, gemini, opencode).

Code anchors: owlex/roles.py (BUILTIN_ROLES, BUILTIN_TEAMS, RoleDefinition, TeamPreset, DEFAULT_AGENT_ORDER, RoleResolver.resolve, get_merged_roles_and_teams); owlex/prompts.py (inject_role_prefix, COUNCIL_SYSTEM_INSTRUCTION).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 edge-case-adversary exists in BUILTIN_ROLES with non-empty round_1_prefix and round_2_prefix.
- [x] #2 test-spec exists in BUILTIN_TEAMS assigning edge-case-adversary to each seat in DEFAULT_AGENT_ORDER.
- [x] #3 council_ask(prompt=..., team="test-spec") resolves the role for every seat (i.e. RoleResolver.resolve returns the edge-case-adversary definition per seat).
- [x] #4 Role/team tests extended to cover the new role + team — assert resolution returns the expected role per seat; do NOT assert on exact prompt wording (only structure/behavior).
- [x] #5 README / roles docs list the new test-spec team in the presets section.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented on branch feat/test-spec-role; 318 tests pass. test-spec maps all 6 seats (superset of DEFAULT_AGENT_ORDER).
<!-- SECTION:NOTES:END -->
