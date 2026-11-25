"""Workspace identity constant shared by the storage layer and the SaaS layer.

The local/offline workspace id is referenced both by the workflow store (which
stamps every locally created workflow) and by the cloud SaaS metadata. Keeping it
here lets the storage foundation stay independent of the cloud-only SaaS module.
"""

from __future__ import annotations

import re

LOCAL_WORKSPACE_ID = "wrk_local"


SIGNED_OUT_KEY = "_signed_out"


def account_key(identity: dict | None) -> str:
    """Folder name for one signed-in account's local data (AUTH-26).

    Same rule as the cloud's workspace_id: the Clerk org, else ``personal_<user_id>``.
    Signed out (no identity) gets the empty holding folder. Long ids are cut and hashed
    so deep recording paths stay under the Windows path limit.
    """
    import hashlib

    if not identity:
        return SIGNED_OUT_KEY
    raw = identity.get("org_id") or (f"personal_{identity['user_id']}" if identity.get("user_id") else "")
    if not raw:
        return SIGNED_OUT_KEY
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(raw)).strip("_") or "acct"
    if len(slug) > 40:
        slug = f"{slug[:28]}_{hashlib.sha1(str(raw).encode()).hexdigest()[:10]}"
    return slug


def workspace_dir_slug(workspace_id: str) -> str:
    """Derive a filesystem/URL-safe slug for a workspace_id.

    One workspace has exactly one skill pack and one installer, forever —
    workspace_id is the sole identity key (see CLAUDE.md Key Invariants).
    This is purely a character-safety transform (skill-pack directories and
    bundle slugs must match ``validate_bundle_slug``'s ``[a-z][a-z0-9_]*``),
    not an identity derivation — unlike the old company_slug(), it takes no
    name input and needs no uniqueness arbitration.
    """
    base = re.sub(r"[^a-z0-9]+", "_", workspace_id.lower()).strip("_") or "ws"
    if not base[0].isalpha():
        base = f"ws_{base}"
    return base
