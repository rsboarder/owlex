#!/usr/bin/env python3
"""Paired A/B benchmark for Grok seat harness changes.

Replays historical council R1 prompts through two harness arms (same model),
measures structural metrics always, and optionally blind-rates A vs B via codex.

Read-only against ~/.owlex/owlex.db. Does NOT modify production owlex config.

Design + ship gates:
  docs/design/ab-grok-harness-benchmark.md

Usage:
  python scripts/ab_grok_harness.py --arm-a baseline --arm-b contract_strip --limit 3
  python scripts/ab_grok_harness.py --arm-a baseline --arm-b contract_strip --limit 20 --rate
  python scripts/ab_grok_harness.py --list-arms
  python scripts/ab_grok_harness.py --corpus-only --limit 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

OWLEX_DB = Path(os.path.expanduser("~/.owlex/owlex.db"))
RESULTS_DIR = Path(__file__).parent / "shadow_results"

GROK_MODEL = os.getenv("OWLEX_GROK_MODEL", "grok-4.5")
GROK_TIMEOUT = int(os.getenv("OWLEX_GROK_TIMEOUT", "450"))
CODEX_MODEL = os.getenv("OWLEX_AGREEMENT_MODEL", "gpt-5.5")
CODEX_TIMEOUT = int(os.getenv("OWLEX_QUALITY_TIMEOUT", "180"))

OUTPUT_CONTRACT_SUFFIX = """

