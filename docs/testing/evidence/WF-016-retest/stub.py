import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(s):
        p=s.path.split('?')[0]
        code={'/s401':401,'/s403':403,'/s304':304}.get(p,200)
        b=b'{"detail":"invalid_sync_token"}' if code in(401,403) else (b'{"ok":true}' if code==200 else b'')
        s.send_response(code); s.send_header('Content-Type','application/json'); s.send_header('Content-Length',str(len(b))); s.end_headers(); s.wfile.write(b)
    def log_message(s,f,*a): print(s.path.split('?')[0], flush=True)
http.server.HTTPServer(('127.0.0.1',8096),H).serve_forever()
