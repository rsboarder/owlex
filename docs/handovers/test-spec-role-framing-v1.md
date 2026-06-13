# Test-spec role framing (TASK-14 / TASK-15) — Handover (2026-06-13)

## Goal
Implement TASK-14 and TASK-15 in owlex: ship a builtin `edge-case-adversary` role + `test-spec` team preset, and add an optional `role` param to the single-model entrypoints — so non-Claude models can be focused on *test-spec / edge-case generation* with a non-default framing, without a separate MCP.

## Status
Designed, not implemented. This session was read-only investigation + backlog authoring.
- Investigated owlex role/prompt/server architecture (anchors in "Context" below).
- Confirmed council already supports per-seat role framing via `roles`/`team` (`owlex/server/_council.py:29-72`), injected by `inject_role_prefix` (`owlex/prompts.py:169`).
- Confirmed the gap: single-model tools (`second_opinion`, `start_*_session`) accept only `prompt` — no framing channel.
- Confirmed the user-config extension point exists: `~/.owlex/roles.json` → `load_user_roles` (`owlex/roles.py:392`) merged by `get_merged_roles_and_teams` (`owlex/roles.py:483`).
- Created **TASK-14** (high) and **TASK-15** (medium) in `backlog/`.
- No owlex source changed this session.

## What's left
1. **TASK-14 — builtin role + team.** Add `edge-case-adversary` to `BUILTIN_ROLES` (`owlex/roles.py:98`) with non-empty `round_1_prefix` + `round_2_prefix` (adversarial test-designer framing: given a flow + interface, NOT the impl, enumerate breaking inputs/states; output structured scenarios with expected behavior; specify what code MUST do). Add `test-spec` to `BUILTIN_TEAMS` (`owlex/roles.py:285`) assigning the role to each seat in `DEFAULT_AGENT_ORDER`. AC: `council_ask(team="test-spec")` resolves the role per seat.
2. **TASK-14 tests + docs.** Extend `tests/test_council_helpers.py` (or add `tests/test_roles.py`): assert `RoleResolver.resolve(..., team="test-spec")` returns `edge-case-adversary` per seat. Add the team to README presets list. Behavior/structure only — do NOT assert exact prompt wording.
3. **TASK-15 — role on single-model tools.** Add optional `role` (string id) to `second_opinion` (`owlex/server/_second_opinion.py` + `owlex/second_opinion.py`) and each `start_*_session` (`owlex/server/_sessions.py`). Resolve via `get_merged_roles_and_teams`/`RoleResolver`; prepend `round_1_prefix` via `inject_role_prefix`. `role=None` ⇒ byte-identical prompt. Unknown id ⇒ clear error, not silent ignore.
4. **TASK-15 tests + docstrings + decision.** Tests: omitted→unchanged, set→prefix present, unknown→error. Update MCP tool docstrings. Document whether `COUNCIL_SYSTEM_INSTRUCTION`'s read-only framing applies to a single-model *generate* call (recommendation: NO).

## Decisions locked
- **Do NOT build a separate MCP.** owlex already has the focusing mechanism (roles/teams + `~/.owlex/roles.json`). A second MCP would duplicate council machinery (anonymization, R2 deliberation, anti-sycophancy preamble, blind rating, evals) and drift from it.
- **The framing is a ROLE, shipped as builtin** — so it's reusable and eval-able inside owlex's existing `bench/`+`evals/`, not a one-off user config.
- **Role injection is a prompt-prefix to the CLIs** (codex/gemini/aichat), not a true system prompt — that's the vendor boundary; a new MCP wouldn't change it.
- **Test assertions = behavior/structure, never exact prompt wording** (repo testing principle; prompt text will churn).
- **TASK-15 relates-to (not depends-on) TASK-14**; `edge-case-adversary` is its natural first consumer. Backlog CLI has no soft "relates-to", so the link lives in TASK-15's description text.

## Open questions
- (non-blocking) Should single-model *generate* calls keep `COUNCIL_SYSTEM_INSTRUCTION` (read-only advisor)? Decide in TASK-15. Recommendation: drop it for generation.
- (non-blocking) Expose a `default_team` config knob too, or builtin team only? Keep minimal — builtin only unless a need appears.