## Output contract (mandatory)
- Start with: **Verdict:** (1–2 sentences)
- Then: **Evidence** with file:line citations where possible
- Then: **Residual risks** (bullet list, max 5)
- Then: **Recommendation** (ship / ship with N / block) and why
- Do NOT include tool plans, "I'll read…", "Pulling…", or intermediate narration in the final answer
- Prefer concise findings over exhaustive dumps unless the question demands depth
"""

_TOOLISH_START = re.compile(
    r"(?is)^\s*(I'll|I will|Let me|Reading|Pulling|Starting|Next I'll|"
    r"Looking|Checking|Digging|I need to|First,?\s+I)\b"
)
_TOOLISH_LINE = re.compile(
    r"(?im)^(I'll |I will |Next I'll |Pulling |Reading |Checking |"
    r"Looking at |Digging |Starting with |Let me )\b"
)
_HEADING_OR_VERDICT = re.compile(
    r"(?im)^(?:#{1,3}\s|\*\*Verdict\*\*|Verdict:|##\s*Executive summary)"
)
_VERDICT_MARK = re.compile(
    r"(?i)\bverdict\b|\bexecutive summary\b|\brecommendation\b"
)
_RISKS_MARK = re.compile(r"(?i)\bresidual risks?\b|\brisks?\b|\bfailure modes?\b")
_REC_MARK = re.compile(r"(?i)\brecommendation\b|\bship\b|\bblock\b")
_FILE_REF = re.compile(
    r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|sql|md|json|yaml|yml)(?::\d+)?\b"
)


# ---------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Arm:
    id: str
    effort: str = "low"
    output_contract: bool = False
    strip_narration: bool = False
    # If set, this arm reuses raw text from `derive_from` arm (no second CLI call).
    derive_from: str | None = None
    description: str = ""


ARMS: dict[str, Arm] = {
    "baseline": Arm(
        id="baseline",
        effort="low",
        output_contract=False,
        strip_narration=False,
        description="Production mirror: low effort, no contract, no strip",
    ),
    "contract": Arm(
        id="contract",
        effort="low",
        output_contract=True,
        strip_narration=False,
        description="Prompt output-contract suffix only",
    ),
    "strip": Arm(
        id="strip",
        effort="low",
        output_contract=False,
        strip_narration=True,
        derive_from="baseline",
        description="Post-process strip of tool narration (derived from baseline raw)",
    ),
    "contract_strip": Arm(
        id="contract_strip",
        effort="low",
        output_contract=True,
        strip_narration=True,
        description="Phase-0 package: contract + strip",
    ),
    "effort_medium": Arm(
        id="effort_medium",
        effort="medium",
        output_contract=False,
        strip_narration=False,
        description="Effort medium only (expect slower)",
    ),
    "contract_strip_medium": Arm(
        id="contract_strip_medium",
        effort="medium",
        output_contract=True,
        strip_narration=True,
        description="Combined contract+strip at medium effort",
    ),
}


# ---------------------------------------------------------------------------
# Text / metrics (pure — unit-tested)
# ---------------------------------------------------------------------------

def parse_grok_text(raw: str) -> str:
    """Extract ``text`` from Grok JSON envelope; fall back to raw."""
    if not raw:
        return ""
    text = raw.strip()
    if text.startswith("Grok Output"):
        text = text.split("\n", 1)[-1].strip() if "\n" in text else text
    try:
        i = text.find("{")
        if i >= 0:
            obj = json.loads(text[i:])
            if isinstance(obj, dict) and "text" in obj:
                return str(obj.get("text") or "")
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    last = text.rfind("}")
    if last > 0:
        try:
            obj = json.loads(text[: last + 1])
            if isinstance(obj, dict) and "text" in obj:
                return str(obj.get("text") or "")
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return text


def strip_tool_narration(text: str) -> str:
    """Drop leading tool-monologue until first structural heading/verdict.

    Also drops isolated mid-document toolish one-liners that look like
    agent chatter (keeps everything else).
    """
    if not text:
        return ""
    m = _HEADING_OR_VERDICT.search(text)
    if m and m.start() > 0:
        # Only strip if the prefix looks like tool chatter, not real content
        prefix = text[: m.start()]
        if _TOOLISH_START.match(prefix) or len(prefix) < 800:
            text = text[m.start() :]
    # Drop pure toolish lines (single-line agent status)
    kept = []
    for ln in text.splitlines():
        if _TOOLISH_LINE.match(ln) and len(ln) < 200:
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def preamble_chars(text: str) -> int:
    if not text:
        return 0
    m = _HEADING_OR_VERDICT.search(text)
    if not m:
        return min(len(text), 2000)
    return m.start()


def structural_metrics(text: str) -> dict[str, Any]:
    if not text:
        return {
            "length": 0,
            "code_blocks": 0,
            "file_refs": 0,
            "bullets": 0,
            "headings": 0,
            "preamble_chars": 0,
            "toolish_start": False,
            "has_verdict": False,
            "has_risks": False,
            "has_recommendation": False,
            "contract_ok": False,
        }
    code_blocks = text.count("```") // 2
    file_refs = len(_FILE_REF.findall(text))
    bullets = sum(
        1
        for ln in text.split("\n")
        if ln.strip().startswith(("- ", "* ", "1.", "2.", "3."))
    )
    headings = sum(1 for ln in text.split("\n") if ln.strip().startswith("#"))
    has_verdict = bool(_VERDICT_MARK.search(text))
    has_risks = bool(_RISKS_MARK.search(text))
    has_rec = bool(_REC_MARK.search(text))
    toolish = bool(_TOOLISH_START.match(text))
    pre = preamble_chars(text)
    # Contract: verdict-ish start region + risks + recommendation signals
    head = text[:400]
    starts_well = bool(
        re.search(r"(?i)(\*\*Verdict\*\*|Verdict:|^#\s)", head)
        or (has_verdict and pre < 120)
    )
    contract_ok = bool(starts_well and has_risks and has_rec)
    return {
        "length": len(text),
        "code_blocks": code_blocks,
        "file_refs": file_refs,
        "bullets": bullets,
        "headings": headings,
        "preamble_chars": pre,
        "toolish_start": toolish,
        "has_verdict": has_verdict,
        "has_risks": has_risks,
        "has_recommendation": has_rec,
        "contract_ok": contract_ok,
    }


def apply_arm_to_text(raw_text: str, arm: Arm) -> str:
    """Parse envelope then optionally strip narration."""
    text = parse_grok_text(raw_text)
    if arm.strip_narration:
        text = strip_tool_narration(text)
    return text


def build_prompt(base_prompt: str, arm: Arm) -> str:
    if arm.output_contract:
        return base_prompt.rstrip() + OUTPUT_CONTRACT_SUFFIX
    return base_prompt


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

def load_corpus(limit: int, days: int = 60) -> list[dict[str, Any]]:
    """Historical councils where aichat/grok completed and codex has the prompt."""
    if not OWLEX_DB.exists():
        raise FileNotFoundError(f"DB not found: {OWLEX_DB}")
    uri = f"file:{OWLEX_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              g.council_id AS council_id,
              g.duration_s AS grok_duration_s,
              g.output_chars AS grok_output_chars,
              g.started_at AS started_at,
              substr(g.result_text, 1, 400) AS grok_preview,
              p.prompt_text AS prompt,
              length(p.prompt_text) AS prompt_len
            FROM calls g
            JOIN calls p
              ON p.council_id = g.council_id
             AND p.agent = 'codex'
             AND p.round = 1
             AND p.status = 'completed'
             AND p.prompt_text IS NOT NULL
             AND length(p.prompt_text) > 500
            WHERE g.agent = 'aichat'
              AND g.model LIKE 'grok%'
              AND g.round = 1
              AND g.status = 'completed'
              AND g.started_at >= datetime('now', ?)
            ORDER BY g.started_at DESC
            """,
            (f"-{days} days",),
        ).fetchall()
    finally:
        conn.close()

    # Dedupe by council_id (keep newest — already ordered DESC)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        cid = r["council_id"]
        if cid in seen:
            continue
        seen.add(cid)
        out.append(
            {
                "council_id": cid,
                "prompt": r["prompt"],
                "prompt_len": r["prompt_len"],
                "started_at": r["started_at"],
                "baseline_prod": {
                    "duration_s": r["grok_duration_s"],
                    "output_chars": r["grok_output_chars"],
                    "preview": r["grok_preview"] or "",
                },
            }
        )
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# CLI runners
# ---------------------------------------------------------------------------

