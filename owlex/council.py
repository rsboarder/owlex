"""
Council orchestration logic for multi-agent deliberation.
Handles parallel execution and deliberation rounds.
"""

import asyncio
import os
import shutil
import sys
from dataclasses import replace
from datetime import datetime
from typing import Any

from .config import config
from .engine import engine, build_agent_response, AGENT_RUNNERS
from .prompts import inject_role_prefix, build_deliberation_prompt_with_role
from .roles import RoleSpec, RoleDefinition, RoleResolver, RoleId, get_resolver
from .models import (
    Agent,
    AgentResponse,
    AgentTiming,
    ClaudeOpinion,
    CouncilResponse,
    CouncilRound,
    CouncilMetadata,
    Participant,
)


ALL_SEATS = ("codex", "gemini", "opencode", "claudeor", "aichat", "cursor")


def _log(msg: str):
    """Log progress to stderr for CLI visibility."""
    print(msg, file=sys.stderr, flush=True)


def _display_name(p: Participant) -> str:
    """Get display name for a participant, showing substitution if applicable."""
    custom = {
        "claudeor": config.claudeor.model or "Claude/OpenRouter",
        "aichat": config.aichat.model or "AiChat",
        "cursor": config.cursor.model or "Cursor",
    }
    name = custom.get(p.seat, p.seat.title())
    if p.is_substituted:
        name = f"{name} (via {p.runner.cli_command})"
    return name


