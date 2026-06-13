# Design: Anti-Sycophancy Deliberation (Council Variant B) + Lightweight Second Opinion

**Status:** Draft for review → `/solution-audit` validation → Plane decomposition
**Author:** orchestrator session, 2026-06-08
**Scope:** Two independent deliverables in the Owlex repo (+ one external skill file).

---

## Audit results (`/solution-audit`, 5 Opus judges, 2026-06-08)

| Dimension | Verdict | Headline |
|---|---|---|
| Systematic | ✅ | Reuse-first; single choke-point; `second_opinion` correctly mirrors `agreement.py`, not the engine. (P2: argv overlaps `_build_judge_command` — factor a shared helper.) |
| Consistent | ⚠ | **P1: error returns must use `TaskResponse(...).model_dump()` + `ErrorCode`** like every other tool (`_council.py:48,52,86,91`), not a bare dict. P2: doc path + role snippet. |
| Over-engineered | ⚠ | **P1 (decision): `dialectic` team flagged premature** — a `balanced` team already exists (`roles.py:321`); synthesizer role alone may suffice. Everything else appropriately scoped. |
| Complexity-locus | ✅/⚠ | Item 1 = flat data. **P1: `_extract_final_message` must fail-closed** — empty extraction → `success=False`, else silent-empty if codex `--json` schema rotates. |
| Performance | ✅ | Cold paths / properly async / parallel-masked latency. **P2: `timeout` default mismatch** — tool sig `=60` overrides module `=120`. |

**Confirmed fixes applied to this doc:** error-shape (§2.3), timeout default (§2.3), fail-closed extraction (§2.2), `CLAUDE.md` path (§2.5/§4), "no balanced team" framing (§0/§1.3), role-snippet clarification (§1.3).
**Resolved (user, 2026-06-08):** keep the `dialectic` team — see §1.3 Change C. **Design is final → ready for Plane.**

---

## 0. Why (research grounding)

A deep-web-research pass (2026-06-08) over the 2025–2026 multi-agent-debate literature
produced three load-bearing findings:

1. **Anonymization mitigates identity bias.** Masking who-said-what cut the
   conformity-obstinacy gap ~96% (arXiv:2510.07517). **Owlex already does this** — R2 is
   relabeled Response A/B/C. No change needed; it is a strength to preserve.
2. **Sycophancy is the dominant failure mode of debate.** Unstructured cross-talk makes
   agents converge to consensus and *drop correct answers under peer pressure*; group
   accuracy can decline across rounds. A **sycophancy prior** (explicitly instructing
   models to weigh arguments on merit, not agreement) gave **+10.5% absolute** accuracy
   (CONSENSAGENT, ACL Findings 2025). **Owlex does NOT do this** — gap.
3. **Balance beats uniformity.** The best configs mix *peacemaker* (cooperative,
   synthesis-seeking) and *troublemaker* (independent, challenging) dispositions, not
   all-skeptic nor all-neutral (arXiv:2509.23055). Owlex has `SKEPTIC` (troublemaker) and
   a `devil_advocate` all-skeptic team, but **no peacemaker/synthesizer role**, and **no
   team that pairs troublemaker(s) with a peacemaker**. (Audit correction: a `balanced`
   team *does* exist — `roles.py:321`, 1 skeptic + 5 specialists — but it has no
   synthesizer and is not a troublemaker/peacemaker dialectic.) — gap.

**Conclusion that frames this design:** "true agent-to-agent communication" (A2A protocol,
HTTP, message bus) is overkill for a single-host CLI-subprocess council and is *not* what
moves the needle. The needle is moved by (a) a sycophancy prior in R2, and (b) explicit
disposition balance — both prompt/role-level, zero new transport. That is **Item 1**.

Separately, the user wants a **lightweight, non-Claude second opinion** the orchestrator
can call cheaply — and specifically wants it wired into the `solution-audit` skill, whose
five judges are all Opus (same-model self-review). That is **Item 2**.

---

## Item 1 — Anti-Sycophancy Deliberation ("Council Variant B")

### 1.1 Goal

