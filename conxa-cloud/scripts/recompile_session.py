"""Recompile a recorded session and dump the compile report.

Usage: python scripts/recompile_session.py <session_id> [--out DIR]

Calls the compiler the way Build Studio's compile does (same pipeline drops, the workflow's task description when the
session belongs to one) and writes compile_report.json / compiled_package.json to DIR (default: a folder under the
system temp directory), never into the recording's own folder.

Without the cloud connection (SKILL_ALLOW_NO_PROVIDERS=1) the model-written parts are rules-only; the step addresses
and identity are the same either way.

The compiler's LLM calls go through the metered cloud connection that Build Studio installs. This script
installs the same one, so it needs a signed-in Studio token:
  CONXA_TOKEN       the token Studio sends to the cloud (required)
  CONXA_CLOUD_API   the cloud address (default http://127.0.0.1:8000)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT.parent / "conxa-builder" / "python"))  # services.llm_proxy_client


def main() -> int:
    args = [a for a in sys.argv[1:]]
    out_dir_arg = ""
    if "--out" in args:
        i = args.index("--out")
        out_dir_arg = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if len(args) != 1:
        print("Usage: python scripts/recompile_session.py <session_id> [--out DIR]")
        return 1

    session_id = args[0]

    token = os.environ.get("CONXA_TOKEN", "").strip()
    offline = os.environ.get("SKILL_ALLOW_NO_PROVIDERS", "").strip() in ("1", "true", "True")
    if not token and not offline:
        print("CONXA_TOKEN is not set. The compiler needs Build Studio's cloud connection for its LLM calls: "
              "set CONXA_TOKEN to a signed-in Studio token (and CONXA_CLOUD_API if the cloud is not on 127.0.0.1:8000).")
        return 1
    if token:
        from conxa_core import llm as core_llm
        from services.llm_proxy_client import LLMProxyClient

        core_llm.set_router(LLMProxyClient(
            os.environ.get("CONXA_CLOUD_API", "http://127.0.0.1:8000"),
            token_provider=lambda: token,
            client_header=os.environ.get("CONXA_PROXY_CLIENT", "build-studio"),
            usage_class="compile",
        ))

    from conxa_compile.compiler.build import compile_skill_package
    from conxa_core.config import settings
    from conxa_compile.pipeline.run import pipeline_drops, run_pipeline

    session_dir = settings.data_dir / "sessions" / session_id
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        print(f"events.jsonl not found at {events_path}")
        return 1

    raw_events: list[dict] = []
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_events.append(json.loads(line))

    print(f"Loaded {len(raw_events)} events from {session_id}")

    try:
        normalized = run_pipeline(raw_events)
    except Exception as exc:
        print(f"Pipeline normalization failed: {exc}")
        return 1

    print(f"Pipeline produced {len(normalized)} normalized events")

    # What Studio passes besides the events: the workflow's description (when this session belongs to one).
    task_text = ""
    try:
        from conxa_core.storage.workflow_store import list_workflows

        owner = next((w for w in list_workflows() if getattr(w, "session_id", "") == session_id), None)
        task_text = (owner.task_description or "").strip() if owner is not None else ""
    except Exception:  # noqa: BLE001 — a recompile without the description is still useful
        task_text = ""

    try:
        package = compile_skill_package(
            normalized,
            skill_id=f"skill_{session_id}",
            source_session_id=session_id,
            title=f"Recompiled {session_id[:8]}",
            version=1,
            **({"task_description": task_text} if task_text else {}),
            pipeline_drops=pipeline_drops(raw_events, normalized),
        )
    except Exception as exc:
        print(f"Compile failed: {exc}")
        import traceback
        traceback.print_exc()
        return 1

    steps = package.skills[0].steps
    print(f"\nCompile produced {len(steps)} steps\n")

    # Print per-step summary
    for i, step in enumerate(steps):
        target = step.target or {}
        sel = target.get("primary_selector") or ""
        conf = target.get("selector_confidence") or 0.0
        source = target.get("selector_source") or "heuristic"
        action = step.action if isinstance(step.action, str) else step.action.get("action", "")
        binding = step.input_binding or "-"
        value_str = str(step.value)[:30] if step.value is not None else "-"
        print(f"  [{i:2d}] {action:<20} sel='{sel[:60]:<60}' conf={conf:.2f} src={source:<10} binding={binding:<25} value={value_str}")

    # Print compile report summary
    report = package.compile_report
    if report:
        print(f"\nCompile report:")
        print(f"  status: {report.get('status')}")
        print(f"  min_confidence: {report.get('min_confidence')}")
        print(f"  steps_total: {report.get('steps_total')}")
        print(f"  steps_with_warnings: {report.get('steps_with_warnings')}")
        router_stats = report.get("llm_router_stats", {})
        if router_stats:
            print(f"  llm_router: pool_size={router_stats.get('pool_size')}")
            for entry in router_stats.get("entries") or []:
                print(f"    {entry['provider']}: sent={entry['requests_sent']} 429={entry['requests_429']} cooled={entry['cooled']}")
        else:
            print(f"  llm_router: not active (no providers configured)")

    # Save full report for inspection, outside the recording's own folder
    import tempfile

    out_dir = Path(out_dir_arg) if out_dir_arg else Path(tempfile.gettempdir()) / "conxa_recompile" / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "compile_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report written to {out_path}")

    # Save the package too
    pkg_path = out_dir / "compiled_package.json"
    with open(pkg_path, "w", encoding="utf-8") as f:
        json.dump(package.model_dump(mode="json"), f, indent=2)
    print(f"Full package written to {pkg_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
