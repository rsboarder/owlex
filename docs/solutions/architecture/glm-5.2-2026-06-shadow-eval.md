---
title: "GLM-5.2 (June 2026) — shadow eval baseline for Owlex seat/judge/rater candidacy"
status: confirmed
category: architecture
type: adr  # point-in-time decision record — re-run if GLM version bumps materially
date: 2026-06-17
model_version: glm-5.2 (released 2026-06-13) via Z.ai Anthropic-compatible API, reasoning=default
---

## Context

GLM-5.2 (Z.ai, released 2026-06-13) is a 744B-param MoE (~40B active), 1M-token context,
dual reasoning modes (High/Max). Vendor claims it beats GPT-5.5 on long-horizon coding at
~1/6th the cost — but it **launched with no third-party benchmarks** (self-eval only). This
is exactly the case the shadow-replay protocol exists for: vendor positioning consistently
diverges from measured behavior on the council-deliberation workload.

Considered for three possible roles: R1 council seat, agreement judge, blind rater.

**Access path:** GLM has no standalone agent CLI (unlike Grok's `grok`). It runs through
Z.ai's Anthropic-compatible endpoint (`https://api.z.ai/api/anthropic`) — the same endpoint
family the `claudeor` seat already uses. A GLM Coding Plan key (flat-rate subscription)
authenticates. The shadow scripts call `/v1/messages` **directly** (not via the `claude` CLI):
the CLI reloads ~30k tokens of owlex project context per call (~$0.45, ~10s) and would pollute
the candidate's view; direct REST isolates the model and is faithful for judge/rater roles.

Followed the shadow-replay protocol (`shadow-replay-protocol-for-seat-evaluation.md`). Three
read-only experiments against `~/.owlex/owlex.db`.

## Measurements (2026-06-17)

### Experiment 1 — Agreement-judge replay (N=67 replayed, 64 valid)

| Metric | GLM-5.2 | Grok-build (baseline) |
|---|---|---|
| Cohen's kappa (vs gpt-5.5 judge, threshold=3.5) | **0.158** | 0.510 |
| Pearson correlation (raw 1-5) | 0.414 | 0.491 |
| R2-decision binary agreement | 64.1% | 75.8% |
| Mean score (orig vs candidate) | 3.47 → **4.28** | — |
| Saves (orig judge failed, candidate scored) | 11/11 | 20/20 |

Confusion matrix (rows = current judge, cols = GLM): TN=36, FP=2, **FN=21**, TP=5.

**Core finding — systematic leniency.** GLM scores agreement ~0.8 points higher than the
current judge (mean 4.28 vs 3.47). With the fixed 3.5 R2-trigger threshold it **misses 21 of
26 councils where R2 was actually needed** (FN=21). Part of the κ collapse is calibration (the
threshold is tuned to gpt-5.5's distribution; GLM's whole scale is shifted up), but the
calibration-robust Pearson (0.414) is still only weak-moderate. GLM is a *worse* judge than
Grok, and its failure mode (under-trigger R2) is more harmful than Grok's (over-trigger).

### Experiment 2 — Seat R1 shadow (N=15, 0 errors, 0 timeouts)

Compared against the seats that ran on these 15 councils (codex, aichat, opencode). Medians:

| Seat | Latency med | Length med | Code blocks med | File refs med | Headings med |
|---|---|---|---|---|---|
| **glm-5.2** | **47.1s (fastest)** | 6246 | **0** (10/15 zero) | 2 | 6 (most structured) |
| codex | 59.7s | 5500 | 1 | 0 | 0 |
| aichat | 65.7s | 6034 | 0 | 0 | 0 |
| opencode | 91.2s | 5707 | 0 | 1 | 0 |

Key structural findings:
- **Fastest and most reliable seat** — median 47s, max 90s, 0 timeouts. The *opposite* of
  Grok (158s median, slowest, 2/15 timeouts, +30s council overhead).
- Length and file-refs are mid-pack — adequate grounding, not shallow.
- **Code-block-light** — 10/15 responses had zero code blocks (mean 0.7), echoing Grok's
  weakness. GLM produces well-structured prose-with-headings rather than code.
- Caveat: this is the *bare model* (no agent tools / project context). A production seat via
  the `claude` CLI with tools could reference more code, so this understates grounding.

### Experiment 3 — Blind-rater replay (N=47 councils, 133 agent ratings)

| Metric | GLM-5.2 | Grok-build (baseline) |
|---|---|---|
| Score exact match (-1/+1) | **88.0%** | 76.9% |
| Top-1 winner agreement | **69.6%** | 58.9% |
| Spearman ρ — groundedness | 0.126 | 0.438 |
| Spearman ρ — helpfulness | 0.289 | 0.225 |
| Spearman ρ — correctness | 0.230 | 0.388 |

**Per-agent flip rate** (the metric that sank Grok-as-rater):

| Seat | GLM flip rate | Grok flip rate |
|---|---|---|
| opencode | 19% (6/32) | 18% |
| codex | 11% (5/46) | 11% |
| gemini | 10% (1/10) | **50%** |
| aichat | 9% (3/32) | 18% |
| claudeor | 8% (1/13) | 19% |

**Core finding — no per-agent bias.** Grok was rejected as a rater because it downgraded
*gemini* 50% of the time (systematic, not noise). GLM-5.2 has **no such concentration** —
flips are 8–19% across all seats. It runs uniformly *stricter* than the Claude blind-rater
(15 of 16 flips are GLM-harsher), but that's a flat offset, not a grudge against one seat.
The graded-dimension correlations are weak (ρ 0.13–0.29), so GLM's value is in the binary
accept/reject + winner pick, **not** the 1–5 dimension scores.

## Methodological caveat — harness & reasoning confound (added 2026-06-17, post deep-research + Max re-run)

The incumbent seats/judge/rater run through full agentic CLIs (codex CLI, cursor, Claude
Code); the shadow above called GLM **bare-API at `reasoning=default`** — its weakest mode,
no tool harness. This is a real confound: holding the model constant, the agent harness alone
moves coding-benchmark scores **5–40 points** (CORE-Bench Opus 42%→78% minimal-vs-Claude-Code;
Cursor 46%→80%; a bare/minimal adapter floors at ~19% on SWE-bench). And reasoning improves
LLM-as-judge calibration by 5–12 points. So the bare run is a **floor**, not a fair verdict.

To separate "weak model" from "weak harness", the judge and rater (pure text tasks where tools
don't matter) were re-run at **`reasoning=max`** (verified param against the Z.ai Anthropic
endpoint: `thinking{type:enabled}` + `reasoning_effort:max`). Baseline preserved as `*_default.*`.

**Judge — default vs Max (N=67):**

| Metric | default | Max |
|---|---|---|
| Cohen's κ | 0.158 | 0.292 |
| Pearson | 0.414 | 0.469 |
| Mean GLM score (real judge = 3.47) | 4.30 | 4.06 |
| Missed-R2 (FN) | 21 | 17 |

**Rater — default vs Max (N=47 councils, 133 ratings):**

| Metric | default | Max |
|---|---|---|
| Binary exact match | 88.0% | 94.0% |
| Top-1 winner agreement | 69.6% | 52.2% |
| Spearman ρ (ground/help/corr) | .13/.29/.23 | .18/.21/.25 |
| Worst per-agent flip | 19% | 10% (still no concentration) |

**Reading.** Max reasoning gave a *mixed* shift, not a uniform lift. Judge improved (κ +0.13,
leniency down). For the rater, binary accept/reject agreement rose (88→94%) and per-agent bias
stayed absent — but winner-pick agreement actually *fell* (70→52%, N=46 so noisy) and graded-
dimension correlations stayed weak. So part of the original weakness WAS a no-reasoning artifact
(as the literature predicts), but **no verdict flipped:** the judge stays well below the 0.6
replacement bar and still over-rates agreement (leniency is partly inherent); the rater is a
solid *binary* second-opinion (flag weak answers) but neither a dimension-score nor a winner-
ranking replacement. The **seat (E2) remains genuinely
confounded** — bare-API has no tools to read files, so the low code-block/file-ref counts are
largely a harness artifact; a fair seat test needs GLM re-run through a **model-agnostic harness
(opencode) with tools + Max** before any verdict. (Caveat from research: a Claude-tuned harness
like Claude Code can itself penalize a third-party model, and Max can trigger "overthinking" on
easy items — so the harnessed re-run is not guaranteed favorable.)

### Harnessed seat re-run (2026-06-17) — opencode + tools + Max (N=13)

The fair seat test: GLM-5.2 driven through **opencode** (model-agnostic harness, read-only
tools, `--variant max`) in the owlex repo, vs the seats present on the same councils. Medians:

| Seat | Latency | Length | Code blocks | File refs |
|---|---|---|---|---|
| **glm (opencode)** | **163s (slowest)** | 8442 (longest) | **0** (mean 0.4, lowest) | **0** (mean 0.6, lowest) |
| codex | 52s | 5500 | 2 | 0 |
| opencode | 86s | 5707 | 0 | 1 |
| aichat | 63s | 6034 | 0 | 0 |

**Result — the harness did NOT rescue the seat verdict.** Given full code-reading tools and Max
reasoning, GLM still produced the **fewest code blocks and fewest file-refs** of any seat (same
profile as the bare-API run) — so "code-block-light" is a genuine model-style trait, not a
harness artifact. It also became the **slowest seat by 2–3×** (median 163s vs 52–86s) with
**2/15 calls timing out at 360s** — as a seat it would dominate council wall-clock. Its one
strength: the longest, most-heading-structured prose reviews (a spot-check showed a genuinely
sharp adversarial review with concrete P0 failure modes). Net: GLM-as-seat = a thorough but
**slow, code-light prose reviewer**. The bare-API run undersold its depth/length, but the
*decisive weaknesses (grounding + latency) hold under the fair harness*. (Caveats: N=13;
compared only vs codex/opencode/aichat — the seats on this subset; read-only harness, no bash.)

### Quality blind-rate (2026-06-18) — settles the proxy debate (N=13)

The structural proxy (fenced ``` code blocks) unfairly penalized GLM, which grounds via
*inline* code/symbol references, not fenced blocks (e.g. council 233423: 0 fenced blocks but
~42 inline-code refs and a sharp, specific P1/P2 review). To bypass proxies entirely, GLM's 13
harnessed R1 answers were dropped in **anonymously alongside the real seats' answers** and
blind-rated by **gpt-5.5** (the owlex judge's model — it never knew which answer was GLM's):

| Metric | GLM-5.2 (opencode) | Incumbents (pooled) |
|---|---|---|
| Accept rate (+1) | 69% (9/13) | 90% (35/39) |
| Top-1 (best in council) | **0% (0/13)** | chance ≈ 25% |
| Mean rank (1=best) | 3.54 / 4 | — |
| Dim means (ground/help/corr) | 3.15 / 3.54 / 3.23 | 3.95 / 4.15 / 4.03 |

**gpt-5.5-only read (since OVERTURNED — see panel below):** by gpt-5.5 alone, GLM landed below
every incumbent (69% accept, 0% top-1, mean rank 3.54, ~0.8 lower on every dimension). That
*looked* like a real quality gap — but gpt-5.5 is a competitor model, and one answer a human read
as sharp (233423) it scored −1, so a 3-rater panel was run to check for single-rater bias.

### Panel blind-rate (2026-06-18) — 3 raters, OVERTURNS the gpt-5.5-only verdict (N=13)

Same blind layout, rated independently by gpt-5.5 (codex), claude, and gemini:

| Rater | Accept (+1) | Top-1 | Mean GLM rank (1=best of 4) | GLM dims (g/h/c) | Incumbent dims |
|---|---|---|---|---|---|
| gpt-5.5 | 62% | 0% | **3.46** | 3.31/3.38/3.15 | 4.05/4.18/4.08 |
| claude  | 92% | 50% | **2.08** | 4.42/4.25/4.75 | 4.28/4.42/4.42 |
| gemini  | 100% | 31% | **2.23** | 4.69/4.62/4.77 | 4.46/4.18/4.49 |

Consensus: GLM was bottom-half for **all three** raters in only **3/12** councils.

**Conclusion (revised): the "weak seat" verdict does NOT survive the panel — it was largely a
gpt-5.5 artifact.** gpt-5.5 is a harsh outlier (ranks GLM last in 9/13, never best); claude and
gemini independently rank GLM **competitive-to-best** (mean rank ~2.1–2.2 of 4, accept 92–100%,
top-1 31–50%, dimension scores **at or above** the incumbent pool). So GLM-5.2 as a harnessed
seat is **quality-competitive, not weak** — the earlier negatives came first from a biased
structural proxy, then from a single competitor-model rater (a live instance of the LLM-as-judge
bias this whole protocol guards against). Honest caveat: this is still an LLM-as-judge result with
visible *style affinity* (gpt-5.5 favors code-heavy answers; claude/gemini favor GLM's prose-review
style), so "competitive / middle-of-pack" is the fair read, not "clearly best." The one
rater-independent negative that DOES hold: **latency** — GLM was the slowest seat (median 163s,
2/15 timeouts at Max; `--variant high` would roughly halve it).

Research sources: CORE-Bench / Scale standardized-harness analyses; "Explicit Reasoning Makes
Better Judges" (arXiv 2509.13332); overthinking (arXiv 2604.10739); model-harness-fit penalty
for third-party models in vendor-tuned harnesses (nicolasbustamante.com/blog/model-harness-fit).

## Decision

| Proposed role | Verdict | Reason |
|---|---|---|
| R1 seat (problem-solver) | **Competitive (panel-corrected)** | 3-rater blind panel overturned the earlier "weak" call (a gpt-5.5/proxy artifact): claude & gemini rank GLM mean ~2.1–2.2 of 4 with dims ≥ incumbents; bottom-half-for-all-3 only 3/12. Quality-competitive, not weak. Only rater-independent cost: slowest seat (median 163s, 2/15 timeouts at Max; `high` ~halves it). A viable, slower optional seat. |
| Replace agreement judge | **Rejected (confirmed at Max)** | κ 0.158→0.292 with Max reasoning — still far below 0.6; over-rates agreement (mean 4.06 vs real 3.47). Leniency partly inherent, not just under-reasoning. |
| Fallback agreement judge | **Rejected** | 11/11 saves, but leniency under-triggers R2 — worse failure mode than Grok's over-trigger; unchanged by Max. |
| Replace blind rater | **Rejected** | Graded-dimension ρ stays weak at Max (.18/.21/.25); winner-pick agreement *fell* to 52% at Max. Diverges on 1–5 scores and on ranking regardless of reasoning. |
| **Ensemble / 2nd-opinion blind rater** | **Approved (confirmed at Max, pending prod-shadow)** | Binary exact match 88%→**94%** at Max, **no per-agent bias** (max 10% vs Grok's 50% gemini), uniform stricter offset. The one role where GLM-5.2 clearly beats Grok. Off by default until prod-shadow validation. |

**Bottom line.** GLM-5.2 is a **niche fit, not a general win**. Its standout — surviving the
fair-reasoning re-run — is as an *unbiased, stricter binary second-opinion rater* (more than
Grok ever earned). Not a judge (too lenient even at Max), not a clean rater replacement (weak
graded correlations). Seat — after a fair harness *and* a 3-rater blind panel — is
**quality-competitive** (the earlier "weak" was a gpt-5.5/proxy artifact); its only real cost is
latency (slowest seat). Net: a strong ensemble-rater (off by default) and a viable *optional,
slower* seat; reject only the judge. The meta-lesson — a single competitor-model rater nearly
shipped a false "weak" verdict — is itself the strongest finding here.

## Re-test trigger

Re-run the scripts if any of the following change materially:
- GLM model bump (current: glm-5.2) — especially a new coding-tuned alias.
- `reasoning=max` tested 2026-06-17 (set `OWLEX_GLM_REASONING=max`): improved judge κ and rater
  binary agreement but flipped no verdict. `high` (cheaper) untested.
- Seat re-run done *harnessed* (opencode + tools + Max, 2026-06-17, N=13): verdict resolved —
  GLM stays code-light (fewest code blocks/file-refs even with tools) and is the slowest seat
  (2/15 timeouts). Re-test only if GLM ships a more code-emitting / faster coding-tuned variant.
- We want to exploit the 1M context for very long councils (current councils don't need it).

Scripts are idempotent (rater/seat resume-safe via JSONL; judge overwrites) and read-only
against the production DB. Re-run cost ≈ 25 min background, ~135 GLM calls on the Coding Plan.

## Artifacts

```
scripts/_glm_client.py                          — shared Z.ai Anthropic-compatible caller
scripts/shadow_glm_judge.py                     — Experiment 1 source
scripts/shadow_glm_seat.py                      — Experiment 2 source
scripts/shadow_glm_rater.py                     — Experiment 3 source
scripts/shadow_results/agreement_replay_glm.jsonl + agreement_summary_glm.md   — E1
scripts/shadow_results/seat_r1_responses_glm.jsonl + seat_r1_metrics_glm.md    — E2
scripts/shadow_results/rater_replay_glm.jsonl + rater_summary_glm.md           — E3
scripts/shadow_results/glm_eval_run.log         — full run log
```
