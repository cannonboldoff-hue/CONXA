"""BUILD-111 (1): the recompile helper died offline ("LLM router is not configured") unless given a cloud token."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "recompile_session.py"
    spec = importlib.util.spec_from_file_location("recompile_session_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_offline_recompile_installs_a_router_that_makes_no_model_call(monkeypatch, tmp_path, capsys):
    import conxa_core.llm as core_llm
    from conxa_core.config import settings

    mod = _script()
    previous = core_llm._router
    monkeypatch.delenv("CONXA_TOKEN", raising=False)
    monkeypatch.setenv("SKILL_ALLOW_NO_PROVIDERS", "1")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("sys.argv", ["recompile_session.py", "no_such_session"])
    try:
        assert mod.main() == 1                                     # no recording: a plain message, not a crash
        assert "No events" in capsys.readouterr().out
        router = core_llm.get_router()
        assert isinstance(router, mod.OfflineRouter)
        with pytest.raises(RuntimeError):                          # fails like an empty provider pool does
            router.route_text("t", {}, 1000)
    finally:
        core_llm.set_router(previous)
