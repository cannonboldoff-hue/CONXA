"""Invoke a tool on the installed Conxa MCP runtime over stdio JSON-RPC.

Split out of conxa_runtime.py: this half spawns the runtime executable as a
subprocess and drives its stdio MCP protocol. Depends on conxa_runtime.py
for runtime-directory resolution (RuntimeToolError, _runtime_exe); nothing
in conxa_runtime.py calls back into this.

The runtime process is cached and reused across calls (keyed by exe + CONXA_DIR)
instead of being spawned fresh every time — see the module-level `_cache` below.
The runtime app itself is built for a long-lived process (run registry, host
locks, its own browser-instance cache all assume many execute_skill calls over
one process lifetime, exactly how a real MCP client uses it); spawning fresh per
call was paying a ~35s cold-start + MCP handshake penalty on every single Build
Studio "Run Test" click for no reason.
"""

from __future__ import annotations

import atexit
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from conxa_compile.conxa_runtime import RuntimeToolError, _runtime_exe

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

# How long a cached runtime process sits idle (no in-flight calls) before it's torn
# down. Not the runtime's own 90s headless-Chromium cache timeout (runtime/app/
# browser.js's IDLE_MS) — that's a different concern (one cached browser instance,
# and it's skipped entirely for Studio's watch-mode test runs anyway). This covers a
# realistic test -> tweak workflow -> retest cadence without leaving a Chromium-
# holding process alive indefinitely once the user steps away.
_IDLE_TIMEOUT_S = 300


class _PersistentRuntimeProcess:
    """One cached runtime subprocess plus everything needed to multiplex concurrent
    JSON-RPC calls over its single stdio connection (mirrors the pending-request-map
    pattern conxa-builder/electron/bridge.js already uses for the same problem)."""

    def __init__(self, proc: subprocess.Popen, version_tag: str = "") -> None:
        self.proc = proc
        self.stdin_lock = threading.Lock()
        # Guards next_id, pending, stderr_lines, phase_sinks — all the small bits of
        # state the reader threads and callers touch concurrently.
        self.state_lock = threading.Lock()
        self.next_id = 1
        self.pending: dict[int, "queue.Queue[dict]"] = {}
        self.phase_sinks: list = []
        self.stderr_lines: list[str] = []
        self.initialized = False
        self.active_calls = 0
        self.idle_timer: threading.Timer | None = None
        # Snapshot of the sandbox's version.json at spawn time — see
        # _staged_version_tag(). A later mismatch means the sandbox was re-staged
        # (a dev rebuild) since this process started, so it's running stale code
        # even though the exe/sandbox *paths* (the cache key) didn't change.
        self.version_tag = version_tag


_cache: dict[str, _PersistentRuntimeProcess] = {}
_cache_lock = threading.Lock()


def _read_stdout(entry: _PersistentRuntimeProcess) -> None:
    """Dispatch each parsed response line to whichever call is waiting on its id.
    A line with no registered waiter (already timed out, or an id we don't
    recognize) is silently dropped — same as the old single-shot behavior."""
    try:
        assert entry.proc.stdout is not None
        for line in entry.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json_loads(line)
            except ValueError:
                continue
            rid = message.get("id")
            if rid is None:
                continue
            with entry.state_lock:
                q = entry.pending.pop(rid, None)
            if q is not None:
                try:
                    q.put_nowait(message)
                except queue.Full:
                    pass
    except Exception:
        pass


def _read_stderr(entry: _PersistentRuntimeProcess) -> None:
    try:
        assert entry.proc.stderr is not None
        for line in entry.proc.stderr:
            line = _ANSI_RE.sub('', line).strip()
            if not line:
                continue
            with entry.state_lock:
                entry.stderr_lines.append(line)
                del entry.stderr_lines[:-20]
            if '"test_phase"' in line:
                try:
                    parsed = json_loads(line)
                except ValueError:
                    parsed = {}
                if parsed.get("msg") == "test_phase":
                    with entry.state_lock:
                        sinks = list(entry.phase_sinks)
                    for sink in sinks:
                        try:
                            sink(parsed)
                        except Exception:
                            pass
    except Exception:
        pass