Make the **default** R2 deliberation resist sycophantic convergence, and offer a
research-aligned balanced team — for the main `council_ask` use-case (many agents).

### 1.2 What already exists (reuse, do not rebuild)

| Asset | Location | Status |
|---|---|---|
| R2 anonymization (Response A/B/C) | `prompts.py: anonymize_round_responses`, `build_deliberation_prompt` | ✅ keep |
| `SKEPTIC` role (troublemaker) | `roles.py: BUILTIN_ROLES["skeptic"]` | ✅ reuse |
| `critique` mode ("do not just agree") | `prompts.py: DELIBERATION_INSTRUCTION_CRITIQUE` | ✅ reuse |
| Role/team resolver + R2 sticky roles | `roles.py: RoleResolver`, `build_deliberation_prompt_with_role` | ✅ reuse |
| `claudeor` defaults to skeptic | `council.py:122-124` | ✅ keep |

### 1.3 Gap → changes

**Change A — sycophancy-prior preamble (the +10.5% lever).** Always-on for R2.

- Add a constant in `prompts.py`:

  ```python
  ANTISYCOPHANCY_PREAMBLE = (
      "DELIBERATION INTEGRITY (read first):\n"
      "- Weigh each argument on its merits and evidence, NOT on how many peers hold it "
      "or who holds it.\n"
      "- Do NOT change your answer simply because others disagree. Change it ONLY if "
      "their reasoning is genuinely stronger than yours.\n"
      "- Preserve real disagreement. Premature consensus is a failure, not a success. "
      "If you still think the majority is wrong, say so and explain why.\n"
      "- If you DO converge, it must be because you were persuaded by a specific point — "
      "name that point.\n\n"
  )
  ```

- Inject it in `build_deliberation_prompt()` (the single choke-point both R2 builders use)
  right after `COUNCIL_SYSTEM_INSTRUCTION + intro`, so it applies in **both** revise and
  critique modes and to **role-injected** R2 (since `build_deliberation_prompt_with_role`
  wraps `build_deliberation_prompt`).

- **Decision:** always-on (chosen over env-flag). Rationale: it is a pure framing
  preamble with strong evidence and no downside; gating adds a config knob for marginal
  benefit. (If A/B measurement is later wanted, gating is a 3-line follow-up — noted as a
  non-goal here.)

**Change B — `synthesizer` (peacemaker) role.** New `RoleId.SYNTHESIZER` + definition in
`BUILTIN_ROLES`. It is the *cooperative* counterweight to `SKEPTIC` — but cooperative
≠ sycophantic (the preamble in Change A still binds it). Placement per `roles.py`: the enum
member goes **inside** `class RoleId(str, Enum)` (roles.py:21-30); the definition is a dict
entry in the `BUILTIN_ROLES` literal keyed by the string `"synthesizer"` (roles.py:97+).
The snippet below is illustrative, not literal placement:

```python
RoleId.SYNTHESIZER = "synthesizer"

RoleId.SYNTHESIZER.value: RoleDefinition(
    id="synthesizer",
    name="Synthesizer / Peacemaker",
    description="Find the strongest common ground and integrate partial truths",
    round_1_prefix=(
        "[ROLE: Synthesizer]\n"
        "You build the strongest integrated answer. Focus on:\n"
        "- Identifying the correct core shared by multiple viewpoints\n"
        "- Reconciling apparent conflicts into a coherent recommendation\n"
        "- Naming the single best path when tradeoffs compete\n\n"
    ),
    round_2_prefix=(
        "[ROLE: Synthesizer - Deliberation]\n"
        "Integrate the council's answers into the strongest single recommendation. "
        "But do NOT paper over real disagreement: if two positions are genuinely "
        "incompatible, surface the tradeoff explicitly rather than splitting the "
        "difference. Synthesis is not appeasement.\n\n"
    ),
)
```

