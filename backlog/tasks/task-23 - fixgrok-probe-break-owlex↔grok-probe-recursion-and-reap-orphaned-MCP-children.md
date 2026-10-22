---
id: TASK-23
title: >-
  fix(grok-probe): break owlex↔grok probe recursion and reap orphaned MCP
  children
status: Done
assignee: []
created_date: '2026-08-11 08:10'
updated_date: '2026-08-14 18:24'
labels:
  - high priority
dependencies: []
priority: high
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The grok seat health-probe in `owlex/agents/grok.py` recursively re-launches owlex and leaks orphaned MCP server processes, driving the machine into swap death. Measured live on 2026-08-11: load average 353 on a 14-core M3 Max, 915 processes / 8130 threads, 0.76% idle CPU with 58.7% in sys, 14 GB in the memory compressor, 9.85 GB of 11.26 GB swap used, `Pages free: 910` (~3.5 MB). kernel_task sat at 254% CPU — not thermal throttling (`pmset -g therm` reported no thermal warning recorded), but pure compressor/swap work.

ROOT CAUSE — three compounding defects:

1. RECURSION. `~/.claude/.mcp.json` registers exactly one MCP server: `owlex` -> `owlex-server`. On startup owlex-server calls `probe_grok_seat()`, whose command (`_build_probe_command()`, grok.py:314-324) is a full agent-CLI session:
   `grok -p "Reply with exactly: ok" --output-format json --always-approve --model <model> --effort <effort> --disable-web-search`
   Launching `grok` boots grok's entire MCP stack — 8 npm servers (figma-developer-mcp, @modelcontextprotocol/server-brave-search, firecrawl-mcp, @upstash/context7-mcp, mcp-sequentialthinking-tools, gemini-mcp-tool, npm-search-mcp-server, mcp-remote) AND owlex itself. That nested owlex-server runs its own probe -> spawns another grok -> unbounded recursion.
   Observed directly in the live process table (parentage, not inference):
     pid=78044 ppid=1     grok -p "Reply with exactly: ok"
     pid=79688 ppid=78044 owlex-server        <- owlex is a child of grok
     pid=80288 ppid=79688 grok -p "Reply..."  <- grok is a child of owlex
     pid=81781 ppid=80288 owlex-server
   Every live `npm exec` MCP server traced had a `grok -p "Reply with exactly: ok"` process as its direct parent.

2. NO PROCESS-GROUP REAP. Probe timeout is 30s. On timeout `_terminate(proc)` calls `proc.kill()`, which kills only the grok process, not its process group. Grok's 8 MCP children are reparented to launchd (PPID 1) and never reaped. They spin in `Rs` state: 130 orphaned MCP processes held 533.8% CPU aggregate and ~13.5 GB RSS.

3. 24h CACHE CLIFF TURNS THIS INTO A DAILY STORM. `_read_probe_cache()` / `PROBE_CACHE_TTL = 86400` at `~/.owlex/cache/grok-probe.json`. The stamp read `{"model": "grok-4.5", "probed_at": 1786341896}` = 2026-08-10 10:04, so it expired 2026-08-11 ~10:04. While the stamp is fresh no probe runs and the recursion is dormant; once it expires, the next cmux launch with many panes starts N claude sessions -> N owlex-servers -> N simultaneous probes, all seeing an expired cache. cmux started 11:41, the orphan storm began 11:42. Reproduces on a ~24h cycle, which is why this has recurred multiple times. The code comment "Failures never cache so a dropped auth session is re-detected on the next MCP start" means a broken grok auth would make the storm continuous rather than daily.

IMPLEMENTATION NOTE: Edit the owlex source repo, NOT `~/.local/share/uv/tools/owlex/lib/python3.11/site-packages/owlex/` — changes there are wiped by the next `uv tool upgrade`.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Re-entrancy guard: the probe sets a marker in the child environment (e.g. OWLEX_IN_PROBE=1); owlex-server skips probe_grok_seat() when it sees that marker. Verified by asserting no owlex-server process ever has a grok -p "Reply with exactly: ok" ancestor.
- [x] #2 _terminate() kills the whole process group: start_new_session=True passed to asyncio.create_subprocess_exec, and termination via os.killpg(os.getpgid(proc.pid), ...). Verified by forcing a probe timeout and asserting zero processes are left with PPID=1 from that probe.
- [x] #3 The probe no longer boots grok's MCP stack — run it against an isolated GROK_HOME/config whose mcpServers is empty. NOTE: grok --help exposes no --no-mcp flag (there is a grok mcp subcommand for config management), so an isolated home is the verified route, not a flag. Verified by asserting a probe spawns 0 npm exec child processes.
- [x] #4 Failed probes are cached with a short TTL (e.g. 5-15 min) so a broken seat degrades to a slow retry instead of a probe storm. The existing "failures never cache" behaviour is explicitly replaced, and the reason is recorded in a comment.
- [x] #5 Regression check: with the cache stamp deleted, starting 5 owlex-servers concurrently produces at most 5 grok probes total and leaves 0 orphaned MCP processes.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause confirmed: grok resolves MCP servers from Claude Codes config — the global mcpServers block in $HOME/.claude.json plus the entry keyed by cwd, and also Cursor .mcp.json. grok inspect from the owlex repo reports "MCP Servers (12)", including owlex (stdio) from ~/.claude.json — that is the recursion edge.

