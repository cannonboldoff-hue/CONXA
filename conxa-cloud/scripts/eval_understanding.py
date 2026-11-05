"""Compare the understanding call across prompt versions on recordings already on disk.

    # what the OLD prompt decided (reads each session's saved understanding.json, no model call)
    python eval_understanding.py baseline --out old.json
    # what the CURRENT prompt decides, through any OpenAI-compatible endpoint
    EVAL_BASE_URL=https://api.groq.com/openai/v1 EVAL_API_KEY=... EVAL_MODEL=... \
        python eval_understanding.py run --out new.json
    python eval_understanding.py diff old.json new.json
    # how often the check dropped / retried / fell back, per prompt version (reads understanding.json, no model call)
    python eval_understanding.py rates -n 50

Run from the repo root with PYTHONPATH=conxa-builder/python:packages/conxa-core and LLM_FREE_API_KEYS=x
(importing conxa_core.config needs one provider key set; it is never used here). Sessions come from
``<data_dir>/sessions/*/merged_timeline.json`` (``--sessions-dir`` to point elsewhere, ``-n`` for how many).
What is compared per recording: goal, each candidate's verdict, the questions (kind, row, ref), how many
points the model settled itself, its grade (ok / weak / unusable) and an input-size estimate. It judges
nothing: read the diff. ``rates`` totals the validation_report that understanding.json now carries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from conxa_compile.conversation import timeline_understanding as tu


def sessions(root: Path, n: int) -> list[Path]:
    found = sorted(root.glob("*/merged_timeline.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.parent for p in found[:n]]


def flat_report(und: dict[str, Any]) -> dict[str, int]:
    """``{"stage.rule": count}`` from an understanding's validation_report (empty for files written before it existed)."""
    return {f"{stage}.{rule}": slot["count"] for stage, rules in (und.get("validation_report") or {}).items()
            for rule, slot in rules.items()}


def summarize(und: dict[str, Any], payload_chars: int = 0) -> dict[str, Any]:
    return {
        "source": und.get("source"), "goal": und.get("goal"), "retried": bool(und.get("retried")),
        "grade": und.get("grade"), "prompt_version": und.get("prompt_version"), "drops": flat_report(und),
        "verdicts": {c["ref"]: c.get("verdict") for c in und.get("candidates") or []},
        "questions": sorted(f"{q.get('kind')}@{q.get('row')}:{q.get('ref') or ''}" for q in und.get("questions") or []),
        "decided": len(und.get("decided") or []), "in_tokens": payload_chars // 4,
    }


def _merged(session: Path) -> list[dict[str, Any]]:
    data = json.loads((session / "merged_timeline.json").read_text(encoding="utf-8"))
    return data.get("rows", data) if isinstance(data, dict) else data


