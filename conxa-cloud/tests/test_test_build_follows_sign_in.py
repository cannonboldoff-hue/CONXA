"""Run test builds a fresh test copy when the group's sign-in setup changed since the last build (WF-1 Leg B, 2026-11-16).

The test itself refuses a pack whose sign-in setup drifted (handlers.protocol._sign_in_setup_drift). The build
decision used only the workflow's edit time, so a person who signed in again was told to "rebuild the skill
package" with no control to do it. Both now use the same rule."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")))

from conxa_core.config import settings  # noqa: E402
from conxa_core.storage.group_store import add_app, create_group, set_group_app_auth  # noqa: E402
from conxa_core.storage.workflow_store import create_workflow  # noqa: E402
from conxa_core.workspace import LOCAL_WORKSPACE_ID, workspace_dir_slug  # noqa: E402
from handlers.test_flow import _sign_in_changed_since_build  # noqa: E402


def _pack(tmp_path, apps):
    d = tmp_path / "skill-packs" / workspace_dir_slug(LOCAL_WORKSPACE_ID)
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.json").write_text(json.dumps({"groups": [{"id": apps[0], "apps": apps[1]}]}), encoding="utf-8")


def test_a_changed_sign_in_setup_means_build_and_an_unchanged_one_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", "")
    group = create_group("Signin", workspace_id=LOCAL_WORKSPACE_ID)
    group = add_app(group.id, "The Internet", "https://the-internet.test/login", "")
    app = group.apps[0]
    set_group_app_auth(group.id, app.id, str(tmp_path / "s.json"))
    workflow = create_workflow("Log out", "https://the-internet.test/secure", group_id=group.id)

    built = {"id": app.id, "login_url": app.login_url, "success_url": app.success_url, "auth_definition": None}
    _pack(tmp_path, (group.id, [built]))
    assert _sign_in_changed_since_build(workflow) is False

    # The person set the success address afterwards (as on the Secure Area leg): the built pack is out of date.
    _pack(tmp_path, (group.id, [{**built, "success_url": "https://the-internet.test/old"}]))
    assert _sign_in_changed_since_build(workflow) is True

    assert _sign_in_changed_since_build(create_workflow("No group", "https://x.test/")) is False
