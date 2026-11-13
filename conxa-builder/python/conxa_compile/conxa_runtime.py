"""Locate the installed Conxa shared runtime and stage skill-pack data into it."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable


class RuntimeToolError(RuntimeError):
    """Raised when the local MCP runtime cannot complete a tool call."""


def _runtime_exe(path: Path) -> Path | None:
    """Return the packed runtime executable in ``path``, or None.

    Both the customer installer and the Studio deps bootstrap use ``conxa-runtime.exe``.
    """
    names = ("conxa-runtime.exe",) if sys.platform == "win32" else ("conxa-runtime-mac",)
    for name in names:
        exe = path / name
        if exe.is_file():
            return exe
    return None


def _is_runtime_dir(path: Path) -> bool:
    """A runnable runtime is either a packed exe or a server.js source tree."""
    if _runtime_exe(path) is not None:
        return True
    return (path / "server.js").is_file() and (path / "package.json").is_file()


def _bootstrap_runtime_dir() -> Path | None:
    """Locate the Studio deps-managed runtime (~/.conxa-build-studio/deps/runtime/<version>/).

    Mirrors services.bootstrap._deps_dir(); kept inline so this module stays
    dependency-free. A local dev build (build-runtime-local.ps1, named
    "host-v0.0.0-local.<timestamp>") always wins over a downloaded release — otherwise
    "host-v1.2.3" outranks it lexicographically, shipping code older than the current
    checkout (see FIX.md 2026-07-30, and the analogous fix to _bootstrap_app_dir()).
    Absent a local build, returns the highest-versioned dir that holds a packed exe.
    """
    runtime_root = _studio_base() / "deps" / "conxa-runtime"
    if not runtime_root.is_dir():
        return None
    candidates = [d for d in runtime_root.iterdir() if d.is_dir() and _runtime_exe(d) is not None]
    if not candidates:
        return None

    local_builds = [d for d in candidates if "-local." in d.name]
    if local_builds:
        return max(local_builds, key=lambda d: d.name)

    candidates.sort(key=lambda d: d.name)
    return candidates[-1]


def _bootstrap_app_dir() -> Path | None:
    """Locate the Studio deps-managed app layer (~/.conxa-build-studio/deps/conxa-app/<version>/).

    Mirrors _bootstrap_runtime_dir(). A local dev build (build-app-local.ps1, named
    "app-v0.0.0-local.<timestamp>") always wins over a downloaded release — otherwise
    "app-v1.3.4" outranks it (both lexicographically and via $CONXA_APP_LOCAL_DIR,
    which the deps bootstrap points at whatever it just downloaded), silently shipping
    an installer built from stale code instead of the current checkout. Absent a local
    build, prefers $CONXA_APP_LOCAL_DIR, then the highest-named subdir. Returns None
    when there is no app layer (e.g. a dev checkout with deps never bootstrapped).
    """
    app_root = _studio_base() / "deps" / "conxa-app"
    candidates = (
        [d for d in app_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if app_root.is_dir() else []
    )

    local_builds = [d for d in candidates if "-local." in d.name]
    if local_builds:
        return max(local_builds, key=lambda d: d.name)

    local = os.environ.get("CONXA_APP_LOCAL_DIR", "").strip()
    if local:
        p = Path(local)
        if p.is_dir():
            return p

    if not candidates:
        return None
    candidates.sort(key=lambda d: d.name)
    return candidates[-1]


def _studio_base() -> Path:
    """Root of all Build Studio user state (~/.conxa-build-studio by default).

    ``CONXA_STUDIO_HOME`` takes priority — the dev/prod launcher sets it so the
    two lanes (``~/.conxa-build-studio-dev`` vs ``~/.conxa-build-studio``) keep
    fully separate deps caches, sandboxes, and generated bundles on one machine.
    Mirrors conxa_core.config.state_base_dir()'s precedence.
    """
    base = (
        os.environ.get("CONXA_STUDIO_HOME")
        or os.environ.get("SKILL_DATA_DIR")
        or "~/.conxa-build-studio"
    )
    return Path(os.path.expanduser(base))


def _deps_chromium_dir() -> Path:
    """Managed Playwright browsers directory — mirrors services.bootstrap.chromium_dir()."""
    return _studio_base() / "deps" / "chromium"


def _is_link(path: Path) -> bool:
    """True for a symlink (all platforms) or a Windows directory junction.

    Path.is_symlink() alone misses junctions: mklink /J creates an NTFS reparse point
    that Windows reports as a plain directory, not a symlink, to os.path.islink().

    Tested via the reparse tag rather than os.path.isjunction(), which only exists on
    Python 3.12+. The Studio backend is frozen against 3.11 (see build-studio.yml), where
    a hasattr() guard on isjunction degrades silently to "no junctions exist" — that made
    _ensure_junction skip its repoint branch and the prune loop below delete through
    `current` into whatever version it pointed at.
    """
    if path.is_symlink():
        return True
    try:
        st = os.lstat(path)
        return st.st_reparse_tag in (
            stat.IO_REPARSE_TAG_MOUNT_POINT,
            stat.IO_REPARSE_TAG_SYMLINK,
        )
    except (AttributeError, OSError):
        return False


def _ensure_junction(link_path: Path, target: Path) -> bool:
    """Ensure link_path is a junction (Windows) or symlink (other) pointing to target.

    Returns True when the link exists and is correct or could be created, False on failure.
    Does NOT remove a real directory in case it already contains valid staged content.

    Repointing an existing junction (Windows only) is retried with backoff: a process that
    just had link_path open (e.g. the test runtime, killed via terminate()/kill() in
    runtime_tool.py right before restaging) can hold the directory briefly after exit,
    making `rmdir` fail transiently rather than permanently. runtime_tool.py now gives that
    process a grace period to shut down cleanly first, so this should rarely be needed — this
    loop is defense-in-depth for whatever residual lock (e.g. AV scanning freshly-copied
    files) survives that.
    """
    if _is_link(link_path):
        try:
            if Path(os.readlink(str(link_path))).resolve() == target.resolve():
                return True
        except (OSError, ValueError):
            pass
        # Wrong target — remove and recreate, retrying past a transient Windows file lock.
        backoffs = [0.2, 0.4, 0.8, 1.0] if sys.platform == "win32" else []
        for attempt in range(len(backoffs) + 1):
            try:
                if sys.platform == "win32":
                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(link_path)],
                        check=False, capture_output=True,
                    )
                else:
                    link_path.unlink()
            except OSError:
                pass
            if not _is_link(link_path) and not link_path.exists():
                break
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
        else:
            return False

    elif link_path.exists():
        # Real directory already present (older install or manual copy) — leave it.
        return True

    # Create junction (Windows, no admin required) or symlink (other).
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(target)],
                check=False, capture_output=True, text=True,
            )
            return result.returncode == 0
        else:
            os.symlink(str(target), str(link_path), target_is_directory=True)
            return True
    except Exception:
        return False


def stage_runtime_payload(
    dest: Path,
    runtime_dir: Path,
    app_dir: Path | None,
    log: Callable[[str], None] | None = None,
) -> None:
    """Copy the runtime binary + app layer into dest/, writing a combined version.json.

    Stages: conxa-runtime.exe (or -mac), keytar.node, version.json, conxa-app/.
    Used by both installer_builder (customer .exe) and ensure_test_sandbox (Studio test),
    so both are assembled by identical code and any divergence is a bug.
    """
    def _info(msg: str) -> None:
        if log:
            log(msg)

    # ── runtime binary ─────────────────────────────────────────────────────────
    exe = _runtime_exe(runtime_dir)
    if exe is None:
        raise RuntimeError(
            f"No packed runtime executable found in {runtime_dir}. "
            "Run dependency bootstrap first."
        )
    exe_name = exe.name
    _info(f"Staging {exe_name} from {exe}")
    shutil.copy2(exe, dest / exe_name)
    _info(f"{exe_name} staged ({(dest / exe_name).stat().st_size // 1024} KB)")

    # ── keytar ────────────────────────────────────────────────────────────────
    keytar = runtime_dir / "keytar.node"
    if not keytar.is_file():
        raise RuntimeError(
            f"keytar.node not found in {runtime_dir}. "
            "Run dependency bootstrap first."
        )
    shutil.copy2(keytar, dest / "keytar.node")
    _info("keytar.node staged")

    # ── version.json (records both layers so sandbox can detect updates) ──────
    (dest / "version.json").write_text(
        json.dumps({
            "runtime_version": runtime_dir.name,
            "app_version": app_dir.name if app_dir else None,
        }),
        encoding="utf-8",
    )
    _info("version.json written")

    # ── app layer ─────────────────────────────────────────────────────────────
    # bootstrap.js's version_manager.resolveCurrent() requires conxa-app/current to be
    # a junction pointing at a versioned subdir (conxa-app/<version>/server.js) — the
    # same shape installer_templates/setup.nsi.tmpl produces for a real customer
    # install. A flat copy leaves no `current` junction, so bootstrap.js can't resolve
    # the app layer and exits FATAL. Mirror the NSIS pattern here.
    if app_dir and app_dir.is_dir():
        app_root = dest / "conxa-app"
        app_root.mkdir(parents=True, exist_ok=True)
        version_dest = app_root / app_dir.name
        if version_dest.exists():
            shutil.rmtree(version_dest)
        shutil.copytree(str(app_dir), str(version_dest))
        link = app_root / "current"
        if not _ensure_junction(link, version_dest):
            detail = f"exists={os.path.lexists(link)} is_link={_is_link(link)}"
            if _is_link(link):
                try:
                    detail += f" target={os.readlink(str(link))}"
                except OSError:
                    pass
            raise RuntimeError(
                f"Failed to point {link} at {version_dest} ({detail}). "
                "Remove it manually and retry the test."
            )

        # The sandbox only ever needs one active version — drop any other (mirrors
        # sync_skill_pack's same cleanup for skill versions).
        for entry in app_root.iterdir():
            if entry.is_dir() and entry != version_dest and not _is_link(entry):
                shutil.rmtree(str(entry), ignore_errors=True)

        kb = sum(f.stat().st_size for f in version_dest.rglob("*") if f.is_file()) // 1024
        _info(f"conxa-app/ staged ({kb} KB, version={app_dir.name}, from {app_dir})")
    else:
        _info("WARNING: conxa-app not found in deps — app layer will not be pre-installed")


def resolve_test_sandbox_dir() -> Path:
    """Return the path for the Studio test sandbox (~/.conxa-build-studio/sandbox)."""
    return _studio_base() / "sandbox"


def ensure_test_sandbox(
    runtime_dir: Path,
    app_dir: Path | None,
) -> tuple[Path, Path]:
    """Assemble or refresh the test sandbox — identical code path for Dev and
    Production, deliberately: Dev isolation comes from CONXA_STUDIO_HOME pointing
    at a separate tree (~/.conxa-build-studio-dev), not from branching this
    function. The only difference between the two is WHERE runtime_dir/app_dir's
    content came from — a download (Production) or scripts/build-runtime-local.ps1
    + build-app-local.ps1 writing into deps/ instead of a download landing there
    (Dev) — see resolve_runtime_dir()/_bootstrap_app_dir().

    Returns ``(conxa_dir, data_dir)`` where:
      conxa_dir = sandbox/.conxa/   mirrors the customer's ~/.conxa
      data_dir  = sandbox/data/     mirrors the customer's ~/AppData/Roaming/Conxa

    The sandbox is persistent: payload is re-staged only when runtime_version or
    app_version changes (a new local build, in Dev; a new download, in Production).
    Skill-packs are NOT staged here — callers do that via sync_skill_pack().
    """
    sandbox = resolve_test_sandbox_dir()
    conxa_dir = sandbox / ".conxa"
    data_dir = sandbox / "data"

    conxa_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "cache").mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)

    need_stage = True
    version_file = conxa_dir / "version.json"
    if version_file.is_file() and _runtime_exe(conxa_dir) is not None:
        try:
            meta = json.loads(version_file.read_text(encoding="utf-8"))
            # version.json is written before the app-layer copy completes (see
            # stage_runtime_payload), so a matching label alone doesn't prove the
            # app layer is actually there — an interrupted or overlapping stage can
            # leave it stale. Confirm the file bootstrap.js actually loads exists too,
            # so a broken sandbox self-heals on the next run instead of wedging.
            app_layer_ok = (
                app_dir is None
                or (conxa_dir / "conxa-app" / "current" / "server.js").is_file()
            )
            if (
                meta.get("runtime_version") == runtime_dir.name
                and meta.get("app_version") == (app_dir.name if app_dir else None)
                and app_layer_ok
            ):
                need_stage = False
        except Exception:
            pass
    if need_stage:
        stage_runtime_payload(conxa_dir, runtime_dir, app_dir)

    # ── chromium: junction/symlink → deps/chromium (no per-test copy) ────────
    chromium_source = _deps_chromium_dir()
    _ensure_junction(conxa_dir / "chromium", chromium_source)

    return conxa_dir, data_dir


def stale_app_layer_files(conxa_dir: Path) -> list[str]:
    """Dev only: the app-layer files edited in this repo's runtime/app/ AFTER the staged app layer was built.

    The sandbox is re-staged only when the version LABEL changes, so a source edit made after the last
    scripts/build-app-local.ps1 run is silently not what a Studio test executes. The staged files are minified, so
    they cannot be compared byte for byte; an edit newer than the build is the signal.
    [] when the source tree is not here (an installed Studio) or nothing is newer."""
    try:
        repo = next((p for p in Path(__file__).resolve().parents if (p / "runtime" / "app-layer-files.json").is_file()), None)
        if repo is None:
            return []
        staged = conxa_dir / "conxa-app" / "current"
        if not (staged / "server.js").is_file():  # `current` is a link; where it cannot be followed, use the newest version folder
            versions = sorted((d for d in (conxa_dir / "conxa-app").glob("app-*") if (d / "server.js").is_file()), key=lambda d: d.stat().st_mtime)
            if not versions:
                return []
            staged = versions[-1]
        names = [f["name"] for f in json.loads((repo / "runtime" / "app-layer-files.json").read_text(encoding="utf-8")).get("files", [])
                 if isinstance(f, dict) and isinstance(f.get("name"), str)]
        built_at = (staged / "server.js").stat().st_mtime
        out = []
        for name in names:
            src = repo / "runtime" / "app" / name
            if src.is_file() and src.stat().st_mtime > built_at + 2:  # 2 s: a build copies files a moment apart
                out.append(name)
        return out
    except Exception:  # noqa: BLE001 -- advisory only
        return []


def resolve_runtime_dir() -> Path | None:
    """Find a runnable Conxa runtime directory (packed exe).

    Identical for Dev and Production, deliberately — this is a mirror of the real
    pipeline, not a parallel one:
      1. $CONXA_RUNTIME_LOCAL_DIR — explicit override, set by the deps bootstrap
         after a download (services.bootstrap._configure_dep_env).
      2. The highest-versioned dir under
         <CONXA_STUDIO_HOME>/deps/conxa-runtime/. In Production this is populated
         by a real download; in Dev, scripts/build-runtime-local.ps1 writes
         straight into this same location instead — same shape, same lookup, same
         code path. Dev/Production isolation comes entirely from CONXA_STUDIO_HOME
         pointing at separate trees (~/.conxa-build-studio-dev vs
         ~/.conxa-build-studio), not from any branching here.

    Returns None if no valid runtime is found.
    """
    local_dir = os.environ.get("CONXA_RUNTIME_LOCAL_DIR", "").strip()
    if local_dir:
        p = Path(local_dir)
        if _is_runtime_dir(p):
            return p

    return _bootstrap_runtime_dir()


def resolve_conxa_data_dir() -> Path:
    """Resolve CONXA_DATA_DIR (user-writable; mirrors runtime/server.js logic)."""
    env_dir = os.environ.get("CONXA_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Roaming" / "Conxa"
    return Path.home() / ".conxa"


def sync_skill_pack(
    company: str,
    source_dir: Path,
    runtime_dir: Path,
    *,
    data_dir: Path | None = None,
) -> None:
    """Copy source_dir → <runtime_dir>/skill-packs/<company>/, then bust the manifest cache.

    Each skill is written straight into a <group_id>/<slug>/v<version>/ directory with a
    `current` junction pointing at it: runtime/skill_loader.js only ever reads
    <group_id>/<slug>/current/manifest.json, and that junction is normally created by
    runtime/sync.js after a real cloud delta sync. A locally built, unpublished (or
    rebuilt-since-published) skill pack never carries a sync_token, so that sync always
    no-ops (runtime/sync.js:77-81) — reproduce the same end state locally here, offline,
    so a freshly built skill is immediately visible to the local test run.

    The runtime caches skill index in CONXA_DATA_DIR/cache/manifests.json for fast startup.
    Deleting that file forces a fresh filesystem scan so the newly synced skill is visible.

    No-op if source_dir doesn't exist.
    """
    if not source_dir.is_dir():
        return

    dest = runtime_dir / "skill-packs" / company
    dest.mkdir(parents=True, exist_ok=True)

    pack_src = source_dir / "pack.json"
    if not pack_src.is_file():
        return
    shutil.copy2(str(pack_src), str(dest / "pack.json"))
    try:
        pack = json.loads(pack_src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Corrupt pack.json at {pack_src} — cannot stage any skills: {exc}") from exc

    skill_groups = pack.get("skill_groups") or {}
    for slug in pack.get("skills") or []:
        # Build output nests each skill under its group_id (skill_package_builder_output.py),
        # and skill_loader.js reads it back the same way (_skillCurrentDir) — mirror that here.
        group = skill_groups.get(slug) or "_default"
        src_slug_dir = source_dir / group / slug
        manifest_src = src_slug_dir / "manifest.json"
        if not manifest_src.is_file():
            continue
        try:
            manifest = json.loads(manifest_src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Corrupt manifest.json for skill {slug!r} at {manifest_src} — "
                f"cannot stage it into the runtime sandbox: {exc}"
            ) from exc

        version = str(manifest.get("version") or "0.0.0")
        slug_dir = dest / group / slug
        version_dir = slug_dir / f"v{version}"

        # The local sandbox only ever needs one active version — drop any other.
        if slug_dir.is_dir():
            for entry in slug_dir.iterdir():
                if entry.is_dir() and entry.name.startswith("v") and entry != version_dir:
                    shutil.rmtree(str(entry), ignore_errors=True)

        if version_dir.exists():
            shutil.rmtree(str(version_dir))
        shutil.copytree(str(src_slug_dir), str(version_dir))
        _ensure_junction(slug_dir / "current", version_dir)

    # Bust the skill manifest cache so the spawned runtime rescans from disk
    cache_file = (data_dir or resolve_conxa_data_dir()) / "cache" / "manifests.json"
    if cache_file.is_file():
        try:
            cache_file.unlink()
        except OSError:
            pass


def _find_local_runtime_source() -> Path | None:
    """Return the repo-local runtime/ source tree when running from a dev checkout.

    The packed exe (conxa-runtime.exe) has no package.json. When a local source tree
    is found, it supplies the correct package.json so node_modules resolves Playwright 1.59.0
    (chromium-1217). Without it, the install command pins the version explicitly via npx playwright@1.59.0.
    """
    # conxa_runtime.py lives at conxa-builder/python/conxa_compile/conxa_runtime.py
    # The repo-local runtime source is three parents up, then "runtime/".
    candidate = Path(__file__).parents[3] / "runtime"
    if (candidate / "server.js").is_file() and (candidate / "package.json").is_file():
        return candidate
    return None


def _chromium_exe_in_browsers_dir(browsers_dir: Path) -> Path | None:
    """Return the Chromium executable if already installed in browsers_dir, else None.

    Playwright stores browsers as: browsers_dir/chromium-REVISION/<platform-dir>/<exe>.
    Checking for the binary directly avoids the multi-second npx startup cost on
    every test run when nothing needs to be downloaded.
    """
    if not browsers_dir.is_dir():
        return None
    if sys.platform == "win32":
        patterns = ["chromium-*/chrome-win64/chrome.exe", "chromium-*/chrome-win/chrome.exe"]
    elif sys.platform == "darwin":
        patterns = [
            "chromium-*/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
            "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:
        patterns = ["chromium-*/chrome-linux/chrome"]
    for pattern in patterns:
        hits = sorted(browsers_dir.glob(pattern), reverse=True)
        if hits:
            return hits[0]
    return None


def ensure_chromium_installed(
    browsers_dir: Path,
    runtime_dir: Path,
    log_sink=None,
) -> None:
    """Install Playwright Chromium into browsers_dir using the correct Playwright version.

    When runtime_dir is a packed-exe directory (no package.json), the install runs
    from the repo-local runtime/ source tree so that the Playwright version in
    node_modules — not the global npx — determines which chromium revision to fetch.

    Skips the npx call entirely when the Chromium binary is already present —
    this avoids the multi-second Node/npx startup overhead on every test run.
    """
    import shutil as _shutil

    browsers_dir.mkdir(parents=True, exist_ok=True)

    if _chromium_exe_in_browsers_dir(browsers_dir) is not None:
        return

    node = _shutil.which("node")
    npx = _shutil.which("npx")
    if not npx or not node:
        raise RuntimeError("Node.js / npx not found. Install Node.js to continue.")

    # Prefer the repo-local runtime/ source (correct Playwright version in node_modules)
    # over the packed-exe directory, which has no package.json and causes npx to fall
    # back to whatever Playwright version is installed globally — potentially a
    # different chromium revision than what the packed runtime expects.
    install_dir = runtime_dir
    if not (runtime_dir / "package.json").is_file():
        local_src = _find_local_runtime_source()
        if local_src is not None:
            install_dir = local_src

    # Pin the version when no package.json is available so we always get chromium-1217
    if (install_dir / "package.json").is_file():
        pw_cmd = [npx, "playwright", "install", "chromium"]
    else:
        pw_cmd = [npx, "playwright@1.59.0", "install", "chromium"]

    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir)}

    if log_sink:
        log_sink("Installing Playwright Chromium for the test runtime…")

    proc = subprocess.Popen(
        pw_cmd,
        cwd=str(install_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    assert proc.stdout is not None
    output_lines: list[str] = []
    for raw_line in proc.stdout:
        line = raw_line.strip()
        if not line:
            continue
        output_lines.append(line)
        if log_sink:
            log_sink(line)
    returncode = proc.wait()
    if returncode != 0:
        tail = "\n".join(output_lines[-10:])
        raise RuntimeError(f"Playwright install failed:\n{tail}")


