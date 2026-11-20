"""Both app-layer builds take the engine's capability list from runtime/app/version.json.

A hand-copied list in a build script once dropped `loop_rows`, so every table-loop test in the
local lane was refused as "needs a newer Conxa engine" (WF-11).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDS = [ROOT / "scripts" / "build-app-local.ps1", ROOT / ".github" / "workflows" / "build-runtime-app.yml"]


def test_builds_read_capabilities_from_source_version_json():
    caps = json.loads((ROOT / "runtime" / "app" / "version.json").read_text(encoding="utf-8"))["capabilities"]
    assert "loop_rows" in caps
    for build in BUILDS:
        text = build.read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.strip().startswith("capabilities"))
        assert "version.json" in line, f"{build.name} must read capabilities from runtime/app/version.json"
        assert not any(f'"{c}"' in line for c in caps), f"{build.name} hard-codes a capability list"
