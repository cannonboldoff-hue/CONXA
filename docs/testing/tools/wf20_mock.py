#!/usr/bin/env python3
"""WF-20 MOCKS (not Clerk, not a real model): usage wf20_mock.py <clerk_port> <llm_port>
Clerk stand-in: /.well-known/jwks.json (RS256, test key made fresh each start unless WF20_KEY pem path exists),
  /_mint?sub=&org=&role=&email=&ttl=&client=&iss=&azp=&kid=   -> {"token"}   (ttl negative = already expired)
  /_ctl?api=ok|down500|drop   how the Clerk BACKEND API behaves (down500 = HTTP 500, drop = connection closed)
  /_ctl?count=<org>:<n>       member count the backend API reports for an org
  /v1/organizations/<org>/memberships   -> {"total_count": n}     (seat gate)
  /v1/users/<id>/organization_memberships -> {"data":[...]}       (role lookup / Execute contexts)
LLM stand-in: OpenAI-style /v1/chat/completions; logs the bearer key so a test can see WHICH pool answered.
Never talks to the internet."""
import sys, json, time, base64, secrets, re, os, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

CP = int(sys.argv[1]) if len(sys.argv) > 1 else 8141
LP = int(sys.argv[2]) if len(sys.argv) > 2 else 8142
ISS = f"http://127.0.0.1:{CP}"
KF = os.environ.get("WF20_KEY", "")
if KF and os.path.exists(KF):
    PRIV = serialization.load_pem_private_key(open(KF, "rb").read(), None)
else:
    PRIV = rsa.generate_private_key(65537, 2048)
    if KF:
        open(KF, "wb").write(PRIV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
PUB = PRIV.public_key().public_numbers()
def b64(n): return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
JWKS = {"keys": [{"kty": "RSA", "kid": "wf20-key", "use": "sig", "alg": "RS256", "n": b64(PUB.n), "e": b64(PUB.e)}]}
S = {"api": "ok", "counts": {}, "members": {}}  # members: sub -> {org: role}

def mint(q):
    now = int(time.time()); ttl = int(q.get("ttl") or 3600)
    c = {"iss": q.get("iss") or ISS, "sub": q.get("sub", "user_x"), "email": q.get("email", ""), "iat": now - 5, "exp": now + ttl,
         "client_id": q.get("client", "wf20-client"), "jti": secrets.token_hex(6)}
    if q.get("azp"): c["azp"] = q["azp"]; c.pop("client_id", None)
    if q.get("org"):
        c["org_id"] = q["org"]; c["org_role"] = q.get("role", "org:member")
        S["members"].setdefault(c["sub"], {})[q["org"]] = c["org_role"]
    return jwt.encode(c, PRIV, algorithm="RS256", headers={"kid": q.get("kid", "wf20-key")})

class Clerk(BaseHTTPRequestHandler):
    def log_message(self, f, *a): print(time.strftime("%H:%M:%S"), "CLERK", self.command, self.path[:140], flush=True)
    def j(self, code, o):
        b = json.dumps(o).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/.well-known/jwks.json": return self.j(200, JWKS)
        if u.path == "/_mint": return self.j(200, {"token": mint(q)})
        if u.path == "/_ctl":
            if "api" in q: S["api"] = q["api"]
            if "count" in q: o, n = q["count"].rsplit(":", 1); S["counts"][o] = int(n)
            return self.j(200, {"api": S["api"], "counts": S["counts"]})
        if u.path.startswith("/v1/"):
            if S["api"] == "drop": self.close_connection = True; self.connection.close(); return
            if S["api"] == "down500": return self.j(500, {"errors": [{"message": "mock outage"}]})
            m = re.match(r"^/v1/organizations/([^/]+)/memberships", u.path)
            if m: return self.j(200, {"data": [], "total_count": S["counts"].get(m.group(1), 1)})
            m = re.match(r"^/v1/users/([^/]+)/organization_memberships", u.path)
            if m: return self.j(200, {"data": [{"organization": {"id": o, "name": o}, "role": r} for o, r in S["members"].get(m.group(1), {}).items()]})
        self.j(404, {})

TL_DATA = json.dumps({"ui_action": [{"action_kind": "click", "visible_target": "SECRET screen text: Quarterly salary sheet", "intent": "open", "purpose": "p", "screen_before": "a", "screen_after": "b", "outside_page": False}]})
class LLM(BaseHTTPRequestHandler):
    def log_message(self, f, *a): pass
    def j(self, o):
        out = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def do_GET(self):  # TwelveLabs stand-in (video analysis) under /tl
        if "/assets/" in self.path: return self.j({"status": "ready"})
        if "/analyze/tasks/" in self.path: return self.j({"status": "ready", "result": {"data": TL_DATA}, "video_source": {"system_metadata": {"duration": 5}}})
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
    def do_DELETE(self): self.j({})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n) or b"{}"
        if self.path.endswith("/assets"): return self.j({"_id": "asset1"})
        if self.path.endswith("/analyze/tasks"): return self.j({"task_id": "task1"})
        b = json.loads(raw)
        key = (self.headers.get("Authorization") or "").replace("Bearer ", "") or ("byok:" + (self.headers.get("api-key") or ""))
        print(f"{time.strftime('%H:%M:%S')} LLM POST {self.path} key={key} model={b.get('model')}", flush=True)
        out = json.dumps({"id": "m", "object": "chat.completion", "model": b.get("model", "mock"),
                          "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({"ok": True, "pool_key": key})}}],
                          "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", LP), LLM).serve_forever, daemon=True).start()
ThreadingHTTPServer(("127.0.0.1", CP), Clerk).serve_forever()
