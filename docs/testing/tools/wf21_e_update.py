#!/usr/bin/env python3
r"""WF-21 phase 5 LIVE drill of the app-layer self-update (bootstrap.js -> manifest_manager.js) against the wf21 backend (:8160, signs with an Ed25519 test key).
Isolated: %TEMP%\wf21\upd (own CONXA_DIR/DATA_DIR/APP_DIR, own copy of runtime host/app with the TEST public key stamped into package.json, zip served on :8171).
Not the packaged exe (that needs a pkg build); same bootstrap.js + manifest_manager.js + min_host_gate.js code. Needs wf21_ctl.py up.
Scenarios: S1 same version; S2 newer app layer downloads+loads; S3 min_host too new -> not taken; S4 sha256 mismatch -> not taken; S5 bad update (server.js throws) -> rollback to last good;
S6 manifest signed by a key the host does not trust -> discarded."""
import os, sys, json, shutil, subprocess, zipfile, hashlib, time, base64, threading, urllib.request, http.server, functools
sys.path.insert(0, os.path.dirname(__file__)); import wf21_ctl as C
ROOT = C.ROOT; U = os.path.join(C.W, "upd"); RT = os.path.join(U, "rt"); CD = os.path.join(U, "conxa"); DATA = os.path.join(U, "data"); ZIPS = os.path.join(U, "zips")
API = f"http://127.0.0.1:{C.BP}"; out = []


def p(*a):
    s = " ".join(str(x) for x in a); print(s); out.append(s)


def pub_b64(pem_path):
    from cryptography.hazmat.primitives import serialization as s
    k = s.load_pem_private_key(open(pem_path, "rb").read(), None).public_key()
    return base64.b64encode(k.public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode()


def stamp(pub):
    f = os.path.join(RT, "package.json"); pj = json.load(open(f)); pj["ed25519_public_key"] = pub; pj["host_version"] = "host-v1.0.0"; json.dump(pj, open(f, "w"))


def setup():
    shutil.rmtree(U, ignore_errors=True); os.makedirs(ZIPS); os.makedirs(CD + "/conxa-app"); os.makedirs(DATA)
    for d in ("host", "app"):
        shutil.copytree(os.path.join(ROOT, "runtime", d), os.path.join(RT, d))
    shutil.copy(os.path.join(ROOT, "runtime", "package.json"), RT)
    subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(RT, "vendor").replace("/", "\\"), os.path.join(ROOT, "runtime", "vendor").replace("/", "\\")], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(RT, "node_modules").replace("/", "\\"), os.path.join(ROOT, "runtime", "node_modules").replace("/", "\\")], check=True, stdout=subprocess.DEVNULL)
    stamp(pub_b64(os.path.join(C.W, "manifest_ed25519.pem")))


