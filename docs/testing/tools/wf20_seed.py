r"""WF-20 test-data seeding in the ISOLATED test store (%TEMP%\wf20\cloud-data) for things the API cannot do without waiting days / real payment:
  wf20_seed.py billing <workspace_id> '<json patch>'     e.g. backdate trial_started_at, expire a timed grant, set entitlement_overrides
  wf20_seed.py wallet <workspace_id> <compile_credits> <ai_tokens>   credit the add-on wallet exactly as a verified Cashfree payment would (grant_credit_wallet)
Runs the real product functions (upsert_billing / grant_credit_wallet) with the test backend's env, so it only ever touches the test data dir."""
import sys, os, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("WF20_SEED_CHILD") != "1":
    import wf20_ctl
    env = wf20_ctl.base_env(); env["WF20_SEED_CHILD"] = "1"
    sys.exit(subprocess.run([sys.executable, os.path.abspath(__file__)] + sys.argv[1:], env=env, cwd=os.path.join(wf20_ctl.W, "run")).returncode)
from conxa_core.config import settings
assert "wf20" in str(settings.data_dir).lower(), f"refusing: data_dir={settings.data_dir}"
from app.services.saas import upsert_billing
from app.services.entitlements import grant_credit_wallet
cmd = sys.argv[1]
if cmd == "billing":
    print(json.dumps(upsert_billing(sys.argv[2], json.loads(sys.argv[3])))[:300])
elif cmd == "agerun":  # agerun <company> <run_id> <days_old>: make a stored run look <days_old> days old (server_ts + event ts), to test analytics retention
    import time
    from conxa_core.db import db_get, db_set
    ns, key, days = f"tracking/{sys.argv[2]}", sys.argv[3], float(sys.argv[4])
    data = db_get(ns, key); batches = data if isinstance(data, list) else [data]
    shift = days * 86400
    for b in batches:
        b["server_ts"] = time.time() - shift
        for e in b.get("events", []):
            if isinstance(e.get("ts"), (int, float)): e["ts"] = int(time.time() * 1000 - shift * 1000)
    db_set(ns, key, batches); print("aged", key, days, "days")
elif cmd == "usage":  # usage <ws> <compile_credits_used>: pre-fill this month's committed compile credits (same row a real commit writes)
    from app.services.entitlements import USAGE_NS, usage_key, current_period, _usage_defaults
    from conxa_core.db import db_get, db_set
    per = current_period(); k = usage_key(sys.argv[2], per)
    row = db_get(USAGE_NS, k) or _usage_defaults(sys.argv[2], per)
    row["compile_credits_used"] = int(sys.argv[3]); db_set(USAGE_NS, k, row); print("usage set", k, row["compile_credits_used"])
elif cmd == "workflows":  # workflows <ws> <n> [prefix]: n published-workflow ledger rows via the real record_published_workflow, oldest first
    import time
    from app.services.entitlements import record_published_workflow
    pre = sys.argv[4] if len(sys.argv) > 4 else "s"
    for i in range(int(sys.argv[3])):
        record_published_workflow(sys.argv[2], f"{pre}{i:03d}"); time.sleep(0.002)
    print("ledger rows", sys.argv[3])
elif cmd == "wallet":
    print(grant_credit_wallet(sys.argv[2], compile_credits=int(sys.argv[3]), human_edit_tokens=int(sys.argv[4])))
elif cmd == "kv":  # kv <namespace> <key> '<json>': one KV row (e.g. cashfree_sub_workspace mapping that checkout would have stored)
    from conxa_core.db import db_set
    db_set(sys.argv[2], sys.argv[3], json.loads(sys.argv[4])); print("kv set", sys.argv[2], sys.argv[3])
