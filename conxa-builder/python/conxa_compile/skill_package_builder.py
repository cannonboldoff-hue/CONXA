"""Workspace-scoped build pipeline.

Compiles every workflow in a workspace (auth session + recording each) into
ONE shared data-only skill pack folder:

  output/skill_package/{bundle_slug}/
    skill_package.json            <- package manifest
    README.md                     <- auto-generated, public-facing
    CLAUDE.md                     <- Claude reads this for skill discovery
    .gitignore                    <- excludes auth/auth.json and local state
    LICENSE                       <- MIT by default
    auth/                         <- runtime-local auth directory only
    skills/
      {workflow_slug}/            <- one per workflow, login steps stripped

auth/auth.json and auth/credentials*.json are NEVER placed in the build output.
Credentials are local runtime state captured by the installed Conxa runtime.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from conxa_compile.skill_package_builder_output import (
    _bundle_root,
    _clean_stale_artifacts,
    _copy_skill_package_templates,
    _render_license,
    _render_readme,
    _write_skill_packs_format,
)
from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill
from conxa_core.sanitize import dumps_safe
from conxa_core.storage.skill_pack_store import get_or_create_skill_pack, set_build
from conxa_core.storage.workflow_store import list_workflows
from conxa_core.storage.json_store import read_skill


# ─────────────────────────────────────────────────
# Main build entry point
# ─────────────────────────────────────────────────

def build_skill_package(
    workspace_id: str,
    *,
    company_name: str | None = None,
    version: str = "0.1.0",
    only_workflow_id: str | None = None,
    realtime_sink: Callable[[dict[str, Any]], None] | None = None,
    require_signed_off: bool = True,
) -> dict[str, Any]:
    """Compile workflow(s) into the shared local package directory.

    ``require_signed_off=True`` (default — publish/installer paths): every workflow built must
    have been approved by a person (``Workflow.signed_off``). A *test* build (the conversation's
    "Test it", the copilot's verify) passes False: a workflow has to be testable before it can be
    approved, and approval itself is the human action that follows a pass.

    ``only_workflow_id=None`` (default for an explicit full-workspace rebuild):
    compiles every workflow in the workspace, as before.

    ``only_workflow_id=<id>``: compiles ONLY that one workflow — a sibling
    workflow that isn't compiled, edited, or passing its tests never blocks
    this build. This is the path Publish uses (see handlers/workflows.py's
    cmd_build_skill_package), so publishing "Create a Lead" never requires
    "Update Opportunity" to be ready. Other already-built skills' output on
    disk is left untouched and still counted in the merged skill_package.json.
    """
    pack = get_or_create_skill_pack(workspace_id, display_name=company_name)
    all_workflows = [w for w in list_workflows(workspace_id) if w.workspace_id == workspace_id]
    if not all_workflows:
        raise ValueError("No workflows recorded yet. Record at least one workflow.")

    if only_workflow_id is not None:
        workflows = [w for w in all_workflows if w.id == only_workflow_id]
        if not workflows:
            raise ValueError(f"No workflow found with id {only_workflow_id}")
    else:
        workflows = all_workflows

    uncompiled = [w.name for w in workflows if not w.skill_id]
    if uncompiled:
        raise ValueError(f"Compile these workflows before building: {', '.join(uncompiled)}")
    unapproved = [w.name for w in workflows if not w.signed_off] if require_signed_off else []
    if unapproved:
        raise ValueError(
            f"Open the editor and sign off on these workflows before building: {', '.join(unapproved)}"
        )

    def _log(msg: str, **extra: Any) -> None:
        entry = {"kind": "skill_package_build", "message": msg, "workspace_id": workspace_id, **extra}
        if realtime_sink:
            realtime_sink(entry)

    from conxa_core.workspace import workspace_dir_slug
    bundle_slug = workspace_dir_slug(workspace_id)
    _log("Starting skill package build", bundle_slug=bundle_slug, version=version)

    bundle_root = _bundle_root(bundle_slug)

    # ── 0. Clean stale artifacts from old build architectures ─────────────
    _clean_stale_artifacts(bundle_root)
    _log("Cleaned stale artifacts")

    # ── 1. Build every workflow's skill ────────────────────────────────────
    skill_slugs: list[str] = []
    skill_target_urls: dict[str, str] = {}
    skill_group_ids: dict[str, str] = {}
    skill_visited_hosts: dict[str, list[str]] = {}
    for wf in workflows:
        saved_skill = read_skill(wf.skill_id) if wf.skill_id else None
        if saved_skill is not None:
            _log("Building workflow from saved skill JSON", workflow=wf.name, workflow_id=wf.id)
            _build_workflow_from_saved_skill(
                bundle_root=bundle_root,
                workflow_slug=wf.slug,
                saved_skill=saved_skill,
                on_warning=lambda msg, wf=wf: _log(msg, warning=True, workflow=wf.name),
            )
            # What the app map knows about popups on the hosts this skill visits (advisory; see
            # app_graph/knowledge.py). Never lets the map fail a build.
            try:
                from conxa_compile.app_graph.knowledge import write_app_knowledge

                meta_hosts = (saved_skill.get("meta") or {}).get("visited_hosts")
                write_app_knowledge(
                    bundle_root / "skills" / wf.slug, workspace_id,
                    [*(meta_hosts if isinstance(meta_hosts, list) else []), wf.target_url],
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"App knowledge skipped: {exc}", warning=True, workflow=wf.name)
            skill_slugs.append(wf.slug)
            skill_target_urls[wf.slug] = wf.target_url
            skill_group_ids[wf.slug] = wf.group_id
            saved_meta = saved_skill.get("meta") if isinstance(saved_skill.get("meta"), dict) else {}
            visited = saved_meta.get("visited_hosts")
            skill_visited_hosts[wf.slug] = [str(h) for h in visited] if isinstance(visited, list) else []
            _log(f"Workflow {wf.name!r} compiled from saved skill JSON")
            continue

        _log(f"Skipping workflow {wf.name!r} — no compiled skill found", warning=True)

    # ── 2. Write skill_package.json (v2 manifest for the installed Conxa runtime) ──
    # target_url/protected_url are package-level display defaults, taken from the
    # first workflow BUILT IN THIS CALL — each skill's own manifest.json (written
    # below) carries its own accurate target_url, since different workflows may
    # automate different pages on the company's site.
    primary = workflows[0]
    var_pattern = re.compile(r"\{\{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*\}\}")
    protected_url_vars = var_pattern.findall(primary.protected_url)

    package_id = bundle_slug
    # Union with any already-built skills' entries rather than replacing —
    # a scoped single-workflow build must never drop a sibling skill that a
    # prior build already wrote to this same package.
    existing_config: dict[str, Any] = {}
    existing_config_path = bundle_root / "skill_package.json"
    if only_workflow_id is not None and existing_config_path.is_file():
        try:
            existing_config = json.loads(existing_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_config = {}
    existing_skill_entries = {
        s["slug"]: s for s in (existing_config.get("skills") or []) if isinstance(s, dict) and s.get("slug")
    }
    for s in skill_slugs:
        existing_skill_entries[s] = {"slug": s, "path": f"skills/{s}"}
    merged_skill_slugs = list(existing_skill_entries.keys())

    skill_package_config = {
        "package_format": 2,
        "id": package_id,
        "slug": bundle_slug,
        "name": pack.display_name,
        "version": version,
        "target_url": primary.target_url if not existing_config else existing_config.get("target_url", primary.target_url),
        "protected_url": primary.protected_url if not existing_config else existing_config.get("protected_url", primary.protected_url),
        "protected_url_vars": protected_url_vars,
        "auth_requirements": {"kind": "cookie", "manual_login": True},
        "skills": list(existing_skill_entries.values()),
        "runtime_min_version": "1.0.0",
        "compatibility": {"conxa_runtime": ">=1.0.0"},
    }
    (bundle_root / "skill_package.json").write_text(
        dumps_safe(skill_package_config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _log("Written skill_package.json", skills=merged_skill_slugs, package_id=package_id)

    # ── 3. Copy skill-package templates (Claude.md, index.md, .gitignore) ──
    # skill_slugs=merged_skill_slugs (not just this call's skill_slugs) so a
    # scoped single-workflow build still documents every already-built sibling.
    _copy_skill_package_templates(
        bundle_root,
        company_name=pack.display_name,
        bundle_slug=bundle_slug,
        target_url=primary.target_url,
        version=version,
        skill_slugs=merged_skill_slugs,
        package_id=package_id,
    )
    _log("Copied skill package templates")

    # ── 4. Write README.md and LICENSE ────────────────────────────────────
    (bundle_root / "README.md").write_text(
        _render_readme(pack.display_name, bundle_slug, primary.target_url, merged_skill_slugs, package_id=package_id),
        encoding="utf-8",
    )
    _log("Written README.md")

    license_path = bundle_root / "LICENSE"
    if not license_path.exists():
        license_path.write_text(_render_license(), encoding="utf-8")
        _log("Written LICENSE")

    # ── 5. Write skill-packs/{company}/ format (for installer runtime) ────────
    from conxa_core.storage.group_store import get_group as _get_group, unclaimed_hosts

    group_models = {}
    for group_id in {gid for gid in skill_group_ids.values() if gid}:
        group = _get_group(group_id)
        if group is not None:
            group_models[group_id] = group

    # The runtime resolves a skill's sign-in from pack.json's `groups` block and has no fallback for
    # a skill whose group is missing from it (AUTH-2 removed the legacy single-session path). A group
    # deleted after its workflows were assigned would otherwise ship a pack that fails only on the
    # customer's machine — fail here, where the vendor can reassign the workflow and rebuild.
    orphaned = [wf.name for wf in workflows if wf.slug in skill_slugs and wf.group_id not in group_models]
    if orphaned:
        raise RuntimeError(
            f"These workflows are not in an existing group: {', '.join(repr(n) for n in orphaned)}. "
            "Move each one into a group (its group was probably deleted), then build again."
        )

    groups_payload = [
        {
            "id": group.id,
            "slug": getattr(group, "slug", "") or group.id,
            "name": group.name,
            "apps": [
                {
                    "id": a.id,
                    "name": a.name,
                    "login_url": a.login_url,
                    "success_url": a.success_url,
                    # Learned at Connect time (P0: Application Authentication Recording) — see
                    # conxa_compile.auth_learning's module docstring. None for an app connected
                    # before this feature, or whose self-test never passed: the runtime falls
                    # back to its generic detection ladder for that app, same as before this
                    # field existed. Never carries cookie values, response bodies, or a query
                    # string/fragment — enforced by auth_learning.learn()/self_test(), not here.
                    "auth_definition": getattr(a, "auth_definition", None),
                }
                for a in group.apps
            ],
        }
        for group in group_models.values()
    ]

    # A group is the unit of sign-in: every workflow in it needs EVERY app in the group signed in, so
    # there is no per-workflow app subset to compute (the manifest's old `required_apps` is gone).
    # What is still per-workflow is the hosts the recording visited that no group app covers
    # (AUTH-1) — same inputs as Build Studio's group-page chip, so the manifest warning can't
    # disagree with it.
    skill_unclaimed_hosts: dict[str, list[str]] = {}
    for wf in workflows:
        if wf.slug not in skill_slugs:
            continue
        group = group_models.get(wf.group_id)
        if not group:
            continue
        visited_hosts = skill_visited_hosts.get(wf.slug) or []
        skill_unclaimed_hosts[wf.slug] = unclaimed_hosts(group.apps, wf.target_url, wf.protected_url, *visited_hosts)

    # skill-packs/ is the ONLY layout publish, the installer, and the runtime read — a failed
    # write here means the build produced nothing usable and must not be reported as a
    # success (previously logged as a warning while still recording/returning success below).
    _write_skill_packs_format(
        bundle_root=bundle_root,
        workspace_id=workspace_id,
        display_name=pack.display_name,
        target_url=primary.target_url,
        protected_url=primary.protected_url,
        skill_slugs=skill_slugs,
        skill_target_urls=skill_target_urls,
        skill_group_ids=skill_group_ids,
        skill_unclaimed_hosts=skill_unclaimed_hosts,
        groups=groups_payload,
        version=version,
    )
    _log("Written skill-packs format", company=bundle_slug, skills=skill_slugs)

    # ── 6. Persist build record ────────────────────────────────────────────
    set_build(workspace_id, output_path=str(bundle_root), version=version)

    return {
        "workspace_id": workspace_id,
        "company_slug": bundle_slug,
        "bundle_slug": bundle_slug,
        "output_path": str(bundle_root),
        "version": version,
        "skills": skill_slugs,
    }