**Change C — `dialectic` balanced team.** ✅ **DECISION: KEEP (user, 2026-06-08).** The
over-engineering judge flagged this as a possibly-premature 7th opt-in preset (a `balanced`
team already exists at `roles.py:321`, `default_team` is `None` at `config.py:130`). Kept
anyway: `dialectic` is the *only* preset that operationalizes the research's "2 troublemaker
+ 1 peacemaker" config — neither `balanced` (1 skeptic, no synthesizer) nor `devil_advocate`
(all skeptic) does — and the cost is ~12 lines of data + one test. Definition:

New `BUILTIN_TEAMS["dialectic"]` operationalizing "peacemaker + troublemaker balance"
across the 6 seats: ~2 troublemakers, 1 peacemaker, 3 domain specialists. Proposed
assignment (seats matched loosely to known strengths):

```python
"dialectic": TeamPreset(
    id="dialectic",
    name="Dialectic (balanced troublemaker/peacemaker)",
    description="Research-aligned mix: 2 skeptics, 1 synthesizer, 3 specialists",
    assignments={
        "claudeor": RoleId.SKEPTIC.value,       # fast unconstrained troublemaker
        "aichat":   RoleId.SKEPTIC.value,        # second independent challenger
        "gemini":   RoleId.SYNTHESIZER.value,    # large-context integrator
        "codex":    RoleId.MAINTAINER.value,
        "opencode": RoleId.ARCHITECT.value,
        "cursor":   RoleId.SECURITY.value,
    },
),
```

`dialectic` is **opt-in** (passed as `team="dialectic"`), not the default. The default
council is unchanged except for Change A's always-on preamble.

### 1.4 Files touched (Item 1)

| File | Change |
|---|---|
| `owlex/prompts.py` | add `ANTISYCOPHANCY_PREAMBLE`; inject into `build_deliberation_prompt` |
| `owlex/roles.py` | add `RoleId.SYNTHESIZER`, its `BUILTIN_ROLES` entry, `BUILTIN_TEAMS["dialectic"]` |
| `tests/test_council_helpers.py` or new `tests/test_deliberation_prompt.py` | assert preamble present in R2 prompt (both modes) |
| `tests/test_resolution.py` / `tests/test_council.py` | assert `synthesizer` role + `dialectic` team resolve to 6 seats |

### 1.5 Test plan (Item 1)

- `build_deliberation_prompt(critique=False)` output **contains** the anti-sycophancy
  marker (assert on a stable substring like `"DELIBERATION INTEGRITY"`, not full text).
- Same for `critique=True` and for `build_deliberation_prompt_with_role(role=SKEPTIC)`.
- `get_resolver().resolve("dialectic", ALL_SEATS)` returns a 6-seat mapping with exactly
  2 `skeptic`, 1 `synthesizer`.
- `BUILTIN_ROLES["synthesizer"].round_2_prefix` is non-empty.
- Per project rule: assert **behavior/structure**, not exact prose wording.

### 1.6 Risks / tradeoffs (Item 1)

- **Confounds dashboard history:** always-on preamble changes R2 behavior, so per-agent
  ratings before/after are not directly comparable. Accepted (the user chose always-on).
  Mitigation note: tag the deploy date so dashboard analysis can split on it.
- **Over-correction risk:** an aggressive anti-sycophancy prior could push models toward
  performative contrarianism (the "all-skeptic underperforms" finding). Mitigation: the
  wording demands *merit-based* change, not blanket disagreement; `dialectic` keeps a
  synthesizer in the mix. Low risk for a preamble.
- **Token cost:** ~80 tokens added per R2 agent call. Negligible.

---

## Item 2 — Lightweight Second Opinion (`second_opinion` MCP tool + solution-audit)

### 2.1 Goal

A **one-call, blocking, returns-clean-text** second opinion from a single **non-Claude**
frontier model, reusable anywhere, and wired into `solution-audit` to add the model
diversity its all-Opus judges lack. At `reasoning=high` (chosen) this is a *thoughtful*
take (~15–40s), not an instant gut-check; no R2, no rating, no DB writes.

### 2.2 Design — lean codex-exec primitive (mirror `agreement.py`, NOT the heavy engine)

`agreement.py` already proves the pattern: `create_subprocess_exec(codex exec … -)` +
`communicate(stdin)` + `wait_for(timeout)`. We mirror it; we do **not** drag in
`engine.run_agent` (Task/heartbeat/output-cap/fail-patterns are unnecessary here).

