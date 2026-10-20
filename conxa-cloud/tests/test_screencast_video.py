from __future__ import annotations

import io
import socket
import time

import pytest
from PIL import Image

from conxa_compile.recorder import screencast_video as sv
from conxa_compile.recorder.frame_extractor import _extract_frame, _find_ffmpeg


class _Pipe:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, buf: bytes) -> None:
        self.frames.append(buf)


def _video(start_wall_ms: int = 10_000) -> tuple[sv.ScreencastVideo, _Pipe]:
    video = sv.ScreencastVideo("ws://unused", "out.webm", "ffmpeg")
    pipe = _Pipe()
    video._ff = type("FF", (), {"stdin": pipe})()
    video._base = 1.0
    video.start_wall_ms = start_wall_ms
    return video, pipe


def _jpeg(size: tuple[int, int], page: tuple[int, int], color=(255, 0, 0)) -> bytes:
    img = Image.new("RGB", size, (255, 255, 255))
    img.paste(Image.new("RGB", page, color), (0, 0))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    return out.getvalue()


def test_first_frame_backfills_from_second_zero() -> None:
    video, pipe = _video()
    video._write(b"A", 1.0, 10.200)  # 200 ms in -> slot 5
    assert pipe.frames == [b"A"] * 5


def test_frames_repeat_until_the_next_one_and_late_frames_replace_pending() -> None:
    video, pipe = _video()
    video._write(b"A", 1.0, 10.000)
    video._write(b"B", 1.0, 10.130)  # slot ceil(3.25) = 4 -> A fills 0..3
    assert pipe.frames == [b"A"] * 4
    video._write(b"C", 1.0, 10.100)  # late (slot 3 < 4): replaces B, nothing written
    assert pipe.frames == [b"A"] * 4
    video._write(b"D", 1.0, 10.200)  # slot 5 -> C fills slot 4
    assert pipe.frames == [b"A"] * 4 + [b"C"]


def test_frame_slot_uses_ceil() -> None:
    video, _ = _video()
    assert video._frame_no(10.001) == 1
    assert video._frame_no(10.040) == 1
    assert video._frame_no(9.0) == 0


def test_page_fraction_comes_from_the_panel_scale_and_the_frame_size() -> None:
    video, _ = _video()
    video._scale = 0.640625
    # Build Studio's panel: the frame is the shrunk view, so the page fills it.
    assert video._fraction({"deviceWidth": 820}) == pytest.approx(1.0)
    # A frame the size of the unshrunk page: the page covers only its top-left 64%.
    assert video._fraction({"deviceWidth": 1280}) == pytest.approx(0.640625)
    assert video._fraction({}) == pytest.approx(0.640625)


def test_ffmpeg_args_crop_the_page_region_back_to_css_pixels() -> None:
    args = sv.ffmpeg_args(0.64)
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("crop=trunc(iw*0.640000):trunc(ih*0.640000):0:0,scale=1280:720")
    assert args[args.index("-r") + 1] == "25"
    assert sv.ffmpeg_args(3.0)[-1].startswith("crop=trunc(iw*1.000000)")


def test_reshape_moves_the_page_region_to_the_starting_scale() -> None:
    # A 1600x900 frame (125% display) with a red page drawn at scale 0.5; recording started at 0.8.
    out = Image.open(io.BytesIO(sv.reshape(_jpeg((1600, 900), (800, 450)), 0.5, 0.8))).convert("RGB")
    assert out.size == (1600, 900)
    assert out.getpixel((1270, 710))[0] > 200 and out.getpixel((1270, 710))[1] < 60  # inside 1280x720
    assert out.getpixel((1300, 740)) == pytest.approx((128, 128, 128), abs=6)  # outside: padding


def test_frames_off_the_starting_scale_are_reshaped() -> None:
    video, pipe = _video()
    video._base = 0.8
    frame = _jpeg((1600, 900), (800, 450))
    video._write(frame, 0.5, 10.000)
    video._write(frame, 0.5, 10.080)
    assert video.frames_reshaped == 1
    assert pipe.frames[0] != frame


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_records_a_page_with_frames_on_the_wall_clock(tmp_path) -> None:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    sync_api = pytest.importorskip("playwright.sync_api")
    port = _free_port()
    with sync_api.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(args=[f"--remote-debugging-port={port}"])
        except Exception as exc:  # browsers not installed on this machine
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_content("<body style='margin:0;background:#f00'></body>")
        target_id = page.context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]
        video = sv.ScreencastVideo(f"ws://127.0.0.1:{port}/devtools/page/{target_id}",
                                   tmp_path / "recording.webm", ffmpeg)
        video.start()
        # Headless Chromium only sends a frame on repaint (a visible view sends one on start).
        page.evaluate("document.body.style.background = '#fe0000'")
        deadline = time.time() + 5
        while video.frames_received == 0 and time.time() < deadline:
            page.wait_for_timeout(50)
        page.wait_for_timeout(1200)
        page.evaluate("document.body.style.background = '#00f'")
        switched_ms = int(time.time() * 1000)
        page.wait_for_timeout(1200)
        result = video.stop()
        browser.close()

    assert video.frames_received > 0
    offset = switched_ms - result["start_wall_ms"]
    before, after = tmp_path / "before.png", tmp_path / "after.png"
    _extract_frame(ffmpeg, tmp_path / "recording.webm", before, offset - 400)
    _extract_frame(ffmpeg, tmp_path / "recording.webm", after, offset + 400)
    r, _, b = Image.open(before).convert("RGB").getpixel((640, 360))
    assert r > 200 and b < 60
    assert Image.open(after).size == (1280, 720)
    r, _, b = Image.open(after).convert("RGB").getpixel((640, 360))
    assert b > 200 and r < 60
