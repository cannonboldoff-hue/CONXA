"""What "the same site" means, in one place.

Two questions that used to be answered four different ways:

* ``same_host`` - are these two addresses the same machine name (``www.`` ignored)? For deciding whether
  a navigation was a site moving on by itself. Deliberately NOT looser: ``accounts.google.com`` and
  ``drive.google.com`` are different hosts.
* ``site_of`` - the registrable domain, using the Public Suffix List (``mail.google.com`` -> ``google.com``,
  ``a.co.uk`` -> ``a.co.uk``, ``b.co.uk`` -> ``b.co.uk``). For deciding whether a host belongs to an app
  (the sign-in a workflow's group already covers).

The runtime's cookie-domain rule (runtime/app/browser.js::_domainBelongsToHost) is a third, different
question - which cookies belong with a host - and stays as it is.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from conxa_core._public_suffixes import RULES


def host_of(url: str) -> str:
    """Lower-case hostname of an address, ``www.`` removed; "" when there is none."""
    return (urlsplit(str(url or "")).hostname or "").removeprefix("www.")


def same_host(a: str, b: str) -> bool:
    return host_of(a) == host_of(b) != ""


def site_of(host: str) -> str:
    """Registrable domain of a hostname. An IP address, a single-label host or a host that is itself a
    public suffix is its own site."""
    host = str(host or "").strip().lower().rstrip(".")
    labels = host.split(".")
    if len(labels) < 2 or all(part.isdigit() for part in labels):
        return host
    suffix_len = 1  # the PSL's default rule is "*": an unlisted last label is a suffix
    for i in range(len(labels)):
        candidate = ".".join(labels[i:])
        if "!" + candidate in RULES:               # an exception rule: this name is registrable itself
            suffix_len = len(labels) - i - 1
            break
        if candidate in RULES or ".".join(["*", *labels[i + 1:]]) in RULES:
            suffix_len = max(suffix_len, len(labels) - i)
    if suffix_len >= len(labels):
        return host
    return ".".join(labels[-(suffix_len + 1):])
