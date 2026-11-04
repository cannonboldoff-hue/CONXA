"""One meaning of "same site" (conxa_core.site). Hosts a wrong answer here would mix up: a multi-part
suffix like co.uk, a private suffix like github.io, an IP address."""
from __future__ import annotations

import pytest

from conxa_core.site import host_of, same_host, site_of
from conxa_core.storage.group_store import unclaimed_hosts
from conxa_core.models.workflow import GroupApp


@pytest.mark.parametrize("host,site", [
    ("mail.google.com", "google.com"),
    ("accounts.google.com", "google.com"),
    ("google.com", "google.com"),
    ("a.co.uk", "a.co.uk"),
    ("www.b.co.uk", "b.co.uk"),
    ("co.uk", "co.uk"),                       # a public suffix is its own site
    ("user1.github.io", "user1.github.io"),   # a private suffix: each user is a different site
    ("user2.github.io", "user2.github.io"),
    ("a.b.ck", "a.b.ck"),                     # wildcard rule *.ck
    ("www.ck", "www.ck"),                     # exception rule !www.ck
    ("localhost", "localhost"),
    ("127.0.0.1", "127.0.0.1"),
    ("App.Example.COM.", "example.com"),
])
def test_site_of(host: str, site: str) -> None:
    assert site_of(host) == site


def test_same_host_is_not_same_site() -> None:
    assert same_host("https://www.drive.google.com/a", "https://drive.google.com/b")
    assert not same_host("https://accounts.google.com/", "https://drive.google.com/")
    assert not same_host("about:blank", "about:blank")   # no host means no match


def test_host_of() -> None:
    assert host_of("https://WWW.Example.com:8080/x?y=1") == "example.com"
    assert host_of("not a url") == ""


def test_two_co_uk_companies_are_not_covered_by_each_others_sign_in() -> None:
    apps = [GroupApp(id="a", name="A", login_url="https://login.a.co.uk/signin")]
    assert unclaimed_hosts(apps, "https://app.b.co.uk/x") == ["app.b.co.uk"]
    assert unclaimed_hosts(apps, "https://app.a.co.uk/x") == []