async def call_grok(
    prompt: str,
    *,
    effort: str,
    cwd: str | None,
    timeout: int,
) -> tuple[str, float, str | None]:
    t0 = time.time()
    cmd = [
        "grok",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--always-approve",
        "--model",
        GROK_MODEL,
        "--effort",
        effort,
        "--disable-web-search",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return "", time.time() - t0, f"timeout after {timeout}s"
    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:300]
        return "", time.time() - t0, f"grok exit {proc.returncode}: {err}"
    return stdout.decode(errors="replace").strip(), time.time() - t0, None


async def call_codex_rate(prompt: str) -> tuple[str, str | None]:
    with tempfile.TemporaryDirectory() as td:
        proc = await asyncio.create_subprocess_exec(
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--model",
            CODEX_MODEL,
            "--cd",
            td,
            "--dangerously-bypass-approvals-and-sandbox",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(prompt.encode()), timeout=CODEX_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "", f"timeout after {CODEX_TIMEOUT}s"
    if proc.returncode != 0:
        return "", f"codex exit {proc.returncode}: {stderr.decode(errors='replace')[:200]}"
    return stdout.decode(errors="replace"), None


BLIND_PAIR_PROMPT = """\
You are a senior software engineering reviewer. Two anonymized advisors answered
the same question. Rate each on content alone — be strict and discriminating.

ORIGINAL QUESTION (truncated):
{question}

RESPONSE A:
{resp_a}

RESPONSE B:
{resp_b}

Return ONLY a JSON object:
{{"A": {{"score": 1 or -1, "groundedness": 1-5, "helpfulness": 1-5, "correctness": 1-5, "reason": "..."}},
  "B": {{"score": 1 or -1, "groundedness": 1-5, "helpfulness": 1-5, "correctness": 1-5, "reason": "..."}}}}
"""


def extract_question(prompt_text: str, max_len: int = 2000) -> str:
    text = prompt_text or ""
    if text.startswith("IMPORTANT: This is a council deliberation"):
        idx = text.find("- Provide your analysis")
        if idx >= 0:
            rest = text[idx:]
            text = rest.split("\n", 1)[1] if "\n" in rest else rest
    if text.startswith("PROJECT CONTEXT:"):
        idx = text.find("\n\n")
        if idx >= 0:
            text = text[idx + 2 :]
    return text.strip()[:max_len]


def parse_pair_ratings(raw: str) -> dict[str, dict] | None:
    candidates: list[str] = []
    if "```" in raw:
        for chunk in raw.split("```"):
            c = chunk.strip()
            if c.startswith("json"):
                c = c[4:].strip()
            if c.startswith("{"):
                candidates.append(c)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for c in candidates:
        try:
            parsed = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and "A" in parsed and "B" in parsed:
            if isinstance(parsed["A"], dict) and isinstance(parsed["B"], dict):
                return parsed
    return None


# ---------------------------------------------------------------------------
# Generation + rating pipeline
# ---------------------------------------------------------------------------

def results_paths(arm_a: str, arm_b: str) -> tuple[Path, Path]:
    tag = f"ab_grok_{arm_a}_vs_{arm_b}"
    return RESULTS_DIR / f"{tag}.jsonl", RESULTS_DIR / f"{tag}.md"


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def generation_key(arm: Arm) -> str:
    """Arms that only differ by strip share a generation key (derive_from)."""
    if arm.derive_from:
        return arm.derive_from
    # strip-only derive is explicit; contract_strip generates with contract
    return f"gen:{arm.effort}:contract={int(arm.output_contract)}"


async def generate_for_arm(
    item: dict,
    arm: Arm,
    cache: dict[str, dict],
    *,
    cwd: str | None,
    timeout: int,
) -> dict[str, Any]:
    """Return arm result dict; uses cache for shared generations / derive_from."""
    if arm.derive_from:
        base = cache.get(generation_key(ARMS[arm.derive_from]))
        if not base:
            # generate parent first
            parent = ARMS[arm.derive_from]
            base = await generate_for_arm(item, parent, cache, cwd=cwd, timeout=timeout)
        if base.get("error"):
            return {
                "arm": arm.id,
                "error": base["error"],
                "elapsed_s": 0.0,
                "derived": True,
                "derive_from": arm.derive_from,
            }
        text = apply_arm_to_text(base["raw"], arm)
        metrics = structural_metrics(text)
        return {
            "arm": arm.id,
            "elapsed_s": 0.0,  # post-process free
            "derived": True,
            "derive_from": arm.derive_from,
            "text": text,
            "metrics": metrics,
            "raw_chars": len(base.get("raw") or ""),
            "parent_elapsed_s": base.get("elapsed_s"),
        }

    gkey = generation_key(arm)
    if gkey in cache:
        base = cache[gkey]
        if base.get("error"):
            return {
                "arm": arm.id,
                "error": base["error"],
                "elapsed_s": base.get("elapsed_s", 0.0),
            }
        text = apply_arm_to_text(base["raw"], arm)
        return {
            "arm": arm.id,
            "elapsed_s": base["elapsed_s"],
            "derived": False,
            "text": text,
            "metrics": structural_metrics(text),
            "raw_chars": len(base["raw"]),
        }

    prompt = build_prompt(item["prompt"], arm)
    raw, elapsed, err = await call_grok(
        prompt, effort=arm.effort, cwd=cwd, timeout=timeout
    )
    if err:
        cache[gkey] = {"error": err, "elapsed_s": elapsed, "raw": ""}
        return {"arm": arm.id, "error": err, "elapsed_s": round(elapsed, 2)}

    cache[gkey] = {"raw": raw, "elapsed_s": round(elapsed, 2)}
    text = apply_arm_to_text(raw, arm)
    return {
        "arm": arm.id,
        "elapsed_s": round(elapsed, 2),
        "derived": False,
        "text": text,
        "metrics": structural_metrics(text),
        "raw_chars": len(raw),
    }


async def rate_pair(
    item: dict,
    res_a: dict,
    res_b: dict,
    arm_a: str,
    arm_b: str,
) -> dict[str, Any]:
    if res_a.get("error") or res_b.get("error"):
        return {"skipped": True, "reason": "generation_error"}
    text_a = res_a.get("text") or ""
    text_b = res_b.get("text") or ""
    if not text_a.strip() or not text_b.strip():
        return {"skipped": True, "reason": "empty_text"}

    question = extract_question(item["prompt"])
    # Deterministic swap so letter A is not always arm_a
    salt = f"ab:{item['council_id']}:{arm_a}:{arm_b}"
    rng = random.Random(salt)
    swap = rng.random() < 0.5
    if swap:
        letter_to_arm = {"A": arm_b, "B": arm_a}
        resp_a, resp_b = text_b[:3500], text_a[:3500]
    else:
        letter_to_arm = {"A": arm_a, "B": arm_b}
        resp_a, resp_b = text_a[:3500], text_b[:3500]

    prompt = BLIND_PAIR_PROMPT.format(
        question=question, resp_a=resp_a, resp_b=resp_b
    )
    t0 = time.time()
    raw, err = await call_codex_rate(prompt)
    elapsed = time.time() - t0
    if err:
        return {"error": err, "elapsed_s": round(elapsed, 2)}
    parsed = parse_pair_ratings(raw)
    if not parsed:
        return {"parse_error": raw[:400], "elapsed_s": round(elapsed, 2)}

    by_arm: dict[str, dict] = {}
    for letter, rating in parsed.items():
        arm_id = letter_to_arm.get(letter)
        if arm_id and isinstance(rating, dict):
            by_arm[arm_id] = rating

    return {
        "elapsed_s": round(elapsed, 2),
        "swap": swap,
        "letter_to_arm": letter_to_arm,
        "by_arm": by_arm,
    }


# ---------------------------------------------------------------------------
# Summary / ship gates
# ---------------------------------------------------------------------------

def _mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def _median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _dim(rating: dict | None, key: str) -> float | None:
    if not rating:
        return None
    v = rating.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def evaluate_gates(
    arm_a: str,
    arm_b: str,
    rows: list[dict],
    *,
    rated: bool,
) -> dict[str, Any]:
    """Apply pre-registered ship gates from the design doc."""
    ok_a = [r for r in rows if r.get("a") and not r["a"].get("error")]
    ok_b = [r for r in rows if r.get("b") and not r["b"].get("error")]
    n = len(rows)
    err_a = sum(1 for r in rows if r.get("a", {}).get("error")) / n if n else 1.0
    err_b = sum(1 for r in rows if r.get("b", {}).get("error")) / n if n else 1.0

    def met(side: str, key: str) -> list[float]:
        out = []
        for r in rows:
            block = r.get(side) or {}
            m = block.get("metrics") or {}
            if key in m and m[key] is not None and not block.get("error"):
                v = m[key]
                out.append(float(v) if not isinstance(v, bool) else (1.0 if v else 0.0))
        return out

    def elapsed(side: str) -> list[float]:
        # For derived arms use parent_elapsed when present
        out = []
        for r in rows:
            block = r.get(side) or {}
            if block.get("error"):
                continue
            if block.get("derived") and block.get("parent_elapsed_s") is not None:
                out.append(float(block["parent_elapsed_s"]))
            elif block.get("elapsed_s") is not None and not block.get("derived"):
                out.append(float(block["elapsed_s"]))
            elif block.get("elapsed_s") is not None:
                out.append(float(block["elapsed_s"]))
        return out

    el_a, el_b = elapsed("a"), elapsed("b")
    pre_a, pre_b = met("a", "preamble_chars"), met("b", "preamble_chars")
    tool_a, tool_b = met("a", "toolish_start"), met("b", "toolish_start")
    len_a, len_b = met("a", "length"), met("b", "length")

    # Quality
    wins_b = 0.0
    n_rated = 0
    d_help: list[float] = []
    for r in rows:
        q = r.get("rating") or {}
        by = q.get("by_arm") or {}
        if arm_a not in by or arm_b not in by:
            continue
        n_rated += 1
        ra, rb = by[arm_a], by[arm_b]
        sa = _dim(ra, "score") or 0
        sb = _dim(rb, "score") or 0
        ha = _dim(ra, "helpfulness")
        hb = _dim(rb, "helpfulness")
        if ha is not None and hb is not None:
            d_help.append(hb - ha)
        if sb > sa:
            wins_b += 1
        elif sb == sa:
            # tie-break on helpfulness then correctness
            ha2 = ha or 0
            hb2 = hb or 0
            if hb2 > ha2:
                wins_b += 1
            elif hb2 == ha2:
                wins_b += 0.5

    win_rate_b = wins_b / n_rated if n_rated else None
    delta_help = _mean(d_help) if d_help else None

    g1 = err_b <= err_a + 0.05 and err_b <= 0.10
    g2 = True
    if rated and n_rated > 0 and win_rate_b is not None and delta_help is not None:
        g2 = win_rate_b >= 0.45 and delta_help >= -0.25
    elif rated and n_rated == 0:
        g2 = False  # requested rate but got nothing

    med_a, med_b = _median(el_a), _median(el_b)
    latency_win = med_a > 0 and med_b <= 0.90 * med_a
    quality_win = False
    if win_rate_b is not None and delta_help is not None:
        quality_win = win_rate_b >= 0.55 or delta_help >= 0.15

    effort_only = arm_b == "effort_medium" or (
        ARMS.get(arm_b) and ARMS[arm_b].effort != "low" and not ARMS[arm_b].output_contract
    )
    if rated and n_rated > 0:
        if effort_only:
            g3 = quality_win  # latency alone insufficient for effort bumps
        else:
            g3 = quality_win or (g2 and latency_win)
    else:
        # structural-only run: G3 via latency if cleanliness also holds later
        g3 = latency_win

    mean_pre_a, mean_pre_b = _mean(pre_a), _mean(pre_b)
    mean_tool_a, mean_tool_b = _mean(tool_a), _mean(tool_b)
    g4 = (
        (mean_pre_a > 0 and mean_pre_b <= 0.5 * mean_pre_a)
        or (mean_tool_a > 0 and mean_tool_b <= 0.5 * mean_tool_a)
        or (mean_tool_b <= 0.15 and mean_pre_b <= 80)  # already clean absolute
    )
    med_len_a, med_len_b = _median(len_a), _median(len_b)
    g5 = med_len_a == 0 or med_len_b <= 1.25 * med_len_a

    ship = bool(g1 and g2 and g3 and g4 and g5)

    return {
        "n": n,
        "n_ok_a": len(ok_a),
        "n_ok_b": len(ok_b),
        "n_rated": n_rated,
        "error_rate_a": round(err_a, 3),
        "error_rate_b": round(err_b, 3),
        "median_elapsed_a": round(med_a, 1),
        "median_elapsed_b": round(med_b, 1),
        "mean_preamble_a": round(mean_pre_a, 1),
        "mean_preamble_b": round(mean_pre_b, 1),
        "toolish_rate_a": round(mean_tool_a, 3),
        "toolish_rate_b": round(mean_tool_b, 3),
        "median_chars_a": round(med_len_a, 0),
        "median_chars_b": round(med_len_b, 0),
        "win_rate_b": None if win_rate_b is None else round(win_rate_b, 3),
        "delta_help": None if delta_help is None else round(delta_help, 3),
        "gates": {
            "G1_reliability": g1,
            "G2_quality_noninferior": g2,
            "G3_quality_or_latency": g3,
            "G4_cleanliness": g4,
            "G5_no_verbosity_explosion": g5,
        },
        "ship": ship,
        "rated": rated,
    }


def write_summary(
    path: Path,
    arm_a: Arm,
    arm_b: Arm,
    rows: list[dict],
    gate: dict[str, Any],
) -> None:
    g = gate["gates"]
    lines = [
        f"# A/B Grok harness: `{arm_a.id}` vs `{arm_b.id}`",
        "",
        f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Model**: `{GROK_MODEL}`",
        f"**N**: {gate['n']} councils (ok A={gate['n_ok_a']}, ok B={gate['n_ok_b']}, rated={gate['n_rated']})",
        f"**Arm A**: {arm_a.description}",
        f"**Arm B**: {arm_b.description}",
        "",
        "## Verdict",
        "",
        f"**{'SHIP' if gate['ship'] else 'DO NOT SHIP'}** Phase-0 change for arm B",
        "",
        "| Gate | Pass |",
        "|------|------|",
        f"| G1 reliability | {'yes' if g['G1_reliability'] else 'no'} |",
        f"| G2 quality non-inferior | {'yes' if g['G2_quality_noninferior'] else 'no'} |",
        f"| G3 quality or latency | {'yes' if g['G3_quality_or_latency'] else 'no'} |",
        f"| G4 cleanliness | {'yes' if g['G4_cleanliness'] else 'no'} |",
        f"| G5 no verbosity explosion | {'yes' if g['G5_no_verbosity_explosion'] else 'no'} |",
        "",
        "## Structural",
        "",
        "| Metric | A | B |",
        "|--------|---|---|",
        f"| error_rate | {gate['error_rate_a']} | {gate['error_rate_b']} |",
        f"| median_elapsed_s | {gate['median_elapsed_a']} | {gate['median_elapsed_b']} |",
        f"| mean_preamble_chars | {gate['mean_preamble_a']} | {gate['mean_preamble_b']} |",
        f"| toolish_start_rate | {gate['toolish_rate_a']} | {gate['toolish_rate_b']} |",
        f"| median_clean_chars | {gate['median_chars_a']} | {gate['median_chars_b']} |",
        "",
        "## Quality (blind pairwise)",
        "",
    ]
    if gate["win_rate_b"] is None:
        lines.append("_Not run. Re-invoke with `--rate`._")
    else:
        lines += [
            f"| win_rate(B) | {gate['win_rate_b']} |",
            f"| delta_help (B−A) | {gate['delta_help']} |",
            "",
        ]

    # contract_ok rates
    def rate(side: str, key: str) -> float:
        xs = []
        for r in rows:
            m = (r.get(side) or {}).get("metrics") or {}
            if key in m and not (r.get(side) or {}).get("error"):
                xs.append(1.0 if m[key] else 0.0)
        return _mean(xs)

    lines += [
        "",
        "## Contract compliance",
        "",
        f"- contract_ok A: {rate('a', 'contract_ok'):.0%}",
        f"- contract_ok B: {rate('b', 'contract_ok'):.0%}",
        f"- has_verdict A/B: {rate('a', 'has_verdict'):.0%} / {rate('b', 'has_verdict'):.0%}",
        "",
        "## Per-council",
        "",
        "| council_id | A s | B s | preA | preB | toolA | toolB | win |",
        "|------------|-----|-----|------|------|-------|-------|-----|",
    ]
    for r in rows:
        cid = (r.get("council_id") or "")[:8]
        a, b = r.get("a") or {}, r.get("b") or {}
        ma, mb = a.get("metrics") or {}, b.get("metrics") or {}
        ea = a.get("parent_elapsed_s") if a.get("derived") else a.get("elapsed_s")
        eb = b.get("parent_elapsed_s") if b.get("derived") else b.get("elapsed_s")
        if a.get("error"):
            ea = "err"
        if b.get("error"):
            eb = "err"
        win = ""
        by = (r.get("rating") or {}).get("by_arm") or {}
        if arm_a.id in by and arm_b.id in by:
            sa = _dim(by[arm_a.id], "score") or 0
            sb = _dim(by[arm_b.id], "score") or 0
            win = "B" if sb > sa else ("A" if sa > sb else "tie")
        lines.append(
            f"| {cid} | {ea} | {eb} | {ma.get('preamble_chars', '')} | "
            f"{mb.get('preamble_chars', '')} | {ma.get('toolish_start', '')} | "
            f"{mb.get('toolish_start', '')} | {win} |"
        )

    lines += [
        "",
        "## Next",
        "",
        "- If SHIP: implement Phase-0 in `owlex/agents/grok.py` (contract suffix + strip in cleaner).",
        "- If DO NOT SHIP: inspect failing gates; iterate arm design; do not change prod defaults.",
        "",
        "See `docs/design/ab-grok-harness-benchmark.md`.",
        "",
    ]
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace) -> int:
    if args.list_arms:
        for arm in ARMS.values():
            der = f" (derive_from={arm.derive_from})" if arm.derive_from else ""
            print(f"  {arm.id:24} effort={arm.effort:6} contract={arm.output_contract} strip={arm.strip_narration}{der}")
            print(f"    {arm.description}")
        return 0

    if args.arm_a not in ARMS or args.arm_b not in ARMS:
        print(f"Unknown arm. Choose from: {', '.join(ARMS)}", file=sys.stderr)
        return 2
    if args.arm_a == args.arm_b:
        print("arm-a and arm-b must differ", file=sys.stderr)
        return 2

    arm_a, arm_b = ARMS[args.arm_a], ARMS[args.arm_b]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[init] loading corpus limit={args.limit} days={args.days}")
    corpus = load_corpus(args.limit, days=args.days)
    print(f"[init] {len(corpus)} councils (model={GROK_MODEL})")
    if not corpus:
        print("FATAL: empty corpus — need aichat/grok completions + codex prompts", file=sys.stderr)
        return 1

    if args.corpus_only:
        for item in corpus:
            print(
                f"  {item['council_id'][:8]}  prompt={item['prompt_len']}  "
                f"prod_s={item['baseline_prod'].get('duration_s')}  {item['started_at']}"
            )
        return 0

    jsonl_path, md_path = results_paths(arm_a.id, arm_b.id)
    existing = load_jsonl(jsonl_path)
    done = {r["council_id"] for r in existing if "council_id" in r and not r.get("partial")}
    # Resume: also re-rate rows missing rating if --rate
    rows_by_id = {r["council_id"]: r for r in existing if "council_id" in r}
    todo = [c for c in corpus if c["council_id"] not in done]
    print(f"[init] resume={len(done)} remaining_generate={len(todo)} rate={args.rate}")
    print(f"[init] A={arm_a.id} B={arm_b.id} → {jsonl_path.name}")

    # Generation pass
    with jsonl_path.open("a") as f:
        for i, item in enumerate(todo, 1):
            cid = item["council_id"]
            cache: dict[str, dict] = {}
            print(f"[{i}/{len(todo)}] {cid[:8]} generating…", flush=True)
            res_a = await generate_for_arm(
                item, arm_a, cache, cwd=args.cwd, timeout=args.timeout
            )
            res_b = await generate_for_arm(
                item, arm_b, cache, cwd=args.cwd, timeout=args.timeout
            )
            # Drop full text from disk if huge? keep for rating — store text
            row = {
                "council_id": cid,
                "started_at": item.get("started_at"),
                "prompt_len": item.get("prompt_len"),
                "baseline_prod": item.get("baseline_prod"),
                "model": GROK_MODEL,
                "a": _compact_result(res_a),
                "b": _compact_result(res_b),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            # Keep text for potential --rate later
            row["a"]["text"] = res_a.get("text") or ""
            row["b"]["text"] = res_b.get("text") or ""

            if args.rate:
                print(f"[{i}/{len(todo)}] {cid[:8]} rating…", flush=True)
                row["rating"] = await rate_pair(item, res_a, res_b, arm_a.id, arm_b.id)

            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            rows_by_id[cid] = row
            done.add(cid)

            a_s = res_a.get("elapsed_s")
            b_s = res_b.get("elapsed_s")
            print(
                f"[{i}/{len(todo)}] {cid[:8]} "
                f"A={a_s}s pre={ (res_a.get('metrics') or {}).get('preamble_chars')} "
                f"B={b_s}s pre={ (res_b.get('metrics') or {}).get('preamble_chars')} "
                f"errA={res_a.get('error')} errB={res_b.get('error')}",
                flush=True,
            )
            if args.sleep > 0:
                await asyncio.sleep(args.sleep)

    # Optional: rate previously generated rows missing ratings
    if args.rate:
        need_rate = [
            rows_by_id[c["council_id"]]
            for c in corpus
            if c["council_id"] in rows_by_id
            and not (rows_by_id[c["council_id"]].get("rating") or {}).get("by_arm")
            and not (rows_by_id[c["council_id"]].get("a") or {}).get("error")
            and not (rows_by_id[c["council_id"]].get("b") or {}).get("error")
        ]
        if need_rate:
            print(f"[rate] backfilling {len(need_rate)} rows without ratings")
            # rewrite file fully after backfill
            for i, row in enumerate(need_rate, 1):
                cid = row["council_id"]
                item = next(c for c in corpus if c["council_id"] == cid)
                print(f"[rate {i}/{len(need_rate)}] {cid[:8]}", flush=True)
                row["rating"] = await rate_pair(
                    item, row["a"], row["b"], arm_a.id, arm_b.id
                )
                rows_by_id[cid] = row
                if args.sleep > 0:
                    await asyncio.sleep(args.sleep)
            # rewrite JSONL
            ordered = [rows_by_id[c["council_id"]] for c in corpus if c["council_id"] in rows_by_id]
            with jsonl_path.open("w") as f:
                for row in ordered:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Summary over intersection of corpus and results
    final_rows = [rows_by_id[c["council_id"]] for c in corpus if c["council_id"] in rows_by_id]
    gate = evaluate_gates(arm_a.id, arm_b.id, final_rows, rated=args.rate)
    write_summary(md_path, arm_a, arm_b, final_rows, gate)
    print(f"\n[done] {jsonl_path}")
    print(f"[done] {md_path}")
    print(f"[gates] ship={gate['ship']} {gate['gates']}")
    print(
        f"[stats] med_s A/B={gate['median_elapsed_a']}/{gate['median_elapsed_b']} "
        f"preamble={gate['mean_preamble_a']}/{gate['mean_preamble_b']} "
        f"win_rate_B={gate['win_rate_b']} delta_help={gate['delta_help']}"
    )
    return 0 if gate["ship"] or not args.fail_on_no_ship else 3


def _compact_result(res: dict) -> dict:
    """Store metrics + meta; caller may re-attach text."""
    out = {
        "arm": res.get("arm"),
        "elapsed_s": res.get("elapsed_s"),
        "derived": res.get("derived"),
        "derive_from": res.get("derive_from"),
        "parent_elapsed_s": res.get("parent_elapsed_s"),
        "raw_chars": res.get("raw_chars"),
        "metrics": res.get("metrics"),
    }
    if res.get("error"):
        out["error"] = res["error"]
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arm-a", default="baseline", help="Control arm id")
    p.add_argument("--arm-b", default="contract_strip", help="Treatment arm id")
    p.add_argument("--limit", type=int, default=20, help="Max councils")
    p.add_argument("--days", type=int, default=60, help="Lookback window for corpus")
    p.add_argument("--timeout", type=int, default=GROK_TIMEOUT, help="Per-call grok timeout s")
    p.add_argument("--cwd", default=None, help="Working directory for grok tools")
    p.add_argument("--sleep", type=float, default=0.3, help="Pause between councils")
    p.add_argument("--rate", action="store_true", help="Blind pairwise rate A vs B via codex")
    p.add_argument("--list-arms", action="store_true")
    p.add_argument("--corpus-only", action="store_true", help="Print corpus and exit")
    p.add_argument(
        "--fail-on-no-ship",
        action="store_true",
        help="Exit 3 if ship gates fail (CI-friendly)",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        rc = asyncio.run(async_main(args))
    except FileNotFoundError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