def _staged_version_tag(conxa_dir: Path) -> str:
    """Signature of what's currently staged in the sandbox, from the version.json
    conxa_runtime.py::stage_runtime_payload writes on every (re)stage. Used to detect
    a dev rebuild that happened while a cached process was sitting idle — the exe and
    sandbox *paths* (the process cache key) never change across a restage, only their
    contents do, so this is the only signal that a cached process might be stale.
    Returns "" (never raises) when version.json is missing/unreadable — that just
    disables the staleness check rather than breaking the call.
    """
    try:
        data = json_loads((conxa_dir / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return f"{data.get('runtime_version')}|{data.get('app_version')}"


def _spawn(exe: str, runtime_dir: Path, proc_env: dict[str, str], version_tag: str = "") -> _PersistentRuntimeProcess:
    proc = subprocess.Popen(
        [exe],
        cwd=str(runtime_dir),
        env=proc_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
    entry = _PersistentRuntimeProcess(proc, version_tag=version_tag)
    threading.Thread(target=_read_stdout, args=(entry,), daemon=True).start()
    threading.Thread(target=_read_stderr, args=(entry,), daemon=True).start()
    return entry


def _graceful_shutdown(entry: _PersistentRuntimeProcess) -> None:
    """Closing stdin is server.js's cue to run gracefulShutdown() (closes any cached
    Playwright browser before exiting) — give it a moment to do that on its own
    rather than jumping straight to a hard kill, which orphans the browser's child
    processes and can leave them holding a lock on conxa-app/current for the next
    stage_runtime_payload() call (see conxa_runtime.py::_ensure_junction)."""
    proc = entry.proc
    try:
        if proc.stdin:
            proc.stdin.close()
    except OSError:
        pass
    if proc.poll() is None:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    # A persistent process can live for minutes between teardowns (vs. the old
    # single-shot version's near-immediate GC of the same handles) — close stdout/
    # stderr explicitly rather than leaving them for garbage collection.
    for pipe in (proc.stdout, proc.stderr):
        try:
            if pipe:
                pipe.close()
        except OSError:
            pass


def _teardown_if_idle(key: str) -> None:
    """Idle-timer callback. Re-checks under the lock before tearing down — a call
    may have started between the timer being armed and firing."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None or entry.active_calls > 0:
            return
        del _cache[key]
    _graceful_shutdown(entry)


def _cleanup_all() -> None:
    with _cache_lock:
        entries = list(_cache.values())
        _cache.clear()
    for entry in entries:
        if entry.idle_timer is not None:
            entry.idle_timer.cancel()
        _graceful_shutdown(entry)


atexit.register(_cleanup_all)


def _send(entry: _PersistentRuntimeProcess, method: str, params: dict) -> tuple[int, "queue.Queue[dict]"]:
    if entry.proc.stdin is None:
        raise RuntimeToolError("Runtime stdin is not available.")
    q: "queue.Queue[dict]" = queue.Queue(maxsize=1)
    with entry.state_lock:
        req_id = entry.next_id
        entry.next_id += 1
        entry.pending[req_id] = q
    payload = json_dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
    with entry.stdin_lock:
        entry.proc.stdin.write(payload)
        entry.proc.stdin.flush()
    return req_id, q


def _wait_response(entry: _PersistentRuntimeProcess, req_id: int, q: "queue.Queue[dict]", deadline: float) -> dict:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            message = q.get(timeout=min(remaining, 0.5))
        except queue.Empty:
            if entry.proc.poll() is not None:
                break
            continue
        if "error" in message:
            err = message.get("error") or {}
            raise RuntimeToolError(str(err.get("message") or err))
        return message
    with entry.state_lock:
        entry.pending.pop(req_id, None)
    tail = "\n".join(entry.stderr_lines[-5:])
    suffix = f"\nRuntime log tail:\n{tail}" if tail else ""
    raise RuntimeToolError(f"Runtime tool call timed out or exited before responding.{suffix}")


def call_runtime_tool(
    runtime_dir: Path,
    tool_name: str,
    arguments: dict,
    *,
    conxa_dir: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int = 900,
    phase_sink=None,
) -> dict:
    """Call a tool on the local MCP stdio runtime and return its JSON-RPC result.

    The underlying runtime process is cached per (exe, CONXA_DIR) and reused across
    calls — see `_cache` above — so a caller making a single call sees identical
    behavior to before (cold spawn, one `initialize`, one `tools/call`), while a
    caller that calls again shortly after skips straight to `tools/call` on the
    already-running, already-initialized process. An idle process is torn down
    after `_IDLE_TIMEOUT_S` with no in-flight calls; a dead/crashed cached process
    is detected and replaced transparently on the next call.

    ``conxa_dir`` is CONXA_DIR for the spawned process — in Dev this is the same
    locally-built dev-runtime/ folder as ``runtime_dir`` (resolve_dev_runtime_dir());
    in Production it's the customer-faithful staged sandbox.

    ``CONXA_APP_DIR`` IS explicitly injected (derived from ``conxa_dir``), even
    though runtime/env.js would derive the same value on its own when unset. It
    can't be left unset here: when Build Studio itself was launched via
    scripts/conxa.ps1, CONXA_APP_DIR is already present in os.environ (pointing
    at ~/.conxa-dev/conxa-app), and proc_env inherits from os.environ — so an
    omitted override lets that stale value leak through and beat env.js's
    from-CONXA_DIR derivation, sending bootstrap.js looking in the wrong place.

    ``phase_sink``, if given, is called live (from the stderr-reader thread, so
    it must be thread-safe) with each ``test_phase`` diagnostic entry the runtime
    logs via server.js's ``log()`` — host-lock wait, the two sequential Chromium
    launches inside getCachedBrowser, and time-to-first-step. Lets a slow "Run
    Test" click be broken down into real numbers instead of one opaque wait.
    """
    # Both Dev and Production always resolve to a real packed exe now (Dev: built
    # locally by scripts/build-runtime-local.ps1; Production: deps-managed).
    exe: str | None = None
    if conxa_dir is not None:
        _exe = _runtime_exe(conxa_dir)
        if _exe is not None:
            exe = str(_exe)
    if exe is None:
        _exe = _runtime_exe(runtime_dir)
        if _exe is not None:
            exe = str(_exe)
    if exe is None:
        raise RuntimeToolError(f"No packed runtime executable found at {runtime_dir}.")

    effective_conxa_dir = conxa_dir if conxa_dir is not None else runtime_dir
    proc_env = {
        **os.environ,
        **(env or {}),
        "CONXA_DIR": str(effective_conxa_dir),
        "CONXA_APP_DIR": str(effective_conxa_dir / "conxa-app"),
        "CONXA_SKIP_SELF_UPDATE": os.environ.get("CONXA_SKIP_SELF_UPDATE", "1"),
        # Build Studio tests the compiled pack on its deterministic merits: only the
        # zero-token Tier 1 (exception ladder) + Tier 2 (a11y / fallback) cascade. Tiers 3
        # (LLM semantic) and 4 (vision) are agent-mediated and only fire under live Claude/MCP
        # execution — there is no agent in a headless Studio run to act on a recovery request.
        # An explicit caller-supplied value (via env=) still wins.
        "CONXA_MAX_RECOVERY_TIER": (env or {}).get("CONXA_MAX_RECOVERY_TIER")
            or os.environ.get("CONXA_MAX_RECOVERY_TIER", "2"),
        # The runtime's 210 s run cap is sized for Claude Desktop's MCP timeout. Studio is
        # attended and has an immediate Stop, so a long workflow gets 30 min. Explicit value wins.
        "CONXA_EXECUTION_DEADLINE_MS": (env or {}).get("CONXA_EXECUTION_DEADLINE_MS")
            or os.environ.get("CONXA_EXECUTION_DEADLINE_MS", "1800000"),
    }

    key = f"{exe}|{effective_conxa_dir}"
    current_version_tag = _staged_version_tag(effective_conxa_dir)
    deadline = time.monotonic() + timeout_s

    # Get-or-create the cached process AND run its one-time `initialize` handshake
    # (if needed) all under _cache_lock. This means a rare cold spawn (~10s for the
    # handshake alone) blocks any concurrent call to a *different* cache key too —
    # accepted rather than adding per-key locking: Studio only ever has one test
    # sandbox (one key) active in practice, so that cross-key blocking never
    # actually happens.
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and (
            entry.proc.poll() is not None  # Died since last use (crash, external kill).
            # A dev rebuild (build-app-local.ps1) re-staged the sandbox in place since
            # this process was spawned — its exe/sandbox *paths* didn't change, so the
            # cache key alone can't catch this, but it's now running stale code. Only
            # evict for staleness when nothing is still using it — an in-flight call
            # rides out the process it started on; the *next* call reaps it instead.
            or (current_version_tag and entry.version_tag != current_version_tag and entry.active_calls == 0)
        ):
            _cache.pop(key, None)
            if entry.idle_timer is not None:
                entry.idle_timer.cancel()
            stale_entry = entry
            entry = None
        else:
            stale_entry = None
        if entry is None:
            entry = _spawn(exe, runtime_dir, proc_env, version_tag=current_version_tag)
            _cache[key] = entry
            if stale_entry is not None:
                # Shut down outside the lock — _graceful_shutdown can block for
                # seconds and must not hold up other callers.
                threading.Thread(target=_graceful_shutdown, args=(stale_entry,), daemon=True).start()
        elif entry.idle_timer is not None:
            entry.idle_timer.cancel()
            entry.idle_timer = None
        entry.active_calls += 1

        if not entry.initialized:
            try:
                init_id, init_q = _send(
                    entry,
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "conxa-build-studio", "version": "1.0.0"},
                    },
                )
                _wait_response(entry, init_id, init_q, deadline)
                entry.initialized = True
            except Exception:
                # Wedged mid-handshake — drop it so the next call gets a fresh spawn
                # instead of reusing something stuck forever.
                entry.active_calls -= 1
                _cache.pop(key, None)
                _graceful_shutdown(entry)
                raise

    try:
        if phase_sink is not None:
            with entry.state_lock:
                entry.phase_sinks.append(phase_sink)
        try:
            call_id, call_q = _send(entry, "tools/call", {"name": tool_name, "arguments": arguments})
            response = _wait_response(entry, call_id, call_q, deadline)
            return dict(response.get("result") or {})
        finally:
            if phase_sink is not None:
                with entry.state_lock:
                    try:
                        entry.phase_sinks.remove(phase_sink)
                    except ValueError:
                        pass
    finally:
        with _cache_lock:
            entry.active_calls -= 1
            if entry.active_calls <= 0 and _cache.get(key) is entry:
                timer = threading.Timer(_IDLE_TIMEOUT_S, _teardown_if_idle, args=(key,))
                timer.daemon = True
                entry.idle_timer = timer
                timer.start()


def json_dumps(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=True)


def json_loads(value: str) -> dict:
    import json

    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}
