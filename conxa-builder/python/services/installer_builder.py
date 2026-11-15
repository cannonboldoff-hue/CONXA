"""Build Studio wrapper over conxa_compile.installer_builder.

The shared builder already locates makensis via the MAKENSIS_PATH env var, which
bootstrap.ensure_nsis sets to the cached deps\\nsis\\makensis.exe. This wrapper
just guarantees that wiring is in place and delegates.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


def build_installer(
    workspace_id: str,
    *,
    company_slug: str = "",
    logo_path: str | None = None,
    version: str | None = None,
    release_notes: str = "",
    installer_name: str | None = None,
    realtime_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    # Ensure all deps (NSIS, runtime) are at the latest cloud version before packaging.
    # This is a safety net for sessions started before a dep release was available.
    #
    # Must resolve to the same cloud backend.py publishes to (it reads CONXA_CLOUD_API and
    # defaults to the local cloud whatever the environment; this is env-aware) — defaulting straight to prod here would make a
    # Dev build silently fetch deps from apis.conxa.in even though everything else
    # (publish, sync_endpoint, tracking_url) was resolved against the local dev cloud.
    from conxa_core.config import active_environment, settings as _core_settings

    _env_default_cloud_api = (
        f"http://127.0.0.1:{_core_settings.port}" if active_environment() == "dev" else "https://apis.conxa.in"
    )
    cloud_api = os.environ.get("CONXA_CLOUD_API", _env_default_cloud_api)
    from services import bootstrap as _bs

    def _fwd(evt: dict) -> None:
        if realtime_sink:
            realtime_sink(evt)

    result = _bs.ensure_all(cloud_api, on_event=_fwd)
    if not result.get("ok"):
        dep_errors = result.get("errors", [])
        raise RuntimeError(
            "Dependency bootstrap failed before installer build: "
            + "; ".join(dep_errors)
        )

    # Point the shared builder at the bootstrapped NSIS if not already set.
    if not os.environ.get("MAKENSIS_PATH"):
        base = (
            os.environ.get("CONXA_STUDIO_HOME")
            or os.environ.get("SKILL_DATA_DIR")
            or "~/.conxa-build-studio"
        )
        nsis_dir = Path(os.path.expanduser(base)) / "deps" / "nsis"
        if nsis_dir.is_dir():
            for p in nsis_dir.rglob("makensis.exe"):
                os.environ["MAKENSIS_PATH"] = str(p)
                break

    from conxa_compile.installer_builder import build_installer as _build

    return _build(
        workspace_id,
        company_slug=company_slug,
        logo_path=logo_path,
        version=version,
        release_notes=release_notes,
        installer_name=installer_name,
        realtime_sink=realtime_sink,
    )
