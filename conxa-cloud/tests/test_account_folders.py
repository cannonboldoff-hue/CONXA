"""AUTH-26: each signed-in account keeps its local work in its own folder."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conxa-builder" / "python"))

from conxa_core.workspace import SIGNED_OUT_KEY, account_key  # noqa: E402
from services import account_switch  # noqa: E402


def test_key_follows_cloud_workspace_rule():
    assert account_key({"org_id": "org_2abc", "user_id": "user_1"}) == "org_2abc"
    assert account_key({"org_id": None, "user_id": "user_1"}) == "personal_user_1"
    assert account_key(None) == SIGNED_OUT_KEY
    assert account_key({"user_id": ""}) == SIGNED_OUT_KEY


def test_key_is_a_safe_short_folder_name():
    key = account_key({"org_id": "org/../x " + "y" * 80})
    assert "/" not in key and ".." not in key and len(key) <= 40
    assert account_key({"org_id": "org/../x " + "y" * 80}) == key  # stable


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CONXA_STUDIO_HOME", str(tmp_path))
    monkeypatch.setenv("CONXA_ACCOUNT_HOME", str(tmp_path / "accounts" / "_signed_out"))
    return tmp_path


def _seed_legacy(home: Path):
    (home / "data" / "workflows").mkdir(parents=True)
    (home / "data" / "workflows" / "wf.json").write_text("{}")
    (home / "sandbox").mkdir()
    (home / "sandbox" / "x").write_text("1")


def test_sync_writes_pointer_and_does_nothing_outside_electron(home, monkeypatch):
    assert account_switch.sync_account({"user_id": "u1"}) == "personal_u1"
    assert json.loads((home / "active_account.json").read_text())["account_key"] == "personal_u1"
    monkeypatch.delenv("CONXA_ACCOUNT_HOME")
    (home / "active_account.json").unlink()
    assert account_switch.sync_account({"user_id": "u1"}) is None
    assert not (home / "active_account.json").exists()


def test_first_account_takes_the_old_shared_folders_and_nothing_is_lost(home):
    _seed_legacy(home)
    account_switch.sync_account({"org_id": "org_a", "user_id": "u1"})
    assert (home / "accounts" / "org_a" / "data" / "workflows" / "wf.json").is_file()
    assert (home / "accounts" / "org_a" / "sandbox" / "x").is_file()
    assert not (home / "data").exists()


def test_second_account_starts_empty(home):
    _seed_legacy(home)
    account_switch.sync_account({"org_id": "org_a", "user_id": "u1"})
    account_switch.sync_account({"org_id": "org_b", "user_id": "u2"})
    assert not (home / "accounts" / "org_b" / "data").exists()
    assert (home / "accounts" / "org_a" / "data" / "workflows" / "wf.json").is_file()


def test_signed_out_never_claims_the_old_folders(home):
    _seed_legacy(home)
    account_switch.sync_account(None)
    assert (home / "data" / "workflows" / "wf.json").is_file()


def test_account_with_its_own_work_does_not_get_overwritten(home):
    _seed_legacy(home)
    own = home / "accounts" / "org_a" / "data"
    own.mkdir(parents=True)
    (own / "mine.json").write_text("{}")
    account_switch.sync_account({"org_id": "org_a", "user_id": "u1"})
    assert (own / "mine.json").is_file()
    assert (home / "data" / "workflows" / "wf.json").is_file()  # left for a person to look at


def test_sandbox_and_bundles_follow_the_account_folder(home):
    from conxa_compile.conxa_runtime import resolve_test_sandbox_dir
    from conxa_core.config import account_root

    assert resolve_test_sandbox_dir() == home / "accounts" / "_signed_out" / "sandbox"
    assert account_root() == home / "accounts" / "_signed_out"
