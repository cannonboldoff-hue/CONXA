"""AUTH-24 (4): the user name typed in a sign-in window is kept only as salted hashes, and surrounding text that
names the account ("Logged in as tomsmith") is dropped from what ships."""

from __future__ import annotations

import json

from conxa_compile import signin_names
from conxa_compile.compiler.selector_filters import drop_personal_target_text
from tests.test_recorder_bridge_js import browser, page  # noqa: F401  (fixtures: a real Chromium)

_FORM = """
<form id="login" onsubmit="return false">
  <input type="text" name="username" id="username">
  <input type="password" name="password" id="password">
  <input type="text" name="otp" id="otp">
  <button type="submit">Login</button>
</form>
<input type="text" id="search">
"""


def test_only_the_user_name_box_is_read_never_a_password_or_secret_box(page):  # noqa: F811
    seen: list[str] = []
    page.expose_binding(signin_names.BINDING, lambda _src, v: seen.append(v))
    page.add_init_script(signin_names.SCRIPT)
    # A real page load (set_content's document.open would drop the script's listeners).
    page.route("https://signin.test/", lambda route: route.fulfill(body=_FORM, content_type="text/html"))
    page.goto("https://signin.test/")
    page.fill("#username", "tomsmith")
    page.fill("#password", "SuperSecretPassword!")
    page.fill("#otp", "123456")
    page.fill("#search", "weekly report")  # not in a sign-in form, no user-name markup
    page.click("button")
    page.wait_for_timeout(200)
    assert seen and set(seen) == {"tomsmith"}

    # A page with no form (a script-driven sign-in): still only the box before the password box.
    seen.clear()
    page.route("https://signin.test/app", lambda route: route.fulfill(
        body=_FORM.replace("<form", "<div").replace("</form>", "</div>"), content_type="text/html"))
    page.goto("https://signin.test/app")
    page.fill("#username", "tomsmith")
    page.fill("#password", "SuperSecretPassword!")
    page.fill("#search", "weekly report")
    page.focus("#username")
    page.wait_for_timeout(200)
    assert seen == ["tomsmith"]


def test_the_names_file_holds_hashes_only_and_a_signed_in_page_text_is_dropped(tmp_path):
    folder = tmp_path / "groups" / "g1" / "auth"
    folder.mkdir(parents=True)
    signin_names.remember(folder, "TomSmith")
    raw = (folder / signin_names.FILE).read_text(encoding="utf-8")
    assert "tomsmith" not in raw.lower()

    names = signin_names.load(tmp_path)
    ev = {"action": {"action": "click"}, "target": {"text": "Logout"},
          "context": {"siblings": ["Logged in as tomsmith", "Logout", "Welcome to the Secure Area"],
                      "parent": "Hi, TOMSMITH!", "form_context": "Secure Area"}}
    out = drop_personal_target_text([ev], names)[0]
    assert out["context"]["siblings"] == ["Logout", "Welcome to the Secure Area"]  # other text kept
    assert out["context"]["parent"] == "" and out["context"]["form_context"] == "Secure Area"
    assert out["target"] == {"text": "Logout"}
    # Whole words only: a longer word that merely contains the name stays.
    assert not signin_names.mentions("tomsmithson's report", names)
    assert drop_personal_target_text([ev], [])[0] is ev  # no names: nothing changes


def test_an_email_user_name_drops_its_name_pieces_not_its_mail_host(tmp_path):
    folder = tmp_path / "groups" / "g1" / "auth"
    folder.mkdir(parents=True)
    signin_names.remember(folder, "tom.smith@acme-mail.com")
    names = signin_names.load(tmp_path)
    assert signin_names.mentions("Signed in: Tom Smith", names)
    assert not signin_names.mentions("Pay with acme-mail", names)


def test_nothing_raw_reaches_the_built_package(tmp_path, monkeypatch):
    from conxa_core.config import settings
    from conxa_compile.skill_package_builder_saved_skill import _build_workflow_from_saved_skill

    data = tmp_path / "data"
    folder = data / "groups" / "g1" / "auth"
    folder.mkdir(parents=True)
    signin_names.remember(folder, "tomsmith")
    rec = signin_names.read_state(folder / signin_names.FILE)
    monkeypatch.setattr(settings, "data_dir", data)

    saved_skill = {
        "meta": {"id": "skill_x", "title": "Secure area logout"},
        "skills": [{"steps": [
            {"action": {"action": "navigate", "url": "https://the-internet.herokuapp.com/secure"}},
            {"action": "click", "intent": "click_logout", "target": {"primary_selector": 'text="Logout"'},
             "signals": {"context": {"siblings": ["Logged in as tomsmith", "Secure Area"], "parent": "div.example"}}},
        ]}],
    }
    bundle = tmp_path / "bundle"
    _build_workflow_from_saved_skill(bundle_root=bundle, workflow_slug="logout", saved_skill=saved_skill)

    recovery = json.loads((bundle / "skills" / "logout" / "recovery.json").read_text(encoding="utf-8"))
    entry = next(e for e in recovery["steps"] if e["intent"] == "click_logout")
    assert entry["recorded_context"]["siblings"] == ["Secure Area"]
    shipped = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in bundle.rglob("*") if p.is_file()).lower()
    assert "tomsmith" not in shipped and rec["salt"] not in shipped
    assert not any(h in shipped for h in rec["hashes"])


def test_the_sign_in_window_gets_only_the_name_capture_and_saves_hashes_beside_its_sign_in(tmp_path):
    from conxa_compile.recorder.session import RecordingSession

    class _Page:
        def __init__(self):
            self.bindings, self.scripts = [], []

        def on(self, *_a):
            pass

        def expose_binding(self, name, fn):
            self.bindings.append((name, fn))

        def add_init_script(self, script):
            self.scripts.append(script)

    auth = tmp_path / "groups" / "g1" / "auth"
    auth.mkdir(parents=True)
    sess = RecordingSession(session_id="signin-capture", auth_mode=True,
                            storage_state_autosave_path=str(auth / "app_1.pending.json"))
    page = _Page()
    sess._adopt_run_page_sync(page)
    assert [n for n, _ in page.bindings] == [signin_names.BINDING]  # no recording bridge in a sign-in
    assert page.scripts == [signin_names.SCRIPT]
    page.bindings[0][1]({}, "tomsmith")
    assert signin_names.mentions("Logged in as tomsmith", signin_names.load(tmp_path))
    assert "tomsmith" not in (auth / signin_names.FILE).read_text(encoding="utf-8")
    assert not sess.binding_errors
