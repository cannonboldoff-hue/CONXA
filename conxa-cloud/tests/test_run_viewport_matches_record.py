"""A run must see the page at the size it was recorded at. The recorder (Python) and the runtime
(Node) each hold the number; this keeps them equal."""
import re
from pathlib import Path

from conxa_compile.recorder import host_view

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_run_viewport_equals_recorder_viewport():
    src = (ROOT / "runtime" / "app" / "page_geometry.js").read_text(encoding="utf-8")
    m = re.search(r"const RUN_VIEWPORT = \{ width: (\d+), height: (\d+) \}", src)
    assert m, "RUN_VIEWPORT not found in runtime/app/page_geometry.js"
    assert {"width": int(m.group(1)), "height": int(m.group(2))} == host_view.RECORD_VIEWPORT
