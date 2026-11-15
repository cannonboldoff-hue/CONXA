"""W11-d: listing a namespace on the filesystem store re-reads only the files that changed."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from conxa_core import db
from conxa_core.config import settings


def _store(tmp_path):
    return patch.object(settings, "data_dir", tmp_path), patch.object(settings, "database_url", "")


def test_each_file_is_parsed_once_until_it_changes(tmp_path):
    a, b = _store(tmp_path)
    with a, b:
        db._MAPPED_CACHE.clear()
        db.db_set("things", "x", {"n": 1})
        db.db_set("things", "y", {"n": 2})
        calls = []

        def fn(doc):
            calls.append(doc["n"])
            return {"n": doc["n"]}

        assert sorted(d["n"] for d in db.db_list_mapped("things", fn)) == [1, 2]
        assert len(calls) == 2
        db.db_list_mapped("things", fn)
        assert len(calls) == 2, "unchanged files are not parsed again"

        path = db._fs_path("things", "x")
        db.db_set("things", "x", {"n": 30})
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 5_000_000))
        assert sorted(d["n"] for d in db.db_list_mapped("things", fn)) == [2, 30]
        assert len(calls) == 3, "only the changed file was parsed again"


def test_a_caller_cannot_change_what_the_cache_holds(tmp_path):
    a, b = _store(tmp_path)
    with a, b:
        db._MAPPED_CACHE.clear()
        db.db_set("things", "x", {"n": 1})
        first = db.db_list_mapped("things", lambda d: {"n": d["n"]})
        first[0]["n"] = 999
        assert db.db_list_mapped("things", lambda d: {"n": d["n"]})[0]["n"] == 1
