# WF-19 launcher: runs the real cloud app with auth ON but skips the "production config complete" startup check (needs DB, Cashfree...).
# Test hook only; no product file changes. usage: (cwd conxa-cloud/backend) python wf19_backend.py <port>
import sys, uvicorn
sys.path.insert(0, ".")
import app.main as m
m._validate_production_config = lambda: None
uvicorn.run(m.app, host="127.0.0.1", port=int(sys.argv[1]))
