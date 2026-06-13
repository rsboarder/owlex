# Anti-Sycophancy Deliberation + Second Opinion — Implementation Handover (2026-06-08)

## Goal
Implement all 8 work items from the validated design doc — two independent tracks: (1) anti-sycophancy R2 deliberation for the council, (2) a lightweight `second_opinion` MCP tool wired into the `solution-audit` skill.

**Single source of truth:** [`docs/design/owlex-deliberation-and-second-opinion.md`](../design/owlex-deliberation-and-second-opinion.md) (415 lines, audit-validated). This handover is the runbook; the design doc has the full prompt text, code sketches, and rationale. Read it first.

## Status
- ✅ **Designed + audit-validated, NOT implemented.** Design passed `/solution-audit` (5 Opus judges, claims verified against real code). All confirmed fixes folded back into the design doc.
- ✅ Deep-research grounding done (multi-agent-debate failure modes → anti-sycophancy is the lever; A2A protocol is overkill for single-host).
- ✅ codex `--json` output format probed live (extraction is settled — see Context).
- ❌ Zero implementation code written. Plane deployment deferred by user (design doc §4 has the decomposition if/when wanted).

## What's left (8 work items, dependency-ordered)

**Track 1 — Anti-sycophancy deliberation** (independent of Track 2)
1. **A1 — preamble in `owlex/prompts.py`.** Add `ANTISYCOPHANCY_PREAMBLE` constant (text in design §1.3 Change A) and inject it at the single choke-point `build_deliberation_prompt` — at `prompts.py:121` (`parts = [COUNCIL_SYSTEM_INSTRUCTION + intro]`), right after the intro. *Accept:* the marker substring appears in R2 prompts for `critique=False`, `critique=True`, AND `build_deliberation_prompt_with_role(role=SKEPTIC)` (the latter wraps the former at `prompts.py:203`). *(no deps)*
2. **A2 — `synthesizer` role + `dialectic` team in `owlex/roles.py`.** Add `SYNTHESIZER = "synthesizer"` **inside** `class RoleId(str, Enum)` (`roles.py:21-30`); add the `RoleDefinition` as a dict entry keyed by the **string** `"synthesizer"` in `BUILTIN_ROLES` (`roles.py:97-259`, near skeptic at :142-162); add `BUILTIN_TEAMS["dialectic"]` (`roles.py:264-348`). Definitions in design §1.3 Changes B & C. *Accept:* `get_resolver().resolve("dialectic", ALL_SEATS)` → 6-seat map with exactly 2 `skeptic` + 1 `synthesizer`; `BUILTIN_ROLES["synthesizer"].round_2_prefix` non-empty. *(no deps)*
3. **A3 — validate Track 1.** `python -m pytest tests/ -q` green → `uv tool install --reinstall .` → `pkill -9 -f owlex-server`. *(deps: A1, A2)*

