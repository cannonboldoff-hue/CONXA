import wf21_tok as T, urllib.request, sys
SFX = "g_f7dfRx"  # sha1(publishable key) base64url[:8]
def cookie(tok): return "; ".join(f"{n}={v}" for n, v in [("__session", tok), (f"__session_{SFX}", tok), ("__client_uat", "1700000000"), (f"__client_uat_{SFX}", "1700000000"), ("__clerk_db_jwt", "dvb_x"), (f"__clerk_db_jwt_{SFX}", "dvb_x")])
def get(path, tok=None, method="GET", body=None, extra=None):
    h = dict(extra or {})
    if tok: h["Cookie"] = cookie(tok)
    class NR(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    op = urllib.request.build_opener(NR)
    try: r = op.open(urllib.request.Request("http://localhost:3100" + path, headers=h, method=method, data=body), timeout=30); return r.status, r.headers.get("location"), r.read()[:300]
    except urllib.error.HTTPError as e: return e.code, e.headers.get("location"), e.read()[:300]
if __name__ == "__main__":
    tok = T.mint("user_owner", "org_a", "org:admin")
    for p in ("/dashboard", "/api/v1/entitlements/current"): print(p, "owner", get(p, tok))
