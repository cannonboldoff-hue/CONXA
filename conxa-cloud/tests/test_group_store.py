"""CRUD, per-app auth stamping, and default-group behavior for WorkflowGroup."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from conxa_core.config import settings
from conxa_core.storage import group_store


@pytest.fixture()
def tmp_groups_dir(tmp_path: Path):
    with (
        patch.object(settings, "data_dir", tmp_path),
        patch.object(settings, "database_url", ""),
        patch.object(group_store, "_groups_dir", return_value=tmp_path),
    ):
        yield tmp_path


def test_create_and_get_group(tmp_groups_dir):
    group = group_store.create_group("Sales")
    fetched = group_store.get_group(group.id)
    assert fetched is not None
    assert fetched.name == "Sales"
    assert fetched.apps == []


def test_ensure_default_group_idempotent(tmp_groups_dir):
    first = group_store.ensure_default_group()
    second = group_store.ensure_default_group()
    assert first.id == second.id
    assert len([g for g in group_store.list_groups() if g.name == "Default"]) == 1


def test_add_app_assigns_stable_unique_id(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "Salesforce", "https://sf.com/login", "https://sf.com")
    group = group_store.add_app(group.id, "Salesforce", "https://sf2.com/login", "https://sf2.com")
    ids = [a.id for a in group.apps]
    assert ids[0] == "salesforce"
    assert ids[1] == "salesforce-2"
    assert ids[0] != ids[1]


def test_set_and_clear_group_app_auth(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "GitHub", "https://github.com/login", "https://github.com")
    app_id = group.apps[0].id

    updated = group_store.set_group_app_auth(group.id, app_id, str(tmp_groups_dir / "state.json"))
    assert updated.apps[0].captured_at is not None
    assert updated.apps[0].storage_state_path

    cleared = group_store.clear_group_app_auth(group.id, app_id)
    assert cleared.apps[0].captured_at is None
    assert cleared.apps[0].storage_state_path == ""


def test_remove_app_drops_it_from_the_list(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "GitHub", "https://github.com/login", "https://github.com")
    app_id = group.apps[0].id
    updated = group_store.remove_app(group.id, app_id)
    assert updated.apps == []


def test_delete_group(tmp_groups_dir):
    group = group_store.create_group("Sales")
    assert group_store.delete_group(group.id) is True
    assert group_store.get_group(group.id) is None
    assert group_store.delete_group(group.id) is False


def test_set_group_app_auth_stamps_checked_at(tmp_groups_dir):
    """A fresh capture is inherently verified — checked_at should be set, not None,
    the moment set_group_app_auth runs."""
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "GitHub", "https://github.com/login", "https://github.com")
    app_id = group.apps[0].id
    updated = group_store.set_group_app_auth(group.id, app_id, str(tmp_groups_dir / "state.json"))
    assert updated.apps[0].checked_at is not None
    assert updated.apps[0].checked_at == updated.apps[0].captured_at


def test_set_group_app_checked_records_verdict(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "GitHub", "https://github.com/login", "https://github.com")
    app_id = group.apps[0].id
    group_store.set_group_app_auth(group.id, app_id, str(tmp_groups_dir / "state.json"))

    expired = group_store.set_group_app_checked(group.id, app_id, "expired")
    assert expired.apps[0].last_error
    assert expired.apps[0].checked_at is not None

    ready = group_store.set_group_app_checked(group.id, app_id, "ready")
    assert ready.apps[0].last_error == ""


def test_set_group_app_checked_unverified_is_never_verified(tmp_groups_dir):
    """An inconclusive probe must not stamp checked_at (that is what reads as verified) and
    must neither clear nor invent an expired verdict."""
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "GitHub", "https://github.com/login", "https://github.com")
    app_id = group.apps[0].id
    group_store.set_group_app_auth(group.id, app_id, str(tmp_groups_dir / "state.json"))  # stamps checked_at

    after = group_store.set_group_app_checked(group.id, app_id, "unverified")
    assert after.apps[0].checked_at is None
    assert after.apps[0].check_note
    assert after.apps[0].last_error == ""

    group_store.set_group_app_checked(group.id, app_id, "expired")
    again = group_store.set_group_app_checked(group.id, app_id, "unverified")
    assert again.apps[0].last_error  # still expired: an inconclusive read is no evidence of recovery

    ready = group_store.set_group_app_checked(group.id, app_id, "ready")
    assert ready.apps[0].check_note == ""
    assert ready.apps[0].checked_at is not None


def test_clear_group_app_auth_clears_checked_at(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "GitHub", "https://github.com/login", "https://github.com")
    app_id = group.apps[0].id
    group_store.set_group_app_auth(group.id, app_id, str(tmp_groups_dir / "state.json"))
    cleared = group_store.clear_group_app_auth(group.id, app_id)
    assert cleared.apps[0].checked_at is None


def test_apps_for_workflow_matches_bare_hostnames(tmp_groups_dir):
    """apps_for_workflow must accept bare hostnames (SkillMeta.visited_hosts), not just
    full URLs, so required_apps can be computed from everywhere a workflow actually went."""
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "Salesforce", "https://sf.com/login", "https://sf.com/home")
    group = group_store.add_app(group.id, "Billing", "https://billing.example.com/login", "https://billing.example.com/home")

    # Neither the start URL nor the protected URL touches Billing — only a mid-flow
    # visited host does. A bare hostname (no scheme) must still match.
    matched = group_store.apps_for_workflow(
        group.apps, "https://sf.com/login", "https://sf.com/home", "billing.example.com",
    )
    assert {a.name for a in matched} == {"Salesforce", "Billing"}


def test_apps_for_workflow_ignores_urls_disguised_as_hosts(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "Salesforce", "https://sf.com/login", "https://sf.com/home")
    # A malformed/URL-shaped "hostname" (has a path) must not match anything by accident.
    matched = group_store.apps_for_workflow(group.apps, "sf.com/login")
    assert matched == []


def test_apps_for_workflow_matches_same_site_not_just_exact_hostname(tmp_groups_dir):
    """A workflow that visits drive.google.com must match an app whose login_url is
    accounts.google.com (same site) — not just an app whose login_url is exactly
    drive.google.com. Regression: Github-to-Drive skills built with required_apps=[]
    even though a Google app existed in the group, because the old exact-hostname
    match disagreed with unclaimed_hosts' same-site rule (which raised no warning)."""
    group = group_store.create_group("Enterprise")
    group = group_store.add_app(group.id, "Google", "https://accounts.google.com", "")
    group = group_store.add_app(group.id, "Github", "https://www.github.com/login", "")
    group = group_store.add_app(group.id, "Render", "https://dashboard.render.com/login", "https://dashboard.render.com")

    matched = group_store.apps_for_workflow(group.apps, "drive.google.com", "github.com")
    assert {a.name for a in matched} == {"Google", "Github"}


