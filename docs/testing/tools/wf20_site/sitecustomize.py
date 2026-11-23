# WF-20 test hook (only via PYTHONPATH on the WF-20 backend): calls to https://api.clerk.com go to the local mock (port in WF20_CLERK_PORT).
import os as _os, urllib.request as _u
_orig = _u.urlopen
def _urlopen(url, *a, **k):
    try:
        full = url.full_url if hasattr(url, "full_url") else str(url)
        if full.startswith("https://api.clerk.com"):
            new = f"http://127.0.0.1:{_os.environ.get('WF20_CLERK_PORT', '8141')}" + full[len("https://api.clerk.com"):]
            if hasattr(url, "full_url"): url.full_url = new
            else: url = new
    except Exception:
        pass
    return _orig(url, *a, **k)
_u.urlopen = _urlopen
