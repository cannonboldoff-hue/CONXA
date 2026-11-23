#!/usr/bin/env python3
"""WF-21 mock Clerk BACKEND API for the Next dashboard (CLERK_API_URL=http://127.0.0.1:8163): serves /v1/jwks (RS256 test key) so the dashboard's server-side
session check accepts mock-minted session cookies. Logs every request, 404s the rest. Never talks to the internet."""
import os, sys, json, base64, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, os.path.dirname(__file__)); import wf21_tok as T
pn = T.PRIV.public_key().public_numbers()
b64 = lambda n: base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
JWKS = {"keys": [{"kty": "RSA", "kid": "wf20-key", "use": "sig", "alg": "RS256", "n": b64(pn.n), "e": b64(pn.e)}]}
class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a): print(time.strftime("%H:%M:%S"), "BAPI", self.command, self.path[:120], flush=True)
    def do_GET(self):
        ok = self.path.startswith("/v1/jwks"); b = json.dumps(JWKS if ok else {"errors": [{"message": "mock: not implemented"}]}).encode()
        self.send_response(200 if ok else 404); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8163), H).serve_forever()
