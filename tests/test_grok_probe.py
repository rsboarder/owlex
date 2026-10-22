"""Tests for grok seat startup health-check (auth / CLI / model).

Never raises. Successes stamp for ``PROBE_CACHE_TTL``; failures stamp for the
much shorter ``PROBE_FAIL_TTL``. The probe must never boot grok's MCP stack and
must never recurse into another owlex-server (TASK-23).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from owlex.agents import grok as grok_mod


@pytest.fixture(autouse=True)
def _clear_nesting_markers(monkeypatch):
    """A probe only runs when it is not already inside grok/a council agent."""
    monkeypatch.delenv("OWLEX_IN_PROBE", raising=False)
    monkeypatch.delenv("OWLEX_COUNCIL_DEPTH", raising=False)


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self.pid = 424242
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, *a, **k):
        return self._stdout, self._stderr

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _ok_stdout(text: str = "ok") -> bytes:
    return json.dumps({"text": text, "stopReason": "EndTurn"}).encode()


def _patch_exec(monkeypatch, proc_factory, calls):
    async def fake_exec(*args, **kw):
        calls.append({"argv": args, "kwargs": kw})
        return proc_factory()

    monkeypatch.setattr(grok_mod.shutil, "which", lambda _: "/usr/bin/grok")
    monkeypatch.setattr(grok_mod.asyncio, "create_subprocess_exec", fake_exec)
    return calls


@pytest.mark.asyncio
async def test_probe_ok_and_caches(monkeypatch):
    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, _ok_stdout()), [])

    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is True
    assert "probed ok" in msg
    assert len(calls) == 1

    ok2, msg2 = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert ok2 is True
    assert len(calls) == 1, "second call must reuse cache"
    assert "cached" in msg2


@pytest.mark.asyncio
async def test_probe_missing_cli(monkeypatch):
    monkeypatch.setattr(grok_mod.shutil, "which", lambda _: None)
    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is False
    assert "not found" in msg.lower()



@pytest.mark.asyncio
async def test_stale_cache_reprobes(monkeypatch):
    grok_mod._write_probe_cache("old ok", ok=True)
    path = grok_mod._probe_cache_path()
    data = json.loads(path.read_text())
    data["probed_at"] = time.time() - (grok_mod.PROBE_CACHE_TTL + 10)
    data["model"] = grok_mod.config.grok.model
    path.write_text(json.dumps(data))

    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, _ok_stdout()), [])

    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert ok is True
    assert len(calls) == 1
    assert "cached" not in msg


def test_probe_command_uses_configured_model():
    cmd = grok_mod._build_probe_command()
    assert cmd[0] == "grok"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == grok_mod.config.grok.model
    # Probe must stay tiny — no output-contract appendix
    p = cmd[cmd.index("-p") + 1]
    assert "Output contract" not in p


# --- TASK-23: recursion fence -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "marker,value",
    [("OWLEX_IN_PROBE", "1"), ("OWLEX_COUNCIL_DEPTH", "1"), ("OWLEX_COUNCIL_DEPTH", "3")],
)
async def test_probe_skipped_when_nested(monkeypatch, marker, value):
    """An owlex-server booted underneath grok must not probe grok again."""
    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, _ok_stdout()), [])
    monkeypatch.setenv(marker, value)

    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is True
    assert "skipped" in msg.lower()
    assert calls == [], "nested probe must spawn no subprocess"


@pytest.mark.asyncio
@pytest.mark.parametrize("falsy", ["0", "false", "no", "n", "off", ""])
async def test_falsy_in_probe_marker_does_not_disable_the_probe(monkeypatch, falsy):
    """`OWLEX_IN_PROBE=0` is how an operator turns a flag OFF.

    Bare truthiness would have silently disabled the health-check forever while
    startup kept logging [ok].
    """
    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, _ok_stdout()), [])
    monkeypatch.setenv("OWLEX_IN_PROBE", falsy)

    ok, _ = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is True
    assert len(calls) == 1, "a falsy marker must not skip the probe"


@pytest.mark.asyncio
async def test_probe_runs_at_depth_zero(monkeypatch):
    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, _ok_stdout()), [])
    monkeypatch.setenv("OWLEX_COUNCIL_DEPTH", "0")

    ok, _ = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is True
    assert len(calls) == 1


def test_probe_env_carries_the_canonical_council_fence():
    """A nested owlex-server must also refuse council_ask, not just its probe.

    `_validators.council_recursion_block` reads OWLEX_COUNCIL_DEPTH only, so
    OWLEX_IN_PROBE alone would fence the probe but leave council_ask open.
    """
    env = grok_mod._build_probe_env()
    assert env["OWLEX_IN_PROBE"] == "1"
    assert int(env["OWLEX_COUNCIL_DEPTH"]) > 0


# --- TASK-23: MCP-stack isolation ---------------------------------------


def test_probe_env_isolates_home_but_keeps_grok_auth(monkeypatch):
    """HOME must miss ``.claude.json`` while GROK_HOME still finds auth.json."""
    monkeypatch.delenv("GROK_HOME", raising=False)
    env = grok_mod._build_probe_env()

    assert env["HOME"] == str(grok_mod._isolated_grok_home())
    assert env["HOME"] != str(Path.home())
    assert env["GROK_HOME"] == str(Path.home() / ".grok")


def test_probe_env_respects_explicit_grok_home(monkeypatch):
    monkeypatch.setenv("GROK_HOME", "/custom/grok")
    assert grok_mod._build_probe_env()["GROK_HOME"] == "/custom/grok"


@pytest.mark.parametrize("build", ["probe", "seat"])
def test_isolation_redirects_xdg_and_preserves_gitconfig(monkeypatch, build):
    """Both paths must isolate identically.

    XDG_CONFIG_HOME is REDIRECTED, not popped: `AgentCommand.env_overrides` can
    only add keys, so popping would isolate the probe and leave the seat
    resolving the real config. GIT_CONFIG_GLOBAL keeps `~/.gitconfig` reachable
    for the shell commands a seat runs under the empty HOME.
    """
    import dataclasses

    if build == "probe":
        env = grok_mod._build_probe_env()
    else:
        grok_cfg = dataclasses.replace(grok_mod.config.grok, isolate_mcp=True)
        monkeypatch.setattr(
            grok_mod, "config", dataclasses.replace(grok_mod.config, grok=grok_cfg)
        )
        env = grok_mod.GrokRunner().build_exec_command("hi").env_overrides

    assert env["XDG_CONFIG_HOME"].startswith(str(grok_mod._isolated_grok_home()))
    assert env["GIT_CONFIG_GLOBAL"].endswith(".gitconfig")
    assert str(grok_mod._isolated_grok_home()) not in env["GIT_CONFIG_GLOBAL"]


@pytest.mark.asyncio
async def test_probe_spawn_is_isolated_and_own_session(monkeypatch):
    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, _ok_stdout()), [])

    ok, _ = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is True

    kw = calls[0]["kwargs"]
    # Own process group so a timeout can reap grok's children (AC#2).
    assert kw["start_new_session"] is True
    # Empty cwd + empty HOME so grok discovers no MCP servers (AC#3).
    assert kw["cwd"] == str(grok_mod._isolated_grok_home())
    assert kw["env"]["HOME"] == str(grok_mod._isolated_grok_home())
    assert kw["env"]["OWLEX_IN_PROBE"] == "1"


# --- TASK-23: isolation must fail CLOSED --------------------------------


def test_seat_isolation_survives_unmakeable_dir(monkeypatch):
    """A failed mkdir must NOT hand the seat the real HOME.

    Falling back to an un-isolated run silently restored the ~17-server MCP
    stack — the exact bug this module exists to prevent. Grok runs fine with a
    nonexistent HOME (verified live: exit 0), so isolation never depends on the
    directory existing.
    """
    import dataclasses

    grok_cfg = dataclasses.replace(grok_mod.config.grok, isolate_mcp=True)
    monkeypatch.setattr(grok_mod, "config", dataclasses.replace(grok_mod.config, grok=grok_cfg))
    monkeypatch.setattr(grok_mod, "_ensure_isolated_home", lambda: None)

    env = grok_mod.GrokRunner().build_exec_command("hi").env_overrides
    assert env is not None, "isolation must not fail open"
    assert env["HOME"] == str(grok_mod._isolated_grok_home())


@pytest.mark.asyncio
async def test_probe_stays_isolated_when_dir_is_unmakeable(monkeypatch):
    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, _ok_stdout()), [])
    monkeypatch.setattr(grok_mod, "_ensure_isolated_home", lambda: None)

    await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    kw = calls[0]["kwargs"]
    assert kw["cwd"] is None, "unusable dir must not be passed as cwd"
    assert kw["env"]["HOME"] == str(grok_mod._isolated_grok_home())


# --- TASK-23: process-group reap ----------------------------------------


class _Live:
    returncode = None
    pid = 555

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return -9


@pytest.mark.asyncio
async def test_terminate_kills_process_group(monkeypatch):
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(grok_mod.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(grok_mod.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    await grok_mod._terminate(_Live(), pgid=555)

    assert killed, "must signal the process group, not just the leader"
    assert {pgid for pgid, _ in killed} == {555}
    assert killed[0][1] == grok_mod.signal.SIGTERM
    # grok exits promptly on SIGTERM; the members that spin are the whole point,
    # so SIGKILL must reach the group even after the leader is gone.
    assert grok_mod.signal.SIGKILL in [sig for _, sig in killed]


@pytest.mark.asyncio
async def test_terminate_never_signals_own_group(monkeypatch):
    """A pgid equal to owlex-server's own group would kill owlex-server."""
    killed = []
    own = os.getpgid(0)
    monkeypatch.setattr(grok_mod.os, "getpgid", lambda pid: own)
    monkeypatch.setattr(grok_mod.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    await grok_mod._terminate(_Live(), pgid=own)
    assert killed == [], "must fall back to proc.kill() rather than killpg(own_group)"


@pytest.mark.asyncio
async def test_terminate_never_raises(monkeypatch):
    """probe_grok_seat promises "Never raises"; _terminate is on that path."""
    def boom(*a, **k):
        raise PermissionError("nope")

    monkeypatch.setattr(grok_mod.os, "getpgid", boom)
    monkeypatch.setattr(grok_mod.os, "killpg", boom)

    class _Stubborn(_Live):
        def kill(self):
            raise PermissionError("nope")

    await grok_mod._terminate(_Stubborn(), pgid=777)  # must not raise


@pytest.mark.asyncio
async def test_probe_reaps_group_on_cancellation(monkeypatch):
    """Shutdown mid-probe must not leak the tree.

    CancelledError is a BaseException, so it bypasses `except Exception`. With
    start_new_session the tree is in its own session — nothing else would ever
    signal it, so a leak here is permanent.
    """
    reaped: list[int | None] = []

    class _Hang:
        returncode = None
        pid = 909

        async def communicate(self, *a, **k):
            raise asyncio.CancelledError()

        def kill(self):
            pass

        async def wait(self):
            return -9

    _patch_exec(monkeypatch, _Hang, [])
    monkeypatch.setattr(grok_mod.os, "getpgid", lambda pid: 909 if pid else 1)

    async def spy(proc, pgid=None):
        reaped.append(pgid)

    monkeypatch.setattr(grok_mod, "_terminate", spy)

    with pytest.raises(asyncio.CancelledError):
        await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)

    assert reaped == [909], "cancellation must still reap the grok process group"