Fix, all in owlex/agents/grok.py + owlex/config.py:
1. AC#1 re-entrancy: _probe_should_skip() returns a skip reason when OWLEX_IN_PROBE=1 (set in the probes child env) or when OWLEX_COUNCIL_DEPTH>0 (already set by TaskEngine for every agent subprocess). A nested owlex-server now skips its probe instead of spawning another grok.
2. AC#2 process-group reap: probe spawned with start_new_session=True; _terminate() escalates SIGTERM→SIGKILL via os.killpg(os.getpgid(pid), ...), with an explicit guard that never signals owlex-servers own process group.
3. AC#3 no MCP stack: probe and seat runs get HOME=$OWLEX_HOME/cache/grok-isolated-home (empty dir) while GROK_HOME keeps ~/.grok so auth still works. grok inspect under that env reports "MCP Servers (0)".
4. AC#4 failure caching: new PROBE_FAIL_TTL (OWLEX_GROK_PROBE_FAIL_TTL, default 600s). Failures now cache with the short TTL; the old "failures never cache" behaviour is replaced and the reason is recorded in a comment. Legacy stamps without an ok field are still read as successes.
5. AC#5 regression: measured live — 5 concurrent owlex-servers with the cache stamp deleted produced exactly 5 grok probes, 0 owlex-servers with a probe parent, 0 orphaned npm exec processes with PPID=1, and 0 grok MCP logs touched.

Scope extension beyond the original ACs, approved by the user in-session: the same HOME isolation is applied to grok COUNCIL SEAT runs via AgentCommand.env_overrides, gated by OWLEX_GROK_ISOLATE_MCP (default true). Justification: across 20 sampled grok council sessions the seat used only built-in grep / read_file / run_terminal_command and never an mcp__* tool. Measured end-to-end: a seat run went from 25 descendant processes to 2, with cwd preserved so the repos own CLAUDE.md still loads. Known behaviour change: the seat no longer sees user-global instructions under $HOME (4 instruction files → 1) or user-global skills (184 → 26).

Verification: python -m pytest tests/ -q → EXIT=0, 529 passed. tests/test_grok_probe.py grew from 6 to 22 tests and is now listed as a regression contract in CLAUDE.md.

## Post-implementation audit round (9-judge solution-audit)

A 9-dimension fresh-context audit found defects in the first implementation. All were verified by reading the cited lines, and several by running the code. Fixed in a second pass:

1. **Import-time crash (❌).** `PROBE_FAIL_TTL = int(os.getenv("OWLEX_GROK_PROBE_FAIL_TTL", "600"))` raised `ValueError` on an empty or non-numeric value, and `owlex/agents/__init__.py` imports `GrokRunner` unconditionally — so a typo in a newly documented operator knob took down the entire agents package and with it the MCP server. Reproduced: `OWLEX_GROK_PROBE_FAIL_TTL= python -c "import owlex.agents"` → ValueError. Both TTLs now use the codebase's own `config._get_int(..., min_value=1)`, which warns and falls back; this also rejects 0/negative, which would have silently disabled caching and restored the probe storm.

2. **Cancellation leaked the whole grok process group (❌).** `asyncio.CancelledError` is a `BaseException`, so it bypassed both `except asyncio.TimeoutError` and `except Exception`, and `_terminate` never ran. Because the fix had just added `start_new_session=True`, the leaked tree was in its own session where nothing would ever signal it — a permanent leak, reachable on every server shutdown inside the 30s probe window. Now wrapped in `except BaseException: await _terminate(proc, pgid); raise`.

3. **Seat isolation failed OPEN (❌).** When `mkdir` failed, `_seat_env_overrides()` returned `None`, handing the seat the real `HOME` — silently restoring the ~17-server MCP stack the ticket exists to strip. Isolation is now unconditional; `_ensure_isolated_home()` is best-effort for `cwd` only. Verified that grok runs fine with a nonexistent `HOME` (exit 0, correct answer, dir not created), so nothing depends on the directory existing.