class Council:
    """
    Orchestrates multi-agent deliberation between CLI agents.

    The council process:
    1. Round 1: All agents answer the question in parallel
    2. Round 2 (optional): Agents see each other's answers and revise/critique

    Supports specialist roles ("hats") for agents to operate with specific perspectives.
    """

    def __init__(
        self,
        context: Any = None,
        task_engine: Any = None,
        role_resolver: RoleResolver | None = None,
    ):
        self.context = context
        self._engine = task_engine if task_engine is not None else engine
        self._resolver = role_resolver if role_resolver is not None else get_resolver()
        self.log_entries: list[str] = []
        self.council_id: str = datetime.now().strftime("%H%M%S")

    def log(self, msg: str):
        """Add to log and print to stderr."""
        self.log_entries.append(msg)
        _log(msg)

    async def notify(self, message: str, level: str = "info", progress: float | None = None):
        """Send notification to MCP client if context supports it.

        Safe because council_ask runs synchronously (blocking) — all notifications
        happen during the active tool call, which MCP protocol supports.
        """
        if not self.context:
            return
        handler = getattr(self.context, level, None)
        if not callable(handler):
            handler = getattr(self.context, 'info', None)
        if handler:
            try:
                await handler(f"[council] {message}")
            except Exception:
                pass

    def build_participants(self, role_spec: RoleSpec = None) -> list[Participant]:
        """Build the complete participant list with runners, roles, and substitutions.

        This is the single source of truth for seat-to-runner mapping.
        Can be called without starting a deliberation (e.g., for preview in server.py).
        """
        excluded = config.council.exclude_agents
        seats = [s for s in ALL_SEATS if s not in excluded]

        # Resolve roles for all seats
        resolved_roles = self._resolver.resolve(role_spec, seats)

        # Partition by CLI availability and configuration
        available = []
        unavailable = []
        for seat in seats:
            runner = AGENT_RUNNERS[Agent(seat)]
            if shutil.which(runner.cli_command) and runner.is_configured:
                available.append(seat)
            else:
                unavailable.append(seat)

        # Build donor pool: only configured donors that are actually available
        donor_pool = [s for s in config.council.substitution_donors if s in available]
        if not donor_pool and available:
            donor_pool = available  # fallback: use all available

        participants = []

        # Native seats
        for seat in available:
            participants.append(Participant(
                seat=seat,
                runner=AGENT_RUNNERS[Agent(seat)],
                is_substituted=False,
                role=resolved_roles[seat],
            ))

        # Substituted seats — round-robin across donor pool
        if unavailable:
            subs = []
            for i, seat in enumerate(unavailable):
                if not donor_pool:
                    self.log(f"No donors available for {seat}, skipping")
                    continue
                donor = donor_pool[i % len(donor_pool)]
                participants.append(Participant(
                    seat=seat,
                    runner=AGENT_RUNNERS[Agent(donor)],
                    is_substituted=True,
                    donor=donor,
                    role=resolved_roles[seat],
                ))
                subs.append(f"{seat}->{donor}")
            if subs:
                self.log(f"Substituting unavailable agents: {', '.join(subs)}")

        return participants

    async def deliberate(
        self,
        prompt: str,
        working_directory: str | None = None,
        claude_opinion: str | None = None,
        deliberate: bool = True,
        critique: bool = True,
        timeout: int | None = None,
        roles: RoleSpec = None,
        team: str | None = None,
    ) -> CouncilResponse:
        """
        Run a council deliberation session.

        Args:
            prompt: The question or task to deliberate on
            working_directory: Working directory context for agents (defaults to CWD)
            claude_opinion: Optional Claude opinion to share with agents
            deliberate: If True, run a second round where agents see each other's answers
            critique: If True, Round 2 asks agents to find flaws instead of revise
            timeout: Timeout per agent in seconds
            roles: Role specification - dict, list, or None (see RoleSpec)
            team: Team preset name (alternative to roles parameter)

        Returns:
            CouncilResponse with all rounds and metadata

        Raises:
            ValueError: If both roles and team are specified
        """
        if roles is not None and team is not None:
            raise ValueError("Cannot specify both 'roles' and 'team' parameters. Use one or the other.")

        if timeout is None:
            timeout = config.default_timeout
        COUNCIL_MIN_TIMEOUT = 300
        if timeout > 0 and timeout < COUNCIL_MIN_TIMEOUT:
            self.log(f"Timeout {timeout}s below council minimum, using {COUNCIL_MIN_TIMEOUT}s")
            timeout = COUNCIL_MIN_TIMEOUT
        effective_timeout = None if timeout == 0 else timeout

        if working_directory is None:
            working_directory = os.getcwd()

        # Build participants (single source of truth for seats, runners, roles)
        role_spec = roles if roles is not None else team
        participants = self.build_participants(role_spec)

        council_start = datetime.now()

        # Log role assignments
        role_msgs = [
            f"{_display_name(p)}: {p.role.name}"
            for p in participants
            if p.role.id != RoleId.NEUTRAL.value
        ]
        if role_msgs:
            roles_summary = ", ".join(role_msgs)
            self.log(f"Roles assigned: {roles_summary}")
            await self.notify(f"Council roles: {roles_summary}", progress=10)

        # === Round 1 ===
        if claude_opinion and claude_opinion.strip():
            self.log(f"Claude's opinion received ({len(claude_opinion)} chars)")

        seat_names = [p.seat.title() for p in participants]
        self.log(f"Round 1: querying {', '.join(seat_names)}...")
        await self.notify(f"Council Round 1: querying {', '.join(seat_names)}", progress=20)

        round_1 = await self._run_round_1(prompt, working_directory, effective_timeout, participants)
        await self.notify("Council Round 1 complete", progress=50)

        # === Round 2 ===
        round_2 = None
        if deliberate:
            await self.notify("Council Round 2: deliberation phase", progress=60)
            round_2 = await self._run_round_2(
                prompt=prompt,
                working_directory=working_directory,
                round_1=round_1,
                claude_opinion=claude_opinion,
                critique=critique,
                timeout=effective_timeout,
                participants=participants,
            )

        # Build response
        claude_opinion_obj = None
        if claude_opinion and claude_opinion.strip():
            claude_opinion_obj = ClaudeOpinion(
                content=claude_opinion.strip(),
                provided_at=council_start.isoformat(),
            )

        total_duration = (datetime.now() - council_start).total_seconds()
        await self.notify(f"Council deliberation complete ({total_duration:.1f}s)", progress=100)

        timing = self._collect_timing(round_1, 1) + (self._collect_timing(round_2, 2) if round_2 else [])
        timing.sort(key=lambda t: t.duration_seconds, reverse=True)
        slowest = timing[0].agent if timing else None

        resolved_roles = {p.seat: p.role for p in participants}

        return CouncilResponse(
            prompt=prompt,
            working_directory=working_directory,
            deliberation=deliberate,
            critique=critique,
            claude_opinion=claude_opinion_obj,
            round_1=round_1,
            round_2=round_2,
            roles=self._build_role_assignments(resolved_roles),
            metadata=CouncilMetadata(
                total_duration_seconds=(datetime.now() - council_start).total_seconds(),
                rounds=2 if deliberate else 1,
                log=self.log_entries,
                timing=timing,
                slowest_agent=slowest,
            ),
        )

    # === Helper methods ===

    @staticmethod
    def _r1_kwargs(runner_seat: str) -> dict:
        """Agent-specific kwargs for R1 execution, based on the runner's native seat."""
        if runner_seat == "codex":
            return {"enable_search": config.codex.enable_search}
        return {}

    @staticmethod
    def _r2_kwargs(agent_name: str) -> dict:
        """Agent-specific kwargs for R2 execution. No search in R2."""
        return {}

    @staticmethod
    def _collect_timing(round_data: CouncilRound, round_num: int) -> list[AgentTiming]:
        """Extract per-agent timing from a council round."""
        timings = []
        for agent_name in Agent:
            response: AgentResponse | None = getattr(round_data, agent_name.value, None)
            if response and response.duration_seconds is not None:
                timings.append(AgentTiming(
                    agent=response.agent,
                    round=round_num,
                    duration_seconds=response.duration_seconds,
                    status=response.status,
                ))
        return timings

    def _build_role_assignments(
        self,
        resolved_roles: dict[str, RoleDefinition],
    ) -> dict[str, str] | None:
        """Build role assignments dict for response (excludes neutral roles)."""
        assignments = {
            agent: role.id
            for agent, role in resolved_roles.items()
            if role.id != RoleId.NEUTRAL.value
        }
        return assignments if assignments else None

    def _log_round_summary(self, round_num: int, tasks: dict[str, Any], round_start: datetime):
        """Log timing summary for a round."""
        round_elapsed = (datetime.now() - round_start).total_seconds()
        self.log(f"Round {round_num} complete ({round_elapsed:.1f}s)")

        agent_timings = []
        for agent_name, task in tasks.items():
            if task.completion_time:
                dur = (task.completion_time - task.start_time).total_seconds()
                agent_timings.append((agent_name, dur, task.status))
        agent_timings.sort(key=lambda x: x[1])
        if agent_timings:
            self._engine.log_council_summary(self.council_id, round_num, agent_timings)
            order_str = " < ".join(f"{n}({d:.0f}s)" for n, d, _ in agent_timings)
            self.log(f"R{round_num} order: {order_str}")

    async def _wait_and_handle_timeouts(self, tasks: dict[str, Any], async_tasks: list, timeout: int | None):
        """Wait for async tasks and handle timeouts."""
        if not async_tasks:
            return

        done, pending = await asyncio.wait(
            async_tasks,
            timeout=timeout,
            return_when=asyncio.ALL_COMPLETED,
        )

        for task in tasks.values():
            if task.async_task in pending:
                self.log(f"{task.command} timed out")
                # Set status before killing so CancelledError handler skips duplicate logging
                task.status = "failed"
                task.error = f"Timed out after {timeout} seconds" if timeout else "Timed out"
                task.completion_time = datetime.now()
                await self._engine.kill_task_subprocess(task)
                self._engine._log_timing(task)

    @staticmethod
    def _is_capacity_error(task, runner) -> bool:
        """Check if a failed task hit capacity/quota errors the runner defines."""
        if not task.error or not runner.capacity_fail_patterns:
            return False
        return any(p in task.error for p in runner.capacity_fail_patterns)

    @staticmethod
    def _get_fallback_config(agent_name: str):
        """Get fallback (runner_name, model) for an agent, or None if not configured."""
        # Currently only Gemini supports fallback — extend here for other agents
        agent_configs = {
            "gemini": lambda: (config.gemini.fallback_runner, config.gemini.fallback_model)
                if config.gemini.fallback_model else None,
        }
        getter = agent_configs.get(agent_name)
        return getter() if getter else None

    async def _run_fallback(
        self,
        seat: str,
        original_runner_name: str,
        prompt: str,
        working_directory: str | None,
        round_start: datetime,
    ):
        """Retry a failed seat using its configured fallback runner."""
        fallback = self._get_fallback_config(original_runner_name)
        if not fallback:
            return None, None
        fallback_runner_name, fallback_model = fallback

        try:
            fallback_runner = AGENT_RUNNERS[Agent(fallback_runner_name)]
        except (ValueError, KeyError):
            self.log(f"Unknown fallback runner '{fallback_runner_name}' for {seat}")
            return None, None

        if not shutil.which(fallback_runner.cli_command):
            self.log(f"Fallback runner '{fallback_runner.cli_command}' not available for {seat}")
            return None, None

        task = self._engine.create_task(
            command=f"council_{seat}",
            args={"prompt": prompt, "working_directory": working_directory},
            context=self.context,
            council_id=self.council_id,
        )
        self.log(f"Capacity error on {seat} — retrying via {fallback_runner_name} ({fallback_model})")
        await self.notify(f"Fallback: retrying {seat} via {fallback_runner_name}")

        await self._engine.run_agent(
            task, fallback_runner, mode="exec", prompt=prompt,
            working_directory=working_directory,
            model_override=fallback_model,
        )
        elapsed = (datetime.now() - round_start).total_seconds()
        status = "completed" if task.status == "completed" else "failed"
        self.log(f"{seat} fallback {status} ({elapsed:.1f}s)")
        await self.notify(f"{seat} fallback {status} ({elapsed:.1f}s)")
        return task, fallback_runner_name

    # === Round execution ===

    async def _run_round_1(
        self,
        prompt: str,
        working_directory: str | None,
        timeout: int | None,
        participants: list[Participant],
    ) -> CouncilRound:
        """Run the first round of parallel queries."""
        round_start = datetime.now()
        tasks = {}
        async_tasks = []

        for p in participants:
            agent_prompt = inject_role_prefix(prompt, p.role)

            task = self._engine.create_task(
                command=f"council_{p.seat}",
                args={"prompt": agent_prompt, "working_directory": working_directory},
                context=self.context,
                council_id=self.council_id,
            )
            tasks[p.seat] = task

            # Use the runner's native seat for kwargs (e.g. codex search)
            kwargs = self._r1_kwargs(p.donor or p.seat)
            display = _display_name(p)

            async def run(t=task, r=p.runner, pr=agent_prompt, d=display, kw=kwargs):
                await self._engine.run_agent(
                    t, r, mode="exec", prompt=pr,
                    working_directory=working_directory, **kw,
                )
                elapsed = (datetime.now() - round_start).total_seconds()
                status = "completed" if t.status == "completed" else "failed"
                self.log(f"{d} {status} ({elapsed:.1f}s)")
                await self.notify(f"{d} {status} ({elapsed:.1f}s)")

            task.async_task = asyncio.create_task(run())
            async_tasks.append(task.async_task)

        await self._wait_and_handle_timeouts(tasks, async_tasks, timeout)
        self._log_round_summary(1, tasks, round_start)

        # Fallback: retry failed agents that hit capacity errors via configured fallback runner
        participant_map = {p.seat: p for p in participants}
        for p in participants:
            if p.is_substituted:
                continue
            task = tasks.get(p.seat)
            if task and task.status == "failed" and self._is_capacity_error(task, p.runner):
                agent_prompt = inject_role_prefix(prompt, p.role)
                fallback_task, fallback_runner_name = await self._run_fallback(
                    p.seat, p.runner.name, agent_prompt, working_directory, round_start,
                )
                if fallback_task:
                    tasks[p.seat] = fallback_task
                    fallback_runner = AGENT_RUNNERS[Agent(fallback_runner_name)]
                    participant_map[p.seat] = replace(
                        p, is_substituted=True, runner=fallback_runner, donor=fallback_runner_name,
                    )

        # Parse session IDs in parallel for R2 resume
        r1_start_mtime = round_start.timestamp() - 1.0

        async def parse_session(name: str) -> tuple[str, str | None]:
            p = participant_map[name]
            if name not in tasks or tasks[name].status != "completed":
                return name, None
            # Substituted agents can't reliably resume (shared session stores)
            if p.is_substituted:
                return name, None
            session = await p.runner.parse_session_id(
                "", since_mtime=r1_start_mtime, working_directory=working_directory,
            )
            if session and not p.runner.validate_session_id(session):
                self.log(f"{name.title()} session ID validation failed: {session}")
                return name, None
            if not session:
                self.log(f"{name.title()} session ID not found, R2 will use exec mode")
            return name, session

        session_results = await asyncio.gather(*[parse_session(n) for n in tasks])
        sessions = dict(session_results)

        round_data = {}
        for name, task in tasks.items():
            p = participant_map[name]
            # Use the runner's actual prefix for substituted seats
            prefix = p.runner.output_prefix if p.is_substituted else None
            round_data[name] = build_agent_response(
                task, Agent(name),
                session_id=sessions.get(name),
                output_prefix_override=prefix,
            )
        return CouncilRound(**round_data)

    async def _run_round_2(
        self,
        prompt: str,
        working_directory: str | None,
        round_1: CouncilRound,
        claude_opinion: str | None,
        critique: bool,
        timeout: int | None,
        participants: list[Participant],
    ) -> CouncilRound:
        """Run the second round of deliberation."""
        self.log("Round 2: deliberation phase...")
        participant_map = {p.seat: p for p in participants}

        # Skip agents that failed in R1
        r1_failed = set()
        for agent_name in Agent:
            r1_result: AgentResponse | None = getattr(round_1, agent_name.value, None)
            if r1_result and r1_result.status == "failed":
                r1_failed.add(agent_name.value)
                self.log(f"Skipping {agent_name.value} in R2 (failed in R1)")

        # Collect R1 content and sessions
        r1_content = {}
        sessions = {}
        for agent_name in Agent:
            r1_result = getattr(round_1, agent_name.value, None)
            if r1_result:
                r1_content[agent_name.value] = r1_result.content or r1_result.error or "(no response)"
                sessions[agent_name.value] = r1_result.session_id

        claude_content = claude_opinion.strip() if claude_opinion else None

        # Build answer kwargs for deliberation prompts
        answer_kwargs = {f"{a.value}_answer": r1_content.get(a.value) for a in Agent}

        round_start = datetime.now()
        tasks = {}
        async_tasks = []

        for p in participants:
            if p.seat in r1_failed:
                continue

            # Substituted agents can't resume — multiple instances may share session store
            session = None if p.is_substituted else sessions.get(p.seat)

            resume_prompt = build_deliberation_prompt_with_role(
                original_prompt=prompt, role=p.role, claude_answer=claude_content,
                critique=critique, include_original=False, **answer_kwargs,
            )
            exec_prompt = build_deliberation_prompt_with_role(
                original_prompt=prompt, role=p.role, claude_answer=claude_content,
                critique=critique, include_original=True, **answer_kwargs,
            )

            task = self._engine.create_task(
                command=f"council_{p.seat}_delib",
                args={"prompt": resume_prompt, "working_directory": working_directory},
                context=self.context,
                council_id=self.council_id,
            )
            tasks[p.seat] = task

            kwargs = self._r2_kwargs(p.seat)
            display = _display_name(p)

            async def run_delib(
                t=task, r=p.runner, s=session,
                rp=resume_prompt, ep=exec_prompt, d=display, kw=kwargs,
            ):
                if s:
                    await self._engine.run_agent(
                        t, r, mode="resume", session_ref=s,
                        prompt=rp, working_directory=working_directory, **kw,
                    )
                else:
                    await self._engine.run_agent(
                        t, r, mode="exec",
                        prompt=ep, working_directory=working_directory, **kw,
                    )
                elapsed = (datetime.now() - round_start).total_seconds()
                self.log(f"{d} revised ({elapsed:.1f}s)")
                await self.notify(f"{d} revised ({elapsed:.1f}s)")

            task.async_task = asyncio.create_task(run_delib())
            async_tasks.append(task.async_task)

        await self._wait_and_handle_timeouts(tasks, async_tasks, timeout)
        self._log_round_summary(2, tasks, round_start)

        # Fallback: retry failed agents that hit capacity errors
        for p in participants:
            task = tasks.get(p.seat)
            if task and task.status == "failed" and self._is_capacity_error(task, p.runner):
                fallback_prompt = build_deliberation_prompt_with_role(
                    original_prompt=prompt, role=p.role, claude_answer=claude_content,
                    critique=critique, include_original=True, **answer_kwargs,
                )
                fallback_task, fallback_runner_name = await self._run_fallback(
                    p.seat, p.runner.name, fallback_prompt, working_directory, round_start,
                )
                if fallback_task:
                    tasks[p.seat] = fallback_task
                    fallback_runner = AGENT_RUNNERS[Agent(fallback_runner_name)]
                    participant_map[p.seat] = replace(
                        p, is_substituted=True, runner=fallback_runner, donor=fallback_runner_name,
                    )

        await self.notify("Council Round 2 complete", progress=90)

        round_data = {}
        for name, task in tasks.items():
            p = participant_map[name]
            prefix = p.runner.output_prefix if p.is_substituted else None
            round_data[name] = build_agent_response(
                task, Agent(name),
                output_prefix_override=prefix,
            )
        return CouncilRound(**round_data)