@pytest.mark.asyncio
async def test_probe_timeout(monkeypatch):
    class _Hang:
        returncode = None
        pid = 424243

        async def communicate(self, *a, **k):
            await asyncio.sleep(1000)
            return b"", b""

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return -9

    _patch_exec(monkeypatch, _Hang, [])
    monkeypatch.setattr(grok_mod.os, "getpgid", lambda pid: 999 if pid else 1)
    monkeypatch.setattr(grok_mod.os, "killpg", lambda *a: None)

    real_wait_for = asyncio.wait_for
    calls = {"n": 0}

    async def fake_wait_for(awaitable, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(grok_mod.asyncio, "wait_for", fake_wait_for)

    ok, msg = await grok_mod.probe_grok_seat(timeout=1.0, use_cache=False)
    assert ok is False
    assert "timed out" in msg.lower()


# --- TASK-23: failures cache with a short TTL ---------------------------


@pytest.mark.asyncio
async def test_auth_failure_caches_with_short_ttl(monkeypatch):
    calls = _patch_exec(
        monkeypatch,
        lambda: _FakeProc(1, b"", b"Error: not signed in. Please login.\n"),
        [],
    )

    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert ok is False
    assert "auth" in msg.lower() or "signed" in msg.lower()

    ok2, msg2 = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert ok2 is False
    assert len(calls) == 1, "a broken seat must not re-probe on every respawn"
    assert "cached" in msg2


def test_failure_stamp_is_invisible_to_a_legacy_reader():
    """Older owlex reads only grok-probe.json and assumes any hit is a success.

    Writing failures into that same file would turn a 10-minute failure into a
    24-hour false [ok] after a downgrade — so failures live in their own file.
    """
    grok_mod._write_probe_cache("broken", ok=False)
    assert grok_mod._probe_fail_path().exists()
    assert not grok_mod._probe_cache_path().exists()


def test_success_clears_a_stale_failure_stamp():
    grok_mod._write_probe_cache("broken", ok=False)
    assert grok_mod._probe_fail_path().exists()

    grok_mod._write_probe_cache("recovered", ok=True)
    assert not grok_mod._probe_fail_path().exists(), "recovery must bust the failure stamp"
    cached = grok_mod._read_probe_cache()
    assert cached is not None and cached[0] is True


@pytest.mark.asyncio
async def test_failure_stamp_expires_far_sooner_than_success(monkeypatch):
    assert grok_mod.PROBE_FAIL_TTL < grok_mod.PROBE_CACHE_TTL

    calls = _patch_exec(monkeypatch, lambda: _FakeProc(1, b"", b"boom\n"), [])
    await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert len(calls) == 1

    path = grok_mod._probe_fail_path()
    data = json.loads(path.read_text())
    data["probed_at"] = time.time() - (grok_mod.PROBE_FAIL_TTL + 10)
    path.write_text(json.dumps(data))

    await grok_mod.probe_grok_seat(timeout=5.0, use_cache=True)
    assert len(calls) == 2, "expired failure stamp must re-probe"


def test_legacy_success_stamp_is_honored():
    """Stamps written before this change are plain success stamps."""
    path = grok_mod._probe_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "model": grok_mod.config.grok.model,
        "probed_at": time.time(),
        "message": "grok seat probed ok",
    }))
    cached = grok_mod._read_probe_cache()
    assert cached is not None
    assert cached[0] is True