**New module `owlex/second_opinion.py`:**

```python
MODEL     = os.getenv("OWLEX_SECOND_OPINION_MODEL", "gpt-5.5")
REASONING = os.getenv("OWLEX_SECOND_OPINION_REASONING", "high")    # quality-first; a real review, not the judge's shallow "low"
TIMEOUT   = int(os.getenv("OWLEX_SECOND_OPINION_TIMEOUT", "120"))  # high reasoning runs longer; generous headroom

FRAME = ("You are an independent second opinion for another AI engineer. Be concise. "
         "Give your own take, name the top risks, end with a clear recommendation.\n\n")

def _cmd(cwd):  # codex exec, read-only sandbox, JSON event stream
    cmd = ["codex","exec","--skip-git-repo-check","--json",
           "-c", f'model_reasoning_effort="{REASONING}"',
           "--model", MODEL, "--sandbox","read-only"]
    if cwd: cmd += ["--cd", cwd]
    return cmd + ["-"]

async def get_second_opinion(prompt, working_directory=None, timeout=None) -> tuple[bool,str]:
    # timeout = timeout or TIMEOUT          # None falls through to the 120s module default
    # create_subprocess_exec(*_cmd(...)) ; communicate(FRAME+prompt) ; wait_for(timeout)
    # returncode 0 AND non-empty extraction -> (True, text)
    # returncode 0 but EMPTY extraction    -> (False, "empty/unparseable codex --json output")
    #     ^^ FAIL-CLOSED (audit P1): without this, a rotated --json schema yields a silent
    #        success=True with an empty opinion. Mirrors agreement._parse_score's sentinel.
    # nonzero / asyncio.TimeoutError / FileNotFoundError -> (False, head of stderr/stdout)
    ...

def _extract_final_message(stdout: str) -> str:
    """codex --json emits JSONL. The answer is the agent_message item(s)."""
    out = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"): continue          # skip codex ERROR/preamble lines
        try: ev = json.loads(line)
        except json.JSONDecodeError: continue
        if ev.get("type") == "item.completed":
            item = ev.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                out.append(item["text"])
    return "\n\n".join(out).strip()
```

**Extraction is verified against real codex (probe 2026-06-08):** the final answer is the
line `{"type":"item.completed","item":{"type":"agent_message","text":"…"}}`; control
events (`thread.started`, `turn.started`, `turn.completed`) and codex's own skill-load
`ERROR` lines are ignored because they are not `item.completed/agent_message`.

**Context-cleanliness note (perf):** codex auto-loads `~/.claude/skills` + cwd `AGENTS.md`
(~14k input tokens observed even for "hello world"). For a pure-reasoning second opinion
that is wasted latency/cost. Mitigation option (design decision, default ON): run with
`--cd <ephemeral tmp dir>` so no repo `AGENTS.md` is pulled. Skill auto-load is codex-side
and not suppressible via flag here; accept it. `working_directory` param overrides the tmp
default when the caller *wants* repo context (e.g. solution-audit passing the repo).

### 2.3 MCP tool surface

**New `owlex/server/_second_opinion.py`** — thin tool, mirrors `_council.py` structure but
**without** the mandatory `rate_council` follow-up contract:

```python
from ..models import ErrorCode, TaskResponse   # match every other tool's error shape

@mcp.tool()
async def second_opinion(
    prompt: str,                       # Field(description=...)
    working_directory: str | None = None,
    timeout: int | None = None,        # None → module TIMEOUT (120); avoids overriding it with a stale 60 (audit P2)
) -> dict:
    """Fast independent second opinion from ONE non-Claude frontier model.
    Single call, no council rounds, no rating, nothing persisted.
    Use for a quick cross-check; for full deliberation use council_ask."""
    wd, error = validate_working_directory(working_directory)
    if error:                          # audit P1: error shape must match siblings
        return TaskResponse(success=False, error=error,
                            error_code=ErrorCode.INVALID_ARGS).model_dump()
    ok, text = await get_second_opinion(prompt.strip(), wd, timeout)
    if ok:
        return {"success": True, "model": MODEL, "opinion": text}
    return TaskResponse(success=False, error=text,
                        error_code=ErrorCode.EXECUTION_FAILED).model_dump()
```

