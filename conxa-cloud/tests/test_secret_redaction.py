"""Secrets never written down (WF-8 secrets check, 2026-11-18).

A form sent by GET put the password and token in the next page's address ("done.html?pw=…&tok=…"); that address was
saved in the recording, the compiled workflow and the logs, and the typed test inputs were saved in clear text."""

from __future__ import annotations

import json

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


def _compile_opening(url: str):
    import shutil
    from contextlib import ExitStack

    from conxa_compile.compiler.build import compile_skill_package
    from conxa_compile.editor.workflow_mutations import reconcile_inputs_with_step_values
    from conxa_compile.pipeline.run import run_pipeline
    from tests.test_phases import _compile_with_vision_mocks, _minimal_click_event

    ev = _minimal_click_event()
    ev["page"] = {**(ev.get("page") or {}), "url": url}
    ev["tab"] = {**(ev.get("tab") or {}), "url": url}
    evs = run_pipeline([scrub_urls(ev)])          # what the recorder writes down
    data_dir, *patchers = _compile_with_vision_mocks("sess-secret-link", evs)
    try:
        with ExitStack() as stack:
            for p in patchers:
                stack.enter_context(p)
            pkg = compile_skill_package(evs, skill_id="s", source_session_id="sess-secret-link", title="t", version=1)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    doc, _ = reconcile_inputs_with_step_values(pkg.model_dump(mode="json"))
    return doc


def test_a_hidden_address_value_becomes_a_secret_input_the_person_fills_in():
    """BUILD-110 (9): an invite link (?token=…, ?code=…) compiled with the literal REDACTED, so the workflow could
    never open it. The value is now a run input whose name reads as secret (hidden on the test card, never saved)."""
    from conxa_core.redact import is_secret_input

    doc = _compile_opening("https://app.test/invite?token=tok_DUMMY_Q7r2x9&code=ZzCode1&page=2")
    nav = doc["skills"][0]["steps"][0]
    assert nav["action"] == "navigate"
    assert nav["url"] == "https://app.test/invite?token={{token}}&code={{secret_code}}&page=2"
    ids = {i["id"] for i in doc["inputs"]}
    assert {"token", "secret_code"} <= ids and all(is_secret_input(i) for i in ("token", "secret_code"))
    text = json.dumps(doc)
    assert "tok_DUMMY" not in text and "ZzCode1" not in text
    assert "REDACTED" not in json.dumps(nav.get("validation"))   # (the step's tab still notes where it was recorded)
    for s in doc["skills"][0]["steps"]:   # no address check carries the placeholder as literal text to match
        for a in (s.get("validation") or {}).get("assertions") or []:
            assert "{{" not in str(a.get("target") or "")


def test_a_plain_address_is_compiled_unchanged():
    doc = _compile_opening("https://app.test/list?page=2&sort=name")
    assert doc["skills"][0]["steps"][0]["url"] == "https://app.test/list?page=2&sort=name"
    assert not any(str(i.get("id")).startswith("secret_") for i in doc.get("inputs") or [])
