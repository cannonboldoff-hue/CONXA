"""The skill-pack delta route, in one place.

The cloud publish (publish_routes.py) and the Studio's pack.json builder both name this path, so they
build it here. The path always starts with /api/v1; callers prepend the cloud base URL.
"""

from __future__ import annotations

# Studio builds before a cloud publish has told it the generation; backend.py's own fallback is also v2.
DEFAULT_GENERATION = "v2"


def sync_delta_path(workspace_id: str, generation: str | None = DEFAULT_GENERATION) -> str:
    """Versioned route for an installer generation; the legacy unversioned route when generation is empty."""
    if generation:
        return f"/api/v1/workflows/{generation}/{workspace_id}/skill-packs/delta"
    return f"/api/v1/skill-packs/{workspace_id}/delta"
