#!/usr/bin/env python3
"""WF-19 MOCK language model (NOT a real model): OpenAI-compatible /v1/chat/completions that streams a SCRIPTED reply.
  last user text has "what skills"  -> tool call list_skills
  last user text has "run <name>"   -> tool call execute_skill {skill:<name>, inputs:{}}
  last message is a tool result     -> short text that quotes the result
  anything else                     -> "MOCK MODEL says hello ..."
usage: wf19_mock_llm.py [port=8130]. Every request is logged: time, bearer key, message count, tool names. Proves wiring, not intelligence."""
import sys, json, time, re, os
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8130
def sse(h, o): h.wfile.write(("data: " + json.dumps(o) + "\n\n").encode()); h.wfile.flush()
def text_of(m):
    c = m.get("content")
    return c if isinstance(c, str) else " ".join(p.get("text", "") for p in (c or []) if isinstance(p, dict))
class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); b = json.loads(self.rfile.read(n) or b"{}")
        msgs = b.get("messages", []); last = msgs[-1] if msgs else {}
        print(f"{time.strftime('%H:%M:%S')} POST {self.path} key={self.headers.get('Authorization','')[:20]} model={b.get('model')} msgs={len(msgs)} tools={[t['function']['name'] for t in b.get('tools', [])]} last_role={last.get('role')}", flush=True)
        for m in msgs[2:]:
            if m.get("tool_calls"): print("   assistant tool_calls:", json.dumps(m["tool_calls"])[:300], flush=True)
            if m.get("role") == "tool": print("   tool result:", text_of(m)[:200].replace(chr(10), " "), flush=True)
        user = next((text_of(m) for m in reversed(msgs) if m.get("role") == "user"), "")
        calls, reply = None, None
        if last.get("role") == "tool":
            prior = next((m for m in reversed(msgs) if m.get("tool_calls")), {})
            nm = prior["tool_calls"][0]["function"]["name"] if prior else ""
            out = text_of(last)
            if nm == "list_skills":
                try: names = [s.get("skill") or s.get("name") for s in json.loads(out).get("skills", [])]
                except Exception: names = []
                reply = "MOCK MODEL: you have these skills: " + ", ".join(str(x) for x in names) + "."
            else: reply = f"MOCK MODEL: the {nm} call returned: " + out.strip().replace("\n", " ")[:300]
        elif user.startswith("[Conxa run update]"): reply = "MOCK MODEL: the run has ended (follow-up note)."
        elif re.search(r"what skills", user, re.I): calls = [("list_skills", {})]
        elif re.search(r"\brun\s+([\w\-]+)", user, re.I):
            calls = [("execute_skill", {"skill": re.search(r"\brun\s+([\w\-]+)", user, re.I).group(1), "inputs": {}})]
        else: reply = f"MOCK MODEL says hello (I saw {len(msgs)} messages)."
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
        base = {"id": "mock-1", "object": "chat.completion.chunk", "model": "mock"}
        if calls:
            # Real models usually say a few words before a tool call. Tool-ONLY replies (file TOOL_ONLY exists next to this script) expose the cloud router's retry-on-empty-text bug.
            if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "TOOL_ONLY")):
                sse(self, {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "One moment. "}}]})
            for i, (nm, args) in enumerate(calls):
                sse(self, {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": i, "id": f"call_{i}", "type": "function", "function": {"name": nm, "arguments": ""}}]}}]})
                sse(self, {**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "function": {"arguments": json.dumps(args)}}]}}]})
            sse(self, {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            words = reply.split(" ")
            for i in range(0, len(words), 2):
                sse(self, {**base, "choices": [{"index": 0, "delta": {"content": " ".join(words[i:i + 2]) + " "}}]}); time.sleep(0.15)
            sse(self, {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