def test_ttl_knobs_survive_operator_typos(monkeypatch):
    """These are hand-typed env vars; a bad value used to kill the import.

    `int(os.getenv(...))` raised ValueError at module scope on an empty or
    non-numeric value, taking down owlex.agents and with it the MCP server, and
    silently accepted 0 (which disables caching and restores the probe storm).
    """
    import importlib

    from owlex import config as cfg_mod

    try:
        for bad in ("", "abc", "0", "-5"):
            monkeypatch.setenv("OWLEX_GROK_PROBE_FAIL_TTL", bad)
            monkeypatch.setenv("OWLEX_GROK_PROBE_TTL", bad)
            # config must be reloaded too — the TTLs are GrokConfig fields now,
            # so reloading only grok would re-read an unchanged config object
            # and assert nothing.
            importlib.reload(cfg_mod)
            assert cfg_mod.config.grok.probe_fail_ttl >= 1
            assert cfg_mod.config.grok.probe_ttl >= 1
            reloaded = importlib.reload(grok_mod)
            assert reloaded.PROBE_FAIL_TTL >= 1
            assert reloaded.PROBE_CACHE_TTL >= 1
    finally:
        monkeypatch.delenv("OWLEX_GROK_PROBE_FAIL_TTL", raising=False)
        monkeypatch.delenv("OWLEX_GROK_PROBE_TTL", raising=False)
        importlib.reload(cfg_mod)
        importlib.reload(grok_mod)


