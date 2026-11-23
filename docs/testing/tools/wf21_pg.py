#!/usr/bin/env python3
r"""WF-21 local Postgres (pgserver wheel in %TEMP%\wf21\pgvenv, no product code). usage: wf21_pg.py start|stop
start -> writes %TEMP%\wf21\pg_uri.txt. Data dir %TEMP%\wf21\pgdata."""
import os, sys, subprocess
W = os.path.join(os.environ["TEMP"], "wf21"); D = os.path.join(W, "pgdata")
PGCTL = os.path.join(W, "pgvenv", "Lib", "site-packages", "pgserver", "pginstall", "bin", "pg_ctl.exe")
if sys.argv[1] == "start":
    import pgserver
    db = pgserver.get_server(D, cleanup_mode=None); open(os.path.join(W, "pg_uri.txt"), "w").write(db.get_uri()); print(db.get_uri())
else:
    print(subprocess.run([PGCTL, "stop", "-D", D, "-m", "fast"], capture_output=True, text=True).stdout)
