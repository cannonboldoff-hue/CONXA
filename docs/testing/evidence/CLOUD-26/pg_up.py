"""Start a throwaway local Postgres (pgserver) and print its URI. Keeps running until killed."""
import sys, time, pgserver

srv = pgserver.get_server(sys.argv[1], cleanup_mode="stop")
print(srv.get_uri(), flush=True)
while True:
    time.sleep(3600)