# --- TASK-23: council seats must not boot grok's MCP stack either -------


def _seat_cmd(monkeypatch, *, isolate: bool = True):
    import dataclasses

    grok_cfg = dataclasses.replace(grok_mod.config.grok, isolate_mcp=isolate)
    monkeypatch.setattr(grok_mod, "config", dataclasses.replace(grok_mod.config, grok=grok_cfg))
    return grok_mod.GrokRunner()


def test_seat_exec_isolates_home_but_keeps_cwd(monkeypatch):
    cmd = _seat_cmd(monkeypatch).build_exec_command("hello", working_directory="/repo/x")
    env = cmd.env_overrides

    assert env is not None
    assert env["HOME"] == str(grok_mod._isolated_grok_home())
    assert env["GROK_HOME"] == str(Path.home() / ".grok")
    # cwd is untouched: the seat still reads the repo it is asked about.
    assert cmd.cwd == "/repo/x"
    # OWLEX_IN_PROBE is probe-only — a seat legitimately runs at depth 0.
    assert "OWLEX_IN_PROBE" not in env


def test_seat_resume_isolates_home(monkeypatch):
    env = _seat_cmd(monkeypatch).build_resume_command("sess-1", "hi").env_overrides
    assert env is not None
    assert env["HOME"] == str(grok_mod._isolated_grok_home())


def test_seat_isolation_can_be_disabled(monkeypatch):
    assert _seat_cmd(monkeypatch, isolate=False).build_exec_command("hi").env_overrides is None


def test_seat_isolation_is_on_by_default():
    assert grok_mod.config.grok.isolate_mcp is True


def test_isolated_home_is_not_the_real_home():
    assert grok_mod._isolated_grok_home() != Path.home()


# --- round-3 regressions (from the second solution-audit) ----------------


