"""Filesystem persistence for generated automation plugin bundles — read/list side.

Layout (per bundle):

  output/skill_package/<bundle_slug>-plugin/
    <bundle_slug>.json           machine-readable plugin index
    README.md                    human-readable docs
    auth/
      auth.json                  authentication config
      credentials.example.json   credential template
    skills/
      <workflow_slug>/
        SKILL.md, manifest.json, execution.json, input.json, recovery.json
        visuals/  tests/
    orchestration/
      index.md, planner.md, schema.json
    execution/
      executor.js, recovery.js, tracker.js, validator.js

This module resolves bundle/workflow paths and lists/reads existing bundles —
the surface both the cloud dashboard and the Build Studio need. Bundle
generation (scaffold, write, delete, rename) is Build-Studio-only pipeline
output and lives in conxa_compile/storage/skill_packages_build.py.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
from pathlib import Path

from conxa_core.config import account_root

WORKFLOW_FILENAMES = ("execution.json", "recovery.json")

SKILLS_SUBDIR = "skills"
WORKFLOWS_SUBDIR = "workflows"  # kept for legacy migration only
FIXED_PACKAGE_ROOT = Path("output") / "skill_package"

VISUAL_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

CONTAINER_LEGACY_NAMES = frozenset(
    {
        WORKFLOWS_SUBDIR,
        SKILLS_SUBDIR,
        "engine",
        "bridge",
        "index.json",
        "skill.json",
        "package.json",
        "index.js",
        "README.md",
        "install.js",
        "install.bat",
        "claude",
    }
)

RESERVED_BUNDLE_SLUGS = frozenset(
    {
        WORKFLOWS_SUBDIR,
        SKILLS_SUBDIR,
        "engine",
        "bridge",
        "packages",
    }
)

_BUNDLE_INDEX_FILENAMES = ("README.md", "CLAUDE.md")

_SKIP_DIRS = frozenset({"node_modules", ".git", "__pycache__"})
_SKIP_FILES = frozenset({"auth.json", "credentials.json", "credentials.example.json"})
_TEXT_SUFFIXES = frozenset({".json", ".md", ".js", ".ts", ".txt", ".yaml", ".yml", ".toml", ".gitignore", ".env", ".example", ""})

# ──────────────────────────────────────────────────────────────────────────────
# Bundle / slug / path resolution
# ──────────────────────────────────────────────────────────────────────────────


def _sanitize_segment(name: str) -> str:
    return Path(str(name or "").strip()).name


def validate_bundle_slug(name: str) -> bool:
    n = _sanitize_segment(name)
    if not n or not re.fullmatch(r"[a-z][a-z0-9_]*", n):
        return False
    if n in RESERVED_BUNDLE_SLUGS:
        return False
    return True


def _bundle_folder_name(slug: str) -> str:
    """On-disk directory name for a bundle: ``{slug}-plugin``."""
    return f"{slug}-plugin"


def _slug_from_folder_name(folder_name: str) -> str | None:
    """Reverse of _bundle_folder_name; returns slug or None if not a plugin folder."""
    if not folder_name.endswith("-plugin"):
        return None
    slug = folder_name[: -len("-plugin")]
    if not slug or not validate_bundle_slug(slug):
        return None
    return slug


def _container_has_nested_bundles(container: Path) -> bool:
    for p in container.iterdir():
        if not p.is_dir():
            continue
        # Detect new-layout plugin folders ({slug}-plugin with skills/ subdir)
        if p.name.endswith("-plugin") and (p / SKILLS_SUBDIR).is_dir():
            return True
        # Detect old-layout nested bundles
        if not p.name.endswith("-plugin") and p.name not in RESERVED_BUNDLE_SLUGS and p.name != "engine":
            if (p / WORKFLOWS_SUBDIR).is_dir():
                return True
    return False


def maybe_migrate_legacy_container_layout(container: Path) -> None:
    """Move flat ``container/workflows`` + ``container/engine`` into ``legacy`` once."""
    if not container.is_dir():
        return
    if _container_has_nested_bundles(container):
        return
    wf = container / WORKFLOWS_SUBDIR
    if not wf.is_dir():
        return
    if not any(p.is_dir() and _workflow_manifest_summary(p) is not None for p in wf.iterdir()):
        return
    legacy = container / "legacy"
    if legacy.exists():
        return
    legacy.mkdir(parents=True, exist_ok=True)
    for name in (WORKFLOWS_SUBDIR, "engine", "bridge", "claude"):
        src = container / name
        if src.exists():
            shutil.move(str(src), str(legacy / name))
    for fname in ("README.md", "index.json", "skill.json", "package.json", "index.js", "install.js", "install.bat"):
        src = container / fname
        if src.is_file():
            shutil.move(str(src), str(legacy / fname))


def skill_package_root_dir() -> Path:
    """Filesystem container holding one directory per skill package bundle.

    Rooted at the writable state base (the user profile in frozen builds, the
    in-repo source dir in development) so generated bundles never target the
    read-only install tree.
    """
    path = account_root() / FIXED_PACKAGE_ROOT  # per account (AUTH-26)
    path.mkdir(parents=True, exist_ok=True)
    maybe_migrate_legacy_container_layout(path)
    return path


def bundle_root_dir(bundle_slug: str) -> Path | None:
    name = _sanitize_segment(bundle_slug)
    if not name or not validate_bundle_slug(name):
        return None
    return skill_package_root_dir() / _bundle_folder_name(name)


# ──────────────────────────────────────────────────────────────────────────────
# Internal skill discovery helpers
# ──────────────────────────────────────────────────────────────────────────────


def _workflow_manifest_summary(path: Path) -> dict[str, str] | None:
    # A valid skill dir must have execution.json
    if not (path / "execution.json").is_file():
        return None
    description = ""
    # Read title/description from SKILL.md first
    skill_md_path = path / "SKILL.md"
    if skill_md_path.is_file():
        try:
            for line in skill_md_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#"):
                    description = line.lstrip("#").strip()
                    break
        except OSError:
            pass
    # Fall back to manifest.json if present (legacy builds)
    if not description:
        manifest_path = path / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    description = str(manifest.get("description") or "").strip()
            except (OSError, json.JSONDecodeError):
                pass
    if not description:
        description = f"Run the {path.name.replace('_', ' ')} workflow."
    return {"name": path.name, "description": description}


def _workflow_package_dirs(bundle_root: Path) -> list[Path]:
    return [path for path, _summary in _workflow_package_entries(bundle_root)]


def _workflow_package_entries(bundle_root: Path) -> list[tuple[Path, dict[str, str]]]:
    by_name: dict[str, tuple[Path, dict[str, str]]] = {}
    skills_parent = bundle_root / SKILLS_SUBDIR
    if skills_parent.is_dir():
        for path in skills_parent.iterdir():
            if not path.is_dir():
                continue
            summary = _workflow_manifest_summary(path)
            if summary is not None:
                by_name[path.name] = (path, summary)
    return [by_name[key] for key in sorted(by_name)]


# ──────────────────────────────────────────────────────────────────────────────
# Visual asset helpers
# ──────────────────────────────────────────────────────────────────────────────


def _read_visual_asset_bytes(workflow_dir: Path) -> dict[str, bytes]:
    visuals_dir = workflow_dir / "visuals"
    if not visuals_dir.is_dir():
        return {}
    out: dict[str, bytes] = {}
    for child in sorted(visuals_dir.iterdir()):
        if not child.is_file() or child.name.startswith("."):
            continue
        if child.suffix.lower() not in VISUAL_IMAGE_SUFFIXES:
            continue
        out[child.name] = child.read_bytes()
    return out


def _read_visual_assets(workflow_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for filename, content in _read_visual_asset_bytes(workflow_dir).items():
        out[f"visuals/{filename}"] = base64.standard_b64encode(content).decode("ascii")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Display / listing helpers
# ──────────────────────────────────────────────────────────────────────────────


def _auto_manifest_fallback_description(workflow_slug: str) -> str:
    return f"Run the {workflow_slug.replace('_', ' ')} workflow."


def _workflow_folder_display_label(package_dir: Path, workflow_slug: str) -> str:
    manifest_path = package_dir / "manifest.json"
    manifest_desc = ""
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_desc = str(data.get("description") or "").strip()
        except (json.JSONDecodeError, OSError):
            manifest_desc = ""
    auto = _auto_manifest_fallback_description(workflow_slug)
    if manifest_desc and manifest_desc != auto:
        return manifest_desc
    skill_path = package_dir / "SKILL.md"
    if skill_path.is_file():
        try:
            lines = skill_path.read_text(encoding="utf-8").splitlines()
            first = lines[0].strip() if lines else ""
            if first.startswith("#"):
                cand = first.lstrip("#").strip()
                if cand and cand.replace(" ", "_") != workflow_slug and cand != workflow_slug:
                    return cand
        except OSError:
            pass
    return workflow_slug


def _workflow_present_files(workflow_dir: Path) -> list[str]:
    return [filename for filename in WORKFLOW_FILENAMES if (workflow_dir / filename).is_file()]


def _workflow_listing_metadata(workflow_dir: Path) -> dict[str, object]:
    workflow_slug = workflow_dir.name
    return {
        "workflow_slug": workflow_slug,
        "display_label": _workflow_folder_display_label(workflow_dir, workflow_slug),
        "modified_at": workflow_dir.stat().st_mtime,
        "files": _workflow_present_files(workflow_dir),
    }


def _walk_bundle_files(bundle_root: Path) -> dict[str, str]:
    """Walk the entire bundle directory and return all readable files as relative-path → content."""
    files: dict[str, str] = {}
    for path in sorted(bundle_root.rglob("*")):
        if not path.is_file():
            continue
        # Skip hidden/system dirs
        rel = path.relative_to(bundle_root)
        parts = rel.parts
        if any(p in _SKIP_DIRS for p in parts):
            continue
        # Never expose auth credentials
        if parts[0] == "auth" and path.name in _SKIP_FILES:
            continue
        key = rel.as_posix()
        suffix = path.suffix.lower()
        # Images in visuals/ are handled separately as base64; skip here
        if suffix in VISUAL_IMAGE_SUFFIXES:
            continue
        if suffix in _TEXT_SUFFIXES or not suffix:
            try:
                files[key] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
    return files


def _bundle_file_keys(bundle_root: Path, bundle_slug: str, workflows_meta: list[dict[str, object]]) -> list[str]:
    return list(_walk_bundle_files(bundle_root).keys())


# ──────────────────────────────────────────────────────────────────────────────
# List / read (API-facing)
# ──────────────────────────────────────────────────────────────────────────────


def list_skill_bundle_summaries() -> list[dict[str, object]]:
    """One entry per bundle directory under the container."""
    container = skill_package_root_dir()
    out: list[dict[str, object]] = []
    for path in sorted(container.iterdir(), key=lambda p: p.name):
        if not path.is_dir():
            continue
        slug = _slug_from_folder_name(path.name)
        if slug is None:
            continue
        workflow_entries = _workflow_package_entries(path)
        if not workflow_entries:
            continue
        workflow_paths = [workflow_path for workflow_path, _summary in workflow_entries]
        workflow_paths.sort(key=lambda wp: wp.stat().st_mtime_ns, reverse=True)
        max_mtime = max(wp.stat().st_mtime for wp in workflow_paths)
        workflows_meta = [_workflow_listing_metadata(wp) for wp in workflow_paths]
        out.append(
            {
                "package_name": slug,
                "modified_at": max_mtime,
                "workflows": workflows_meta,
                "files": _bundle_file_keys(path, slug, workflows_meta),
            }
        )
    out.sort(key=lambda row: float(row["modified_at"]), reverse=True)
    return out


def list_skill_package_summaries() -> list[dict[str, object]]:
    """Backward-compatible alias: returns bundle summaries (not per-workflow rows)."""
    return list_skill_bundle_summaries()


def read_skill_package_bundle_files(bundle_slug: str) -> dict[str, str] | None:
    """Flatten entire bundle tree into relative-path → content (text files + base64 images)."""
    root = bundle_root_dir(bundle_slug)
    if root is None or not root.is_dir():
        return None

    out = _walk_bundle_files(root)
    # Add images from all skill visuals/ dirs as base64
    for wf_path in _workflow_package_dirs(root):
        wf_name = wf_path.name
        prefix = f"{SKILLS_SUBDIR}/{wf_name}/"
        for vk, vv in _read_visual_assets(wf_path).items():
            out[prefix + vk] = vv

    return out or None
