"""Time the saas metadata document ops against the already-seeded 500-tenant Postgres (run after bench pg 500)."""
import os, sys, time, cProfile, pstats
os.environ["SKILL_DATABASE_URL"] = sys.argv[1]
os.environ["SKILL_DATA_DIR"] = sys.argv[2]
os.environ["SKILL_AUTH_REQUIRED"] = "false"
os.environ["SKILL_ALLOW_NO_PROVIDERS"] = "1"
root = r"C:\Users\kiran\Desktop\Conxa"
sys.path[:0] = [rf"{root}\conxa-cloud\backend", rf"{root}\packages\conxa-core"]
from fastapi.testclient import TestClient
from app.main import app
from app.services import saas

def t(fn, n=30):
    s = time.perf_counter()
    for _ in range(n):
        fn()
    return round((time.perf_counter() - s) * 1000 / n, 1)

st = saas._read_state()
print("read_state_ms", t(saas._read_state), "write_state_ms", t(lambda: saas._write_state(st)))
c = TestClient(app)
c.get("/api/v1/entitlements/current")
pr = cProfile.Profile(); pr.enable()
for _ in range(20):
    c.get("/api/v1/entitlements/current")
pr.disable()
pstats.Stats(pr).sort_stats("cumulative").print_stats("saas.py|entitlements.py|db.py", 18)
