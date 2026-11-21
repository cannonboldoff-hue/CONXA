"""AUTH-23 live harness: sign in to a public demo site in a headless browser, then run the real
RecordingSession._perform_learn_auth_sync (desktop, non-embedded path) against it and print every
observation it took plus the outcome. Usage: python auth23_live.py <site> <login_url> <success_url> <out.json>"""
import json
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from conxa_compile import auth_learning
from conxa_compile.recorder import session as S

SITES = {
    "saucedemo": {
        "start": "https://www.saucedemo.com/",
        "user": ("#user-name", "standard_user"),
        "pw": ("#password", "secret_sauce"),
        "submit": "#login-button",
    },
    "parabank": {
        "start": "https://parabank.parasoft.com/parabank/index.htm",
        "user": ("input[name=username]", "john"),
        "pw": ("input[name=password]", "demo"),
        "submit": "input[type=submit][value='Log In']",
    },
}


def main(site, login_url, success_url, out_path):
    cfg = SITES[site]
    observations = []
    real_nav = S._navigate_and_observe_sync

    def logged_nav(page, context, url, **kw):
        obs = real_nav(page, context, url, **kw)
        observations.append({"url": url, **obs})
        return obs

    S._navigate_and_observe_sync = logged_nav
    tmp = Path(tempfile.mkdtemp())
    sess = S.RecordingSession(session_id="auth23-live", data_root=tmp,
                              storage_state_autosave_path=str(Path(out_path).with_suffix(".state.json").resolve()))
    sess.auth_mode = True
    pw = sync_playwright().start()
    try:
        sess._playwright = pw
        sess._browser = pw.chromium.launch(headless=True)
        sess._context = sess._browser.new_context(**S._STEALTH_CONTEXT_OPTIONS)
        sess._page = sess._context.new_page()
        sess.browser_open = True
        p = sess._page
        p.goto(cfg["start"], wait_until="load", timeout=30000)
        p.fill(*cfg["user"])
        p.fill(*cfg["pw"])
        p.click(cfg["submit"])
        p.wait_for_load_state("load")
        p.wait_for_timeout(2000)
        landed = p.url
        definition, reason = sess._perform_learn_auth_sync(login_url, None, success_url)
        result = {
            "site": site, "login_url": login_url, "success_url": success_url, "landed_after_sign_in": landed,
            "learned": definition is not None, "reason": reason, "definition": definition,
            "observations": [
                {k: v for k, v in o.items() if k != "markers"} | {"marker_count": len(o.get("markers") or []),
                 "markers_sample": [m["name"] for m in (o.get("markers") or [])][:15]}
                for o in observations
            ],
        }
        if definition:
            result["verdicts"] = [auth_learning.evaluate(definition, o) for o in observations]
        Path(out_path).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: result[k] for k in ("site", "login_url", "learned", "reason", "landed_after_sign_in")}))
    finally:
        try:
            sess._browser.close()
        finally:
            pw.stop()


if __name__ == "__main__":
    main(*sys.argv[1:5])
