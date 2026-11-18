"""Robot checks (reCAPTCHA, hCaptcha, Cloudflare Turnstile): a wall no workflow can get past by itself.

A click inside one is not a repeatable step. It is recorded, left out at compile, and put to the person, who can add a
hand-over step so someone does the check on each run (WF-8 8.13). Mirrored in runtime/app/human_check.js, which stops
a run that meets one instead of acting on it.
"""

from __future__ import annotations

import re
from typing import Any

# The frames a robot check lives in. Only frames: reCAPTCHA's own demo page (google.com/recaptcha/api2/demo) is a
# normal page, and a step on it is a normal step.
HUMAN_CHECK_FRAME = re.compile(
    r"^https?://(?:[\w-]+\.)*(?:google\.com|recaptcha\.net)/recaptcha/(?:api2|enterprise)/(?:anchor|bframe)"
    r"|^https?://(?:[\w-]+\.)*hcaptcha\.com/"
    r"|^https?://challenges\.cloudflare\.com/",
    re.IGNORECASE,
)


def in_human_check(ev: dict[str, Any]) -> bool:
    """True when the recorded action happened inside a robot-check frame."""
    frame = ev.get("frame") if isinstance(ev.get("frame"), dict) else {}
    return any(isinstance(c, dict) and HUMAN_CHECK_FRAME.match(str(c.get("url") or "")) for c in frame.get("chain") or [])
