"""The Python and JavaScript sign-in word lists must be the same words."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conxa_core import login_paths

JS = Path(__file__).resolve().parents[2] / "runtime" / "app" / "login_paths.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_lists_match_python() -> None:
    out = subprocess.run(
        ["node", "-e", f"const m = require({json.dumps(str(JS))}); console.log(JSON.stringify([m.SIGN_IN_PAGE, m.SSO_HOP]))"],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout
    sign_in, hops = json.loads(out)
    assert tuple(sign_in) == login_paths.SIGN_IN_PAGE
    assert tuple(hops) == login_paths.SSO_HOP
