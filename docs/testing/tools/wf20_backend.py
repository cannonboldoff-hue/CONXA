# WF-20 launcher: the real cloud app with auth ON, skipping only the "production config complete" startup check (needs Postgres, Cashfree...).
# Test hook, no product file changes. usage: (cwd = a folder WITHOUT .env) python wf20_backend.py <port>
# Needs PYTHONPATH to include conxa-cloud/backend and docs/testing/tools/wf20_site (wf20_up.ps1 sets it).
import sys, uvicorn
import app.main as m
m._validate_production_config = lambda: None
uvicorn.run(m.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
