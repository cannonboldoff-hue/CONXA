#!/usr/bin/env python3
"""WF-17 MOCK identity provider (NOT Clerk) so Studio's own PKCE login code can be exercised offline.
usage: wf17_mock_clerk.py [port=8110]. Control: GET /_ctl?mode=approve|abandon|deny&acct=1|2&ttl=SECONDS&revoke=0|1 . Log on stdout.
Checks PKCE (sha256(verifier) must equal the challenge sent to /oauth/authorize)."""
import sys, json, time, base64, hashlib, secrets
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8110
S = {"mode": "approve", "acct": "1", "ttl": 3600, "revoke": False}; CODES = {}; N = [0]
class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a): print(time.strftime("%H:%M:%S"), self.command, self.path.split("&code_challenge")[0][:140], flush=True)
    def j(self, code, o): b = json.dumps(o).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/_ctl":
            for k in ("mode", "acct"):
                if k in q: S[k] = q[k]
            if "ttl" in q: S["ttl"] = int(q["ttl"])
            if "revoke" in q: S["revoke"] = q["revoke"] == "1"
            return self.j(200, S)
        if u.path == "/oauth/authorize":
            if S["mode"] == "abandon":
                b = b"<h1>Mock sign-in (waiting for a person)</h1>"; self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(b))); self.end_headers(); return self.wfile.write(b)
            if S["mode"] == "deny":
                self.send_response(302); self.send_header("Location", f"{q['redirect_uri']}?error=access_denied&error_description=The+person+said+no&state={q['state']}"); self.send_header("Content-Length", "0"); return self.end_headers()
            N[0] += 1; code = f"code-{S['acct']}-{N[0]}"; CODES[code] = q["code_challenge"]
            self.send_response(302); self.send_header("Location", f"{q['redirect_uri']}?code={code}&state={q['state']}"); self.send_header("Content-Length", "0"); return self.end_headers()
        if u.path == "/oauth/userinfo":
            tok = (self.headers.get("Authorization") or "").split(" ")[-1]; a = tok.split("-")[1] if tok.startswith("at-") else None
            if not a: return self.j(401, {"error": "invalid_token"})
            return self.j(200, {"sub": f"user_mock{a}", "email": f"mock{a}@example.test", "name": f"Mock Person {a}", "org_id": f"org_mock{a}"})
        self.j(404, {})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); f = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if self.path == "/oauth/token":
            if f.get("grant_type") == "authorization_code":
                ch = CODES.pop(f.get("code", ""), None)
                v = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if ch is None or v != ch: print("  PKCE/code REJECTED", flush=True); return self.j(400, {"error": "invalid_grant", "error_description": "bad code or verifier"})
                a = f["code"].split("-")[1]; N[0] += 1
                print(f"  token issued acct={a} ttl={S['ttl']} (PKCE verified)", flush=True)
                return self.j(200, {"access_token": f"at-{a}-{N[0]}", "refresh_token": f"rt-{a}-{N[0]}", "expires_in": S["ttl"]})
            if f.get("grant_type") == "refresh_token":
                if S["revoke"]: return self.j(400, {"error": "invalid_grant", "error_description": "refresh token revoked"})
                a = f["refresh_token"].split("-")[1]; N[0] += 1; print(f"  REFRESH served acct={a}", flush=True)
                return self.j(200, {"access_token": f"at-{a}-{N[0]}", "refresh_token": f["refresh_token"], "expires_in": S["ttl"]})
        self.j(404, {})
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
