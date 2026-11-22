"""A run must see the page at the width it was recorded at. The recorder (Python) and the runtime
(Node) each hold the number; this keeps them equal. The panel's height limits are held by three files
(Studio's and Execute's browser panels and the runtime's size check); this keeps those equal too."""
import re
from pathlib import Path

from conxa_compile.recorder import host_view

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_run_viewport_equals_recorder_viewport():
    src = (ROOT / "runtime" / "app" / "page_geometry.js").read_text(encoding="utf-8")
    m = re.search(r"const RUN_VIEWPORT = \{ width: (\d+), height: (\d+) \}", src)
    assert m, "RUN_VIEWPORT not found in runtime/app/page_geometry.js"
    assert {"width": int(m.group(1)), "height": int(m.group(2))} == host_view.RECORD_VIEWPORT


def test_panel_height_limits_equal_runtime_limits():
    pattern = r"const MIN_H = (\d+), MAX_H = (\d+);"
    runtime = re.search(pattern, (ROOT / "runtime" / "app" / "page_geometry.js").read_text(encoding="utf-8"))
    panel = re.search(pattern, (ROOT / "conxa-builder" / "electron" / "browser_panel.js").read_text(encoding="utf-8"))
    execute = re.search(pattern, (ROOT / "conxa-execute" / "app" / "electron" / "browser_panel.js").read_text(encoding="utf-8"))
    assert runtime, "MIN_H/MAX_H not found in runtime/app/page_geometry.js"
    assert panel, "MIN_H/MAX_H not found in conxa-builder/electron/browser_panel.js"
    assert execute, "MIN_H/MAX_H not found in conxa-execute/app/electron/browser_panel.js"
    assert runtime.groups() == panel.groups() == execute.groups()
