#!/usr/bin/env python3
"""WF-19 MOCK identity provider (NOT Clerk). Serves what Execute's real PKCE code and the cloud backend's real JWT check need:
  /oauth/authorize (302 to redirect_uri), /oauth/token (code + refresh, PKCE checked), /oauth/userinfo,
  /.well-known/jwks.json (RS256 key), Clerk Backend API stand-in /v1/users/{id}/organization_memberships,
  /_mint?acct=..&org=..&role=.. (a bearer token for API calls made by the tester), /_ctl?exec=1&studio=2 (account per app).
Account is chosen by the callback port: 5284x -> Execute account, 5274x -> Studio account (so two apps signing in at once get different people).
usage: wf19_mock_clerk.py [port=8110]   (log on stdout)"""
import sys, json, time, base64, hashlib, secrets, re, os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8110
ISS = f"http://127.0.0.1:{PORT}"
KF = os.environ.get("WF19_KEY", "")  # optional pem path so a restart keeps the same key
if KF and os.path.exists(KF):
    PRIV = serialization.load_pem_private_key(open(KF, "rb").read(), None)
else:
    PRIV = rsa.generate_private_key(65537, 2048)
    if KF:
        open(KF, "wb").write(PRIV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
PUB = PRIV.public_key().public_numbers()
def b64(n): return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
KID = "wf19-key"
JWKS = {"keys": [{"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256", "n": b64(PUB.n), "e": b64(PUB.e)}]}

ACCTS = {
    "1": {"sub": "user_mock1", "email": "mock1@example.test", "name": "Mock Person 1", "orgs": [("org_alpha", "Alpha Team (mock)", "org:member")]},
    "2": {"sub": "user_mock2", "email": "mock2@example.test", "name": "Mock Person 2", "orgs": []},
    "3": {"sub": "user_newseat", "email": "newseat@example.test", "name": "New Seat Person", "orgs": []},
    "admin": {"sub": "user_admin", "email": "admin@example.test", "name": "Mock Admin", "orgs": [("org_alpha", "Alpha Team (mock)", "org:admin"), ("org_gamma", "Gamma Team (mock)", "org:admin")]},
    "4": {"sub": "user_solo", "email": "solo@example.test", "name": "Solo Person", "orgs": []},
}
PENDING = []  # authorize URLs left waiting in abandon mode: /_ctl?pending=1 lists them so a tester can "approve later"
S = {"exec": "1", "studio": "2", "ttl": 3600, "mode": "approve"}; CODES = {}; N = [0]; BYSUB = {a["sub"]: k for k, a in ACCTS.items()}

def mint(acct, org=None, role=None, ttl=None, client="mock-client"):
    a = ACCTS[acct]; now = int(time.time())
    claims = {"iss": ISS, "sub": a["sub"], "email": a["email"], "name": a["name"], "iat": now, "exp": now + int(ttl or S["ttl"]), "client_id": client, "jti": secrets.token_hex(6)}
    if org: claims["org_id"] = org; claims["org_role"] = role or "org:member"
    return jwt.encode(claims, PRIV, algorithm="RS256", headers={"kid": KID})

class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a): print(time.strftime("%H:%M:%S"), self.command, self.path.split("&code_challenge")[0][:150], flush=True)
    def j(self, code, o):
        b = json.dumps(o).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/_ctl":
            for k in ("exec", "studio", "mode"):
                if k in q: S[k] = q[k]
            if "ttl" in q: S["ttl"] = int(q["ttl"])
            if "pending" in q: return self.j(200, PENDING)
            return self.j(200, S)
        if u.path == "/_mint": return self.j(200, {"token": mint(q.get("acct", "1"), q.get("org"), q.get("role"), q.get("ttl"))})
        if u.path == "/.well-known/jwks.json": return self.j(200, JWKS)
        if u.path == "/oauth/authorize":
            m = re.search(r":(\d+)/", q.get("redirect_uri", "")); port = int(m.group(1)) if m else 0
            who = "exec" if 52841 <= port <= 52850 else "studio" if 52741 <= port <= 52750 else "exec"
            if S["mode"] == "abandon":
                PENDING.append({"who": who, "port": port, "path": self.path, "at": time.strftime("%H:%M:%S")})
                b = b"<h1>Mock sign-in (waiting for a person)</h1>"; self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(b))); self.end_headers(); return self.wfile.write(b)
            acct = S[who]; N[0] += 1; code = f"code-{acct}-{N[0]}"; CODES[code] = q["code_challenge"]
            print(f"  authorize for {who} (callback port {port}) -> account {acct} {ACCTS[acct]['email']}", flush=True)
            self.send_response(302); self.send_header("Location", f"{q['redirect_uri']}?code={code}&state={q['state']}"); self.send_header("Content-Length", "0"); return self.end_headers()
        if u.path == "/oauth/userinfo":
            tok = (self.headers.get("Authorization") or "").split(" ")[-1]
            try: c = jwt.decode(tok, PRIV.public_key(), algorithms=["RS256"], options={"verify_aud": False})
            except Exception as e: return self.j(401, {"error": "invalid_token", "why": str(e)})
            return self.j(200, {"sub": c["sub"], "email": c["email"], "name": c["name"]})
        m = re.match(r"^/v1/users/([^/]+)/organization_memberships", u.path)
        if m:
            a = ACCTS.get(BYSUB.get(m.group(1), ""), None)
            return self.j(200, {"data": [{"organization": {"id": i, "name": n}, "role": r} for i, n, r in (a["orgs"] if a else [])]})
        self.j(404, {})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); f = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if self.path == "/oauth/token":
            if f.get("grant_type") == "authorization_code":
                ch = CODES.pop(f.get("code", ""), None)
                v = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if ch is None or v != ch: print("  PKCE/code REJECTED", flush=True); return self.j(400, {"error": "invalid_grant", "error_description": "bad code or verifier"})
                a = f["code"].split("-")[1]; N[0] += 1
                print(f"  token issued acct={a} (PKCE verified) client_id={f.get('client_id')}", flush=True)
                return self.j(200, {"access_token": mint(a, client=f.get("client_id", "")), "refresh_token": f"rt-{a}-{N[0]}", "expires_in": S["ttl"]})
            if f.get("grant_type") == "refresh_token":
                a = f["refresh_token"].split("-")[1]; N[0] += 1; print(f"  REFRESH served acct={a}", flush=True)
                return self.j(200, {"access_token": mint(a, client=f.get("client_id", "")), "refresh_token": f["refresh_token"], "expires_in": S["ttl"]})
        self.j(404, {})
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
