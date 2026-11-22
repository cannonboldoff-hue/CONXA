#!/usr/bin/env python3
"""WF-17 test site: cookie login with server-side expiry, two-step login, two 'apps' (127.0.0.1 and localhost).
usage: wf17_authsite.py [port=8101]   (request log on stdout)
Users: demo / pass123.  Two-step: /login?two=1 sends a code page (code 123456) after the password.
/expire[?host=localhost] kills sessions on the server (emulates 'signed out from another browser').
/reports?slow=N sleeps N s then checks the session at answer time (window to expire mid-run)."""
import sys, time, secrets, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8101
SESS = {}   # sid -> host
PRE = {}    # pre-code token -> host
LOCK = threading.Lock()
APP = {"127.0.0.1": ("App A", "localhost"), "localhost": ("App B", "127.0.0.1")}


def page(title, body):
    return f"<!doctype html><html><head><meta charset=utf-8><title>{title}</title></head><body><main>{body}</main></body></html>".encode()


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print(f"{time.strftime('%H:%M:%S')} {self.headers.get('Host','')} {self.command} {self.path} cookie={'sid' in (self.headers.get('Cookie') or '')}", flush=True)

    def host(self):
        return (self.headers.get("Host") or "").split(":")[0]

    def sid(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sid":
                return v
        return ""

    def authed(self):
        with LOCK:
            return SESS.get(self.sid()) == self.host()

    def send(self, code, body, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, to, headers=()):
        self.send_response(302); self.send_header("Location", to); self.send_header("Content-Length", "0")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); h = self.host(); name, other = APP.get(h, ("App", ""))
        if u.path == "/login":
            if self.authed() and not q.get('err'): return self.redirect('/app')
            two = "1" if q.get("two") else ""
            err = "<p role=alert>Wrong username or password</p>" if q.get("err") else ""
            self.send(200, page(f"{name} sign in", f"<h1>{name} sign in</h1>{err}<form method=post action='/login?two={two}'><label>Username <input name=user></label><label>Password <input name=pw type=password></label><button type=submit>Sign in</button></form><p>Demo user: demo / pass123</p><a href='/app'>Skip, go to the app</a>"))
        elif u.path == "/code":
            self.send(200, page(f"{name} code", f"<h1>Enter the code we sent you</h1><form method=post action='/code'><label>Code <input name=code></label><button type=submit>Verify</button></form><p>Test code: 123456</p>"))
        elif u.path == "/app":
            if not self.authed():
                return self.redirect("/login")
            self.send(200, page(f"{name} home", f"<h1>{name} home</h1><p>You are signed in on {h}</p><a href='/reports'>Open reports</a> | <a href='/reports?slow=14'>Open slow reports</a> | <a href='http://{other}:{PORT}/app'>Go to the other app</a> | <a href='/logout'>Sign out</a>"))
        elif u.path == "/reports":
            slow = float((q.get("slow") or ["0"])[0]); time.sleep(slow)
            if not self.authed():
                return self.redirect("/login")
            self.send(200, page(f"{name} reports", f"<h1>Reports ready</h1><p>{name} reports loaded</p><a href='/app'>Back to home</a>"))
        elif u.path == "/logout":
            with LOCK:
                SESS.pop(self.sid(), None)
            self.redirect("/login", [("Set-Cookie", "sid=; Path=/; Max-Age=0")])
        elif u.path == "/expire":
            with LOCK:
                only = (q.get("host") or [""])[0]
                n = 0
                for k in list(SESS):
                    if not only or SESS[k] == only:
                        SESS.pop(k); n += 1
            self.send(200, page("expired", f"expired {n} sessions"))
        elif u.path == "/status":
            with LOCK:
                self.send(200, page("status", f"sessions {sorted(SESS.values())}"))
        else:
            self.send(404, page("404", "not found"))

    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query); h = self.host()
        n = int(self.headers.get("Content-Length") or 0); form = parse_qs(self.rfile.read(n).decode())
        if u.path == "/login":
            if self.authed() and not q.get('err'): return self.redirect('/app')
            if form.get("user", [""])[0] == "demo" and form.get("pw", [""])[0] == "pass123":
                if q.get("two"):
                    tok = secrets.token_hex(8)
                    with LOCK:
                        PRE[tok] = h
                    return self.redirect("/code", [("Set-Cookie", f"pre={tok}; Path=/")])
                sid = secrets.token_hex(16)
                with LOCK:
                    SESS[sid] = h
                return self.redirect("/app", [("Set-Cookie", f"sid={sid}; Path=/")])
            return self.redirect("/login?err=1" + ("&two=1" if q.get("two") else ""))
        if u.path == "/code":
            tok = next((p.strip()[4:] for p in (self.headers.get("Cookie") or "").split(";") if p.strip().startswith("pre=")), "")
            if form.get("code", [""])[0] == "123456" and PRE.get(tok) == h:
                sid = secrets.token_hex(16)
                with LOCK:
                    SESS[sid] = h
                return self.redirect("/app", [("Set-Cookie", f"sid={sid}; Path=/")])
            return self.send(200, page("code", "<h1>Enter the code we sent you</h1><p role=alert>That code is not right</p><form method=post action='/code'><label>Code <input name=code></label><button type=submit>Verify</button></form>"))
        self.send(404, page("404", "not found"))


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