def mkzip(version, min_host="0.0.0", broken=False):
    """app layer zip: copy of runtime/app (+ version.json); broken -> server.js throws on load."""
    d = os.path.join(U, "build_" + version); shutil.rmtree(d, ignore_errors=True); shutil.copytree(os.path.join(ROOT, "runtime", "app"), d)
    json.dump({"app_version": version, "min_host": min_host, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}, open(d + "/version.json", "w"))
    if broken:
        open(d + "/server.js", "w").write("throw new Error('WF21 deliberately broken app layer');\n")
    zp = os.path.join(ZIPS, f"conxa-app-{version}.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _d, fs in os.walk(d):
            for f in fs:
                z.write(os.path.join(r, f), os.path.relpath(os.path.join(r, f), d))
    return zp, hashlib.sha256(open(zp, "rb").read()).hexdigest()


def install_v1():
    d = os.path.join(CD, "conxa-app", "app-v1.0.0"); shutil.copytree(os.path.join(ROOT, "runtime", "app"), d)
    json.dump({"app_version": "app-v1.0.0", "min_host": "0.0.0"}, open(d + "/version.json", "w"))
    code = "require(%s).activate(%s, %s, {requiredFiles:['server.js'],keep:3})" % (json.dumps(RT + "/app/version_manager.js"), json.dumps(CD + "/conxa-app"), json.dumps(d))
    subprocess.run(["node", "-e", code], check=True)


def publish(version, sha, min_host="0.0.0"):
    body = {"version": version, "released_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "min_host": min_host, "files": [{"filename": f"conxa-app-{version}.zip", "url": f"http://127.0.0.1:8171/conxa-app-{version}.zip", "sha256": sha}]}
    rq = urllib.request.Request(f"{API}/api/v1/admin/component-versions/conxa_app", data=json.dumps(body).encode(), method="POST", headers={"Authorization": "Bearer " + C.ADMIN, "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(rq, timeout=15))


def current():
    try:
        return os.path.basename(os.path.realpath(os.path.join(CD, "conxa-app", "current")))
    except Exception:
        return None


def boot(label, secs=14):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CONXA_", "SKILL_"))}
    env.update({"CONXA_ENV": "prod", "CONXA_DIR": CD, "CONXA_DATA_DIR": DATA, "CONXA_APP_DIR": CD + "/conxa-app", "CONXA_API_URL": API, "CONXA_UPDATE_CHANNEL": "stable"})
    pr = subprocess.Popen(["node", os.path.join(RT, "host", "bootstrap.js")], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=U, creationflags=0x08000000)
    time.sleep(secs); subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr.pid)], capture_output=True)
    err = pr.stderr.read().decode(errors="replace")
    open(os.path.join(os.path.dirname(__file__), "..", "evidence", "WF-021", "wf21_e_stderr_%s.txt" % label.split()[0]), "w").write(err[-4000:])
    lines = [ln for ln in err.splitlines() if "[bootstrap]" in ln or "manifest" in ln.lower() or "app_update" in ln or "WF21" in ln]
    p(f"[{label}] current={current()} | stderr: " + (" || ".join(ln[:170] for ln in lines[:6]) or "(no bootstrap lines)"))
    return err


def main():
    setup(); install_v1()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8171), functools.partial(http.server.SimpleHTTPRequestHandler, directory=ZIPS))
    srv.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    p("setup: current=%s (installed app-v1.0.0), host_version=host-v1.0.0, test public key stamped" % current())
    m0 = json.load(urllib.request.urlopen(API + "/api/v1/manifest.json", timeout=10)); p("manifest signed:", bool(m0.get("signature")), "conxa_app default:", m0["conxa_app"]["version"])
    publish("app-v1.0.0", "0" * 64); boot("S1 same version published -> no update")
    zp, sha = mkzip("app-v1.1.0"); publish("app-v1.1.0", sha); boot("S2 newer app layer app-v1.1.0 (valid)")
    ok = current() == "app-v1.1.0"; p("S2 PASS" if ok else "S2 FAIL", "- app-v1.1.0 active:", ok, "dirs:", sorted(os.listdir(CD + "/conxa-app")))
    zp, sha = mkzip("app-v1.2.0", min_host="host-v9.0.0"); publish("app-v1.2.0", sha, "host-v9.0.0"); boot("S3 min_host host-v9.0.0 > host-v1.0.0")
    p("S3 PASS" if current() == "app-v1.1.0" and not os.path.exists(CD + "/conxa-app/app-v1.2.0") else "S3 FAIL", "- not downloaded, current stays", current())
    zp, sha = mkzip("app-v1.3.0"); publish("app-v1.3.0", "f" * 64); boot("S4 sha256 mismatch on app-v1.3.0")
    p("S4 PASS" if current() == "app-v1.1.0" and not os.path.exists(CD + "/conxa-app/app-v1.3.0") else "S4 FAIL", "- current", current())
    zp, sha = mkzip("app-v1.4.0", broken=True); publish("app-v1.4.0", sha); err = boot("S5 bad update app-v1.4.0 (server.js throws)", 20)
    ok5 = current() == "app-v1.1.0" and "rolled back" in err; p("S5 PASS" if ok5 else "S5 FAIL", "- rolled back to", current(), "| 'rolled back' logged:", "rolled back" in err)
    err2 = boot("S5b next launch after rollback", 20); p("S5b current after 2nd launch:", current(), "(bad version tried again:", "WF21" in err2, ")")
    zp, sha = mkzip("app-v1.5.0"); publish("app-v1.5.0", sha)
    other = os.path.join(U, "other.pem")
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization as s
    open(other, "wb").write(ed25519.Ed25519PrivateKey.generate().private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
    stamp(pub_b64(other))
    try:
        os.remove(CD + "/manifest.json")
    except Exception:
        pass
    cur_before = current(); boot("S6 host trusts a different Ed25519 key (manifest signature must fail)")
    p("S6 PASS" if current() == cur_before and not os.path.exists(CD + "/conxa-app/app-v1.5.0") else "S6 FAIL", "- no update from a manifest that fails verification; current", current())
    srv.shutdown()
    open(os.path.join(os.path.dirname(__file__), "..", "evidence", "WF-021", "wf21_e_update.txt"), "w").write("\n".join(out) + "\n")


main()
