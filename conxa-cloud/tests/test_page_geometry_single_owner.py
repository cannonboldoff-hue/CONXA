"""A page's size is one shared setting inside Chromium, and every program that writes it overwrites the
others. The embedded test browser stayed zoomed and clipped because Playwright's clipped screenshot
rewrote the size the host panel had set. Two rules keep that from coming back:

  1. Only the two host panels (Build Studio's and Execute's browser_panel.js) set or clear the page size.
  2. Every runtime screenshot goes through runtime/app/page_geometry.js (raw CDP, no clip).

See docs/TRD.md ("Page size") and runtime/app/page_geometry.js."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PANELS = {
    ROOT / "conxa-builder" / "electron" / "browser_panel.js",
    ROOT / "conxa-execute" / "app" / "electron" / "browser_panel.js",
}
SIZE_WRITERS = re.compile(
    r"setDeviceMetricsOverride|clearDeviceMetricsOverride|setViewportSize|set_viewport_size|enableDeviceEmulation"
)
SCREENSHOT = re.compile(r"\.screenshot\s*\(")
SKIP_DIRS = {"node_modules", "test", "tests", "renderer", "dist", "__pycache__"}


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(^|[^:\"'])//.*$", r"\1", src, flags=re.M)


def _files(base: Path, patterns):
    for pattern in patterns:
        for p in base.glob(pattern):
            if p.is_file() and not (set(p.relative_to(base).parts) & SKIP_DIRS):
                yield p


def test_only_the_host_panels_write_the_page_size():
    scanned = [
        *_files(ROOT / "runtime" / "app", ["**/*.js"]),
        *_files(ROOT / "conxa-builder" / "electron", ["*.js"]),
        *_files(ROOT / "conxa-execute" / "app" / "electron", ["*.js"]),
        *_files(ROOT / "conxa-builder" / "python", ["**/*.py", "**/*.js"]),
    ]
    assert any(p.name == "run.js" for p in scanned), "scan found nothing: the paths moved"
    offenders = []
    for p in scanned:
        if p in PANELS:
            continue
        src = _strip_comments(p.read_text(encoding="utf-8"))
        if p.suffix == ".py":  # '#' comments
            src = re.sub(r"#.*$", "", src, flags=re.M)
        for m in SIZE_WRITERS.finditer(src):
            offenders.append(f"{p.relative_to(ROOT)}: {m.group(0)}")
    assert not offenders, (
        "only a host panel may set the page size (ask it with the 'ensure_geometry' control op instead):\n  "
        + "\n  ".join(offenders)
    )


def test_every_runtime_screenshot_goes_through_page_geometry():
    offenders = []
    for p in _files(ROOT / "runtime" / "app", ["**/*.js"]):
        if p.name == "page_geometry.js":
            continue
        for _ in SCREENSHOT.finditer(_strip_comments(p.read_text(encoding="utf-8"))):
            offenders.append(f"{p.relative_to(ROOT)}: .screenshot(")
    assert not offenders, (
        "page.screenshot() sends a clip, which resets the host panel's page size; use "
        "page_geometry.capture():\n  " + "\n  ".join(offenders)
    )
