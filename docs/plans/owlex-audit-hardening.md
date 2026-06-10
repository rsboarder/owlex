# Owlex Audit Hardening — Plane Deployment Plan

**Status:** drafted 2026-06-09, NOT yet deployed to Plane (Plane API returned HTTP 403 — token expired/no `chapta` access; re-deploy once auth restored).
**Source:** self-challenge of the `solution-audit` cross-model (`second_opinion`) approach (session 2026-06-09).
**Hard constraint (user):** every ticket REQUIRES a BEFORE/AFTER benchmark, run through the AUDIT-0 harness.
**Corpus decision:** **seeded + real** — synthetic diffs with known injected bugs (ground-truth for precision/recall, incl. decoys) PLUS real owlex git-history diffs (cost/realism).
**Dropped (kept as documented known-limitations, NOT tickets):** #7 enforcement-is-honor-system (skill-format ceiling), #8 auditing-the-auditor circularity (philosophical).

## Project
- Name: owlex (EXISTING project — locate by name/identifier; fall back to a new "Owlex audit hardening" project only if none exists)
- Workspace: chapta
- Identifier: (existing project's)

## Execution order (step-by-step via handover)
`AUDIT-0` → `AUDIT-2` → `AUDIT-1` → `AUDIT-6` → `AUDIT-3` → `AUDIT-4` → `AUDIT-5` → `AUDIT-9`
(AUDIT-5 and AUDIT-9 touch the `FRAME`/`_cmd` path; sequence them after AUDIT-2/AUDIT-6 to avoid conflicts.)

## Dependencies
| Task | Blocked by |
|------|------------|
| AUDIT-1 | AUDIT-0 |
| AUDIT-2 | AUDIT-0 |
| AUDIT-3 | AUDIT-0 |
| AUDIT-4 | AUDIT-0 |
| AUDIT-5 | AUDIT-0 |
| AUDIT-6 | AUDIT-0 |
| AUDIT-9 | — (independent) |

---

## AUDIT-0 — Benchmark harness + corpus for solution-audit
**Problem:** solution-audit improvements need BEFORE/AFTER benchmarks, but the audit is non-deterministic (LLM judges) and there is no fixed corpus or measurement infra. Detection-quality items (#1/#2/#4/#6) are unmeasurable without ground-truth-labeled diffs.
**Change:** build a harness under `bench/`:
- **Corpus (a) seeded** — synthetic diffs with known injected bugs, each labeled `{bug_type, file, line, description}` in `bench/corpus/seeded/manifest.json`; include **decoys** (plausible-but-not-a-bug) to measure precision.
- **Corpus (b) real** — a handful of real owlex git-history diffs (`git show <sha>`), unlabeled, for cost/realism.
- **Runner** — executes a given audit sub-step under test (cross-model `second_opinion` call / a single Opus judge / the full panel) on each corpus item, K times (default K=5) for variance; records per run: findings (file:line + text), tokens (where exposed), wall-time, reviewer count.
- **Scorer** — vs seeded ground-truth → precision / recall / detection-rate per item + aggregate (mean ± stdev over K); cost metrics for all items.
- **Baselines** — capture the "было" snapshot for every downstream metric to `bench/baselines/*.json` (committed).
**Acceptance:** `python bench/run.py --corpus seeded --target cross_model --runs 5` emits a reproducible JSON report (precision/recall + cost); seeded manifest has ≥10 labeled bugs across ≥3 types + ≥2 decoys; scorer unit tests (precision/recall on a hand-checked fixture) green; README documents adding a corpus item + running a before/after comparison.
**Benchmark (было/стало):** N/A — this IS the measurement infra. Self-test: scorer returns correct precision/recall on a fixture with known TP/FP/FN.
**Files:** `bench/` (new), `bench/corpus/seeded/`, `bench/baselines/`, scorer tests.
**Depends on:** none. **Blocks:** AUDIT-1, AUDIT-2, AUDIT-3, AUDIT-4, AUDIT-5, AUDIT-6.

## AUDIT-2 — Feed the cross-model the real git diff, not the orchestrator's prose
**Problem:** the Phase-1 cross-model reviewer is sold as "independent, diff-anchored, blind," but the orchestrator hands `second_opinion` a PROSE SUMMARY of the changes (its own editorialized description) + repo read access — reintroducing the orchestrator's framing/bias, the exact thing model-diversity should remove. Evidence: both 2026-06 audit runs led with a hand-written "1. REAP… 2. RETURN SIGNATURE…" summary, not raw hunks.
**Change:** SKILL.md Phase 1 — pass the actual `git diff <base>..HEAD` (or staged+unstaged) text to `second_opinion`, OR instruct codex to run `git diff` itself (read-only sandbox allows it) with `working_directory`=repo root. Remove the prose-summary step from the cross-model path; keep the 5-dimension lens as the only framing.
**Acceptance:** the cross-model prompt contains raw diff hunks (or an explicit "run git diff yourself" instruction), no editorialized prose; SKILL.md Phase-1 block updated; a manual run shows codex receiving the real diff.
**Benchmark (было/стало):**
- Metric: **recall** on the seeded corpus = % of seeded bugs detected (file:line match), mean ± stdev over K=5. Secondary: tokens.
- Procedure: run the cross-model step per corpus item — (было) prose-summary input vs (стало) raw-diff input.
- Success: raw-diff recall ≥ prose-summary recall (hypothesis: strictly higher); no recall regression on any bug type.
**Files:** `~/.claude/skills/solution-audit/SKILL.md` (Phase 1).
**Depends on:** AUDIT-0.

## AUDIT-1 — Verify cross-model findings in Phase 2
**Problem:** the cross-model reviewer surfaced the TOP finding in both audit runs (subprocess leak; wd-path logging) that the 5 Opus judges missed — yet its output is labeled "lower-trust, citations NOT Phase-2-verified" and Phase 2 only citation-checks the Opus judges. The most valuable reviewer is the least verified; a cross-model hallucination passes unchecked.
**Change:** extend SKILL.md Phase 2 to run the same mechanical citation-check on the cross-model's cited findings (open each file:line, confirm the cited code matches, drop non-resolving). Update Phase 3 so the cross-model block presents *verified* findings (and can promote passing ones above "lower-trust").
**Acceptance:** Phase 2 iterates cross-model findings with the same drop-on-non-resolve rule; SKILL.md updated.
**Benchmark (было/стало):**
- Metric: **precision** of cross-model findings = real (resolve to a seeded bug / true issue) / total.
- Procedure: on the seeded corpus (with decoys), (было) no verification vs (стало) post-Phase-2 — precision + # hallucinated/decoy findings dropped.
- Success: verified-set precision ≥ raw-set precision; ≥1 planted decoy/hallucination dropped on a bait corpus item.
**Files:** `~/.claude/skills/solution-audit/SKILL.md` (Phase 2, Phase 3).
**Depends on:** AUDIT-0.

## AUDIT-6 — Structured cross-model output
**Problem:** `second_opinion` returns free prose (`{"opinion": <text>}`); the orchestrator eyeball-parses it into per-dimension verdicts + convergence vs the Opus judges. Manual, error-prone, non-reproducible.
**Change:** have the cross-model emit a fixed per-dimension structure (JSON: `[{dimension, verdict, findings:[{file,line,issue}]}]`) — via a prompt contract in SKILL.md or a structured-output mode on the tool. Orchestrator matches convergence programmatically.
**Acceptance:** cross-model output reliably parseable per-dimension; SKILL.md (and/or tool) updated; orchestrator does a programmatic convergence match.
**Benchmark (было/стало):**
- Metric: **parse robustness + convergence-detection accuracy** — parse-error rate; # mis-attributed/missed convergences vs a hand-labeled gold set.
- Procedure: on the corpus, (было) manual prose-parse vs (стало) structured-parse.
- Success: structured parse-error rate ≈ 0; convergence-match accuracy ≥ manual baseline.
**Files:** `~/.claude/skills/solution-audit/SKILL.md` (Phase 1/3); optionally `owlex/server/_second_opinion.py` / `owlex/second_opinion.py` if a structured mode is added.
**Depends on:** AUDIT-0.

## AUDIT-3 — Size/risk gate on the reviewer panel
**Problem:** the full 6-reviewer panel (5 Opus + 1 cross-model) runs regardless of diff size — it ran on a ~40-line delta. The audit process is itself over-engineered for small/low-risk changes (it would fail its own "over-engineered" dimension).
**Change:** SKILL.md Phase 0/1 — add a size/risk gate that scales the reviewer set: under N changed lines (and no high-risk paths) run a reduced set (static + cross-model + 1 combined judge); full panel for large or high-risk diffs. Define thresholds.
**Acceptance:** a documented gate in SKILL.md selects the reviewer set from diff size/risk.
**Benchmark (было/стало):**
- Metric: **cost** — tokens + wall-time + reviewer-count; plus **quality-guard** — detection-rate must not drop.
- Procedure: small vs large corpus diffs, (было) always-full-panel vs (стало) gated; AND on a few labeled small diffs confirm detection-rate unchanged (within stdev).
- Success: material cost reduction on small diffs with detection-rate unchanged (overlapping stdev) on the labeled small set.
**Files:** `~/.claude/skills/solution-audit/SKILL.md` (Phase 0/1).
**Depends on:** AUDIT-0.

## AUDIT-4 — Define the council-escalation boundary
**Problem:** a single `second_opinion` call (n=1, gpt-5.5) isn't real "model diversity" — one extra sample, no variance estimate. `council_ask` (6 heterogeneous models, anonymized R1/R2 cross-critique, blind rating) is the actual diversity engine. The Phase-1↔Phase-4 boundary is fuzzy ("suggest council if ≥2 ⚠").
**Change:** SKILL.md — define an explicit escalation rule for WHEN the audit routes the cross-model step to `council_ask` instead of/in addition to `second_opinion`: e.g., diff touches high-risk domains (auth, subprocess, data) OR ≥ N changed lines OR ≥2 ⚠/❌ dimensions. Tighten Phase 4 from "suggest" to a rule.
**Acceptance:** explicit, testable escalation predicate in SKILL.md.
**Benchmark (было/стало):**
- Metric: **detection delta** — bugs caught by council (n=6) minus second_opinion (n=1) on high-stakes seeded diffs; plus cost delta.
- Procedure: on the "high-stakes"-tagged seeded subset, (было) second_opinion only vs (стало) council; compare recall + cost.
- Success: threshold set where council's recall gain on high-stakes diffs justifies its cost (documented break-even); low-stakes diffs → no escalation.
**Files:** `~/.claude/skills/solution-audit/SKILL.md` (Phase 1/4).
**Depends on:** AUDIT-0.

## AUDIT-5 — Split the two masters (FRAME + reasoning per use)
**Problem:** `second_opinion` serves both a generic quick gut-check AND the structured audit reviewer through one hardcoded `FRAME` ("independent second opinion… be concise…") + one reasoning/timeout default (high/120). Result: double-persona framing when the audit prompt also says "independent non-Claude reviewer," and the generic use overpays (high/120) for a quick check.
**Change:** let the caller supply the frame (or make FRAME minimal — drop the redundant persona, keep only output-shape hints), and right-size reasoning/timeout per use: generic default lower (e.g. medium/60), audit passes high explicitly. Decouple the two uses.
**Acceptance:** FRAME no longer hardcodes a persona the audit prompt duplicates; reasoning/timeout caller-controllable with use-appropriate defaults; tests updated.
**Benchmark (было/стало):**
- Metric: **cost** for the generic gut-check — tokens + latency on a fixed generic question; plus **quality-guard** — audit-path detection-rate unchanged.
- Procedure: (было) generic call at high/120 vs (стало) generic call at the new lower default; AND confirm audit path (explicit high) detection-rate on the corpus unchanged.
- Success: generic-use latency/tokens drop materially; audit detection-rate unchanged.
**Files:** `owlex/second_opinion.py` (FRAME, defaults), `owlex/server/_second_opinion.py`, `tests/test_second_opinion.py`.
**Depends on:** AUDIT-0 (for the audit-path quality-guard). **Note:** touches the same FRAME/prompt path as AUDIT-2/AUDIT-6 — sequence after them.

## AUDIT-9 — Extract shared `owlex/_codex.py` (argv + terminate)
**Problem:** `second_opinion._cmd` ≈ `agreement._build_judge_command` (both build a `codex exec … --sandbox read-only … -` argv) AND `_terminate(proc)` is byte-identical in `owlex/second_opinion.py` and `owlex/agreement.py` — a 2×2 duplication cluster. The audit deferred extraction to "when a 3rd consumer appears," but the cluster already exists.
**Change:** extract `owlex/_codex.py` with `build_codex_exec_argv(model, reasoning, *, json=False, sandbox="read-only", cwd=None, skip_git_repo_check=True)` + `terminate(proc)`; route `second_opinion.py` and `agreement.py` through it. Behavior byte-identical (each call site's generated argv unchanged).
**Acceptance:** both modules import the shared helpers; generated argv for each existing call site identical to before (assert in a test); full suite green (303+).
**Benchmark (было/стало):**
- Metric: **duplication** — duplicate-line/duplicate-block count across the two modules (`cloc --diff` or a simple counter), plus argv-equality.
- Procedure: (было) count duplicated lines of `_cmd`/`_build_judge_command`/`_terminate`×2 vs (стало) after extraction (single source); assert generated argv identical for both call sites; run full suite.
- Success: duplication eliminated (one source of truth), argv byte-identical, zero behavior change, suite green.
**Files:** `owlex/_codex.py` (new), `owlex/second_opinion.py`, `owlex/agreement.py`, tests.
**Depends on:** none. **Note:** touches `_cmd` — sequence after AUDIT-2/AUDIT-5 if they change `_cmd`/FRAME.