def test_unclaimed_hosts_warns_only_for_hosts_no_app_covers(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "Render", "https://dashboard.render.com/login", "https://dashboard.render.com")

    hosts = group_store.unclaimed_hosts(
        group.apps,
        "https://dashboard.render.com", "https://dashboard.render.com/",
        "dashboard.render.com",   # the app's own host
        "accounts.render.com",    # same site as the app -> its own sign-in hop, not a new one
        "drive.google.com",       # nothing covers this
        "drive.google.com",       # duplicates collapse
        "",                       # blanks are ignored
    )
    assert hosts == ["drive.google.com"]


def test_unclaimed_hosts_treats_ip_addresses_as_their_own_site(tmp_groups_dir):
    group = group_store.create_group("Sales")
    group = group_store.add_app(group.id, "Local", "http://127.0.0.1:8000/login", "http://127.0.0.1:8000/home")
    # last-two-labels would call 10.0.0.1 and 127.0.0.1 the same site ("0.1") — IPs must match exactly.
    assert group_store.unclaimed_hosts(group.apps, "10.0.0.1") == ["10.0.0.1"]
    assert group_store.unclaimed_hosts(group.apps, "127.0.0.1") == []


def test_unclaimed_hosts_with_no_apps_lists_every_visited_host(tmp_groups_dir):
    group = group_store.create_group("Sales")
    assert group_store.unclaimed_hosts(group.apps, "https://a.example.com", "b.example.org") == ["a.example.com", "b.example.org"]