**Register** in `owlex/server/__init__.py`: add `from . import _second_opinion` (with the
other tool-module imports) + back-compat re-export `from ._second_opinion import second_opinion`.

### 2.4 solution-audit integration ("Phase 1 — independent cross-model review, run in parallel")

`solution-audit`'s 5 dimension judges are all Opus → no model diversity, and they are the
ONLY reviewers. Add a **6th reviewer that is a different model**, running **in parallel**
with the Opus judges and reviewing the **same diff** — NOT the judges' findings.

**Why parallel + diff-anchored (not after + findings-anchored):** a second opinion that
reads the Claude judges' findings inherits their framing and blind spots — it can only
react to what they already surfaced, which defeats the point of model diversity. An
independent reviewer that sees only the raw changes can catch what the Opus judges missed
*entirely*. This is the same independence / anti-anchoring principle the research
established for R1 (independent answers *before* any cross-talk). Anchoring the second
opinion on the findings would reintroduce exactly the bias we are trying to remove.

- **Where:** Phase 1, in the **same parallel batch** as the 5 Opus dimension judges. The
  orchestrator (main session) fires the `mcp__owlex__second_opinion` call **concurrently**
  with the `Agent` judge spawns (one message, judges + second_opinion together). It is
  **blind** to the judges' output, and they to it.
- **Input:** the **diff / changed files only** — the same scope the dimension judges get —
  plus the same five dimensions as a lens, so its review is comparable. `working_directory`
  = repo root so codex can read the changed files.
