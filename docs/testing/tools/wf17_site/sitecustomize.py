# WF-17 test hook: webbrowser.open -> wf17_fake_browser.py (follows the URL like a person approving). Loaded only via PYTHONPATH.
import webbrowser, subprocess, sys, os
def _open(url, new=0, autoraise=True):
    subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "..", "wf17_fake_browser.py"), url], creationflags=0x08000000); return True
webbrowser.open = _open
