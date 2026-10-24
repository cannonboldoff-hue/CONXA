"""Build Studio Python backend — stdio JSON-RPC dispatcher.

Electron spawns this process and talks to it over stdin/stdout. The protocol is
newline-delimited JSON:

  request  (stdin) : {"id": "<uuid>", "type": "<command>", "payload": {...}}
  result   (stdout): {"id": "<uuid>", "type": "result", "result": {...}}
  error    (stdout): {"id": "<uuid>", "type": "error", "code": "...", "message": "..."}
  event    (stdout): {"type": "event", "id": "<uuid>"|null, ...}   (streaming progress)

The shared ``app/*`` package is used unchanged as a library; compile-time LLM
calls are redirected to the cloud proxy by swapping the router singleton.
Recording runs on a persistent asyncio loop in a background thread because the
Playwright recorder is async and long-lived.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
import re

import greenlet
from pathlib import Path
from urllib.parse import urlencode, urlparse
from typing import Any, Callable

# Make this `python` dir importable (for the local `services` package and the
# bundled `conxa_compile` pipeline), regardless of launch CWD. The shared
# `conxa_core` package is installed as a dependency, not imported by path.
_PY_DIR = os.path.abspath(os.path.dirname(__file__))
if _PY_DIR not in sys.path:
    sys.path.insert(0, _PY_DIR)

from services import bootstrap as _bootstrap_pkg  # noqa: E402

# Point Playwright at the managed Chromium build before the recorder is imported
# or used. In frozen builds the browser lives in ~/.conxa-build-studio/deps/chromium;
# without this, launches on a non-bootstrap startup fail with "Executable doesn't exist".
_bootstrap_pkg.configure_playwright_browsers_path()

# Pre-import the recorder, workflow store, and command handlers at startup (main
# thread, before serve() starts blocking on stdin). Importing these lazily in a
# dispatch thread causes a deadlock: two simultaneous record clicks hit Python's
# per-module import lock while conxa_core.config.Settings() tries to read the
# repo .env from a piped-stdin context. The handlers.* imports below transitively
# import conxa_compile.recorder.session and conxa_core.storage.workflow_store, so
# this ordering also pre-warms those modules.
from handlers.protocol import _CommandError, _write  # noqa: E402
from handlers.session import SessionMixin  # noqa: E402
from handlers.compile import CompileMixin  # noqa: E402
from handlers.workflows import WorkflowsMixin  # noqa: E402
from handlers.groups import GroupsMixin  # noqa: E402
from handlers.workflow_editor import WorkflowEditorMixin  # noqa: E402
from handlers.visual import VisualMixin  # noqa: E402
from handlers.skill_packages import SkillPackagesMixin  # noqa: E402
from handlers.runs import RunsMixin  # noqa: E402
from handlers.legal import LegalMixin  # noqa: E402
from handlers.copilot import CopilotMixin  # noqa: E402
from handlers.conversation import ConversationMixin  # noqa: E402
from handlers.workflow_flow import WorkflowFlowMixin  # noqa: E402
from handlers.compile_flow import CompileFlowMixin  # noqa: E402
from handlers.test_flow import TestFlowMixin  # noqa: E402
from handlers.app_graph import AppGraphMixin  # noqa: E402
from conxa_core.progress import set_event_sink  # noqa: E402

# Compile-time sub-step logging (conxa_compile/compiler/build.py's
# _compile_log calls) routes through conxa_core.progress.append_current_job_event,
# which is a no-op unless a sink is registered. Wiring it here — once, at
# module scope — turns those existing per-step logs ("Compiling step N.",
# "Generating vision anchors for step N.", ...) into live compile_log IPC
# events instead of being silently dropped. Stateless and routed purely by
# the job_id argument, so it's safe across the per-request dispatch threads
# spawned below.
def _progress_event_sink(job_id: str, event: str, message: str, data: dict[str, Any] | None) -> None:
    data = data or {}
    payload: dict[str, Any] = {
        "type": "event",
        "id": job_id,
        "phase": "compile_log",
        "message": message,
        "level": data.get("level", "info"),
        "ts": time.time(),
    }
    # step_index/step_count ride along (when the underlying _compile_log call
    # carries them) so the renderer's compile ETA can track progress *within*
    # a long-running pipeline step (e.g. vision-anchor generation across all
    # recorded events), not just which of the 7 coarse pipeline steps is
    # active. Without this every one of those per-event calls collapsed into
    # a single "selectors" step, so elapsed time kept climbing against a
    # doneCount that never moved and the ETA counted up instead of down.
    if "step_index" in data:
        payload["step_index"] = data["step_index"]
    if "step_count" in data:
        payload["step_count"] = data["step_count"]
    _write(payload)
    from handlers.protocol import notify_observer  # noqa: PLC0415

    notify_observer(job_id, payload)


set_event_sink(_progress_event_sink)

# App maps follow every skill write (compile, editor edit, undo) and delete.
from conxa_compile.app_graph import install_hooks as _install_app_graph_hooks  # noqa: E402

_install_app_graph_hooks()

# --- background asyncio loop for the recorder --------------------------------

class _Loop:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.loop.set_exception_handler(self._on_exception)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _on_exception(self, loop: asyncio.AbstractEventLoop, context: dict) -> None:
        # Playwright's sync driver can only be touched from the thread that started
        # it. On process shutdown (Ctrl+C, force-kill) a RecordingSession's own
        # thread can be torn down mid-callback, and its greenlet-based sync_api
        # raises this from asyncio's callback machinery — benign shutdown noise,
        # not a real fault. Anything else still goes to the default handler.
        exc = context.get("exception")
        if isinstance(exc, greenlet.error) and "different thread" in str(exc):
            return
        loop.default_exception_handler(context)

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()


# --- the backend -------------------------------------------------------------

class Backend(
    SessionMixin,
    CompileMixin,
    WorkflowsMixin,
    GroupsMixin,
    WorkflowEditorMixin,
    VisualMixin,
    SkillPackagesMixin,
    RunsMixin,
    LegalMixin,
    CopilotMixin,
    ConversationMixin,
    WorkflowFlowMixin,
    CompileFlowMixin,
    TestFlowMixin,
    AppGraphMixin,
):
    """JSON-RPC dispatcher. Command handlers (cmd_*) live in the handlers/
    package, grouped by domain and mixed in here; this class owns shared
    instance state (undo/redo stacks, auth, cloud API helpers) and dispatch."""

    _MAX_UNDO = 50

    def __init__(self) -> None:
        self._loop = _Loop()
        self._active_recording: str | None = None
        self._rec_lock = threading.Lock()
        self._auth = None  # AuthService, lazily built once configured
        self._cloud_api = os.environ.get("CONXA_CLOUD_API", "http://127.0.0.1:8000")
        self._undo_stacks: dict[str, list] = {}
        self._redo_stacks: dict[str, list] = {}
        self._installer_generation_cache: str | None = None
        self._synced_company_slug: str | None = None

    # -- undo / redo helpers -------------------------------------------------

    def _push_undo(self, skill_id: str, snapshot: dict[str, Any]) -> None:
        """Push a pre-mutation snapshot and clear redo. Caller must pass a safe copy."""
        stack = self._undo_stacks.setdefault(skill_id, [])
        stack.append(snapshot)
        if len(stack) > self._MAX_UNDO:
            stack.pop(0)
        self._redo_stacks[skill_id] = []

    def _history_flags(self, skill_id: str) -> dict[str, bool]:
        return {
            "can_undo": len(self._undo_stacks.get(skill_id, [])) > 0,
            "can_redo": len(self._redo_stacks.get(skill_id, [])) > 0,
        }

    # -- lazy auth wiring ----------------------------------------------------

    def _auth_service(self):
        if self._auth is None:
            from services.auth_service import AuthService

            self._auth = AuthService(
                clerk_domain=os.environ.get("CONXA_CLERK_DOMAIN", ""),
                client_id=os.environ.get("CONXA_CLERK_CLIENT_ID", ""),
                client_secret=os.environ.get("CONXA_CLERK_CLIENT_SECRET", ""),
                cloud_api=self._cloud_api,
            )
        return self._auth

    def _install_proxy_router(
        self,
        sink: Callable[[dict[str, Any]], None] | None = None,
        *,
        usage_class: str = "compile",
    ) -> None:
        """Redirect every compiler LLM call to the metered cloud proxy."""
        from services.llm_proxy_client import LLMProxyClient
        from conxa_core import llm as core_llm

        def _on_api_call(info: dict[str, Any]) -> None:
            if sink is not None:
                sink({"phase": "api_call", **info})

        client = LLMProxyClient(
            self._cloud_api,
            token_provider=lambda: self._auth_service().get_token(),
            client_header=os.environ.get("CONXA_PROXY_CLIENT", "build-studio"),
            usage_class=usage_class,
            on_api_call=_on_api_call,
        )
        core_llm.set_router(client)
        # Also scope it to this request's thread: each RPC runs on its own thread, so concurrent
        # commands (a compile and a chat turn) each keep their own usage class and event sink.
        # serve() runs every request on its own short-lived thread, so the scope dies with the
        # request. A handler called directly on the main thread (tests, scripts) would leave it
        # set for whoever runs next, so it only scopes on a worker thread.
        if threading.current_thread() is not threading.main_thread():
            core_llm.set_scoped_router(client)

    def _apply_vision_fallback_entitlement(self) -> None:
        """Fetch this workspace's vision_fallback_on_exhaustion capability and
        apply it as the local process-global setting for the scope of this
        compile — mirroring _install_proxy_router's existing per-compile setup
        pattern. The compiler itself is local-only (see build.py's own comment
        on this); this is only the cloud-controlled policy VALUE for it, fetched
        once per compile rather than requiring a Windows environment variable on
        every Build Studio install.

        Best-effort: on any failure (cloud unreachable, entitlements service
        down) this leaves conxa_core.config.settings.vision_anchor_fallback_on_exhaustion
        untouched — whatever the local env var / default already set it to —
        rather than blocking or failing the compile over one policy lookup.
        """
        from conxa_core.config import settings as core_settings

        try:
            entitlements = self._cloud_json("/api/v1/entitlements/current")
            capabilities = entitlements.get("capabilities") or {}
            value = capabilities.get("vision_fallback_on_exhaustion")
            if isinstance(value, bool):
                core_settings.vision_anchor_fallback_on_exhaustion = value
        except Exception:
            pass

    def _cloud_api_base(self) -> str:
        return (self._cloud_api or "https://apis.conxa.in").rstrip("/")

    def _auto_publish_enabled(self) -> bool:
        if os.environ.get("CONXA_DISABLE_AUTO_PUBLISH") == "1":
            return False
        parsed = urlparse(self._cloud_api_base())
        return parsed.hostname not in {"127.0.0.1", "localhost", ""}

    def _installer_generation(self) -> str:
        """The Conxa-owned installer platform generation ("v1"/"v2"/...) that
        Build Studio should stamp into new skill-pack publishes and installer
        builds. Cached for the process lifetime. Never blocks the build —
        falls back to "v2" if the cloud is unreachable."""
        if self._installer_generation_cache is not None:
            return self._installer_generation_cache
        try:
            payload = self._cloud_json("/api/v1/workflows/generations")
            generation = str(payload.get("current") or "").strip() or "v2"
        except Exception:
            generation = "v2"
        self._installer_generation_cache = generation
        return generation

    def _cloud_token(self) -> str:
        try:
            token = self._auth_service().get_token()
        except Exception as exc:
            raise _CommandError(
                "cloud_auth_required",
                "Sign in to Conxa Build Studio and try again.",
            ) from exc
        if not token:
            raise _CommandError(
                "cloud_auth_required",
                "Sign in to Conxa Build Studio and try again.",
            )
        return token

    def _cloud_json(self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
        import urllib.request

        from services.machine_id import get_machine_id_hash

        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{self._cloud_api_base()}{path}", data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self._cloud_token()}")
        machine_hash = get_machine_id_hash()
        if machine_hash:
            req.add_header("X-Conxa-Machine", machine_hash)
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
                detail = str(error_payload.get("detail") or "")
            except Exception:
                detail = ""
            if detail:
                raise _CommandError(detail, self._entitlement_error_message(detail)) from exc
            raise _CommandError("cloud_unreachable", f"Conxa Cloud returned HTTP {exc.code}.") from exc
        except Exception as exc:
            raise _CommandError("cloud_unreachable", f"Conxa Cloud is unreachable: {exc}") from exc
        return payload if isinstance(payload, dict) else {}

    def _entitlement_error_message(self, code: str) -> str:
        messages = {
            "compile_credit_limit_exceeded": "Monthly compile credits are exhausted for this workspace.",
            "human_edit_pool_exceeded": "Monthly AI credits are exhausted for this workspace.",
            "ai_credits_exceeded": "Monthly AI credits are exhausted for this workspace.",
            "seat_limit_exceeded": "Seat limit reached for this workspace.",
            "machine_limit_exceeded": "This workspace's plan is limited to fewer build machines than are currently registered.",
            "execute_seat_limit_exceeded": "This workspace's Conxa Execute seat limit has been reached.",
            "trial_expired": "The 30-day free trial has ended. Upgrade to keep building.",
            "distribution_not_permitted": "This plan can only build installers for internal use. Upgrade to Pro to distribute to customers.",
            "white_label_not_permitted": "White-label installer branding requires the Enterprise plan.",
            "entitlements_unavailable": "Cloud entitlements are unavailable, so quota-gated actions are blocked.",
            "invalid_usage_class": "Invalid LLM usage class.",
            "legal_version_mismatch": "The Terms and Privacy Policy changed while you were reading them. Reopen Build Studio to see the current version.",
            "legal_acceptance_required": "Accept the Conxa Terms and Privacy Policy to continue.",
        }
        return messages.get(code, code)

    def _compile_reservation_id(self, rid: str, workflow_id: str, session_id: str) -> str:
        raw = f"cmp_{rid}_{workflow_id}_{session_id}"
        return re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)[:240]

    def _reserve_compile_credit(
        self,
        *,
        reservation_id: str,
        workflow_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        return self._cloud_json(
            "/api/v1/usage/compile/reserve",
            method="POST",
            body={
                "reservation_id": reservation_id,
                "workflow_id": workflow_id,
                "session_id": session_id,
            },
        )

    def _commit_compile_credit(self, reservation_id: str) -> dict[str, Any]:
        return self._cloud_json(
            "/api/v1/usage/compile/commit",
            method="POST",
            body={"reservation_id": reservation_id},
        )

    def _release_compile_credit(self, reservation_id: str) -> None:
        try:
            self._cloud_json(
                "/api/v1/usage/compile/release",
                method="POST",
                body={"reservation_id": reservation_id},
            )
        except Exception:
            pass

    def _refund_compile_credit(self, reservation_id: str) -> None:
        """Refund a *committed* reservation — used only when a compile aborts
        on an infrastructure failure after the credit was already spent (see
        handlers/compile.py). Best-effort like _release_compile_credit: a
        failed refund call shouldn't turn one broken thing into two."""
        try:
            self._cloud_json(
                "/api/v1/usage/compile/refund",
                method="POST",
                body={"reservation_id": reservation_id},
            )
        except Exception:
            pass

    def _read_pack_json(self, company_slug: str) -> tuple[Path, dict[str, Any]]:
        """Locate + parse the already-built pack.json for a company slug.
        Read-only — callers that need to mutate it write it back themselves."""
        from conxa_core.config import settings as _settings

        packs_dir = Path(_settings.data_dir) / "skill-packs" / company_slug
        pack_path = packs_dir / "pack.json"
        if not pack_path.is_file():
            raise _CommandError("pack_not_built", f"No built skill pack for {company_slug}")
        return pack_path, json.loads(pack_path.read_text(encoding="utf-8"))

    def _skill_group_id(self, company_slug: str, skill_slug: str) -> str:
        """This skill's group folder name, as already written into the local
        pack.json mirror by ``_write_skill_packs_format`` (falls back to
        "_default", matching that function's own fallback)."""
        _pack_path, pack = self._read_pack_json(company_slug)
        return str((pack.get("skill_groups") or {}).get(skill_slug) or "_default")

    def _collect_skill_pack_files(self, company_slug: str, skill_slug: str) -> list[dict[str, str]]:
        """Every file under ONE skill's own directory (``{group_id}/{skill_slug}/``
        under the built pack's root — see skill_package_builder_output.py's
        ``_write_skill_packs_format``), base64-encoded the same way a publish
        upload encodes them. Paths stay relative to the pack root (not the skill
        directory) so they round-trip unchanged through the cloud's mutable
        mirror and delta-sync, which both key on ``{group_id}/{skill_slug}/...``.

        Shared by ``_publish_skill_pack`` (the real upload) and
        ``cmd_release_preview`` (a read-only dry run against the exact same
        bytes, so the preview never drifts from what publish sends). Never
        walks the whole company directory — that would upload every other
        skill's files on every single-skill publish."""
        from conxa_core.config import settings as _settings
        import base64

        packs_dir = Path(_settings.data_dir) / "skill-packs" / company_slug
        group_id = self._skill_group_id(company_slug, skill_slug)
        skill_dir = packs_dir / group_id / skill_slug
        files: list[dict[str, str]] = []
        for fpath in sorted(skill_dir.rglob("*")):
            if fpath.is_file():
                files.append(
                    {
                        "path": fpath.relative_to(packs_dir).as_posix(),
                        "content_base64": base64.b64encode(fpath.read_bytes()).decode("ascii"),
                    }
                )
        return files

    def _publish_skill_pack(
        self,
        *,
        company_slug: str,
        company_name: str,
        skill_slug: str,
        version: str,
        release_notes: str,
        group_id: str,
        group_name: str = "",
        workflow_name: str = "",
        tests_passed: bool = False,
        recording_duration_seconds: float | None = None,
        sink: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        """Publish ONE skill's built files and rewrite local pack.json with cloud
        tracking. Never uploads a sibling skill's files — see
        ``_collect_skill_pack_files``.

        This only ever gets the version to "ready" in Conxa Cloud — it does
        NOT deploy it. A Cloud admin's explicit Release/Deploy action is what
        moves the stable channel and makes runtimes start receiving it; see
        docs/App-Flow.md.

        Mandatory operation: any real-cloud failure raises _CommandError (see
        cmd_publish_skill_pack). Only a local dev cloud that's simply unreachable
        is swallowed — see _auto_publish_enabled.
        """
        import urllib.request

        pack_path, pack = self._read_pack_json(company_slug)
        # Display-only, best-effort: each skill has its own independent version
        # now (see the cloud's per-skill version history), so this local field
        # just reflects whichever skill was most recently published — it's read
        # only as installer_builder.py's last-resort fallback when no explicit
        # version is passed to Build Installer.
        pack["skill_pack_version"] = version
        pack["release_notes"] = release_notes
        pack_path.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")

        # Collected AFTER the write above so pack.json's own uploaded bytes (if
        # ever included) would carry this release's release_notes. Deliberately
        # uses the on-disk snapshot's own group id to find the built files —
        # that's wherever the last build actually wrote them — which can lag
        # behind `group_id` (the workflow's *current* group, passed in by the
        # caller) if the workflow was reassigned to a different group after
        # its last build. The two are independent on purpose: reporting the
        # live group_id to Cloud is what keeps Skill Packages from splitting
        # one group into two when that happens; the disk lookup must still use
        # the snapshot's id or it won't find the files at all.
        files = self._collect_skill_pack_files(company_slug, skill_slug)

        cloud_api = self._cloud_api_base()
        generation = self._installer_generation()
        body = json.dumps(
            {
                "skill_slug": skill_slug,
                "group_id": group_id,
                "group_name": group_name,
                "workflow_name": workflow_name,
                "display_name": company_name or company_slug,
                "target_url": str(pack.get("target_url") or ""),
                "protected_url": str(pack.get("protected_url") or ""),
                "skill_pack_version": version,
                "release_notes": release_notes,
                "tests_passed": tests_passed,
                "recording_duration_seconds": recording_duration_seconds,
                "files": files,
            }
        ).encode("utf-8")
        sink({"kind": "skill_pack_publish", "stage": "validated", "message": f"Validated {len(files)} files for {skill_slug}."})
        sink({"kind": "skill_pack_publish", "stage": "uploading", "message": f"Publishing {skill_slug} to Conxa Cloud..."})
        try:
            req = urllib.request.Request(
                f"{cloud_api}/api/v1/workflows/{generation}/skill-packs/upload",
                data=body,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            # _cloud_token() also belongs inside this try: not being signed in is just as
            # much a reason a local dev cloud attempt can't proceed as it being unreachable.
            req.add_header("Authorization", f"Bearer {self._cloud_token()}")
            with urllib.request.urlopen(req, timeout=120) as resp:
                published = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The cloud responded — it's reachable but rejected the request (bad auth,
            # bad payload, duplicate version, unchanged artifact, etc). Always a real
            # failure, local cloud or not — the stable channel was never touched (see
            # publish_routes._publish_skill_pack_impl's write ordering).
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            if exc.code == 409 and "skill_pack_artifact_unchanged" in body_text:
                raise _CommandError(
                    "skill_pack_artifact_unchanged",
                    f"Nothing changed since the current stable release of {company_slug} — "
                    "this exact skill pack is already published. Make a change before republishing.",
                ) from exc
            if exc.code == 409:
                raise _CommandError(
                    "skill_pack_version_exists",
                    f"Skill pack version {version} already exists in Conxa Cloud. Bump the version and republish.",
                ) from exc
            sink({
                "kind": "skill_pack_publish",
                "stage": "failed",
                "message": f"Cloud publish failed — Conxa Cloud responded {exc.code}: {body_text or exc}",
            })
            raise _CommandError("cloud_publish_failed", f"Cloud publish failed: {exc} — {body_text}") from exc
        except Exception as exc:
            # Nothing responded at all (connection refused, DNS failure, timeout, or not
            # signed in). For a local API base this just means there's no usable dev cloud
            # to publish to right now — skip publishing gracefully rather than blocking the
            # build. A real, non-local cloud failing is still fatal — skill-pack publish is
            # mandatory once a real cloud is configured (see cmd_publish_skill_pack).
            if not self._auto_publish_enabled():
                sink({
                    "kind": "skill_pack_publish",
                    "stage": "skipped",
                    "message": f"Cloud publish skipped — {cloud_api} is not reachable ({exc})",
                })
                return {"skipped": True, "slug": company_slug, "version": version}
            sink({
                "kind": "skill_pack_publish",
                "stage": "failed",
                "message": f"Cloud publish failed — could not reach {cloud_api} ({exc})",
            })
            raise _CommandError("cloud_publish_failed", f"Cloud publish failed: {exc}") from exc

        tracking = dict(published.get("tracking") or {})
        if not tracking.get("tracking_token"):
            sink({
                "kind": "skill_pack_publish",
                "stage": "failed",
                "message": "Cloud publish failed — Conxa Cloud accepted the upload but did not return a tracking token.",
            })
            raise _CommandError("cloud_publish_failed", "Cloud publish did not return a tracking token.")

        sync_token = str(published.get("sync_token") or "")
        if not sync_token:
            sink({
                "kind": "skill_pack_publish",
                "stage": "failed",
                "message": "Cloud publish failed — Conxa Cloud accepted the upload but did not return a sync_token.",
            })
            raise _CommandError(
                "cloud_publish_failed",
                "Cloud publish did not return a sync_token. "
                "The installer cannot be built — the runtime needs this token to pull skill-pack updates. "
                "Ensure the cloud backend is up-to-date.",
            )

        # sync_url/tracking_url are already correctly versioned by the cloud (see
        # publish_routes._publish_skill_pack_impl) — just qualify the relative sync_url.
        sync_url = str(published.get("sync_url") or f"/api/v1/workflows/{generation}/{company_slug}/skill-packs/delta")
        pack["tracking"] = tracking
        pack["sync_endpoint"] = f"{cloud_api}{sync_url}"
        pack["sync_token"] = sync_token
        pack["installer_version"] = generation
        pack["published"] = {
            "cloud_api": cloud_api,
            "workspace_id": str(published.get("workspace_id") or ""),
            "published_at": published.get("published_at"),
        }
        pack_path.write_text(json.dumps(pack, indent=2, ensure_ascii=False), encoding="utf-8")
        workspace_id = str(published.get("workspace_id") or "")
        sink(
            {
                "kind": "skill_pack_publish",
                "stage": "published",
                "message": (
                    f"Uploaded {company_slug} v{version} — Ready for Release in Conxa Cloud "
                    f"(workspace {workspace_id or 'unknown'}). A Cloud admin must Release/Deploy "
                    f"it before any runtime receives it."
                ),
            }
        )
        return {
            "slug": company_slug,
            "version": version,
            "cloud_api": cloud_api,
            "workspace_id": workspace_id,
            "tracking_url": tracking.get("tracking_url", ""),
            "tracking_token_present": True,
            "sync_token_present": True,
            "sync_endpoint": pack["sync_endpoint"],
            "published_at": published.get("published_at"),
        }

    def _upload_installer_for_download(
        self,
        *,
        company_slug: str,
        result: dict[str, Any],
        release_notes: str,
        sink: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        import urllib.request

        if not self._auto_publish_enabled():
            return result

        installer_path = Path(str(result.get("installer_path") or ""))
        if not installer_path.is_file():
            raise _CommandError("installer_upload_failed", f"Installer not found: {installer_path}")

        cloud_api = self._cloud_api_base()
        generation = self._installer_generation()
        params = urlencode(
            {
                "filename": str(result.get("filename") or installer_path.name),
                "version": str(result.get("version") or "0.0.0"),
                "release_notes": release_notes,
            }
        )
        url = f"{cloud_api}/api/v1/workflows/{generation}/installer/upload?{params}"
        req = urllib.request.Request(url, data=installer_path.read_bytes(), method="POST")
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Authorization", f"Bearer {self._cloud_token()}")
        sink({"kind": "installer_build", "message": "Uploading installer to Conxa Cloud..."})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                uploaded = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
                detail = str(error_payload.get("detail") or "")
            except Exception:
                detail = ""
            if detail in {"distribution_not_permitted", "white_label_not_permitted", "entitlements_unavailable"}:
                raise _CommandError(detail, self._entitlement_error_message(detail)) from exc
            if exc.code == 409:
                raise _CommandError(
                    "installer_version_exists",
                    f"Installer version {result.get('version') or ''} already exists in Conxa Cloud.",
                ) from exc
            if exc.code == 413:
                result = dict(result)
                result["cloud_upload_error"] = "installer_upload_too_large"
                sink(
                    {
                        "kind": "installer_build",
                        "message": "Installer upload skipped: cloud rejected the file as too large. The local installer still contains cloud tracking.",
                        "warning": True,
                    }
                )
                return result
            raise _CommandError("installer_upload_failed", f"Installer upload failed: {exc}") from exc
        except Exception as exc:
            raise _CommandError("installer_upload_failed", f"Installer upload failed: {exc}") from exc
        result = dict(result)
        result["cloud_download_url"] = f"{cloud_api}{uploaded.get('download_url', '')}"
        if uploaded.get("version_download_url"):
            result["cloud_version_download_url"] = f"{cloud_api}{uploaded.get('version_download_url', '')}"
        result["cloud_sha256"] = uploaded.get("sha256", "")
        sink({"kind": "installer_build", "message": "Installer uploaded to Conxa Cloud"})
        return result

    # -- dispatch ------------------------------------------------------------

    # Commands that mutate a skill document from Human Edit — every command
    # here persists to a skill's steps and is worth attributing an edit-log
    # entry to (BUILD-25 stage a). Hooked once in dispatch() rather than at
    # each cmd_* so undo/redo (which bypass _push_undo) are covered too.
    _EDIT_COMMANDS = frozenset({
        "patch_step", "insert_branch_step", "delete_branch_step", "reorder_branch_steps",
        "confirm_optional_interstitial", "reorder_steps", "insert_step", "delete_step",
        "update_workflow_inputs", "replace_literals", "undo_workflow", "redo_workflow",
        "retarget_apply", "sign_off_workflow", "apply_recording_visual", "apply_step_frame",
        "clear_step_visual", "update_visual_bbox",
        # BUILD-26 stage (d): logged with source="copilot" below, not "patch_step" — the command
        # this delegates to internally is a plain method call, not a re-entrant dispatch(), so it
        # never trips this hook a second time under the wrong name.
        "accept_copilot_proposal",
        # One-click "generalize this to a loop" suggestion — deterministic, no LLM, but shares
        # the accept/undo/edit-log machinery above; logged with its own honestly-labeled source
        # below rather than "copilot" since no model produced this finding.
        "accept_for_each_suggestion",
    })

    def dispatch(self, msg: dict[str, Any]) -> None:
        rid = msg.get("id")
        cmd = str(msg.get("type") or "")
        payload = msg.get("payload") or {}
        handler = getattr(self, f"cmd_{cmd}", None)
        if handler is None:
            _write({"id": rid, "type": "error", "code": "unknown_command", "message": cmd})
            return
        skill_id_for_log = str(payload.get("skill_id") or "").strip() if cmd in self._EDIT_COMMANDS else ""
        before_doc = None
        if skill_id_for_log:
            try:
                from conxa_core.storage.json_store import read_skill  # noqa: PLC0415
                before_doc = read_skill(skill_id_for_log)
            except Exception:  # noqa: BLE001 — edit-log read must never block dispatch
                before_doc = None
        try:
            result = handler(payload, rid)
            _write({"id": rid, "type": "result", "result": result})
            if skill_id_for_log:
                try:
                    from conxa_core.storage.json_store import read_skill  # noqa: PLC0415
                    from conxa_compile.editor.edit_log import append_edit  # noqa: PLC0415
                    # BUILD-26 stage (d): an accepted copilot proposal is attributed here, not
                    # inside cmd_accept_copilot_proposal — that handler delegates to cmd_patch_step
                    # via a plain method call (never re-entering dispatch()), so this is the one
                    # place that ever sees the true top-level command name for this request.
                    if cmd == "accept_copilot_proposal":
                        source = "copilot"
                    elif cmd == "accept_for_each_suggestion":
                        source = "for_each_suggestion"
                    else:
                        source = "human"
                    # accept_for_each_suggestion carries its proposal id nested under
                    # payload["suggestion"]["id"] rather than a top-level proposal_id (its
                    # payload shape is {skill_id, suggestion}, not {skill_id, proposal_id, patch}
                    # like the copilot proposal commands) — check both.
                    proposal_id = str(
                        payload.get("proposal_id")
                        or (payload.get("suggestion") or {}).get("id")
                        or ""
                    ).strip() or None
                    append_edit(
                        skill_id_for_log, cmd, before_doc, read_skill(skill_id_for_log),
                        source=source, proposal_id=proposal_id,
                    )
                except Exception:  # noqa: BLE001 — edit-log write must never block dispatch
                    pass
        except _CommandError as exc:
            _write({"id": rid, "type": "error", "code": exc.code, "message": exc.message})
        except Exception as exc:  # noqa: BLE001 — report any handler failure to the renderer
            _write({
                "id": rid,
                "type": "error",
                "code": "internal_error",
                "message": str(exc),
                "trace": traceback.format_exc()[-2000:],
            })

    def serve(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                _write({"type": "error", "code": "bad_json", "message": line[:200]})
                continue
            # Each request is handled on its own thread so a long build does not
            # block recording stop/cancel commands.
            threading.Thread(target=self.dispatch, args=(msg,), daemon=True).start()


def _shutdown_open_sessions(be) -> None:
    """Mirror cmd_stop_recording's teardown for any session still open when the
    process exits (Ctrl+C, or Electron closing our stdin) instead of letting the
    interpreter rip Playwright's sync driver out from under its own thread."""
    from conxa_compile.recorder.session import registry as _shutdown_registry

    for sess in _shutdown_registry.all():
        try:
            be._loop.run(sess.stop())
        except Exception:
            pass


if __name__ == "__main__":
    be = Backend()
    try:
        be.serve()
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_open_sessions(be)