4. **PID-reuse race in `_terminate`.** `os.getpgid(proc.pid)` was resolved at kill time, but the 3.11 `ThreadedChildWatcher` frees the pid on another thread before `returncode` is published — so it could read a recycled pid's group and `killpg` an unrelated process tree. The pgid is now captured immediately after spawn, while the pid is guaranteed live, and passed into `_terminate`.

5. **SIGKILL never reached the group.** The escalation loop returned as soon as the leader exited, and grok exits promptly on SIGTERM — so the spinning group members (the actual 533%-CPU failure) were never SIGKILLed. SIGKILL is now sent to the group unconditionally after the leader is reaped.

6. **`os.getpgid(0)` sat outside the try/except**, so it could raise through `probe_grok_seat`, contradicting its documented "Never raises". Same for `proc.kill()`, which caught only `ProcessLookupError` and let `PermissionError` through. Both guarded; the final `proc.wait()` is now bounded rather than unbounded.

7. **`OWLEX_IN_PROBE=0` disabled the probe forever** while startup still logged `[ok]` — the marker was tested for bare truthiness. Now parsed with explicit falsy handling (`""`/`0`/`false`/`no`/`off`).

8. **Stamp format could invert after a downgrade.** An older owlex reading a new `ok:false` stamp returned it as a 24h success. Failure stamps now live in a separate file (`grok-probe-fail.json`) that older readers never open; a success also deletes it, so recovery busts the cache immediately instead of waiting out the TTL.

9. **Isolation was asymmetric between probe and seat.** The probe popped `XDG_CONFIG_HOME`, but `AgentCommand.env_overrides` can only ADD keys (`engine.py` does `env.update`), so the seat could not. `XDG_CONFIG_HOME` is now REDIRECTED into the isolated home for both paths.

10. **Seats lost git identity.** A seat can call grok's built-in `run_terminal_command`, and those children inherit the empty `HOME` — dropping `~/.gitconfig`, so `user.name`, aliases and `safe.directory` vanished. Verified: `HOME=<iso> git config --get user.name` returned empty vs `Konstantin`. `GIT_CONFIG_GLOBAL` now points at the real `~/.gitconfig`; re-verified it returns `Konstantin`.

11. Also: deleted the dead `_probe_isolation_dir()` alias (0 production call-sites, documented as "back-compat" for a symbol introduced in the same diff); renamed `ensure_isolated_home` → `_ensure_isolated_home` to match the module's private-helper convention; moved `_probe_failure` above its seven call-sites; added `isolate_mcp` to `_mock_grok_cfg` in `tests/test_agents.py` (it was an unset MagicMock, silently truthy); removed the stale `~/.owlex/cache/grok-probe-home/` directory.

**AC#5 is now reproducible**: the measurement was previously only prose in this ticket. Added `scripts/verify_grok_probe_isolation.sh` (added to the working tree, NOT yet committed; exits non-zero on failure). It contains a load-bearing measurement note — match candidates on `comm`, never a bare `pgrep -f <prompt>`, because the script's own text contains the probe prompt and an argv-substring match counts the measuring shell itself; an earlier ad-hoc run reported 10 phantom probes and 10 phantom nested owlex-servers from exactly that self-match.

**Verification after the audit round**: `python -m pytest tests/ -q` → EXIT=0, 544 passed, 0 failures. `ruff check` clean on all changed files (it also caught a duplicated `test_probe_timeout` where the later definition silently shadowed the earlier — stale copy removed). `scripts/verify_grok_probe_isolation.sh 5` → PASS (produced at most 5 concurrent grok probes (the instrument measured concurrency, not a true total), 0 nested, 0 orphans, 0 MCP boots). Seat end-to-end re-verified: 2 descendants, 0 MCP logs touched, cwd preserved.

## Second audit round (9 judges) — defects found in the first fix

A second 9-dimension audit was run on the post-fix code. It found that the first fix's own repairs were incomplete. Fixed in a third pass:

1. **❌ The group reap was a no-op in the exact case it was written for.** `_terminate` began with `if proc is None or proc.returncode is not None: return`. `proc.returncode` is published as soon as the LEADER (grok) is reaped, while `communicate()` stays blocked until every fd-inheriting descendant closes the pipes. So the primary orphan path — grok exits, its ~17 npm children keep holding stdout, `communicate()` times out — hit that guard and returned WITHOUT ever calling `killpg`. Reproduced standalone: leader `returncode=0`, `communicate()` timed out, process group still alive. The guard now gates only the leader `proc.kill()` fallback; the group reap always runs when a pgid is known.

