"""Skill-package output-directory scaffolding: skill-packs format, templates, docs.

Split out of skill_package_builder.py: writing the skill-packs/{company}/ output
layout, resolving/cleaning the shared package's output directory, copying its
template files, and rendering the generated README/LICENSE.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from conxa_core.sanitize import dumps_safe


# ─────────────────────────────────────────────────
# skill-packs/{company}/ output format
# ─────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _read_execution_steps(path: Path) -> list[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def execution_capabilities(steps: list[Any]) -> list[str]:
    """App-layer features the built steps (runtime shape, nested bodies included) rely on, named as in
    the app layer's version.json ``capabilities``. An engine without one would misread the step."""
    need: set[str] = set()

    def walk(items: Any) -> None:
        for s in items if isinstance(items, list) else []:
            if not isinstance(s, dict):
                continue
            hints = s.get("handler_hints") if isinstance(s.get("handler_hints"), dict) else {}
            if isinstance((hints.get("date_picker") or {}).get("date_offset_days"), int):
                need.add("date_offset")
            if s.get("on_missing") == "ask":
                need.add("ask_missing")
            if s.get("type") == "wait_for_one_of" and s.get("flow"):
                need.add("variant_branch")
            bundle = s.get("identity_bundle") if isinstance(s.get("identity_bundle"), dict) else {}
            if "{{" in json.dumps([bundle.get("signals"), bundle.get("fingerprint")], default=str):
                need.add("templated_click")
            walk(s.get("steps"))
            walk(s.get("body"))
            for o in s.get("options") if isinstance(s.get("options"), list) else []:
                walk(o.get("steps") if isinstance(o, dict) else None)

    walk(steps)
    return sorted(need)


def _compute_inputs_required(idata: dict[str, Any]) -> list[str]:
    """Derive the manifest's `inputs_required` from a parsed inputs.json.

    Every declared input is required EXCEPT one flagged `optional` in the editor — that flag
    means the skill may run without it, so it must not block execution (runtime/server.js's
    pre-execution gate) or be advertised as mandatory to the calling agent (runtime/server.js's
    `_skillToolDefinitions`).
    """
    if "inputs" in idata and isinstance(idata["inputs"], list):
        return [
            i["name"]
            for i in idata["inputs"]
            if isinstance(i, dict) and "name" in i and not i.get("optional")
        ]
    return []


