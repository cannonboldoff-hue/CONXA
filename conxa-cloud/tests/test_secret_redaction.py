"""Secrets never written down (WF-8 secrets check, 2026-11-18).

A form sent by GET put the password and token in the next page's address ("done.html?pw=…&tok=…"); that address was
saved in the recording, the compiled workflow and the logs, and the typed test inputs were saved in clear text."""

from __future__ import annotations

from conxa_core.redact import MASK, mask_secret_inputs, scrub_url, scrub_urls, secret_hash

DONE = "http://localhost:8128/secrets-done.html?dn=Ada&pw=ZzDummyPw%218812&tok=tok_DUMMY_Q7r2x9"


def test_an_address_keeps_everything_but_its_secret_looking_values():
    assert scrub_url(DONE) == "http://localhost:8128/secrets-done.html?dn=Ada&pw=REDACTED&tok=REDACTED"
    assert scrub_url("https://shop.test/list?country_code=US&sort_key=price&q=red+shoes") == \
        "https://shop.test/list?country_code=US&sort_key=price&q=red+shoes"          # nothing secret: byte for byte
    assert scrub_url("https://a.test/cb?code=abc&state=1#access_token=xyz") == "https://a.test/cb?code=REDACTED&state=1#access_token=REDACTED"
    # A value the person typed into a password box, under any name.
    assert scrub_url("https://a.test/?q=Hunter2%21&n=1", [secret_hash("Hunter2!")]) == "https://a.test/?q=REDACTED&n=1"


def test_every_address_in_a_recorded_event_is_scrubbed():
    ev = {"page": {"url": DONE}, "tab": {"url": DONE}, "state": {"after": {"url": DONE}},
          "ancestors": [{"outer_html": f'<a href="{DONE}">done</a>'}], "action": {"value": "Ada"}}
    out = scrub_urls(ev)
    assert "ZzDummyPw" not in str(out) and "tok_DUMMY" not in str(out) and out["action"]["value"] == "Ada"


def test_the_compile_never_carries_a_secret_address_from_an_older_recording():
    from conxa_compile.compiler.build import prepare_compile_events
    from conxa_compile.policy.bundle import get_policy_bundle

    ev = {"action": {"action": "click", "timestamp": "t"}, "target": {"tag": "button", "aria_label": "Back"},
          "selectors": {"css": "#b"}, "page": {"url": DONE}, "tab": {"url": DONE}, "extras": {"sequence": 1}}
    assert "ZzDummyPw" not in str(prepare_compile_events([ev], get_policy_bundle().data))


def test_secret_test_inputs_are_saved_as_a_mask_and_read_back_as_absent(tmp_path, monkeypatch):
    assert mask_secret_inputs({"user_password": "ZzDummyPw!8812", "api_token": "tok_x", "display_name": "Ada"}) == \
        {"user_password": MASK, "api_token": MASK, "display_name": "Ada"}
    from conxa_core.config import settings
    from conxa_core.models.workflow import Workflow
    from conxa_core.storage import workflow_store

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    wf = Workflow(id="wf_secret", slug="wf-secret", name="t", target_url="https://a.test/", last_test_inputs={"user_password": "ZzDummyPw!8812", "display_name": "Ada"})
    workflow_store.save_workflow(wf)
    assert "ZzDummyPw" not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))
    assert workflow_store.get_workflow("wf_secret").last_test_inputs == {"display_name": "Ada"}