@pytest.mark.asyncio
async def test_terminate_reaps_group_even_when_leader_already_exited(monkeypatch):
    """The primary orphan path: grok exits, its children still hold the pipes.

    `proc.returncode` is published as soon as the LEADER is reaped, while
    `communicate()` blocks until every fd-inheriting descendant closes stdout.
    Bailing out on `returncode is not None` made the group reap a no-op in
    exactly the timeout case it exists for (verified live: leader returncode 0,
    communicate timed out, process group still alive).
    """
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(grok_mod.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(grok_mod.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    class _LeaderDead:
        returncode = 0          # grok itself already reaped
        pid = 777

        def kill(self):
            raise AssertionError("leader is already dead; must not be signalled")

        async def wait(self):
            return 0

    await grok_mod._terminate(_LeaderDead(), pgid=777)

    assert killed, "group must still be signalled after the leader exits"
    assert {p for p, _ in killed} == {777}
    assert grok_mod.signal.SIGKILL in [s for _, s in killed]


@pytest.mark.asyncio
async def test_terminate_delivers_sigkill_even_when_cancelled(monkeypatch):
    """Shutdown can cancel us at the first await; the guaranteed kill must land.

    `except (asyncio.TimeoutError, Exception)` does NOT catch CancelledError, so
    before the `finally` the group could receive only SIGTERM — and with
    start_new_session nothing else would ever signal that session again.
    """
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(grok_mod.os, "getpgid", lambda pid: 4242)
    monkeypatch.setattr(grok_mod.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    class _Hang:
        returncode = None
        pid = 888

        def kill(self):
            self.returncode = -9

        async def wait(self):
            await asyncio.sleep(60)
            return -9

    task = asyncio.ensure_future(grok_mod._terminate(_Hang(), pgid=888))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert grok_mod.signal.SIGTERM in [s for _, s in killed]
    assert grok_mod.signal.SIGKILL in [s for _, s in killed], (
        "the finally must deliver SIGKILL even when cancelled mid-reap"
    )


def test_fresh_failure_outranks_a_stale_success_stamp():
    """Recency, not file precedence.

    A success inside its 24h TTL must not mask a failure recorded minutes ago,
    or a seat that is broken right now reports [ok] for the rest of the day.
    """
    grok_mod._write_stamp(grok_mod._probe_cache_path(), "old success")
    stale = json.loads(grok_mod._probe_cache_path().read_text())
    stale["probed_at"] = time.time() - 3600  # an hour ago, still fresh (24h TTL)
    grok_mod._probe_cache_path().write_text(json.dumps(stale))

    grok_mod._write_stamp(grok_mod._probe_fail_path(), "broken just now")

    cached = grok_mod._read_probe_cache()
    assert cached is not None
    assert cached[0] is False, "the newer failure must win"
    assert "broken just now" in cached[1]


def test_failure_retires_the_success_stamp():
    grok_mod._write_probe_cache("healthy", ok=True)
    assert grok_mod._probe_cache_path().exists()

    grok_mod._write_probe_cache("broken", ok=False)
    assert not grok_mod._probe_cache_path().exists()
    cached = grok_mod._read_probe_cache()
    assert cached is not None and cached[0] is False


def test_discard_stamp_never_raises_on_an_unremovable_path():
    """probe_grok_seat promises "Never raises"; an unguarded unlink broke that.

    Reproduced: a directory where the stamp file belongs makes unlink raise
    PermissionError AFTER the success stamp was written, so a healthy seat got
    logged as [WARN].
    """
    path = grok_mod._probe_fail_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    path.mkdir()
    grok_mod._discard_stamp(path)  # must not raise
    path.rmdir()


def test_transitional_ok_false_stamp_is_retired():
    """A stamp from the interim single-file layout must not read as a success."""
    path = grok_mod._probe_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "model": grok_mod.config.grok.model,
        "probed_at": time.time(),
        "ok": False,
        "message": "auth broken",
    }))
    assert grok_mod._read_stamp(path, grok_mod.PROBE_CACHE_TTL) is None


@pytest.mark.asyncio
async def test_empty_answer_is_a_failure_not_a_24h_success(monkeypatch):
    calls = _patch_exec(monkeypatch, lambda: _FakeProc(0, json.dumps({"text": ""}).encode()), [])

    ok, msg = await grok_mod.probe_grok_seat(timeout=5.0, use_cache=False)
    assert ok is False
    assert "empty output" in msg.lower()
    assert len(calls) == 1
    assert not grok_mod._probe_cache_path().exists(), "a mute model must not stamp 24h ok"
