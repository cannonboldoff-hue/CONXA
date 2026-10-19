"""The docked-login-window rect the renderer sends to Connect: sanitised, never fatal."""
from handlers.groups import _window_rect
from conxa_compile.recorder.session import SessionRegistry


def test_window_rect_accepts_a_sane_rect():
    assert _window_rect({"x": 300, "y": 120, "width": 700.4, "height": 640}) == {"x": 300, "y": 120, "width": 700, "height": 640}


def test_window_rect_drops_junk_instead_of_raising():
    assert _window_rect(None) is None
    assert _window_rect({"x": 1}) is None
    assert _window_rect({"x": "a", "y": 0, "width": 800, "height": 600}) is None
    assert _window_rect({"x": 0, "y": 0, "width": 50, "height": 50}) is None


def test_registry_carries_the_rect_onto_the_session():
    rect = {"x": 10, "y": 20, "width": 800, "height": 600}
    sess = SessionRegistry().create(auth_mode=True, window_rect=rect)
    assert sess.window_rect == rect
