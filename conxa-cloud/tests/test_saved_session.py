"""Studio's saved sign-ins are locked with Windows' protection (SG-27): one read/write helper, a startup
migration that removes a plain copy only after the locked copy reads back identical, and a file that
cannot be opened here reads as "sign in again"."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

_PY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")
sys.path.insert(0, os.path.abspath(_PY_DIR))

import pytest  # noqa: E402

from conxa_compile import saved_session as ss  # noqa: E402

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the lock is Windows' own protection")

STATE = {"cookies": [{"name": "sid", "value": "SECRET-COOKIE-123", "domain": "x.test", "path": "/"}],
         "origins": [{"origin": "https://x.test", "localStorage": [{"name": "tok", "value": "SECRET-LS-456"}]}]}


def _plain(path, state=STATE):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


@windows_only
def test_write_locks_the_file_and_reads_back(tmp_path):
    p = tmp_path / "a.json"
    ss.write_state(p, STATE)
    body = p.read_text(encoding="utf-8")
    assert "SECRET" not in body and json.loads(body)[ss.MARK] == 1
    assert ss.read_state(p) == STATE
    assert not (tmp_path / "a.json.tmp").exists()


def test_a_plain_file_still_reads(tmp_path):
    assert ss.read_state(_plain(tmp_path / "a.json")) == STATE


def test_write_keeps_the_sign_in_plain_when_the_lock_fails(tmp_path, monkeypatch):
    def broken(_protect, _data):
        raise OSError("no protection")
    monkeypatch.setattr(ss, "_dpapi", broken)
    p = tmp_path / "a.json"
    ss.write_state(p, STATE)
    assert json.loads(p.read_text(encoding="utf-8")) == STATE


@windows_only
def test_a_locked_file_from_another_machine_asks_to_sign_in_again(tmp_path):
    from conxa_compile.recorder.session import check_group_sessions_sync
    from conxa_core.models.workflow import GroupApp

    p = tmp_path / "a.json"
    ss.write_state(p, STATE)
    env = json.loads(p.read_text(encoding="utf-8"))
    env["dpapi"] = env["dpapi"][:-12] + "AAAAAAAAAAA="  # another machine's key opens nothing here
    p.write_text(json.dumps(env), encoding="utf-8")
    with pytest.raises(ss.SavedSessionUnreadable, match="Sign in again"):
        ss.read_state(p)
    app = GroupApp(id="a1", name="X", login_url="https://x.test/login", storage_state_path=str(p))
    assert check_group_sessions_sync([app], [app]) == {"a1": "expired"}


@windows_only
def test_migration_locks_every_plain_sign_in_with_identical_content(tmp_path):
    files = [
        _plain(tmp_path / "groups" / "g1" / "auth" / "app1.json"),
        _plain(tmp_path / "groups" / "g1" / "auth" / "app1.json.bak", {"cookies": [], "origins": []}),
        _plain(tmp_path / "groups" / "g2" / "auth" / "app2.pending.json"),
        _plain(tmp_path / "workflows" / "w1" / "merged_group_state.json"),
    ]
    before = {f: json.loads(f.read_text(encoding="utf-8")) for f in files}
    odd = tmp_path / "groups" / "g1" / "auth" / "notes.txt"
    odd.write_text("not json", encoding="utf-8")

    assert ss.seal_plain_sessions(tmp_path) == []
    for f in files:
        assert ss.MARK in json.loads(f.read_text(encoding="utf-8"))
        assert ss.read_state(f) == before[f]
    assert odd.read_text(encoding="utf-8") == "not json"
    assert not list(tmp_path.rglob("*.tmp"))

    sealed = files[0].read_bytes()
    assert ss.seal_plain_sessions(tmp_path) == [] and files[0].read_bytes() == sealed  # run twice: no change


@windows_only
@pytest.mark.parametrize("failure", ["lock_fails", "opens_to_something_else"])
def test_migration_keeps_the_plain_file_untouched_when_locking_is_not_proven(tmp_path, monkeypatch, failure):
    real = ss._dpapi

    def flaky(protect, data):
        if failure == "lock_fails":
            raise OSError("no protection")
        return real(protect, data) if protect else b'{"cookies": [], "origins": []}'
    monkeypatch.setattr(ss, "_dpapi", flaky)
    f = _plain(tmp_path / "groups" / "g1" / "auth" / "app1.json")
    original = f.read_bytes()

    assert ss.seal_plain_sessions(tmp_path) == [f]
    assert f.read_bytes() == original
    assert not list(tmp_path.rglob("*.tmp"))


@windows_only
def test_test_sandbox_gets_a_plain_copy_for_the_runtime_with_the_saved_files_time(tmp_path, monkeypatch):
    from handlers import protocol

    saved = tmp_path / "saved.json"
    ss.write_state(saved, STATE)
    os.utime(saved, (1_700_000_000, 1_700_000_000))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({ss.MARK: 1, "dpapi": "AAAA"}), encoding="utf-8")
    group = SimpleNamespace(apps=[SimpleNamespace(id="a1", storage_state_path=str(saved)),
                                  SimpleNamespace(id="a2", storage_state_path=str(bad))])
    monkeypatch.setattr("conxa_core.storage.group_store.get_group", lambda _gid: group)

    protocol._stage_runtime_auth(SimpleNamespace(group_id="g1"), "acme", tmp_path / "sandbox")
    dest = tmp_path / "sandbox" / "cache" / "sessions" / "acme__a1_raw_state.json"
    assert json.loads(dest.read_text(encoding="utf-8")) == STATE
    assert int(dest.stat().st_mtime) == 1_700_000_000
    assert not (tmp_path / "sandbox" / "cache" / "sessions" / "acme__a2_raw_state.json").exists()
