# WF-19 test hook (loaded only via PYTHONPATH on the MOCK cloud backend): calls to https://api.clerk.com go to the local mock identity provider.
import urllib.request as _u
_orig = _u.urlopen
def _urlopen(url, *a, **k):
    try:
        full = url.full_url if hasattr(url, "full_url") else str(url)
        if full.startswith("https://api.clerk.com"):
            new = "http://127.0.0.1:8110" + full[len("https://api.clerk.com"):]
            if hasattr(url, "full_url"): url.full_url = new
            else: url = new
    except Exception:
        pass
    return _orig(url, *a, **k)
_u.urlopen = _urlopen

# Studio's Python backend only (WF19_FAKE_BROWSER=1): webbrowser.open follows the sign-in URL like a person who approves, and logs it.
import os as _os
if _os.environ.get("WF19_FAKE_BROWSER") == "1":
    import webbrowser, subprocess, sys
    def _open(url, new=0, autoraise=True):
        subprocess.Popen([sys.executable, _os.path.join(_os.path.dirname(__file__), "..", "wf19_fake_browser.py"), url], creationflags=0x08000000); return True
    webbrowser.open = _open