def _write_skill_packs_format(
    *,
    bundle_root: Path,
    workspace_id: str,
    display_name: str,
    target_url: str,
    protected_url: str,
    skill_slugs: list[str],
    skill_target_urls: dict[str, str],
    version: str,
    skill_group_ids: dict[str, str] | None = None,
    skill_unclaimed_hosts: dict[str, list[str]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    conxa_api_url: str = "",
    required_runtime: str = "",
) -> None:
    """Write the skill-packs/{workspace_dir_slug}/ layout alongside the legacy build output.

    This format is consumed by conxa-runtime.exe (the installer-distributed MCP server).
    The legacy skills/ layout is kept untouched for backward compatibility.

    target_url/protected_url are the workspace-level display defaults (from the first
    workflow, best-effort) — each skill's own manifest.json carries its own accurate
    target_url from skill_target_urls, since different workflows in one shared package
    may automate different pages on the workspace's site.
    """
    from conxa_core.config import active_environment
    from conxa_core.config import settings
    from conxa_core.workspace import workspace_dir_slug

    company      = workspace_dir_slug(workspace_id)
    # Resolve the public base that gets FROZEN into pack.json (sync + tracking) from
    # the active environment, so a dev-built installer embeds the dev cloud and a
    # prod-built one embeds prod — consistently across BOTH sync_endpoint and
    # tracking_url. Explicit overrides win; then SKILL_API_BASE_URL from .env.<env>;
    # then an env-appropriate default. (The old code defaulted to prod and separately
    # special-cased tracking to localhost, which could split the two across envs.)
    _env = active_environment()
    _env_default_base = f"http://127.0.0.1:{settings.port}" if _env == "dev" else "https://apis.conxa.in"
    api_base     = (
        conxa_api_url
        or os.environ.get("CONXA_API_URL")
        or os.environ.get("CONXA_CLOUD_API")
        or (settings.api_base_url or "").strip()
        or _env_default_base
    ).rstrip("/")
    tracking_base = os.environ.get("CONXA_TRACKING_API_URL", api_base).rstrip("/")
    tracking_events_url = os.environ.get("CONXA_TRACKING_EVENTS_URL", "").strip()
    if not tracking_events_url:
        tracking_events_url = f"{tracking_base}/api/tracking/{company}/events"
    skill_packs  = settings.data_dir / "skill-packs" / company

    skill_packs.mkdir(parents=True, exist_ok=True)

    # Directory name for each group — its stable, human-readable slug (e.g. "sales-257c9ff0")
    # rather than the raw group_id, so a customer poking around skill-packs/ sees a name, not
    # a UUID. Auth resolution never uses this: manifest.group_id below stays the raw id.
    group_dir_by_id = {
        g.get("id"): g.get("slug") for g in (groups or []) if isinstance(g, dict) and g.get("id") and g.get("slug")
    }

    written_slugs: list[str] = []
    skill_groups: dict[str, str] = {}
    for slug in skill_slugs:
        src_dir  = bundle_root / "skills" / slug
        group_id = (skill_group_ids or {}).get(slug) or "_default"
        dir_group = group_dir_by_id.get(group_id) or group_id
        dest_dir = skill_packs / dir_group / slug
        if not src_dir.is_dir():
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copy execution.json, recovery.json and visuals unchanged
        for fname in ("execution.json", "recovery.json", "app_knowledge.json"):
            src = src_dir / fname
            if src.is_file():
                shutil.copy2(src, dest_dir / fname)
            elif fname == "app_knowledge.json":
                (dest_dir / fname).unlink(missing_ok=True)   # a stale copy from an earlier build

        if (src_dir / "visuals").is_dir():
            dest_visuals = dest_dir / "visuals"
            if dest_visuals.exists():
                shutil.rmtree(dest_visuals)
            shutil.copytree(src_dir / "visuals", dest_visuals)

        # inputs.json (rename from input.json)
        input_src = src_dir / "input.json"
        if input_src.is_file():
            shutil.copy2(input_src, dest_dir / "inputs.json")

        # Compute checksums over the files we wrote
        checksums: dict[str, str] = {}
        for fname in ("execution.json", "recovery.json", "inputs.json"):
            p = dest_dir / fname
            if p.is_file():
                checksums[fname] = _sha256_file(p)

        # Read name/description from input.json or SKILL.md fallback. inputs.json was
        # written moments ago by this same build (skill_package_builder_saved_skill.py) —
        # if it exists but fails to parse, that's a real bug (a race, a disk error, a
        # writer that emitted invalid JSON), not a normal miss, so it raises rather than
        # silently shipping the skill with no description and every input demoted to
        # optional (inputs_required feeds the runtime's pre-execution gate).
        skill_name = slug.replace("_", " ").title()
        description = ""
        inputs_required: list[str] = []
        inputs_p = dest_dir / "inputs.json"
        if inputs_p.is_file():
            try:
                idata = json.loads(inputs_p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Skill {slug!r}: inputs.json at {inputs_p} was just written by this "
                    f"build but is unreadable — cannot compute its required inputs: {exc}"
                ) from exc
            description = idata.get("description", "")
            inputs_required = _compute_inputs_required(idata)
        if not description:
            md_p = src_dir / "SKILL.md"
            if md_p.is_file():
                first = next((l.lstrip("# ").strip() for l in md_p.read_text(encoding="utf-8").splitlines() if l.strip()), "")
                description = first

        def _read_json_sidecar(path: Path, label: str, slug: str = slug) -> dict[str, Any]:
            """A sidecar this same build just wrote (structural_fingerprint.json,
            environment.json, compensation_skill.json) that exists but won't parse is a
            real bug, not a normal miss — raise instead of silently disabling the
            feature it feeds (drift gate / environment gate / compensation link)."""
            if not path.is_file():
                return {}
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Skill {slug!r}: {label} at {path} was just written by this build "
                    f"but is unreadable: {exc}"
                ) from exc
            return loaded if isinstance(loaded, dict) else {}

        # Structural fingerprint (optional) for the runtime's pre-execution drift gate.
        structural_fp = _read_json_sidecar(src_dir / "structural_fingerprint.json", "structural_fingerprint.json")

        # EXEC-36 (optional) — recording environment for the runtime's replay-time environment
        # mismatch warning. Same read-from-disk pattern as structural_fingerprint above.
        environment = _read_json_sidecar(src_dir / "environment.json", "environment.json")

        # PROD-3-DRYRUN layer 5 (optional) — compensation-workflow link.
        loaded_comp = _read_json_sidecar(src_dir / "compensation_skill.json", "compensation_skill.json")
        compensation_skill = str(loaded_comp.get("compensation_skill") or "").strip()

        required_capabilities = execution_capabilities(_read_execution_steps(src_dir / "execution.json"))
        manifest = {
            "slug":             slug,
            "name":             skill_name,
            "description":      description,
            "version":          version,
            # NOTE(branch-steps): if_present/try_dismiss/wait_for_one_of (EXEC-1) require a
            # runtime that recognizes those step types — an older runtime silently no-ops an
            # unknown step type (see runtime/run.js executeStep), which for a branch step means
            # skipping its entire nested body. The branch executor (probePresent/runBranchBody/
            # HANDLERS.if_present|try_dismiss|wait_for_one_of, commit 45896e7) is on `main` but,
            # as of the 2026-07-10 Human Edit branch-authoring work, is NOT YET in any tagged
            # app-vX.Y.Z release (confirmed: `git merge-base --is-ancestor 45896e7 app-v1.3.4`
            # fails). Do NOT hardcode a bump here until that changes — a fabricated version floor
            # would make every pack (branch steps or not) refuse to run on the current app layer.
            # Once the app layer containing 45896e7 is tagged (see MIN_HOST precedent in
            # .github/workflows/build-runtime-app.yml), bump the default below to that tag so
            # packs using branch steps refuse on older runtimes instead of silently skipping
            # their body. Until then, set CONXA_REQUIRED_RUNTIME explicitly for any pack that
            # uses branch steps.
            #
            # NOTE(PROD-3): the same trap applies to entity_binding/destructive — an older
            # runtime silently ignores both unknown fields (ignoring is fine for a step that
            # never had them, but a pack that DOES carry an entity binding on an old runtime
            # would run without that binding's protection while claiming it, since no error
            # surfaces). No tagged app-vX.Y.Z release contains this work yet, so — same rule as
            # above — do NOT hardcode a bump here; set CONXA_REQUIRED_RUNTIME explicitly for any
            # pack with a destructive/bound step until one exists.
            #
            # Same trap a third time for handler_hints.control_kind == "choice" (multiple-choice
            # controls — CLAUDE.md's multiple-choice plan): an older app layer ignores
            # control_kind/choice entirely and clicks whatever option was recorded regardless of
            # the caller's input, which is silent wrong behavior, not a visible failure. Until an
            # app-vX.Y.Z release containing runtime/app/choice.js is tagged, set
            # CONXA_REQUIRED_RUNTIME explicitly for any pack with a compiled choice step.
            "required_runtime": required_runtime or os.environ.get("CONXA_REQUIRED_RUNTIME", ">=1.0.3"),
            "company":          company,
            "target_url":       skill_target_urls.get(slug, target_url),
            "group_id":         group_id,
            "inputs_required":  inputs_required,
            "structural_fingerprint": structural_fp,
            "environment":      environment,
            "compensation_skill": compensation_skill,
            "checksum":         checksums,
        }
        # Engine features this pack uses that older app layers don't have: the runtime refuses the pack
        # instead of running a step it would misread (required_runtime compares the host exe, not this).
        if required_capabilities:
            manifest["required_capabilities"] = required_capabilities
        # AUTH-1: hosts the recording visited that no group app covers — the runtime warns (never
        # blocks) about them before step 0. Omitted when empty so the common manifest is unchanged
        # and a runtime that predates the field simply never reads it.
        _unclaimed = (skill_unclaimed_hosts or {}).get(slug)
        if _unclaimed:
            manifest["unclaimed_hosts"] = _unclaimed
        # PROD-3 "Strict Mode": a per-skill recovery-tier ceiling, honoured by the runtime as
        # min(host ceiling, this value) — never looser than the host (runtime/app/server.js::
        # _effectiveRecoveryTier). No Studio UI sets this yet (tracked as a follow-up); until
        # then it's settable per build via CONXA_STRICT_MODE_MAX_TIER, e.g. for a payroll/finance
        # workflow a CI pipeline wants to always build at ceiling 2 regardless of the host.
        # Omitted (not written as null) when unset, so an older/unaware runtime and the manifest
        # schema itself both read "no pack ceiling, defer to host" the same way.
        _strict_tier = os.environ.get("CONXA_STRICT_MODE_MAX_TIER", "").strip()
        if _strict_tier:
            try:
                manifest["max_recovery_tier"] = int(_strict_tier)
            except ValueError:
                pass
        (dest_dir / "manifest.json").write_text(
            dumps_safe(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        written_slugs.append(slug)
        skill_groups[slug] = dir_group

    # pack.json at company root. Merge `skills`/`skill_groups`/`groups` with
    # whatever's already there rather than replacing wholesale — a scoped
    # single-workflow build (only `written_slugs` for this one call) must
    # never drop a sibling skill a prior build already wrote here.
    existing_pack: dict[str, Any] = {}
    existing_pack_path = skill_packs / "pack.json"
    if existing_pack_path.is_file():
        try:
            existing_pack = json.loads(existing_pack_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Silently treating this as "no prior pack" would drop every sibling skill a
            # previous build already wrote here — the exact bug this merge exists to avoid.
            raise RuntimeError(
                f"Existing pack.json at {existing_pack_path} is corrupted and cannot be "
                f"merged — fix or delete it before building again: {exc}"
            ) from exc
    merged_skills = [s for s in (existing_pack.get("skills") or []) if s not in written_slugs] + written_slugs
    merged_skill_groups = {**(existing_pack.get("skill_groups") or {}), **skill_groups}
    existing_groups_by_id = {g.get("id"): g for g in (existing_pack.get("groups") or []) if isinstance(g, dict)}
    for g in (groups or []):
        existing_groups_by_id[g.get("id")] = g
    merged_groups = list(existing_groups_by_id.values())

    _req_rt = required_runtime or os.environ.get("CONXA_REQUIRED_RUNTIME", ">=1.0.3")
    pack = {
        "workspace_id":       workspace_id,
        "display_name":       display_name,
        "skill_pack_version": version,
        "required_runtime":   _req_rt,
        "target_url":         target_url,
        "protected_url":      protected_url,
        "skills":             merged_skills,
        "skill_groups":       merged_skill_groups,
        "groups":             merged_groups,
        "sync_endpoint":      f"{api_base}/skill-packs/{company}/delta",
        "built_at":           datetime.now(timezone.utc).isoformat(),
    }

    # Tracking config: embed in pack.json so the runtime knows where to send events
    import hmac as _hmac_mod
    import hashlib as _hash_mod

    _tracking_secret = os.environ.get("SKILL_TRACKING_HMAC_SECRET", "")
    _tracking_token  = ""
    if _tracking_secret:
        _raw = f"{workspace_id}:{version}".encode()
        _tracking_token = _hmac_mod.new(_tracking_secret.encode(), _raw, _hash_mod.sha256).hexdigest()
        from conxa_core.db import db_set
        db_set("tracking_tokens", workspace_id, {"token": _tracking_token, "version": version})

    # tracking_events_url already derives from the env-consistent api_base above
    # (via tracking_base), so no per-env localhost special-case is needed — dev builds
    # embed the dev cloud, prod builds embed prod, for both sync and tracking.
    pack["tracking"] = {
        "enabled":          True,
        "tracking_url":     tracking_events_url,
        "tracking_token":   _tracking_token,
        "workspace_id":     workspace_id,
        "schema_version":   1,
        "protocol_version": 1,
    }

    (skill_packs / "pack.json").write_text(
        dumps_safe(pack, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ─────────────────────────────────────────────────
# Skill-package output directory helpers
# ─────────────────────────────────────────────────

def _bundle_root(bundle_slug: str) -> Path:
    from conxa_core.storage.skill_packages import bundle_root_dir
    from conxa_compile.storage.skill_packages_build import ensure_bundle_scaffold
    root = bundle_root_dir(bundle_slug)
    if root is None:
        root = ensure_bundle_scaffold(bundle_slug)
    return root


# ─────────────────────────────────────────────────
# Template helpers
# ─────────────────────────────────────────────────

def _templates_dir() -> Path:
    # Templates ship alongside this module in conxa_compile (bundled by PyInstaller
    # via the source-glob collector in pyinstaller.spec, which walks conxa_compile
    # for *.tmpl/*.gitignore files).
    return Path(__file__).parent / "skill_package_templates"


def _render_skill_package_template(name: str, **subs: str) -> str:
    tmpl = (_templates_dir() / "skill_package" / name).read_text(encoding="utf-8")
    for k, v in subs.items():
        tmpl = tmpl.replace("{{" + k + "}}", v)
    return tmpl


def _copy_skill_package_templates(
    bundle_root: Path,
    *,
    company_name: str,
    bundle_slug: str,
    target_url: str,
    version: str,
    skill_slugs: list[str],
    package_id: str | None = None,
) -> None:
    """Copy skill-package-side templates into bundle_root with substitutions."""
    templates = _templates_dir()

    # .gitignore
    tmpl_gi = templates / "skill_package" / ".gitignore"
    if tmpl_gi.exists():
        (bundle_root / ".gitignore").write_text(tmpl_gi.read_text(encoding="utf-8"), encoding="utf-8")

    install_id = package_id or bundle_slug

    # Claude.md (from template)
    skills_list = "\n".join(f"- `{s}`" for s in skill_slugs)
    (bundle_root / "Claude.md").write_text(
        _render_skill_package_template(
            "Claude.md.tmpl",
            company_name=company_name,
            slug=bundle_slug,
            package_id=install_id,
            target_url=target_url,
            skills_list=skills_list,
        ),
        encoding="utf-8",
    )

    # index.md (from template)
    skills_catalog = "\n".join(f"- `{s}` — see `skills/{s}/SKILL.md`" for s in skill_slugs)
    (bundle_root / "index.md").write_text(
        _render_skill_package_template(
            "index.md.tmpl",
            company_name=company_name,
            slug=bundle_slug,
            target_url=target_url,
            skills_catalog=skills_catalog,
        ),
        encoding="utf-8",
    )

    # Build output stays data-only. Runtime installation is handled by the
    # Windows installer flow, not by an npm CLI package.


def _clean_stale_artifacts(bundle_root: Path) -> None:
    """Remove files from old build architectures before rebuilding."""
    stale_files = [
        "server.js", "run.js", "browser.js", "config.js", "runtime.js",
        "package.json", "package-lock.json", ".mcp.json",
        "plugin.json", "plugin.config.json",  # pre-workflow-collapse manifest filename
        "schema.json",         # removed from build output
        "auth/credentials.example.json",
        "auth/credentials.json",
    ]
    stale_dirs = [
        "execution", "node_modules", "auth/login",
        "runtime", "runtime/node_modules",
        ".claude-plugin",  # old marketplace shim, removed in shared-runtime migration
    ]
    for name in stale_files:
        p = bundle_root / name
        if p.exists():
            p.unlink()
    for name in stale_dirs:
        p = bundle_root / name
        if p.is_dir():
            shutil.rmtree(p)
    # Remove hash-suffixed per-workflow registry json files (e.g. render_c759c810.json)
    for p in bundle_root.glob("*_*.json"):
        p.unlink()


# ─────────────────────────────────────────────────
# Data-only package file generators (kept for README/LICENSE)
# ─────────────────────────────────────────────────


def _render_readme(
    company_name: str,
    bundle_slug: str,
    target_url: str,
    skill_slugs: list[str],
    package_id: str | None = None,
) -> str:
    skills_md = "\n".join(f"- `{s}` — see `skills/{s}/SKILL.md`" for s in skill_slugs)
    install_id = package_id or bundle_slug
    return f"""\
# {company_name}

Automate [{target_url}]({target_url}) with Claude using this Conxa skill pack.

Package ID: `{install_id}`

## Install

This is a data-only skill pack. Use Conxa's Build Installer page to create the
Windows setup file that installs the runtime, registers Claude, and copies these
skills onto the user's machine.

## Available Skills

{skills_md}

## How It Works

This skill pack works with the installed `conxa` MCP runtime. When Claude calls
a skill, a real Chromium browser opens on your machine, executes the recorded
workflow, and returns a result and screenshot. Your auth session stays on your
machine and is never uploaded anywhere.

---
*Generated by [Conxa](https://conxa.in)*
"""


def _render_license() -> str:
    year = datetime.now(timezone.utc).year
    return f"""\
MIT License

Copyright (c) {year} Conxa

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
