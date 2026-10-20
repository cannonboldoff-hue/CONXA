"""Per-tab recording video for a page Build Studio's own browser panel shows.

Rebuilds Playwright's page video (playwright-core/lib/server/videoRecorder.js) on a raw CDP
connection to one page target: Page.startScreencast JPEG frames -> constant-25fps image pipe ->
ffmpeg VP8 webm, repeating the last frame to fill gaps. Second 0 of the file is `start_wall_ms`,
so the videos.json contract frame_extractor reads is unchanged: an event's offset into the file
is its wall time minus start_wall_ms.

The panel shows the fixed 1280x720 page shrunk by its fit-to-panel scale (`scale_fn`, from the
panel). A frame covers the view (metadata.deviceWidth x deviceHeight, in DIPs, times the display
scale), and the page occupies its top-left `fraction` = 1280 * scale / deviceWidth of it: the whole
frame in Build Studio's panel, only a corner in some other Electron layouts seen while proving this
out. ffmpeg crops that region and scales it back to CSS pixels, so a bridge bbox lands on the pixels
compile crops. Playwright's bundled ffmpeg has no concat, so one tab is one ffmpeg run with its crop
fixed at the first frame's fraction; a frame with a different one is reshaped in Python to match.
See docs/archive/spikes/in-app-recording-2026-10.md.
"""

from __future__ import annotations

import base64
import io
import json
import math
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from PIL import Image
from websockets.sync.client import connect

FPS = 25
WIDTH, HEIGHT = 1280, 720


def _clamp(scale: float) -> float:
    return min(1.0, max(0.05, float(scale)))


def ffmpeg_args(fraction: float) -> list[str]:
    """Playwright's FfmpegVideoRecorder args, with the page region cropped out of the frame."""
    s = _clamp(fraction)
    vf = (
        f"crop=trunc(iw*{s:.6f}):trunc(ih*{s:.6f}):0:0,scale={WIDTH}:{HEIGHT}:flags=bilinear,"
        f"pad={WIDTH}:{HEIGHT}:0:0:gray,crop={WIDTH}:{HEIGHT}:0:0"
    )
    return (
        "-loglevel error -f image2pipe -avioflags direct -fpsprobesize 0 -probesize 32 "
        "-analyzeduration 0 -c:v mjpeg -i pipe:0 -y -an -r 25 -c:v vp8 -qmin 0 -qmax 50 -crf 8 "
        "-deadline realtime -speed 8 -b:v 1M -threads 1"
    ).split(" ") + ["-vf", vf]


def reshape(jpeg: bytes, from_scale: float, to_scale: float) -> bytes:
    """Redraw a frame whose page covers `from_scale` of it as if it covered `to_scale`."""
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    fw, fh = img.size
    src, dst = _clamp(from_scale), _clamp(to_scale)
    page = img.crop((0, 0, max(1, round(fw * src)), max(1, round(fh * src))))
    page = page.resize((max(1, round(fw * dst)), max(1, round(fh * dst))), Image.BILINEAR)
    canvas = Image.new("RGB", (fw, fh), (128, 128, 128))
    canvas.paste(page, (0, 0))
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=90)
    return out.getvalue()


