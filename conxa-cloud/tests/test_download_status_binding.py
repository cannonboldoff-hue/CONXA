import json

from conxa_compile.compiler.upload_binding import _BindingState


def test_failed_download_is_not_counted_or_bound():
    state = _BindingState()
    state.add_download(json.dumps({"suggested_filename": "a.csv", "status": "cancelled"}))
    state.add_download(json.dumps({"suggested_filename": "b.csv"}))  # no status = completed
    assert state.total_downloads == 1
    assert "a.csv" not in state.pending_by_name
    assert state.pending_by_name["b.csv"] == [1]
