"""BUILD-117 (4).1 fixture: http://127.0.0.1:8098/box.html. The first visit (the recording) raises no OK/Cancel box;
every later visit (a test run) does, so the box is one the recording never saw."""
from http.server import BaseHTTPRequestHandler, HTTPServer
hits = {"n": 0}
PAGE = """<!doctype html><title>Box fixture</title><h1>Box fixture</h1>
<button id="open">Open details</button><p id="result" role="status"></p>
<script>const n=%d;
document.getElementById('open').onclick=function(){
  if(n>1 && !confirm('Open the details panel?')){document.getElementById('result').textContent='Details kept closed';return;}
  document.getElementById('result').textContent='Details opened';};</script>"""
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/box.html"):
            hits["n"] += 1
            body = (PAGE % hits["n"]).encode()
        elif self.path.startswith("/reset"):
            hits["n"] = 0; body = b"reset"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", 8098), H).serve_forever()