class ScreencastVideo:
    """One tab's video. `ws_url` is the page target's devtools WebSocket
    (ws://127.0.0.1:<port>/devtools/page/<targetId>); `scale_fn` returns how much the panel
    currently shrinks the page (1.0 when omitted)."""

    def __init__(self, ws_url: str, out_path: Path, ffmpeg: str,
                 scale_fn: Callable[[], float] | None = None) -> None:
        self.ws_url = ws_url
        self.out_path = Path(out_path)
        self.ffmpeg = ffmpeg
        self._scale_fn = scale_fn
        self._scale = 1.0
        self._base: float | None = None  # page fraction ffmpeg crops; fixed by the first frame
        self.start_wall_ms = 0
        self.frames_received = 0
        self.frames_written = 0
        self.frames_reshaped = 0
        # (jpeg, fraction, frame_number) of the newest frame; written once the next one arrives.
        self._last: tuple[bytes, float, int] | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ws = None
        self._ff: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._msg_id = 0

    def _send(self, method: str, params: dict | None = None) -> None:
        self._msg_id += 1
        self._ws.send(json.dumps({"id": self._msg_id, "method": method, "params": params or {}}))

    def start(self) -> None:
        if self._scale_fn:
            self._scale = _clamp(self._scale_fn())
            threading.Thread(target=self._poll_scale, daemon=True, name="screencast-scale").start()
        self.start_wall_ms = int(time.time() * 1000)
        # Entered by hand (websockets >= 17.1 warns otherwise); stop() closes it.
        self._ws = connect(self.ws_url, max_size=None, open_timeout=5).__enter__()
        # No maxWidth/maxHeight: full device-pixel frames, so a high-DPI display's extra pixels
        # survive the panel's shrink-to-fit.
        self._send("Page.startScreencast", {"format": "jpeg", "quality": 90})
        self._thread = threading.Thread(target=self._run, daemon=True, name="screencast")
        self._thread.start()

    def _poll_scale(self) -> None:
        while not self._stop.is_set():
            try:
                self._scale = _clamp(self._scale_fn())
            except Exception:
                pass  # panel briefly unreachable: keep the last known scale
            self._stop.wait(0.2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._ws.recv(timeout=0.25)
            except TimeoutError:
                continue
            except Exception:
                break  # target gone (tab closed) — stop() pads the tail
            msg = json.loads(raw)
            if msg.get("method") != "Page.screencastFrame":
                continue
            params = msg["params"]
            try:
                self._send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:
                break
            meta = params.get("metadata") or {}
            ts = meta.get("timestamp") or time.time()
            self._write(base64.b64decode(params["data"]), self._fraction(meta), ts)
            self.frames_received += 1

    def _fraction(self, meta: dict) -> float:
        device_width = meta.get("deviceWidth")
        if not device_width:
            return _clamp(self._scale)
        return _clamp(WIDTH * self._scale / float(device_width))

    def _frame_no(self, ts_s: float) -> int:
        # ceil, not floor: a frame shows from the slot at/after the moment it was painted, never
        # the slot before it (floor put content ~half a frame early; measured in the spike).
        return max(0, math.ceil((ts_s * 1000 - self.start_wall_ms) / 1000 * FPS))

    def _emit(self, buf: bytes, fraction: float, count: int) -> None:
        if count <= 0:
            return
        if self._ff is None:
            # ffmpeg's crop is fixed per run, so it starts with the first picture it will get.
            self._base = fraction
            self._ff = subprocess.Popen(
                [self.ffmpeg, *ffmpeg_args(fraction), str(self.out_path)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        # Off by more than ~a pixel in 100 (DIP rounding of the view's size is well under that).
        if abs(fraction - self._base) > 0.01:
            buf = reshape(buf, fraction, self._base)
            self.frames_reshaped += 1
        for _ in range(count):
            self._ff.stdin.write(buf)
        self.frames_written += count

    def _write(self, buf: bytes, fraction: float, ts_s: float) -> None:
        n = self._frame_no(ts_s)
        with self._lock:
            if self._last is None:
                # Back-fill from second 0 with the first picture, so file time 0 == start_wall_ms.
                self._emit(buf, fraction, n)
            else:
                last_buf, last_scale, last_n = self._last
                if n > last_n:
                    self._emit(last_buf, last_scale, n - last_n)
                elif n < last_n:
                    n = last_n  # late frame: replaces the pending one, the clock stays monotonic
            self._last = (buf, fraction, n)

    def stop(self) -> dict:
        """Stop capturing, pad the video to now, and return {path, start_wall_ms}."""
        self._stop.set()
        if self._ws is not None:
            try:
                self._send("Page.stopScreencast")
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
        with self._lock:
            if self._last is not None:
                buf, scale, n = self._last
                self._emit(buf, scale, max(1, self._frame_no(time.time()) - n + 1))
                self._last = None
        if self._ff is None:
            raise RuntimeError("no picture arrived from the page, so no video was written")
        self._ff.stdin.close()
        err = self._ff.stderr.read().decode("utf-8", "replace")
        rc = self._ff.wait(timeout=60)
        if rc != 0:
            raise RuntimeError(f"ffmpeg exited {rc}: {err[:400]}")
        return {"path": str(self.out_path), "start_wall_ms": self.start_wall_ms}
