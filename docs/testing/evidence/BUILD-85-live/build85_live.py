"""BUILD-85 live harness: run the REAL recorder (RecordingSession.start -> pump loop -> bridge.js -> stop) against
the OrangeHRM public demo, headless, and drive the page like a person over CDP from a second Playwright client
(trusted mouse/keyboard input, so the bridge records it exactly as it would a real hand).

Two passes on Admin > User Management > Username filter:
  natural: click the box, type "Admin", press Search.            -> caption read at the moment of the step
  late:    the "Username" caption is emptied before the box is used and drawn back 1.5 s later (a late-rendered
           form), then click + type.                              -> caption must arrive through _lateLabel + amend

Usage (from repo root):
  PYTHONPATH="conxa-builder/python;packages/conxa-core" LLM_FREE_API_KEYS=x python build85_live.py <natural|late> <out.json> [delay_ms]
Public demo login only (Admin / admin123)."""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import BrowserType, sync_playwright

from conxa_compile.recorder import session as S

BASE = "https://opensource-demo.orangehrmlive.com"
LOGIN = BASE + "/web/index.php/auth/login"
USERS = BASE + "/web/index.php/admin/viewSystemUsers"
PORT = 9333
DELAY_MS = int(sys.argv[3]) if len(sys.argv) > 3 else 1500  # late mode: when the caption is drawn back
USERNAME_BOX = "div.oxd-input-group:has(label:text-is('Username')) input"

_real_launch = BrowserType.launch


def _headless_launch(self, *a, **kw):
    kw["headless"] = True
    kw["args"] = list(kw.get("args") or []) + [f"--remote-debugging-port={PORT}"]
    return _real_launch(self, *a, **kw)


def sign_in(state_path: Path) -> None:
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        p = b.new_page()
        p.goto(LOGIN, wait_until="load", timeout=60000)
        p.fill("input[name=username]", "Admin")
        p.fill("input[name=password]", "admin123")
        p.click("button[type=submit]")
        p.wait_for_url("**/dashboard/**", timeout=60000)
        p.context.storage_state(path=str(state_path))
        b.close()


def drive(mode: str) -> None:
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        page = next(pg for c in b.contexts for pg in c.pages if "orangehrm" in pg.url)
        page.wait_for_selector(USERNAME_BOX, timeout=60000)
        page.wait_for_timeout(1500)
        box = page.locator(USERNAME_BOX).element_handle()
        if mode == "late":
            # The caption is taken out of the page and put back DELAY_MS later, as a form whose captions draw late.
            print("caption removed:", page.evaluate("""(ms) => {
              const lab = [...document.querySelectorAll('label')].find(l => l.textContent.trim() === 'Username');
              const home = lab.parentElement;
              lab.remove();
              setTimeout(() => home.prepend(lab), ms);
              return !lab.isConnected;
            }""", DELAY_MS))
        box.click()
        print("captions on page right after the click:",
              page.evaluate("() => [...document.querySelectorAll('label')].map(l => l.textContent.trim())"))
        page.keyboard.type("Admin", delay=60)
        page.wait_for_timeout(2500)  # the late caption is back by now; let the settle + amend arrive
        page.click("button[type=submit]")
        page.wait_for_timeout(4000)
        # disconnect only; the recorder owns the browser


def main(mode: str, out_path: str) -> None:
    tmp = Path(tempfile.mkdtemp())
    state = tmp / "state.json"
    sign_in(state)
    BrowserType.launch = _headless_launch
    sess = S.RecordingSession(session_id=f"build85-{mode}", data_root=tmp, start_url=USERS,
                              storage_state_path=str(state))
    # Log what the page sent: each step's caption on arrival, and each late caption sent as an amendment.
    arrivals = []
    real_consume, real_amend = sess._consume_payload_sync, sess._apply_amend_sync

    def consume(payload, *a, **kw):
        if not payload.get("_amend") and payload.get("uid"):
            arrivals.append({"uid": payload["uid"], "action": (payload.get("action") or {}).get("action"),
                             "label_on_arrival": (payload.get("target") or {}).get("label_text")})
        return real_consume(payload, *a, **kw)

    def amend(payload):
        if payload.get("target_label_text"):
            arrivals.append({"amend": payload["_amend"], "late_label": payload["target_label_text"]})
        return real_amend(payload)

    sess._consume_payload_sync, sess._apply_amend_sync = consume, amend
    asyncio.run(sess.start())
    try:
        drive(mode)
    finally:
        asyncio.run(sess.stop())
    events = sess.snapshot_events()
    rows = [{
        "action": (e.get("action") or {}).get("action"),
        "value": (e.get("action") or {}).get("value"),
        "url": (e.get("page") or {}).get("url"),
        "label_text": (e.get("target") or {}).get("label_text"),
        "target_name": (e.get("target") or {}).get("name"),
        "tag": (e.get("target") or {}).get("tag"),
        "uid": (e.get("extras") or {}).get("uid"),
    } for e in events]
    session_dir = tmp / "sessions" / sess.session_id
    out = Path(out_path)
    shutil.copy(session_dir / "events.jsonl", out.with_suffix(".events.jsonl"))
    out.write_text(json.dumps({"mode": mode, "binding_errors": sess.binding_errors, "arrivals": arrivals,
                               "steps": rows}, indent=2), encoding="utf-8")
    print(json.dumps({"mode": mode, "arrivals": arrivals, "steps": rows}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
