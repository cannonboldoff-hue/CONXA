"""Run today's entity-binding detection over recorded clicks of a session (EXEC-67 probe)."""
import gzip, json, sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\kiran\Desktop\Conxa\conxa-builder\python")
sys.path.insert(0, r"C:\Users\kiran\Desktop\Conxa\packages\conxa-core")
from conxa_compile.compiler.entity_binding import detect_entity_binding, _row_text_segments, _own_texts  # noqa

sess = Path(r"C:\Users\kiran\.conxa-build-studio-dev\data\sessions") / sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 8
n = 0
for line in open(sess / "events.jsonl", encoding="utf-8"):
    ev = json.loads(line)
    if (ev.get("action") or {}).get("action") != "click":
        continue
    n += 1
    if n > limit:
        break
    h = (ev.get("snapshot") or {}).get("dom_hash")
    dom = None
    if h and (sess / "blobs" / f"{h}.html.gz").exists():
        dom = gzip.open(sess / "blobs" / f"{h}.html.gz", "rt", encoding="utf-8").read()
    anc = ev.get("ancestors") or [{}]
    print(n, repr((ev.get("target") or {}).get("inner_text")), "own", _own_texts(ev),
          "segs", _row_text_segments(str(anc[0].get("outer_html") or ""))[:6], "dom", bool(dom))
    for au in (False, True):
        b = detect_entity_binding(ev, dom_html=dom, address_unique=au)
        print("   address_unique", au, "->", b and b.model_dump(exclude_none=True))