- **Prompt shape:** *"You are an independent non-Claude reviewer. Review THESE code changes
  (not anyone's review of them) across: systematic, consistent, over-engineering/ad-hoc,
  complexity locus, performance. Give concrete issues with file:line where you can, and a
  one-line verdict per dimension."*
- **Report (Phase 3):** present its result as a separate **"Independent cross-model review
  (non-Claude)"** block *alongside* the 5 Opus dimensions — a genuine independent column,
  not a critique of the Claude findings. Labeled lower-trust (single model, citations not
  Phase-2-verified) but model-diverse.
- **Convergence signal:** where the non-Claude reviewer and the Opus judges independently
  land on the same issue, that agreement is itself high-signal; where they diverge, flag it
  for the user to look at.
- **Graceful skip:** if `second_opinion` returns `success=false` or owlex MCP is absent,
  the audit proceeds without it and notes "cross-model pass skipped" — never blocks.
- **Allowed-tools:** add `mcp__owlex__second_opinion` to the skill frontmatter.

This is **additive** — it does not change the 5-judge core, their citation contract, or the
Opus pin; it adds an independent parallel reviewer of the same diff.

### 2.5 Files touched (Item 2)

| File | Change |
|---|---|
| `owlex/second_opinion.py` | NEW — lean primitive + `_extract_final_message` |
| `owlex/server/_second_opinion.py` | NEW — `second_opinion` MCP tool |
| `owlex/server/__init__.py` | register import + back-compat export |
| `CLAUDE.md` (repo root, `## Environment` table at line ~81) | document tool + `OWLEX_SECOND_OPINION_*` env |
| `tests/test_second_opinion.py` | NEW — behavior tests (mock subprocess) |
| `~/.claude/skills/solution-audit/SKILL.md` | add independent cross-model reviewer to Phase 1 (parallel, diff-anchored) + allowed-tools entry + See-also |

### 2.6 Test plan (Item 2)

Mock `asyncio.create_subprocess_exec` — never call real codex in tests.
- `_extract_final_message` returns the joined `agent_message` text from a realistic JSONL
  fixture (incl. interleaved `ERROR` + control lines) → asserts the noise is stripped.
- `get_second_opinion` returns `(True, <text>)` on returncode 0; `(False, _)` on non-zero,
  on `asyncio.TimeoutError`, and on `FileNotFoundError`. Assert the **flag/behavior**, not
  error wording (project rule).
- `second_opinion` tool returns `success=True` + echoes `model` on success; `success=False`
  on bad `working_directory`.
- Argv builder includes `--model`, `--json`, `--sandbox read-only`, stdin `-`.

### 2.7 Risks / tradeoffs (Item 2)

- **codex catalog rotation** (documented Owlex failure mode): `gpt-5.5` could leave the
  codex catalog. Mitigation: model is env-pinned; reuse the existing startup-probe pattern
  if desired (non-goal for v1 — the tool degrades to `success=false`, audit skips).
- **codex context bloat / latency:** mitigated by ephemeral `--cd` default (§2.2).
- **Single-model, unverified signal:** explicitly labeled lower-trust in the report; not
  treated as a citation-grade finding.
- **codex-only (no gemini-3-pro) in v1:** accepted; a second runner is a later extension
  via the existing `AGENT_RUNNERS` registry. Explicit non-goal.

---

## 3. Non-goals (explicit)

- No A2A protocol, message bus, agent cards, or peer-to-peer transport (research: overkill
  for single-host; not the accuracy lever).
- No new derivations/analytics for `second_opinion` (ephemeral by design).
- No env-flag A/B harness for the preamble in v1.
- No second (gemini) runner for `second_opinion` in v1.
- No change to the 5-judge core of solution-audit.

## 4. Proposed task decomposition (seeds Plane work items)

Two independent tracks; Item 1 and Item 2 have no dependency on each other.

**Track 1 — Anti-sycophancy deliberation**
- T1.1 `prompts.py`: add `ANTISYCOPHANCY_PREAMBLE` + inject into `build_deliberation_prompt`. *(no deps)*
- T1.2 `roles.py`: add `synthesizer` role + `dialectic` team. *(no deps)*
- T1.3 Tests for T1.1 (preamble present, both modes + role-wrapped). *(deps: T1.1)*
- T1.4 Tests for T1.2 (role/team resolve). *(deps: T1.2)*
- T1.5 Full `pytest -q` green + `uv tool install --reinstall .` + `pkill owlex-server`. *(deps: T1.3, T1.4)*

**Track 2 — Second opinion**
- T2.1 `owlex/second_opinion.py` primitive + `_extract_final_message`. *(no deps)*
- T2.2 `owlex/server/_second_opinion.py` tool + register in `__init__.py`. *(deps: T2.1)*
- T2.3 `tests/test_second_opinion.py` (mocked subprocess). *(deps: T2.1)*
- T2.4 Docs: repo-root `CLAUDE.md` env table + tool entry. *(deps: T2.2)*
- T2.5 `solution-audit/SKILL.md`: independent cross-model reviewer in Phase 1 (parallel with the Opus judges, anchored on the diff — NOT the findings) + allowed-tools. *(deps: T2.2 — needs tool name)*
- T2.6 Full `pytest -q` green + reinstall + `pkill`; manual smoke of `second_opinion`. *(deps: T2.2, T2.3, T2.5)*

**Validation gate (this design):** `/solution-audit` on this document before any Plane task
is created.

## 5. Resolved decisions (review 2026-06-08)

1. **`dialectic` seat→role split:** approved as-is (claudeor + aichat = skeptic, gemini =
   synthesizer, codex/opencode/cursor = maintainer/architect/security).
2. **Preamble wording:** approved as-is (merit-based change demand judged appropriately
   strong, not over-correcting toward contrarianism).
3. **`second_opinion` reasoning:** **`high`** (quality over speed). Consequence: ~15–40s
   per call and `TIMEOUT` default raised to 120s. The "lightweight" property is now about
   *single-model, no-council, no-persistence*, not about sub-10s latency.
4. **`--cd` working dir:** **ephemeral default kept.** `working_directory=None` → codex runs
   in an ephemeral empty dir (clean, deterministic, can't read repo) for the generic
   second-opinion case; callers that need repo context (solution-audit) pass
   `working_directory=<repo root>` explicitly so codex can open the changed files. Both
   behaviors via one parameter — no compromise.
