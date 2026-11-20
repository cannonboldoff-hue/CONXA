"""WF-14 local stand-in for public file hosts. Usage: python filesrv.py [logfile]   (port 8097)
Pages: /multi.html (input multiple), /single.html (single input), /dl.html (download links).
Endpoints: POST /upload (logs name,size,sha256 per file to the log, JSON lines), GET /received, POST /reset,
GET /dl/ok?name=x&size=n, /dl/empty, /dl/big (200 MB), /dl/accent (name with spaces and e-acute), /dl/stuck (never finishes).
"""
import hashlib, json, os, re, sys, time, urllib.parse
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "filesrv.log")
UPLOAD_PAGE = """<!doctype html><meta charset=utf-8><title>%(t)s</title>
<h1>%(t)s</h1>
<button type=button id=begin onclick="document.getElementById('result').textContent='Ready'">Begin</button>
<form id=f><label for=files>Choose files</label> <input type=file id=files name=files %(m)s>
<button type=button id=up>Upload</button></form>
<p id=result>Nothing uploaded yet</p>
<script>
document.getElementById('up').onclick = async () => {
  const fs = document.getElementById('files').files;
  if (!fs.length) { document.getElementById('result').textContent = 'No files chosen'; return; }
  const fd = new FormData(); for (const f of fs) fd.append('files', f, f.name);
  document.getElementById('result').textContent = 'Uploading...';
  const r = await fetch('/upload?page=%(p)s', {method:'POST', body: fd});
  const j = await r.json();
  document.getElementById('result').textContent = 'Received ' + j.count + ' files: ' + j.files.map(x => x.name + ' (' + x.size + ')').join(', ');
};
</script>"""
DL_PAGE = """<!doctype html><meta charset=utf-8><title>Downloads</title><h1>Downloads</h1><ul>
<li><a id=dl-empty href="/dl/empty">Empty report</a></li>
<li><a id=dl-accent href="/dl/accent">Accent report</a></li>
<li><a id=dl-big href="/dl/big">Big archive</a></li>
<li><a id=dl-stuck href="/dl/stuck">Stuck export</a></li>
<li style="margin-left:450px"><a id=dl-export href="/dl/export">Export report</a> (what it serves is set by POST /mode?m=ok|empty|accent|big|stuck)</li></ul>"""
MODE = ["ok"]


def log(rec):
    rec["t"] = time.strftime("%H:%M:%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        if p == "/multi.html":
            self._send(200, UPLOAD_PAGE % {"t": "Multi upload", "m": "multiple", "p": "multi"})
        elif p == "/single.html":
            self._send(200, UPLOAD_PAGE % {"t": "Single upload", "m": "", "p": "single"})
        elif p == "/dl.html":
            self._send(200, DL_PAGE)
        elif p == "/dl2.html":  # one real button (keyboard Space records as a click); behaviour set by POST /mode
            self._send(200, "<!doctype html><meta charset=utf-8><title>Export</title><h1>Export</h1><button type=button id=export onclick=\"location.href='/dl/export'\">Export report</button>")
        elif p == "/received":
            self._send(200, open(LOG, encoding="utf-8").read() if os.path.exists(LOG) else "", "text/plain; charset=utf-8")
        elif p == "/dl/export":  # one link, behaviour switched by POST /mode so one recording can be replayed against every edge case
            self.path = "/dl/" + ("ok?name=export.txt&size=64" if MODE[0] == "ok" else MODE[0])
            log({"ev": "export-served-as", "mode": MODE[0]})
            return self.do_GET()
        elif p == "/dl/ok":
            n = int(q.get("size", ["10"])[0])
            self._send(200, b"x" * n, "application/octet-stream",
                       [("Content-Disposition", 'attachment; filename="%s"' % q.get("name", ["f.txt"])[0])])
        elif p == "/dl/empty":
            self._send(200, b"", "application/octet-stream", [("Content-Disposition", 'attachment; filename="empty.txt"')])
        elif p == "/dl/accent":
            name = "résumé final é.txt"
            self._send(200, b"accent file body\n", "application/octet-stream",
                       [("Content-Disposition", "attachment; filename=\"resume final e.txt\"; filename*=UTF-8''" + urllib.parse.quote(name))])
        elif p == "/dl/big":
            size = 200 * 1024 * 1024
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", 'attachment; filename="big.bin"')
            self.end_headers()
            chunk = b"A" * (1024 * 1024)
            try:
                for _ in range(200):
                    self.wfile.write(chunk)
                log({"ev": "big-served"})
            except Exception:
                log({"ev": "big-aborted"})
        elif p == "/dl/stuck":
            log({"ev": "stuck-start"})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(50 * 1024 * 1024))
            self.send_header("Content-Disposition", 'attachment; filename="stuck.bin"')
            self.end_headers()
            try:
                self.wfile.write(b"B" * 1024)
                self.wfile.flush()
                while True:  # never finishes, until the client drops the connection
                    time.sleep(5)
                    self.wfile.write(b"")
                    self.connection.send(b"")
            except Exception:
                log({"ev": "stuck-client-gone"})
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/mode":
            MODE[0] = urllib.parse.parse_qs(u.query).get("m", ["ok"])[0]
            return self._send(200, MODE[0], "text/plain")
        if u.path == "/reset":
            open(LOG, "w").close()
            return self._send(200, "ok", "text/plain")
        if u.path != "/upload":
            return self._send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        # fast multipart split (the email parser took ~10 s per 100 MB, longer than the runtime waits for the page's reply)
        bnd = b"--" + self.headers["Content-Type"].split("boundary=")[1].strip().strip('"').encode()
        files = []
        for part in body.split(bnd)[1:-1]:
            head, _, data = part.partition(b"\r\n\r\n")
            m = re.search(rb'filename="([^"]*)"', head)
            if not m:
                continue
            data = data[:-2] if data.endswith(b"\r\n") else data
            files.append({"name": m.group(1).decode("utf-8"), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        page = urllib.parse.parse_qs(u.query).get("page", ["?"])[0]
        for f in files:
            log({"ev": "file", "page": page, **f})
        log({"ev": "upload", "page": page, "count": len(files)})
        self._send(200, json.dumps({"count": len(files), "files": files}), "application/json")


if __name__ == "__main__":
    print("filesrv on 8097, log", LOG, flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8097), H).serve_forever()
