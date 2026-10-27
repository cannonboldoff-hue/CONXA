"""Per-org monthly LLM usage metering for the Build Studio proxy.

Usage is keyed by ``{org_id}:{YYYY-MM}`` in the ``llm_usage`` KV namespace and
records cumulative input/output token estimates plus a request count. The cloud
billing/analytics dashboard reads these rows.

Token counts are estimated from character length (~4 chars/token) because the
multi-provider router normalizes away upstream ``usage`` blocks before returning.
The estimate is intentionally conservative and only used for quota enforcement.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from conxa_core.db import db_get, db_set

_NAMESPACE = "llm_usage"
_lock = threading.RLock()


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def usage_key(org_id: str, period: str | None = None) -> str:
    return f"{org_id}:{period or _period()}"


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Never negative."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def get_usage(org_id: str, period: str | None = None) -> dict[str, int]:
    row = db_get(_NAMESPACE, usage_key(org_id, period))
    if not isinstance(row, dict):
        return {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    return {
        "input_tokens": int(row.get("input_tokens") or 0),
        "output_tokens": int(row.get("output_tokens") or 0),
        "requests": int(row.get("requests") or 0),
    }


def quota_exceeded(org_id: str, quota: int) -> bool:
    """True when this org has met or passed its monthly token quota.

    ``quota <= 0`` disables enforcement.
    """
    if quota <= 0:
        return False
    row = get_usage(org_id)
    return (row["input_tokens"] + row["output_tokens"]) >= quota


def record_usage(org_id: str, *, input_tokens: int, output_tokens: int) -> dict[str, int]:
    """Atomically add token counts to this org's monthly row and return the new totals."""
    with _lock:
        key = usage_key(org_id)
        row = db_get(_NAMESPACE, key)
        if not isinstance(row, dict):
            row = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
        row["input_tokens"] = int(row.get("input_tokens") or 0) + max(0, input_tokens)
        row["output_tokens"] = int(row.get("output_tokens") or 0) + max(0, output_tokens)
        row["requests"] = int(row.get("requests") or 0) + 1
        row["org_id"] = org_id
        row["period"] = _period()
        db_set(_NAMESPACE, key, row)
        return row


_VIDEO_NAMESPACE = "video_usage"


def get_video_usage(org_id: str, period: str | None = None) -> dict[str, float]:
    row = db_get(_VIDEO_NAMESPACE, usage_key(org_id, period))
    if not isinstance(row, dict):
        return {"seconds": 0.0, "requests": 0}
    return {"seconds": float(row.get("seconds") or 0), "requests": int(row.get("requests") or 0)}


def video_quota_exceeded(org_id: str, quota_seconds: int) -> bool:
    """True once this org has analyzed its monthly video allowance. ``quota_seconds <= 0`` disables."""
    if quota_seconds <= 0:
        return False
    return get_video_usage(org_id)["seconds"] >= quota_seconds


def record_video_usage(org_id: str, *, seconds: float) -> dict[str, Any]:
    """Add one analyzed recording's duration to this org's monthly ``video_usage`` row."""
    with _lock:
        key = usage_key(org_id)
        row = db_get(_VIDEO_NAMESPACE, key)
        if not isinstance(row, dict):
            row = {"seconds": 0.0, "requests": 0}
        row["seconds"] = float(row.get("seconds") or 0) + max(0.0, seconds)
        row["requests"] = int(row.get("requests") or 0) + 1
        row["org_id"] = org_id
        row["period"] = _period()
        db_set(_VIDEO_NAMESPACE, key, row)
        return row


def _stringify(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(payload)


# Rough per-image input cost for a vision call (~1000 tokens for a 1024px
# image across mainstream providers). Base64 payloads are excluded from the
# char-based estimate entirely — an ~80-200KB blob is not 20k-50k tokens.
_IMAGE_TOKEN_ESTIMATE = 1000

_BLOB_KEYS = {"image_base64", "base64"}


def _strip_base64_blobs(payload: Any) -> tuple[Any, int]:
    """Return (payload with base64 blobs removed, count of blobs removed).

    Mirrors the blob-detection pattern in app/llm/router.py's _redact_value.
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        count = 0
        for key, item in payload.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in _BLOB_KEYS and isinstance(item, str):
                count += 1
            elif isinstance(item, str) and item.startswith("data:") and ";base64," in item[:80]:
                count += 1
            else:
                stripped, sub = _strip_base64_blobs(item)
                out[key_text] = stripped
                count += sub
        return out, count
    if isinstance(payload, list):
        out_list: list[Any] = []
        count = 0
        for item in payload:
            stripped, sub = _strip_base64_blobs(item)
            out_list.append(stripped)
            count += sub
        return out_list, count
    return payload, 0


def estimate_request_tokens(payload: dict[str, Any]) -> int:
    stripped, image_count = _strip_base64_blobs(payload)
    return estimate_tokens(_stringify(stripped)) + image_count * _IMAGE_TOKEN_ESTIMATE


def estimate_response_tokens(response: dict[str, Any] | None) -> int:
    if not response:
        return 0
    return estimate_tokens(_stringify(response))
