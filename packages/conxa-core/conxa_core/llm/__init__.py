"""LLM router singleton + protocol for the compile pipeline.

The compile pipeline (recorder/compiler/selector generators, in the Build
Studio) calls ``get_router().route_text(...)`` / ``route_vision(...)`` without
knowing where the call ultimately goes. The Build Studio installs a metered
cloud-proxy client via :func:`set_router`; the cloud itself does not use this
singleton — it talks to its concrete provider pool (``app/llm/router.py``)
directly inside the proxy endpoint.

Imports of :func:`get_router` in the pipeline are intentionally lazy (inside
functions) so the router can be configured at startup before the first call.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Iterator, Protocol, runtime_checkable


@runtime_checkable
class RouterProtocol(Protocol):
    def route_text(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    def route_vision(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None: ...


_router: RouterProtocol | None = None

# A per-call-chain router. The Build Studio runs every RPC on its own thread, so a single
# process-global router means a compile and a chat turn running at once overwrite each other's
# usage class and event sink. A router installed here is seen only by the thread (context) that
# installed it; threads that never installed one — e.g. worker threads a handler spawns —
# fall back to the global router, which is the previous behaviour.
_scoped_router: contextvars.ContextVar[RouterProtocol | None] = contextvars.ContextVar(
    "conxa_scoped_llm_router", default=None
)


def set_router(router: RouterProtocol | None) -> None:
    """Install the router used by the compile pipeline (e.g. the cloud proxy client)."""
    global _router
    _router = router
    # A global install supersedes any leftover per-context router (e.g. in a test that ran a
    # command on the main thread earlier).
    _scoped_router.set(None)

def set_scoped_router(router: RouterProtocol | None) -> None:
    """Install ``router`` for the current thread/context only (see ``_scoped_router``)."""
    _scoped_router.set(router)


@contextmanager
def llm_scope(router: RouterProtocol) -> Iterator[RouterProtocol]:
    """Use ``router`` for LLM calls made inside the ``with`` block, then restore."""
    token = _scoped_router.set(router)
    try:
        yield router
    finally:
        _scoped_router.reset(token)


def get_router() -> RouterProtocol:
    scoped = _scoped_router.get()
    if scoped is not None:
        return scoped
    if _router is None:
        raise RuntimeError(
            "LLM router is not configured. Call conxa_core.llm.set_router() "
            "(the Build Studio installs the metered cloud-proxy client at startup)."
        )
    return _router
