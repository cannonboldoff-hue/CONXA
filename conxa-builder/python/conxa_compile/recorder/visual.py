"""Element crop helper — reads from a video frame (PNG) and produces a JPEG element snapshot."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image


def crop_element_from_frame(
    frame_path: Path,
    bbox: dict[str, Any],
    out_path: Path,
    *,
    jpeg_quality: int,
) -> str | None:
    """
    Crop the element region from a video frame PNG and write a JPEG element snapshot.

    Video frames are captured at the recording viewport resolution (1280 wide, DPR=1, and as tall
    as the pane the page was drawn in), so bbox x/y/w/h from bridge.js can be used directly
    without DPR scaling. The crop is clamped to the frame below.

    Returns the relative path from the session_dir (posix-style), or None when the
    bbox is too small or the crop would be empty.
    """
    w = int(bbox.get("w") or 0)
    h = int(bbox.get("h") or 0)
    if w < 2 or h < 2:
        return None

    x = max(0, int(round(float(bbox.get("x") or 0))))
    y = max(0, int(round(float(bbox.get("y") or 0))))
    x2 = x + max(1, int(round(float(w))))
    y2 = y + max(1, int(round(float(h))))

    raw = frame_path.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        im = im.convert("RGB")
        W, H = im.size
        x2 = min(W, x2)
        y2 = min(H, y2)
        if x2 <= x or y2 <= y:
            return None
        crop = im.crop((x, y, x2, y2))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(out_path, format="JPEG", quality=jpeg_quality, optimize=True)

    # out_path is always <session_dir>/images/<name>; return images/<name>.
    return f"images/{out_path.name}"
