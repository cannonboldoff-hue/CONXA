"""W5-j: an address the person typed that only ever showed the browser's error page is not a step."""

from __future__ import annotations

import json

from conxa_compile.compiler.build import _is_failed_navigation, prepare_compile_events
from conxa_compile.policy.bundle import get_policy_bundle


def _nav(seq, to_url, page_url, from_url="about:blank"):
    return {"action": {"action": "manual_navigate", "value": json.dumps({"from_url": from_url, "to_url": to_url, "cause": "user"})},
            "page": {"url": page_url, "title": ""}, "extras": {"sequence": seq}, "target": {}, "timing": {"timestamp": seq}}


def test_a_navigation_that_ended_on_the_browsers_error_page_is_a_failed_navigation():
    assert _is_failed_navigation(_nav(1, "https://dive.google.com/", "chrome-error://chromewebdata/"))
    assert not _is_failed_navigation(_nav(2, "https://drive.google.com/", "https://drive.google.com/drive/"))


def test_the_misspelt_address_is_dropped_and_the_correction_kept_and_ledgered():
    events = [
        _nav(1, "https://dive.google.com/", "chrome-error://chromewebdata/"),
        _nav(2, "https://drive.google.com/", "https://drive.google.com/drive/", from_url="https://dive.google.com/"),
    ]
    ledger: list[dict] = []
    kept = prepare_compile_events(events, get_policy_bundle().data, ledger)
    urls = [json.loads(e["action"]["value"])["to_url"] for e in kept if e["action"]["action"] == "manual_navigate"]
    assert "https://dive.google.com/" not in urls
    assert any(row.get("pass") == "failed_navigation" or row.get("reason") == "failed_navigation" or "failed_navigation" in json.dumps(row) for row in ledger)