**Track 2 — Second opinion** (independent of Track 1)
4. **B1 — `owlex/second_opinion.py` (NEW).** Lean codex-exec primitive mirroring `agreement.py` (NOT `engine.run_agent`). Module-level env consts `MODEL`/`REASONING`/`TIMEOUT` (design §2.2 — `gpt-5.5`/`high`/`120`), `_cmd()`, `get_second_opinion()`, `_extract_final_message()`. **`_extract_final_message` MUST fail-closed:** empty extraction → caller returns `success=False` (design §2.2). *Accept:* unit tests on a realistic JSONL fixture (incl. ERROR + control lines) return the joined `agent_message` text; empty/garbage → failure. *(no deps)*
5. **B2 — MCP tool `owlex/server/_second_opinion.py` (NEW) + register.** `@mcp.tool() async def second_opinion(...)` per design §2.3. **Honor 2 audit fixes:** (a) error returns use `TaskResponse(success=False, error=..., error_code=ErrorCode.INVALID_ARGS|EXECUTION_FAILED).model_dump()` — NOT a bare dict; (b) signature `timeout: int | None = None` (None → module `TIMEOUT`; do not hardcode 60). Reuse `validate_working_directory` from `server/_validators.py:15`. Register in `owlex/server/__init__.py`: add `from . import _second_opinion` (near :114) + re-export `from ._second_opinion import second_opinion` (near :129). *Accept:* tool returns `{"success":True,"model":...,"opinion":...}` on success; `TaskResponse` error dict on failure. *(deps: B1)*
6. **B3 — docs.** Add `OWLEX_SECOND_OPINION_MODEL/REASONING/TIMEOUT` rows + a `second_opinion` tool mention to the **repo-root `CLAUDE.md`** `## Environment` table (`CLAUDE.md:81`, alongside `OWLEX_AGREEMENT_MODEL` at :90). ⚠ NOT `owlex/CLAUDE.md` — that path does not exist. *(deps: B2)*
7. **B4 — `solution-audit` skill.** Edit `~/.claude/skills/solution-audit/SKILL.md`: in **Phase 1** (lines 90-147) add an independent cross-model reviewer that runs **in the same parallel batch** as the 5 Opus judges, anchored on the **diff** (NOT the findings), blind to the judges; pass `working_directory=<repo root>`; graceful-skip if `second_opinion` fails/absent; add a separate "Independent cross-model review (non-Claude)" block in Phase 3. Add `mcp__owlex__second_opinion` to the `allowed-tools` frontmatter (lines 5-10). Full spec in design §2.4. *(deps: B2 — needs the tool name)*
8. **B5 — validate Track 2.** `python -m pytest tests/ -q` → `uv tool install --reinstall .` → `pkill -9 -f owlex-server` → manual smoke: call `second_opinion` with a short prompt, confirm clean non-empty `opinion`. *(deps: B2, B4)*

## Decisions locked (do NOT re-debate)
- **`second_opinion` mirrors `agreement.py`'s lean subprocess pattern, NOT `engine.run_agent`** — the engine path drags in Task/heartbeat/output-cap/fail-patterns/recursion-fence, all unnecessary for a one-shot read-only call.
- **Anti-sycophancy preamble is always-on** (no env flag) — pure framing, strong evidence; gating rejected. Confounds dashboard history → tag deploy date.
- **`dialectic` team is KEPT** (user, post-audit) — only preset operationalizing 2-troublemaker+1-peacemaker; ~12 lines + a test.
- **`second_opinion` defaults: `reasoning=high`, `timeout=120`, ephemeral `--cd`** (clean/fast; `working_directory` param overrides so solution-audit can pass repo root to read files).
- **Cross-model reviewer runs Phase 1 parallel, diff-anchored, blind to Claude judges** — anchoring on findings would reintroduce the anchoring bias we remove.
- **codex-only for v1** (no gemini-3-pro runner) — explicit non-goal; later via `AGENT_RUNNERS`.
- **R2 anonymization already exists** (`prompts.py` `build_deliberation_prompt` → `assign_labels`) — do NOT rebuild it.

## Risks / pitfalls
- **P1 — working tree already has UNRELATED uncommitted changes** (`owlex/agents/gemini.py`, `owlex/agreement.py`, `owlex/council.py`, `owlex/server/__init__.py`, `tests/test_agreement_probe.py`, `tests/test_sessions.py`). These are NOT from this session. B2 edits `server/__init__.py` which is **already dirty** — merge, don't clobber. Read the CURRENT `agreement.py`/`council.py` before mirroring/referencing them.
- **P1 — codex `--json` schema or catalog could rotate.** `_extract_final_message` fail-closed + the env-pinned `MODEL` both guard this; the tool degrades to `success=false` and solution-audit skips. Don't assume the probe format is eternal — keep the parser defensive (mirror `agreement.py:_parse_score`'s line-scan tolerance).
- **P2 — high reasoning + large diff** could approach the 120s cap on the solution-audit path (the worst case: repo context + deep reasoning). Acceptable (graceful skip), but solution-audit may pass an explicit higher `timeout`.
- **P2 — codex auto-loads `~/.claude/skills`** (NOT suppressible by flag) → ~14k input-token tax even with ephemeral `--cd`. `--cd` only strips repo `AGENTS.md`. Accepted.
- **Dead-end to avoid:** do NOT re-probe codex output format — it's known (see Context). Do NOT route `second_opinion` through the engine.

## Files touched (this session)
- `docs/design/owlex-deliberation-and-second-opinion.md` — **NEW** (the design, untracked)
- `docs/handovers/owlex-deliberation-second-opinion-implementation.md` — **NEW** (this file)
- No source code changed this session.