def baseline(session: Path) -> dict[str, Any] | None:
    try:
        return summarize(json.loads((session / "understanding.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def endpoint_call(task: str, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any] | None:
    from conxa_core.llm.client import _openai_messages_for_task  # noqa: PLC0415 -- dev tool, private on purpose

    body = {"model": os.environ["EVAL_MODEL"], "temperature": 0.0, "max_tokens": 16384,
            "messages": _openai_messages_for_task(task, {"input": payload}),
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request(os.environ["EVAL_BASE_URL"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["EVAL_API_KEY"]})
    with urllib.request.urlopen(req, timeout=timeout_ms / 1000) as resp:
        text = json.loads(resp.read())["choices"][0]["message"]["content"] or ""
    try:
        out = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
        return out if isinstance(out, dict) else {"text": text}
    except ValueError:
        return {"text": text}          # a cut-off or non-JSON reply, as the router reports it


def run(session: Path) -> dict[str, Any]:
    merged = _merged(session)
    und = tu.understand_timeline(merged, "", "", call=endpoint_call)
    cands = tu.candidates_from_timeline(merged)
    return summarize(und, len(json.dumps(tu.build_input(merged, "", "", cands), separators=(",", ":"))))


def diff(a: dict[str, Any], b: dict[str, Any]) -> int:
    changed = 0
    for sid in sorted(set(a) | set(b)):
        x, y = a.get(sid), b.get(sid)
        if x is None or y is None:
            print(f"{sid[:8]}  only in {'old' if y is None else 'new'}")
            continue
        lines = []
        for ref in sorted(set(x["verdicts"]) | set(y["verdicts"])):
            if x["verdicts"].get(ref) != y["verdicts"].get(ref):
                lines.append(f"  verdict {ref}: {x['verdicts'].get(ref)} -> {y['verdicts'].get(ref)}")
        gone, new = sorted(set(x["questions"]) - set(y["questions"])), sorted(set(y["questions"]) - set(x["questions"]))
        lines += [f"  question dropped: {q}" for q in gone] + [f"  question added:   {q}" for q in new]
        if x["source"] != y["source"]:
            lines.append(f"  source: {x['source']} -> {y['source']}")
        if x.get("grade") != y.get("grade"):
            lines.append(f"  grade: {x.get('grade')} -> {y.get('grade')}")
        if x["decided"] != y["decided"]:
            lines.append(f"  settled by model: {x['decided']} -> {y['decided']}")
        print(f"{sid[:8]}  {'same' if not lines else 'changed'}  questions {len(x['questions'])} -> {len(y['questions'])}")
        print("\n".join(lines))
        changed += bool(lines)
    print(f"\n{changed} of {len(set(a) | set(b))} recordings differ")
    return 0


def rates(root: Path, n: int) -> None:
    """Per prompt version over the last ``n`` understanding.json files: how often the model's answer needed
    help. Files written before the validation_report existed group under ``unknown`` and count no drops."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(root.glob("*/understanding.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:n]:
        try:
            und = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if und.get("source") != "none":
            groups.setdefault(str(und.get("prompt_version", "unknown")), []).append(und)
    for version, items in sorted(groups.items()):
        k = len(items)
        share = lambda pred: f"{100 * sum(1 for u in items if pred(u)) // k}%"      # noqa: E731
        print(f"prompt v{version}: {k} recordings   fell back {share(lambda u: u.get('source') == 'fallback')}   "
              f"asked twice {share(lambda u: 'retried_because' in u)}   ended weak {share(lambda u: u.get('grade') == 'weak')}")
        total = Counter()
        for u in items:
            total.update(flat_report(u))
        for stage in ("shape", "reference", "override", "backfill", "safety"):
            n_stage = sum(c for rule, c in total.items() if rule.startswith(stage + "."))
            print(f"   {stage:<10}{n_stage / k:5.2f} per recording")
        print("   top rules: " + (", ".join(f"{r} x{c}" for r, c in total.most_common(5)) or "none"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["baseline", "run", "diff", "rates"])
    ap.add_argument("files", nargs="*", help="diff: old.json new.json")
    ap.add_argument("--out", help="write the summary here")
    ap.add_argument("-n", type=int, default=8, help="how many recent recordings")
    ap.add_argument("--sessions-dir", type=Path)
    args = ap.parse_args()
    if args.cmd == "diff":
        if len(args.files) != 2:
            ap.error("diff needs old.json new.json")
        return diff(*(json.loads(Path(f).read_text(encoding="utf-8")) for f in args.files))
    if args.cmd == "run" and not all(os.environ.get(k) for k in ("EVAL_BASE_URL", "EVAL_API_KEY", "EVAL_MODEL")):
        ap.error("run needs EVAL_BASE_URL, EVAL_API_KEY and EVAL_MODEL")
    from conxa_core.config import settings

    root = args.sessions_dir or settings.data_dir / "sessions"
    if args.cmd == "rates":
        rates(root, args.n)
        return 0
    out: dict[str, Any] = {}
    for s in sessions(root, args.n):
        row = baseline(s) if args.cmd == "baseline" else run(s)
        if row:
            out[s.name] = row
            print(f"{s.name[:8]}  {row['source']:8} questions={len(row['questions'])} decided={row['decided']} "
                  f"values={len(row['verdicts'])} in~{row['in_tokens']}")
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
