"""
Specialist Role System ("Hats") for council deliberation.

Enables agents to operate with specialized perspectives during council sessions.
Supports three modes of role specification:
1. Explicit mapping: {"codex": "security", "gemini": "perf"}
2. Role list with auto-assign: ["security", "perf", "skeptic"]
3. Team presets: "security_audit"
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Sequence, TypeAlias

from .config import KNOWN_RUNNERS
from .models import COUNCIL_SEATS


class RoleId(str, Enum):
    """Built-in role identifiers."""
    SECURITY = "security"
    PERFORMANCE = "perf"
    SKEPTIC = "skeptic"
    ARCHITECT = "architect"
    MAINTAINER = "maintainer"
    DX = "dx"  # Developer Experience
    TESTING = "testing"
    SYNTHESIZER = "synthesizer"  # Peacemaker: integrate viewpoints without papering over disagreement
    EDGE_CASE_ADVERSARY = "edge_case_adversary"  # Adversarial test designer: enumerate breaking scenarios from the interface
    TEST_ADVERSARY = "test_adversary"
    NEUTRAL = "neutral"  # Default: no role injection


@dataclass(frozen=True)
class RoleDefinition:
    """Definition of a specialist role with prompts for each round."""
    id: str
    name: str
    description: str
    round_1_prefix: str  # Prepended to R1 prompt
    round_2_prefix: str  # Prepended to R2 deliberation prompt (sticky role)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "round_1_prefix": self.round_1_prefix,
            "round_2_prefix": self.round_2_prefix,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RoleDefinition:
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            round_1_prefix=data["round_1_prefix"],
            round_2_prefix=data["round_2_prefix"],
        )


@dataclass(frozen=True)
class TeamPreset:
    """Predefined team composition with role assignments.

    ``runner_overrides`` (seat -> runner name, e.g. ``{"claudeor": "grok"}``)
    lets a team preset force one of its own seats through a different runner,
    independent of the global ``COUNCIL_SUBSTITUTION_MODELS`` env override.
    It names the RUNNER only, never a model id — the runner resolves its own
    model from its own config (e.g. ``GrokRunner`` falls back to
    ``config.grok.model`` / ``OWLEX_GROK_MODEL``). Pinning a model id here
    would create a second, non-env pinning site — see
    docs/solutions/external-tools/cli-catalog-rotation-needs-health-check.md.

    Precedence: when this team is the active role spec, its runner_overrides
    win over a ``COUNCIL_SUBSTITUTION_MODELS`` entry for the SAME seat; seats
    it does not name still fall back to the global override / normal
    availability logic. See ``Council.build_participants``.

    A runner override changes the physical runner, so the participant remains
    marked substituted in later rounds. Round-2 session resume is intentionally
    skipped for that seat because resuming a session owned by a different
    runner is unsafe; round 2 re-executes instead.
    """
    id: str
    name: str
    description: str
    # Maps agent name -> role_id (e.g., {"codex": "security", "gemini": "perf"})
    assignments: dict[str, str]
    # Maps seat -> runner name (e.g., {"claudeor": "grok"}). Deliberately no
    # model string — see class docstring.
    runner_overrides: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "assignments": dict(self.assignments),
            "runner_overrides": dict(self.runner_overrides),
        }

    @classmethod
    def from_dict(cls, data: dict) -> TeamPreset:
        raw_overrides = data.get("runner_overrides", {})
        runner_overrides: dict[str, str] = {}
        if isinstance(raw_overrides, dict):
            for raw_seat, runner_name in raw_overrides.items():
                if not isinstance(raw_seat, str):
                    print(
                        f"[WARNING] Team '{data.get('id')}': seat {raw_seat!r} is not "
                        "a valid council seat; override skipped",
                        file=sys.stderr,
                    )
                    continue
                seat = raw_seat.strip().lower()
                if seat not in COUNCIL_SEATS:
                    print(
                        f"[WARNING] Team '{data.get('id')}': seat '{raw_seat}' is not "
                        "a valid council seat; override skipped",
                        file=sys.stderr,
                    )
                    continue
                if not isinstance(runner_name, str):
                    print(
                        f"[WARNING] Team '{data.get('id')}': runner {runner_name!r} for "
                        f"seat '{raw_seat}' is not a string; override skipped",
                        file=sys.stderr,
                    )
                    continue
                runner = runner_name.strip().lower()
                if runner not in KNOWN_RUNNERS:
                    print(
                        f"[WARNING] Team '{data.get('id')}': runner {runner_name!r} for "
                        f"seat '{raw_seat}' is not one of {sorted(KNOWN_RUNNERS)}; "
                        f"override skipped",
                        file=sys.stderr,
                    )
                    continue
                if seat == runner:
                    print(
                        f"[WARNING] Team '{data.get('id')}': runner '{runner_name}' "
                        f"for seat '{raw_seat}' is the same as the seat; override skipped",
                        file=sys.stderr,
                    )
                    continue
                if seat in runner_overrides:
                    print(
                        f"[WARNING] Team '{data.get('id')}': normalized seat '{raw_seat}' "
                        "replaces an earlier runner override",
                        file=sys.stderr,
                    )
                runner_overrides[seat] = runner
        else:
            print(
                f"[WARNING] Team '{data.get('id')}': 'runner_overrides' must be an "
                f"object, got {type(raw_overrides).__name__}; ignoring",
                file=sys.stderr,
            )

        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            assignments=data["assignments"],
            runner_overrides=runner_overrides,
        )


# Type aliases for role specification modes
RoleMapping: TypeAlias = dict[str, str]  # {"codex": "security", "gemini": "perf"}
RoleList: TypeAlias = list[str]  # ["security", "perf", "skeptic"]
RoleSpec: TypeAlias = RoleMapping | RoleList | str | None  # str = team preset ID


# === Built-in Role Definitions ===

BUILTIN_ROLES: dict[str, RoleDefinition] = {
    RoleId.SECURITY.value: RoleDefinition(
        id=RoleId.SECURITY.value,
        name="Security Expert",
        description="Focus on security vulnerabilities, attack vectors, and defensive practices",
        round_1_prefix=(
            "[ROLE: Security Expert]\n"
            "You are acting as a security specialist. Focus your analysis on:\n"
            "- Potential security vulnerabilities (injection, XSS, CSRF, etc.)\n"
            "- Authentication and authorization flaws\n"
            "- Data exposure and privacy risks\n"
            "- Input validation and sanitization\n"
            "- Secure coding practices and defense in depth\n\n"
        ),
        round_2_prefix=(
            "[ROLE: Security Expert - Deliberation]\n"
            "Maintain your security specialist perspective. When reviewing others' answers:\n"
            "- Identify security implications they may have missed\n"
            "- Challenge assumptions about trust boundaries\n"
            "- Propose security hardening for suggested solutions\n\n"
        ),
    ),

    RoleId.PERFORMANCE.value: RoleDefinition(
        id=RoleId.PERFORMANCE.value,
        name="Performance Optimizer",
        description="Focus on performance, scalability, and resource efficiency",
        round_1_prefix=(
            "[ROLE: Performance Optimizer]\n"
            "You are acting as a performance specialist. Focus your analysis on:\n"
            "- Time and space complexity\n"
            "- Database query efficiency and N+1 problems\n"
            "- Memory usage and potential leaks\n"
            "- Caching opportunities\n"
            "- Scalability bottlenecks\n\n"
        ),
        round_2_prefix=(
            "[ROLE: Performance Optimizer - Deliberation]\n"
            "Maintain your performance specialist perspective. When reviewing others' answers:\n"
            "- Analyze performance implications of suggested approaches\n"
            "- Propose optimizations and benchmark considerations\n"
            "- Identify scalability concerns\n\n"
        ),
    ),

    RoleId.SKEPTIC.value: RoleDefinition(
        id=RoleId.SKEPTIC.value,
        name="Skeptic / Devil's Advocate",
        description="Challenge assumptions and find edge cases",
        round_1_prefix=(
            "[ROLE: Skeptic / Devil's Advocate]\n"
            "You are acting as a critical skeptic. Your job is to:\n"
            "- Question assumptions and 'obvious' solutions\n"
            "- Find edge cases and failure modes\n"
            "- Consider what could go wrong\n"
            "- Propose alternative approaches\n"
            "- Play devil's advocate on popular opinions\n\n"
        ),
        round_2_prefix=(
            "[ROLE: Skeptic - Deliberation]\n"
            "Maintain your skeptical perspective. When reviewing others' answers:\n"
            "- Challenge consensus if you see problems\n"
            "- Point out edge cases others missed\n"
            "- Question whether proposed solutions actually work\n\n"
        ),
    ),

    RoleId.SYNTHESIZER.value: RoleDefinition(
        id=RoleId.SYNTHESIZER.value,
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
    ),

    RoleId.ARCHITECT.value: RoleDefinition(
        id=RoleId.ARCHITECT.value,
        name="System Architect",
        description="Focus on system design, patterns, and long-term maintainability",
        round_1_prefix=(
            "[ROLE: System Architect]\n"
            "You are acting as a system architect. Focus your analysis on:\n"
            "- Overall system design and architecture\n"
            "- Design patterns and their appropriate use\n"
            "- Separation of concerns and modularity\n"
            "- API design and contracts\n"
            "- Long-term maintainability and extensibility\n\n"
        ),
        round_2_prefix=(
            "[ROLE: System Architect - Deliberation]\n"
            "Maintain your architectural perspective. When reviewing others' answers:\n"
            "- Evaluate architectural implications\n"
            "- Consider how solutions fit the larger system\n"
            "- Propose structural improvements\n\n"
        ),
    ),

    RoleId.MAINTAINER.value: RoleDefinition(
        id=RoleId.MAINTAINER.value,
        name="Code Maintainer",
        description="Focus on code quality, readability, and maintenance burden",
        round_1_prefix=(
            "[ROLE: Code Maintainer]\n"
            "You are acting as a code maintainer who will live with this code. Focus on:\n"
            "- Code readability and clarity\n"
            "- Documentation and self-documenting code\n"
            "- Test coverage and testability\n"
            "- Error handling and logging\n"
            "- Future maintenance burden\n\n"
        ),
        round_2_prefix=(
            "[ROLE: Code Maintainer - Deliberation]\n"
            "Maintain your maintainer perspective. When reviewing others' answers:\n"
            "- Consider long-term maintenance implications\n"
            "- Evaluate code clarity and documentation needs\n"
            "- Identify potential technical debt\n\n"
        ),
    ),

    RoleId.DX.value: RoleDefinition(
        id=RoleId.DX.value,
        name="Developer Experience",
        description="Focus on API usability, error messages, and developer ergonomics",
        round_1_prefix=(
            "[ROLE: Developer Experience (DX) Advocate]\n"
            "You are acting as a DX advocate. Focus your analysis on:\n"
            "- API usability and intuitiveness\n"
            "- Quality of error messages and debugging experience\n"
            "- Documentation and examples\n"
            "- Developer onboarding friction\n"
            "- Consistency with conventions\n\n"
        ),
        round_2_prefix=(
            "[ROLE: DX Advocate - Deliberation]\n"
            "Maintain your DX perspective. When reviewing others' answers:\n"
            "- Evaluate how solutions affect developer experience\n"
            "- Suggest improvements for usability\n"
            "- Consider the learning curve\n\n"
        ),
    ),

    RoleId.TESTING.value: RoleDefinition(
        id=RoleId.TESTING.value,
        name="Testing Specialist",
        description="Focus on testability, test coverage, and quality assurance",
        round_1_prefix=(
            "[ROLE: Testing Specialist]\n"
            "You are acting as a testing specialist. Focus your analysis on:\n"
            "- Testability of the proposed solution\n"
            "- Unit, integration, and e2e test strategies\n"
            "- Edge cases and boundary conditions\n"
            "- Mocking and test isolation\n"
            "- Test maintenance and reliability\n\n"
        ),
        round_2_prefix=(
            "[ROLE: Testing Specialist - Deliberation]\n"
            "Maintain your testing perspective. When reviewing others' answers:\n"
            "- Identify testing challenges in proposed solutions\n"
            "- Suggest test strategies and coverage gaps\n"
            "- Point out hard-to-test patterns\n\n"
        ),
    ),

    RoleId.EDGE_CASE_ADVERSARY.value: RoleDefinition(
        id=RoleId.EDGE_CASE_ADVERSARY.value,
        name="Edge-Case Adversary",
        description="Adversarial test designer: enumerate breaking scenarios from the flow + interface, not the implementation",
        round_1_prefix=(
            "[ROLE: Edge-Case Adversary]\n"
            "You are an adversarial TEST DESIGNER. You are given a user flow and its "
            "interface (inputs, outputs, contract) — NOT the implementation. Your job is "
            "to enumerate the inputs and states that BREAK it. Probe every axis:\n"
            "- Boundaries: empty, zero, one, max length, off-by-one, overflow\n"
            "- Auth/permission edges: unauthenticated, wrong role, expired token, revoked access\n"
            "- Concurrency: concurrent modification, double-submit, races, stale reads\n"
            "- Failure injection: network failure, timeout, partial write, dependency down\n"
            "- Malformed input: wrong type, unexpected encoding, injection, null/NaN\n\n"
            "Output a STRUCTURED list of test scenarios. For each: the setup/input, the "
            "axis it probes, and the EXPECTED behavior. Specify what the code MUST do in "
            "each case — never describe what existing code does. You are writing the spec, "
            "not reviewing an implementation.\n\n"
        ),
        round_2_prefix=(
            "[ROLE: Edge-Case Adversary - Deliberation]\n"
            "Maintain your adversarial test-designer perspective. Merge the council's "
            "scenarios into one deduplicated spec, and add any breaking case the others "
            "missed. Where two answers assert DIFFERENT expected behavior for the same "
            "scenario, do NOT silently pick one — flag it as a spec ambiguity the author "
            "must resolve.\n\n"
        ),
    ),

    RoleId.TEST_ADVERSARY.value: RoleDefinition(
        id=RoleId.TEST_ADVERSARY.value,
        name="Test Adversary",
        description="Audits existing tests for vacuity: can this test fail at all when behavior breaks?",
        round_1_prefix=(
            "[ROLE: Test Adversary]\n"
            "You audit TESTS against REQUIREMENTS. You are given (1) the business requirements — these are the\n"
            "ORACLE, they define what correct means — and (2) the tests that claim to enforce them. You are NOT\n"
            "given the implementation and must not go looking for it: a test that mirrors the implementation reads\n"
            "as correct to anyone holding that implementation, and finding those is precisely your job.\n"
            "\n"
            "Produce FOUR tables. All four, always. Where a table has nothing to report, write \"none\" — never pad\n"
            "it with invented rows.\n"
            "\n"
            "TABLE A — REQUIREMENT COVERAGE, one row per clause:\n"
            "  clause | how would you OBSERVE this clause holding? | test(s) enforcing it | the assertion that pins\n"
            "  it | ENFORCED / PARTIAL / UNTESTED / NOT-OBSERVABLE\n"
            "  PARTIAL means part of the clause's stated behavior is pinned — say which part is not.\n"
            "  NOT-OBSERVABLE means the clause names no observable effect, so no test could pin it. That is a\n"
            "  defect of the REQUIREMENT, not of the tests: say what would have to be added to the requirement to\n"
            "  make it observable. Never silently record an unobservable clause as UNTESTED.\n"
            "\n"
            "TABLE B — PROHIBITIONS, one row per thing that must NOT happen:\n"
            "  prohibition | test asserting the absence | ASSERTED / MISSING\n"
            "  Derive prohibitions even when the requirement is worded positively: \"persisted\" implies nothing\n"
            "  ELSE was written; \"only from this surface\" implies a write from another surface is refused;\n"
            "  \"refused\" implies no partial write happened first. Most suites assert what should happen and never\n"
            "  that the forbidden thing did not.\n"
            "\n"
            "TABLE C — BOUNDARIES, one row per threshold, limit, or ordering rule in the requirement:\n"
            "  boundary | input(s) the tests actually use | AT-BOUNDARY / INSIDE-ONLY / ABSENT\n"
            "  INSIDE-ONLY means every test input sits comfortably away from the edge, so no off-by-one at the edge\n"
            "  could be observed. If the requirement states no thresholds, this table is \"none\" — do not invent\n"
            "  boundaries the requirement does not have.\n"
            "\n"
            "TABLE D — TEST QUALITY, one row per test case:\n"
            "  test name | clause it serves | what must break in production behavior for it to go red | SECOND\n"
            "  CAUSE that would produce the same observed outcome | output pinned WHOLE / PARTIAL / n-a |\n"
            "  REAL / WEAK / VACUOUS\n"
            "\n"
            "  The SECOND CAUSE column is mandatory and is the sharpest question here. For every test, name another\n"
            "  reason the same observation would occur. A test asserting \"an error was thrown\" where an unrelated\n"
            "  precondition throws the identical error does not isolate what it claims — such a test can never be\n"
            "  REAL, however precise its name. Write \"none — the observation is unique to the named cause\" only\n"
            "  when you have actually looked for one.\n"
            "\n"
            "  Output pinned WHOLE means the assertion constrains the entire result; PARTIAL means it constrains\n"
            "  one field or property while the rest of the result could be wrong and the test would still pass;\n"
            "  n-a for tests that assert an effect rather than a returned value.\n"
            "\n"
            "  A test serving NO clause is a finding: either a requirement is unwritten, or the test asserts\n"
            "  something nobody asked for. Say which.\n"
            "\n"
            "Then, ranked by risk:\n"
            "  1. Clauses marked ENFORCED whose enforcing test is WEAK or VACUOUS — false confidence, the worst\n"
            "     outcome, because everyone believes that clause is covered.\n"
            "  2. Clauses marked NOT-OBSERVABLE — the requirement cannot be tested as written.\n"
            "  3. MISSING prohibitions and INSIDE-ONLY boundaries.\n"
            "  4. UNTESTED clauses and VACUOUS tests.\n"
            "\n"
            "For every WEAK or VACUOUS verdict, name the ONE concrete change in production behavior that SHOULD\n"
            "have turned that test red and does not — phrased as an executable edit (\"make the function return\n"
            "undefined\", \"let a self-reference through while still rejecting unknown ids\"), never as a description.\n"
            "\n"
            "A test cannot fail if no change in behavior turns it red. Watch for, hardest-to-spot last: assertion\n"
            "on a literal or on a value the test itself constructed; implementation-independent assertion\n"
            "(is-defined / not-null / does-not-throw / truthy) that holds for almost any implementation including a\n"
            "broken one; asserting a mock's own call shape where the required OUTCOME was observable; a setup that\n"
            "stubs the very thing the test claims to check; a setup that makes the asserted branch unreachable or\n"
            "the assertion non-discriminating; asserting on message text; a test whose name promises a guarantee\n"
            "its assertions do not cover; a flow with documented failure paths asserted only on its success path.\n"
            "\n"
            "When a test contradicts a requirement, the TEST is wrong — never bend the requirement to fit it. If\n"
            "the requirement genuinely admits two readings, do not choose: mark it AMBIGUOUS-REQUIREMENT and state\n"
            "both readings. That is for the author to settle, not for the council.\n"
            "\n"
            "Do NOT rewrite tests, do NOT propose new ones, do NOT comment on style, naming, or structure.\n"
            "\n"
        ),
        round_2_prefix=(
            "[ROLE: Test Adversary - Deliberation]\n"
            "Keep auditing tests against requirements. Merge the council's four tables into one set of four.\n"
            "\n"
            "A finding raised by a SINGLE member survives the merge. Majority does not delete it — the sharpest\n"
            "finding in a run is routinely the one only one member saw, and consensus is the mechanism that loses\n"
            "it. Where two answers give the SAME test or clause different verdicts, do not average and do not\n"
            "silently pick: state which concrete production change each side believes would turn that test red,\n"
            "because at most one of them can be right, and mark that row REVIEW-BY-HUMAN. Keep every\n"
            "AMBIGUOUS-REQUIREMENT and NOT-OBSERVABLE flag any member raised.\n"
            "\n"
            "Pay particular attention to the SECOND CAUSE column: if any member named a second cause for a test\n"
            "another member scored REAL, that test is not REAL until the disagreement is settled by a human. Add\n"
            "any requirement, prohibition, boundary or test nobody covered.\n"
            "\n"
        ),
    ),

    RoleId.NEUTRAL.value: RoleDefinition(
        id=RoleId.NEUTRAL.value,
        name="Neutral",
        description="No specialized perspective - default behavior",
        round_1_prefix="",  # No injection
        round_2_prefix="",  # No injection
    ),
}


# === Built-in Team Presets ===

BUILTIN_TEAMS: dict[str, TeamPreset] = {
    "security_audit": TeamPreset(
        id="security_audit",
        name="Security Audit Team",
        description="Team focused on security review with a skeptic for edge cases",
        assignments={
            "codex": RoleId.SECURITY.value,
            "gemini": RoleId.SKEPTIC.value,
            "opencode": RoleId.ARCHITECT.value,
            "claudeor": RoleId.DX.value,
            "aichat": RoleId.TESTING.value,
            "cursor": RoleId.MAINTAINER.value,
        },
    ),

    "code_review": TeamPreset(
        id="code_review",
        name="Code Review Team",
        description="Balanced team for thorough code review",
        assignments={
            "codex": RoleId.MAINTAINER.value,
            "gemini": RoleId.PERFORMANCE.value,
            "opencode": RoleId.TESTING.value,
            "claudeor": RoleId.DX.value,
            "aichat": RoleId.SECURITY.value,
            "cursor": RoleId.ARCHITECT.value,
        },
    ),

    "architecture_review": TeamPreset(
        id="architecture_review",
        name="Architecture Review Team",
        description="Team for evaluating system design decisions",
        assignments={
            "codex": RoleId.ARCHITECT.value,
            "gemini": RoleId.PERFORMANCE.value,
            "opencode": RoleId.MAINTAINER.value,
            "claudeor": RoleId.DX.value,
            "aichat": RoleId.SKEPTIC.value,
            "cursor": RoleId.SECURITY.value,
        },
    ),

    "devil_advocate": TeamPreset(
        id="devil_advocate",
        name="Devil's Advocate Team",
        description="All agents act as skeptics to stress-test ideas",
        assignments={
            "codex": RoleId.SKEPTIC.value,
            "gemini": RoleId.SKEPTIC.value,
            "opencode": RoleId.SKEPTIC.value,
            "claudeor": RoleId.SKEPTIC.value,
            "aichat": RoleId.SKEPTIC.value,
            "cursor": RoleId.SKEPTIC.value,
        },
    ),

    "balanced": TeamPreset(
        id="balanced",
        name="Balanced Review Team",
        description="One each of security, performance, and maintainability",
        assignments={
            "codex": RoleId.SECURITY.value,
            "gemini": RoleId.PERFORMANCE.value,
            "opencode": RoleId.MAINTAINER.value,
            "claudeor": RoleId.DX.value,
            "aichat": RoleId.TESTING.value,
            "cursor": RoleId.SKEPTIC.value,
        },
    ),

    "optimal": TeamPreset(
        id="optimal",
        name="Optimal Strengths Team",
        description="Roles matched to each model's inherent strengths",
        assignments={
            "codex": RoleId.MAINTAINER.value,      # Deep reasoning for surgical changes
            "gemini": RoleId.ARCHITECT.value,       # Large context for system-wide view
            "opencode": RoleId.DX.value,            # Best tone/steerability for docs
            "claudeor": RoleId.SKEPTIC.value,       # Fast, unconstrained critic
            "aichat": RoleId.PERFORMANCE.value,     # Flexible model for perf analysis
            "cursor": RoleId.SECURITY.value,        # Multi-model agent for security review
        },
    ),

    "test_spec": TeamPreset(
        id="test_spec",
        name="Test-Spec Team",
        description="All seats act as edge-case adversaries to generate an exhaustive test specification",
        assignments={
            "codex": RoleId.EDGE_CASE_ADVERSARY.value,
            "gemini": RoleId.EDGE_CASE_ADVERSARY.value,
            "opencode": RoleId.EDGE_CASE_ADVERSARY.value,
            "claudeor": RoleId.EDGE_CASE_ADVERSARY.value,
            "aichat": RoleId.EDGE_CASE_ADVERSARY.value,
            "cursor": RoleId.EDGE_CASE_ADVERSARY.value,
        },
        # claudeor routes through grok for this team only — see TeamPreset docstring.
        runner_overrides={"claudeor": "grok"},
    ),

    "test_review": TeamPreset(
        id="test_review",
        name="Test-Review Team",
        description="All seats audit existing tests for vacuity — can they fail when behavior breaks?",
        assignments={
            "codex": RoleId.TEST_ADVERSARY.value,
            "gemini": RoleId.TEST_ADVERSARY.value,
            "opencode": RoleId.TEST_ADVERSARY.value,
            "claudeor": RoleId.TEST_ADVERSARY.value,
            "aichat": RoleId.TEST_ADVERSARY.value,
            "cursor": RoleId.TEST_ADVERSARY.value,
        },
        runner_overrides={"claudeor": "grok"},
    ),

    "dialectic": TeamPreset(
        id="dialectic",
        name="Dialectic (balanced troublemaker/peacemaker)",
        description="Research-aligned mix: 2 skeptics, 1 synthesizer, 3 specialists",
        assignments={
            "claudeor": RoleId.SKEPTIC.value,
            "aichat": RoleId.SKEPTIC.value,
            "gemini": RoleId.SYNTHESIZER.value,
            "codex": RoleId.MAINTAINER.value,
            "opencode": RoleId.ARCHITECT.value,
            "cursor": RoleId.SECURITY.value,
        },
    ),
}


# === User Config Loading ===

def _user_roles_file() -> Path:
    return Path(os.environ.get("OWLEX_HOME", str(Path.home() / ".owlex"))) / "roles.json"


# Compatibility names for callers that import these paths. Role loading uses
# _user_roles_file() at call time so OWLEX_HOME changes are respected.
USER_ROLES_FILE = _user_roles_file()
USER_CONFIG_DIR = USER_ROLES_FILE.parent


def load_user_roles() -> tuple[dict[str, RoleDefinition], dict[str, TeamPreset]]:
    """
    Load user-defined roles and teams from ~/.owlex/roles.json.

    The file format:
    {
        "roles": {
            "my_custom_role": {
                "id": "my_custom_role",
                "name": "My Custom Role",
                "description": "...",
                "round_1_prefix": "...",
                "round_2_prefix": "..."
            }
        },
        "teams": {
            "my_team": {
                "id": "my_team",
                "name": "My Team",
                "description": "...",
                "assignments": {"codex": "security", "gemini": "my_custom_role"},
                "runner_overrides": {"claudeor": "grok"}
            }
        }
    }

    ``runner_overrides`` is optional for user teams. Its entries are normalized
    and validated independently, so an invalid entry is skipped without
    discarding the team.

    Note: The role's "id" field MUST match the dict key. Mismatches are skipped with a warning.
    Invalid entries are skipped individually (per-entry validation) rather than failing the whole file.

    Returns:
        Tuple of (roles_dict, teams_dict)
    """
    roles_file = _user_roles_file()
    if not roles_file.exists():
        return {}, {}

    try:
        with open(roles_file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[WARNING] Failed to parse {roles_file}: {e}", file=sys.stderr)
        return {}, {}

    # Validate top-level structure
    if not isinstance(data, dict):
        print(f"[WARNING] {roles_file} must be a JSON object, got {type(data).__name__}", file=sys.stderr)
        return {}, {}

    roles_data = data.get("roles", {})
    if not isinstance(roles_data, dict):
        print(f"[WARNING] 'roles' in {roles_file} must be an object, got {type(roles_data).__name__}", file=sys.stderr)
        roles_data = {}

    teams_data = data.get("teams", {})
    if not isinstance(teams_data, dict):
        print(f"[WARNING] 'teams' in {roles_file} must be an object, got {type(teams_data).__name__}", file=sys.stderr)
        teams_data = {}

    roles = {}
    for role_key, role_data in roles_data.items():
        try:
            role = RoleDefinition.from_dict(role_data)
            # Enforce that dict key matches the role's id field
            if role.id != role_key:
                print(
                    f"[WARNING] Role key '{role_key}' does not match id '{role.id}' in {roles_file}, skipping",
                    file=sys.stderr,
                )
                continue
            roles[role_key] = role
        except (KeyError, TypeError) as e:
            print(f"[WARNING] Invalid role '{role_key}' in {roles_file}: {e}", file=sys.stderr)
            continue

    teams = {}
    for team_key, team_data in teams_data.items():
        try:
            team = TeamPreset.from_dict(team_data)
            # Enforce that dict key matches the team's id field
            if team.id != team_key:
                print(
                    f"[WARNING] Team key '{team_key}' does not match id '{team.id}' in {roles_file}, skipping",
                    file=sys.stderr,
                )
                continue
            teams[team_key] = team
        except (KeyError, TypeError) as e:
            print(f"[WARNING] Invalid team '{team_key}' in {roles_file}: {e}", file=sys.stderr)
            continue

    return roles, teams


def get_merged_roles_and_teams() -> tuple[dict[str, RoleDefinition], dict[str, TeamPreset]]:
    """
    Get merged roles and teams (built-in + user overrides).

    User definitions override built-ins with the same ID.

    Returns:
        Tuple of (merged_roles, merged_teams)
    """
    # Start with built-ins
    roles = dict(BUILTIN_ROLES)
    teams = dict(BUILTIN_TEAMS)

    # Merge user definitions (override built-ins)
    user_roles, user_teams = load_user_roles()
    roles.update(user_roles)
    teams.update(user_teams)

    return roles, teams


# === Role Resolution ===

# Default agent order for auto-assignment
DEFAULT_AGENT_ORDER: Sequence[str] = ("codex", "gemini", "opencode")


class RoleResolver:
    """
    Resolves role specifications to concrete agent->role mappings.

    Resolution priority:
    1. Explicit dict mapping (highest specificity)
    2. List of roles (auto-assigned in agent order)
    3. Team preset name
    4. None (no roles - neutral for all)
    """

    def __init__(
        self,
        roles: dict[str, RoleDefinition],
        teams: dict[str, TeamPreset],
    ):
        self.roles = roles
        self.teams = teams

    def resolve(
        self,
        spec: RoleSpec,
        active_agents: Sequence[str] | None = None,
    ) -> dict[str, RoleDefinition]:
        """
        Resolve a role specification to a mapping of agent -> RoleDefinition.

        Args:
            spec: Role specification (dict, list, team name, or None)
            active_agents: Agents participating in this council (for auto-assign)

        Returns:
            Dict mapping agent name to RoleDefinition

        Raises:
            ValueError: If role or team not found, or invalid spec type
        """
        if active_agents is None:
            active_agents = DEFAULT_AGENT_ORDER

        if spec is None:
            # No roles specified - all agents get neutral
            return {agent: self.roles[RoleId.NEUTRAL.value] for agent in active_agents}

        if isinstance(spec, dict):
            # Mode 1: Explicit mapping {"codex": "security", "gemini": "perf"}
            return self._resolve_explicit_mapping(spec, active_agents)

        if isinstance(spec, list):
            # Mode 2: List auto-assign ["security", "perf", "skeptic"]
            return self._resolve_role_list(spec, active_agents)

        if isinstance(spec, str):
            # Mode 3: Team preset name "security_audit"
            return self._resolve_team_preset(spec, active_agents)

        raise ValueError(f"Invalid role specification type: {type(spec)}")

    def _resolve_explicit_mapping(
        self,
        mapping: dict[str, str],
        active_agents: Sequence[str],
    ) -> dict[str, RoleDefinition]:
        """Resolve explicit agent->role mapping."""
        # Validate for unknown agent keys (typos like "codexx")
        known_agents = set(COUNCIL_SEATS)
        unknown_keys = set(mapping.keys()) - known_agents
        if unknown_keys:
            raise ValueError(f"Unknown agent(s) in role mapping: {', '.join(sorted(unknown_keys))}")

        result = {}
        for agent in active_agents:
            role_id = mapping.get(agent)
            if role_id is None:
                # Agent not in mapping - gets neutral
                result[agent] = self.roles[RoleId.NEUTRAL.value]
            elif role_id not in self.roles:
                raise ValueError(f"Unknown role '{role_id}' for agent '{agent}'")
            else:
                result[agent] = self.roles[role_id]

        return result

    def _resolve_role_list(
        self,
        role_ids: list[str],
        active_agents: Sequence[str],
    ) -> dict[str, RoleDefinition]:
        """Auto-assign roles from list to agents in order."""
        result = {}

        for i, agent in enumerate(active_agents):
            if i < len(role_ids):
                role_id = role_ids[i]
                if role_id not in self.roles:
                    raise ValueError(f"Unknown role '{role_id}' in role list")
                result[agent] = self.roles[role_id]
            else:
                # More agents than roles - remaining get neutral
                result[agent] = self.roles[RoleId.NEUTRAL.value]

        return result

    def _resolve_team_preset(
        self,
        team_id: str,
        active_agents: Sequence[str],
    ) -> dict[str, RoleDefinition]:
        """Resolve team preset to role assignments."""
        if team_id not in self.teams:
            raise ValueError(f"Unknown team preset '{team_id}'")

        team = self.teams[team_id]
        # Team preset is just an explicit mapping under the hood
        return self._resolve_explicit_mapping(team.assignments, active_agents)

    def get_role(self, role_id: str) -> RoleDefinition | None:
        """Get a role definition by ID."""
        return self.roles.get(role_id)

    def get_team(self, team_id: str) -> TeamPreset | None:
        """Get a team preset by ID."""
        return self.teams.get(team_id)

    def runner_overrides_for(self, spec: RoleSpec) -> dict[str, str]:
        """Runner overrides (seat -> runner name) for the team preset named by ``spec``.

        Returns ``{}`` for every ``spec`` shape except a ``str`` naming a team
        that exists in this resolver's registry — a dict/list/None spec never
        carries a team, and an unknown team name is a spec error surfaced
        elsewhere by ``resolve()``, not silently defaulted here.
        """
        if not isinstance(spec, str):
            return {}
        team = self.teams.get(spec)
        if team is None:
            return {}
        return dict(team.runner_overrides)

    def list_roles(self) -> list[str]:
        """List all available role IDs."""
        return list(self.roles.keys())

    def list_teams(self) -> list[str]:
        """List all available team preset IDs."""
        return list(self.teams.keys())


# === Global Resolver Instance ===

_resolver: RoleResolver | None = None


def get_resolver() -> RoleResolver:
    """Get the global RoleResolver instance (lazy initialization)."""
    global _resolver
    if _resolver is None:
        _resolver = create_default_resolver()
    return _resolver


def create_default_resolver() -> RoleResolver:
    """Create a RoleResolver with merged built-in and user-defined roles/teams."""
    roles, teams = get_merged_roles_and_teams()
    return RoleResolver(roles=roles, teams=teams)


def reload_resolver():
    """Reload the global resolver (useful after config file changes)."""
    global _resolver
    _resolver = create_default_resolver()


def resolve_generate_role_prefix(role_id: str | None) -> str:
    """Round-1 role prefix for single-model *generate* entrypoints.

    Used by the single-model tools (second_opinion, start_*_session) to focus a
    model with a role framing. Resolves ``role_id`` against the same merged
    builtin + ``~/.owlex/roles.json`` registry that council uses.

    Unlike council's ``inject_role_prefix``, this deliberately does NOT prepend
    ``COUNCIL_SYSTEM_INSTRUCTION``: those tools *generate* content (e.g. a test
    spec) rather than give read-only advice, so the read-only-advisor framing
    would be counter-productive. (TASK-15 decision — see README / docstrings.)

    Returns ``""`` for a falsy or non-``str`` ``role_id`` so the caller's prompt
    stays byte-identical. Raises ``ValueError`` on an unknown id so the caller
    surfaces a clear error instead of silently ignoring the requested framing.
    """
    # Non-str (e.g. a pydantic FieldInfo sentinel on a direct, non-MCP call) or
    # empty → no framing. A whitespace-only id is truthy and falls through to the
    # registry lookup, where it resolves to no role → ValueError (callers reject it).
    if not isinstance(role_id, str) or not role_id:
        return ""
    resolver = get_resolver()  # cached registry — same path council uses
    role = resolver.get_role(role_id)
    if role is None:
        available = ", ".join(sorted(resolver.list_roles()))
        raise ValueError(f"Unknown role '{role_id}'. Available roles: {available}")
    return role.round_1_prefix


def frame_prompt_for_generation(prompt: str, role_id: str | None) -> tuple[str | None, str | None]:
    """Prepend a role's round-1 prefix to ``prompt`` for single-model generate tools.

    Shared by ``second_opinion`` and the ``start_*_session`` tools so role
    resolution + error semantics stay identical across all single-model surfaces.
    Returns ``(framed_prompt, None)`` on success, or ``(None, error_message)`` when
    ``role_id`` is an unknown id — the caller wraps the message in its own response
    type. ``role_id`` falsy/non-str → no framing (prompt returned verbatim).
    """
    try:
        return resolve_generate_role_prefix(role_id) + prompt, None
    except ValueError as e:
        return None, str(e)
