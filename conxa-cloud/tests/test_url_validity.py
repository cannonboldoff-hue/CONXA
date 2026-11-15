"""A starting page is any http(s) URL with a host, including local pages."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "conxa-builder", "python")))

from conxa_compile.conversation.harness import _is_web_url  # noqa: E402


@pytest.mark.parametrize("url", ["https://example.com", "http://localhost:3000/x", "http://127.0.0.1:8099"])
def test_accepts(url):
    assert _is_web_url(url)


@pytest.mark.parametrize("url", ["example.com", "ftp://x.com", "http://", "http://a b.com", ""])
def test_rejects(url):
    assert not _is_web_url(url)
