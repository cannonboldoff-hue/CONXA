import http.server, socketserver, urllib.parse, datetime, sys
MODE = {"v": "1"}
LOG = open(sys.argv[2], "a", buffering=1)
PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Visitor card</title></head><body>
<h1>Visitor card</h1>
<label for="nm">Visitor name</label> <input id="nm" name="nm" type="text">
<button id="go" type="button">Send card</button>
<div id="slot"></div>
<script>
const mode = "%(mode)s";
document.getElementById('go').addEventListener('click', () => {
  const n = document.getElementById('nm').value;
  fetch('/__log?click=go&name=' + encodeURIComponent(n) + '&mode=' + mode);
  const slot = document.getElementById('slot');
  if (mode === '1') slot.innerHTML = '<div id="result" class="box">Card ready for ' + n + '</div>';
  else if (mode === '2') slot.innerHTML = '<section data-k="zz9"><p><b>Card ready for ' + n + '</b></p></section>';
  else if (mode === 'gone') slot.innerHTML = '<div class="box">Something else entirely</div>';
});
</script></body></html>"""
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        LOG.write(f"{datetime.datetime.now().isoformat()} {self.path}\n")
        if u.path == "/__set":
            MODE["v"] = q.get("v", ["1"])[0]; body = f"mode={MODE['v']}".encode()
        elif u.path == "/__log": body = b"ok"
        elif u.path == "/form.html": body = (PAGE % {"mode": MODE["v"]}).encode()
        else: self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
socketserver.TCPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