## Risks / pitfalls
- **P0 — dirty branch overlap.** Repo is on `audit-0-bench-harness` with **uncommitted edits to `owlex/roles.py`, `owlex/prompts.py`, `owlex/server/__init__.py`, `owlex/council.py`, `owlex/agreement.py`** — the exact files this work touches. Do NOT start on this tree. Commit/stash the audit work (or branch from clean `main`) first; run `git status` before editing or you'll entangle two unrelated changes.
- **P1 — mutation corpus coupling.** `bench/corpus/mutants/` contains `mut-roles-*` and `mut-prompts-*` derived from `owlex/roles.py` / `owlex/prompts.py`. Adding roles shifts line numbers; check whether the bench corpus/manifest needs regeneration so the eval harness doesn't silently break.
- **P2 — `roles` param overloading.** `council_ask` accepts `roles` as a JSON string too (`owlex/server/_council.py:54`). Keep TASK-15's `role` a plain string id — don't replicate the JSON-string overload.

## Files touched
This session: none in owlex source. Created only `backlog/tasks/task-14 - *.md` and `backlog/tasks/task-15 - *.md` (via backlog-tasker) and this handover doc. Investigation was read-only.

## How to verify (for the implementer, after the work)
- `cd ~/workspace/owlex && pytest` (`asyncio_mode = "auto"`, `testpaths = ["tests"]`).
- Targeted: `pytest tests/test_council.py tests/test_council_helpers.py tests/test_deliberation_prompt.py`.
- TASK-14 smoke: `council_ask(prompt="<a flow>", team="test-spec")` → role resolves per seat.
- Re-read ACs and tick: `backlog task 14`, `backlog task 15`.

## Context for the next agent
- **Role model:** `RoleDefinition` (`owlex/roles.py:35`) = `round_1_prefix` (R1) + `round_2_prefix` (sticky R2). `inject_role_prefix` (`owlex/prompts.py:169`) builds `{COUNCIL_SYSTEM_INSTRUCTION}{context}{role_prefix}{prompt}`.
- **Registry:** `get_merged_roles_and_teams()` (`owlex/roles.py:483`) = `BUILTIN_ROLES`/`BUILTIN_TEAMS` + `~/.owlex/roles.json` (`load_user_roles`, `owlex/roles.py:392`). Resolver: `RoleResolver` (`owlex/roles.py:510`), `.resolve` (`owlex/roles.py:529`). `DEFAULT_AGENT_ORDER = ("codex","gemini","opencode")` (`owlex/roles.py:507`).
- **council wiring:** `owlex/server/_council.py:29-72` (roles/team params, `config.council.default_team`, `role_spec`).
- **single-model tools:** `owlex/server/_sessions.py` (start_*), `owlex/server/_second_opinion.py` + `owlex/second_opinion.py` (second_opinion); registered in `owlex/server/__init__.py`.
- **Why a dedicated role at all:** the default `COUNCIL_SYSTEM_INSTRUCTION` (`owlex/prompts.py:18`) frames agents as read-only *advisors* doing analysis — wrong for *generating* an exhaustive edge-case spec. There is also an always-on `ANTISYCOPHANCY_PREAMBLE` (`owlex/prompts.py:56`) injected at the R2 choke-point.
- **Upstream product context:** this owlex work is the "focusing" layer of a larger test-authoring pipeline — a separate test-author agent fed a spec (not the implementation), with edge-case enumeration delegated to owlex council and mutation-score (not coverage) as the quality gate. Research report lives at `~/.claude/docs/research/2026-06-13-test-author-agent.md`.

## Branch state
- Repo branch: `audit-0-bench-harness` (dirty — unrelated audit work uncommitted). Base: `main`.
- Backlog tasks created this session are likely uncommitted; this handover doc is untracked.
- **Recommended:** after committing/stashing the audit work, branch fresh off `main` (e.g. `feat/test-spec-role`) for TASK-14, then TASK-15.

## Resume command
> `read that handover docs/handovers/test-spec-role-framing-v1.md, analyze it, challenge if needed, then start execution` — run from `~/workspace/owlex`.
