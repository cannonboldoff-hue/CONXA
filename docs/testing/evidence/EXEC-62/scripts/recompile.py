"""EXEC-62 measurement: recompile the 2026-11-15 "Buy Now" recording with today's compiler.

No LLM: the model router answers None (second opinion / vision anchor off), so every field
written here is code-only. Writes execution.json for the runtime to replay against ?v=t2.
Usage: python recompile.py <session_dir> <out_dir>
"""
import json, shutil, sys, tempfile
from pathlib import Path
from unittest.mock import patch

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

from conxa_core.config import settings

data_dir = Path(tempfile.mkdtemp())
shutil.copytree(src, data_dir / "sessions" / src.name)


class _NoModel:
    pool = (object(),)
    def route_text(self, *a, **k): return None
    def route_vision(self, *a, **k): return None
    def stats(self): return {}


with patch.object(settings, "data_dir", data_dir), patch("conxa_core.llm._router", _NoModel()):
    from conxa_compile.compiler.build import compile_skill_package
    from conxa_compile.pipeline.run import run_pipeline, pipeline_drops
    from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill

    raw = [json.loads(l) for l in (src / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    norm = run_pipeline(raw)
    pkg = compile_skill_package(norm, skill_id=f"skill_{src.name}", source_session_id=src.name,
                                title="Recovery fixture", version=1, pipeline_drops=pipeline_drops(raw, norm))
    doc = pkg.model_dump(mode="json")
    (out / "skill_doc.json").write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    _build_workflow_from_saved_skill(bundle_root=out, workflow_slug="recovery-fixture-exec62", saved_skill=doc,
                                     on_warning=lambda m: print("WARN", m))

shutil.rmtree(data_dir, ignore_errors=True)
ex_path = next(out.rglob("execution.json"))
print("pack files:", sorted(str(p.relative_to(out)) for p in ex_path.parent.rglob("*") if p.is_file()))
execution = json.loads(ex_path.read_text(encoding="utf-8"))

for i, s in enumerate(execution):
    ib = s.get("identity_bundle") or {}
    print(i, s.get("type"), "destructive=", s.get("destructive"), "ib.destructive=", ib.get("destructive"),
          "entity_binding=", s.get("entity_binding"))
    for sig in ib.get("signals") or []:
        print("   ", sig.get("engine"), sig.get("durability"), sig.get("selector"))