2. **❌ A stale success stamp outranked a fresher failure stamp.** `_read_probe_cache` resolved a two-file disagreement by file precedence rather than recency, so a success inside its 24h TTL masked a failure recorded minutes earlier and a broken seat kept reporting `[ok]`. Now compares `probed_at` and the newer stamp wins; a failure also retires the success stamp (previously only the reverse happened).

3. **❌ The AC#5 harness could pass vacuously — disarmed by this ticket's own AC#4.** The script deleted only `grok-probe.json`, not the `grok-probe-fail.json` that AC#4 introduced. Proven by planting a fresh failure stamp and running it: every server cache-hit, zero probes ran, and the script printed **PASS** with exit 0. It now deletes both stamps, refuses to run when the grok MCP log dir is missing (a missing dir made `find` return 0, indistinguishable from a real pass), and — per the "green != exercised" rule — FAILS when zero probes ran or when fewer than N servers reported a health-check. Verified both directions: with a planted failure stamp it now runs 5 real probes and PASSes; forced to skip every probe (`OWLEX_IN_PROBE=1`) it now prints FAILED and exits 1 where it previously printed PASS.

4. **Cancellation could still abort the reap half-done.** `except (asyncio.TimeoutError, Exception)` does not catch `CancelledError` (a BaseException), so a cancel landing during the 5s wait skipped the guaranteed SIGKILL — leaving the group with only SIGTERM, which kills grok but not the spinning members, in a session nothing else can signal. The guaranteed group SIGKILL and leader kill now live in a `finally` (verified that `finally` runs on cancellation).

5. **Silent degradation to leader-only kill.** `os.getpgid(proc.pid)` can lose a race with the child watcher on a fast exit; it set `pgid=None`, which disabled the group branch entirely — the original bug, with no diagnostic. It now falls back to `pgid = proc.pid`, which is exactly correct under `start_new_session=True`.

6. **An unguarded `unlink` broke the documented "Never raises" contract.** `_probe_fail_path().unlink(...)` was the one filesystem call not wrapped, and it sits on the SUCCESS path — reproduced: a directory in the stamp's place raises `PermissionError` after the success stamp is written, so a healthy seat gets logged as `[WARN]`. Now goes through a guarded `_discard_stamp`.

7. **Removed `OWLEX_REAL_HOME` entirely** (flagged independently by three judges). Its `setdefault` in `_build_probe_env` was dead code (the helper read `os.environ`, not the local dict); its two writers disagreed (`setdefault` vs unconditional assign); it was an unvalidated trust root that silently re-roots `GROK_HOME` and `GIT_CONFIG_GLOBAL`; and in the only scenario it existed for, the seat writer would have forwarded the ISOLATED home. `Path.home()` is now read directly.

8. **Consistency/simplification:** the two TTLs moved into `GrokConfig` (as `probe_ttl` / `probe_fail_ttl`), which removes `from ..config import _get_int` — the only cross-module import of a private symbol in production code. Dropped `_env_flag_set` (a second truthiness parser with one call-site that diverged from `config._get_bool`, and treated `OWLEX_IN_PROBE=n` as SET — reintroducing the round-1 defect for that one alias). Collapsed the doubled `try` in `probe_grok_seat` into a single try with `except BaseException` as its last clause (this also stopped `_terminate` running twice). Collapsed misleading except-tuples: on 3.11 `(ProcessLookupError, PermissionError, OSError)` == `OSError` and `(asyncio.TimeoutError, Exception)` == `Exception`. Dropped `asyncio.shield` (it left stray pending tasks and bought nothing). Named the magic 5.0 reap timeout `REAP_GRACE_SECONDS`. Moved the MCP-isolation helpers ABOVE `class GrokRunner` and out from under the "Startup health-check" banner that mislabelled them. An empty grok answer is now a failure instead of a 24h success stamp.

**Mutation-tested, not just green:** re-introducing the early-return guard fails `test_terminate_reaps_group_even_when_leader_already_exited` and nothing else; disabling the `finally` fails `test_terminate_delivers_sigkill_even_when_cancelled` and nothing else.

**KNOWN REMAINING, deliberately out of scope (needs a decision):** `owlex/engine.py` spawns seat subprocesses without `start_new_session` and `_terminate_process` carries the SAME `returncode` early-return guard as defect #1 — so a timed-out grok SEAT still orphans its children, and `OWLEX_GROK_ISOLATE_MCP=false` re-arms the full MCP stack under that weaker kill path. `owlex/agreement.py` and `owlex/second_opinion.py` also still use bare `proc.kill()` with no `start_new_session` and no BaseException reap. Additionally the `scripts/shadow_grok_*.py` harnesses build raw grok argv, bypassing `GrokRunner`, so they still run the full MCP stack and no longer measure the same environment as production seats.
<!-- SECTION:NOTES:END -->