## How to verify
- After every edit: `python -m pytest tests/ -q` (266+ tests, ~14s; autouse `_isolate_owlex_home` fixture isolates the prod DB).
- Server-side change deploy ritual: `uv tool install --reinstall .` then `pkill -9 -f owlex-server` (Claude Code respawns on next council call).
- Track 1 manual: run a council with `team="dialectic"`; confirm R2 prompts carry the preamble (inspect `~/.owlex/logs/server-*.log` or a unit assertion).
- Track 2 manual: invoke `mcp__owlex__second_opinion` with a short question → expect `{"success":true,"opinion":<non-empty>}` in ~15-40s.

## Context for the next agent (non-obvious, took time to discover)
- **codex `--json` extraction format (probe-verified 2026-06-08):** stdout is JSONL. Final answer = line `{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"..."}}`. Control events `thread.started`/`turn.started`/`turn.completed` and codex's own skill-load `ERROR ...` lines are ignored (they're not `item.completed`/`agent_message`, or aren't JSON). Parser: per stdout line → strip → if `startswith("{")` → `json.loads` (skip on `JSONDecodeError`) → collect `item.text` where `type=="item.completed"` and `item.type=="agent_message"` → join `\n\n`. Empty join → fail-closed.
- **The lean pattern to mirror** lives in `owlex/agreement.py`: `_build_judge_command` (`:26-39`), `score_agreement` (`create_subprocess_exec` + `communicate(stdin)` + `await wait_for(timeout)`, `:109-162`), `_parse_score` defensive parse (`:165-204`), env consts (`:12-23`). `second_opinion` adds only `--json` + `--cd` to the argv.
- **Tool/error conventions (verified against `server/_council.py`):** every tool error return is `TaskResponse(success=False, error=..., error_code=ErrorCode.X).model_dump()` (`_council.py:48,52,86,91`); `validate_working_directory` → `INVALID_ARGS`. Success-side custom dicts are fine (`council_ask` returns a bespoke dict at `:125`). `ErrorCode`/`TaskResponse` in `owlex/models.py`.
- **Test conventions (verified against `tests/test_agreement_probe.py:21-36`, `tests/conftest.py:17-26`):** one `test_*.py` per area; `@pytest.mark.asyncio`; mock subprocess via `monkeypatch.setattr(<module>.asyncio,"create_subprocess_exec",fake)` with a `_FakeProc` whose `communicate()` returns `(stdout,stderr)` bytes; assert **behavior/flags, never error-message wording**; autouse `OWLEX_HOME` isolation is global.
- **Env-pinned config convention:** module-scope `os.getenv("OWLEX_*", default)` (as in `agreement.py:12-23`), NOT the `config.py` dataclass helpers — `agreement.py` is the right neighbor for a standalone primitive.
- **R2 choke-point proof:** `build_deliberation_prompt_with_role` (`prompts.py:169`) calls `build_deliberation_prompt` (`:203`) then prepends `round_2_prefix` (`:217-220`); council.py uses only the `_with_role` builder for R2. One injection at `:121` covers all R2.
- **Audit artifacts:** the 5 judges' full findings are in this session's transcript; the actionable subset is the "Audit results" table at the top of the design doc.

## Branch state
- Branch: **`main`** (protected per memory — PRs via `gh pr create -R rsboarder/owlex`). Base: `main`.
- Working tree: design doc + this handover are **untracked**; 6 unrelated source/test files **already modified** before this session (see Files-touched / P1 risk). Nothing committed, pushed, or PR'd this session.
- Recommended: branch off `main` before implementing (`git switch -c feat/anti-sycophancy-second-opinion`), but be aware the 6 pre-existing dirty files will travel with the checkout — decide whether they belong in this branch or should be stashed/separated first.

## Resume command
> `read that handover docs/handovers/owlex-deliberation-second-opinion-implementation.md, analyze it, challenge if needed, then start execution — Track 1 (A1→A2→A3) and Track 2 (B1→B2→{B3,B4}→B5) are independent, run them in parallel; honor every audit-confirmed fix and run python -m pytest tests/ -q after each edit`
