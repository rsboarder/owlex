"""
Council orchestration logic for multi-agent deliberation.
Handles parallel execution and deliberation rounds.
"""

import asyncio
import os
import shutil
import sys
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
)


def _log(msg: str):
    """Log progress to stderr for CLI visibility."""
    print(msg, file=sys.stderr, flush=True)


def _display_name(agent_name: str) -> str:
    """Get display name for an agent, using custom model names where configured."""
    custom = {
        "claudeor": config.claudeor.model or "Claude/OpenRouter",
        "aichat": config.aichat.model or "AiChat",
        "cursor": config.cursor.model or "Cursor",
    }
    return custom.get(agent_name, agent_name.title())


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
        """Send notification to MCP client if context supports it."""
        if not self.context:
            return
        try:
            session = getattr(self.context, 'session', None)
            if not session:
                return

            if hasattr(session, 'send_progress_notification') and progress is not None:
                try:
                    await session.send_progress_notification(
                        progress_token="owlex-council",
                        progress=progress,
                        total=100.0,
                        message=message,
                    )
                except Exception:
                    pass

            if hasattr(session, 'send_log_message'):
                await session.send_log_message(level=level, data=message, logger="owlex")
        except Exception:
            pass

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

        # Build active agent list
        active_agents = self._resolve_active_agents()

        # Resolve roles
        role_spec = roles if roles is not None else team
        resolved_roles = self._resolver.resolve(role_spec, active_agents)

        council_start = datetime.now()

        # Log role assignments
        role_msgs = [f"{agent.title()}: {role.name}" for agent, role in resolved_roles.items() if role.id != RoleId.NEUTRAL.value]
        if role_msgs:
            roles_summary = ", ".join(role_msgs)
            self.log(f"Roles assigned: {roles_summary}")
            await self.notify(f"Council roles: {roles_summary}", progress=10)

        # === Round 1 ===
        if claude_opinion and claude_opinion.strip():
            self.log(f"Claude's opinion received ({len(claude_opinion)} chars)")

        self.log(f"Round 1: querying {', '.join(a.title() for a in active_agents)}...")
        await self.notify(f"Council Round 1: querying {', '.join(a.title() for a in active_agents)}", progress=20)

        round_1 = await self._run_round_1(prompt, working_directory, effective_timeout, resolved_roles, active_agents)
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
                roles=resolved_roles,
                active_agents=active_agents,
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

    def _resolve_active_agents(self) -> list[str]:
        """Build list of active agents after exclusion and availability checks."""
        excluded = config.council.exclude_agents
        all_agents = ["codex", "gemini", "opencode"]
        if config.claudeor.api_key:
            all_agents.append("claudeor")
        all_agents.append("aichat")
        all_agents.append("cursor")
        active = [a for a in all_agents if a not in excluded]

        # Pre-flight: skip agents whose CLI isn't installed
        unavailable = [
            a for a in active
            if not shutil.which(AGENT_RUNNERS[Agent(a)].cli_command)
        ]
        if unavailable:
            active = [a for a in active if a not in unavailable]
            self.log(f"Skipping unavailable agents: {', '.join(unavailable)}")
        return active

    @staticmethod
    def _r1_kwargs(agent_name: str) -> dict:
        """Agent-specific kwargs for R1 execution."""
        if agent_name == "codex":
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

    # === Round execution ===

    async def _run_round_1(
        self,
        prompt: str,
        working_directory: str | None,
        timeout: int | None,
        roles: dict[str, RoleDefinition],
        active_agents: list[str],
    ) -> CouncilRound:
        """Run the first round of parallel queries."""
        round_start = datetime.now()
        tasks = {}
        async_tasks = []

        for agent_name in active_agents:
            agent_enum = Agent(agent_name)
            runner = AGENT_RUNNERS[agent_enum]
            role = roles.get(agent_name)
            agent_prompt = inject_role_prefix(prompt, role)

            task = self._engine.create_task(
                command=f"council_{agent_enum.value}",
                args={"prompt": agent_prompt, "working_directory": working_directory},
                context=self.context,
                council_id=self.council_id,
            )
            tasks[agent_name] = task

            kwargs = self._r1_kwargs(agent_name)
            display = _display_name(agent_name)

            async def run(t=task, r=runner, p=agent_prompt, d=display, kw=kwargs):
                await self._engine.run_agent(
                    t, r, mode="exec", prompt=p,
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

        # Parse session IDs in parallel for R2 resume
        r1_start_mtime = round_start.timestamp() - 1.0

        async def parse_session(name: str) -> tuple[str, str | None]:
            if name not in tasks or tasks[name].status != "completed":
                return name, None
            runner = AGENT_RUNNERS[Agent(name)]
            session = await runner.parse_session_id(
                "", since_mtime=r1_start_mtime, working_directory=working_directory,
            )
            if session and not runner.validate_session_id(session):
                self.log(f"{name.title()} session ID validation failed: {session}")
                return name, None
            if not session:
                self.log(f"{name.title()} session ID not found, R2 will use exec mode")
            return name, session

        session_results = await asyncio.gather(*[parse_session(n) for n in tasks])
        sessions = dict(session_results)

        round_data = {
            name: build_agent_response(task, Agent(name), session_id=sessions.get(name))
            for name, task in tasks.items()
        }
        return CouncilRound(**round_data)

    async def _run_round_2(
        self,
        prompt: str,
        working_directory: str | None,
        round_1: CouncilRound,
        claude_opinion: str | None,
        critique: bool,
        timeout: int | None,
        roles: dict[str, RoleDefinition],
        active_agents: list[str],
    ) -> CouncilRound:
        """Run the second round of deliberation."""
        self.log("Round 2: deliberation phase...")

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

        for agent_name in active_agents:
            if agent_name in r1_failed:
                continue

            agent_enum = Agent(agent_name)
            runner = AGENT_RUNNERS[agent_enum]
            role = roles.get(agent_name)
            session = sessions.get(agent_name)

            resume_prompt = build_deliberation_prompt_with_role(
                original_prompt=prompt, role=role, claude_answer=claude_content,
                critique=critique, include_original=False, **answer_kwargs,
            )
            exec_prompt = build_deliberation_prompt_with_role(
                original_prompt=prompt, role=role, claude_answer=claude_content,
                critique=critique, include_original=True, **answer_kwargs,
            )

            task = self._engine.create_task(
                command=f"council_{agent_enum.value}_delib",
                args={"prompt": resume_prompt, "working_directory": working_directory},
                context=self.context,
                council_id=self.council_id,
            )
            tasks[agent_name] = task

            kwargs = self._r2_kwargs(agent_name)
            display = _display_name(agent_name)

            async def run_delib(
                t=task, r=runner, s=session,
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

        await self.notify("Council Round 2 complete", progress=90)

        round_data = {
            name: build_agent_response(task, Agent(name))
            for name, task in tasks.items()
        }
        return CouncilRound(**round_data)
