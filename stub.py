"""A two-turn OpenAI-compatible endpoint: write a file, then answer."""
import json, http.server, threading
TURNS = [
    {"role":"assistant","content":None,"tool_calls":[{"id":"c1","type":"function","function":{
        "name":"write_file","arguments":json.dumps({"path":"hello.py","content":"print('made by the agent')\n"})}}]},
    {"role":"assistant","content":None,"tool_calls":[{"id":"c2","type":"function","function":{
        "name":"run","arguments":json.dumps({"command":"python3 hello.py"})}}]},
    {"role":"assistant","content":"Created hello.py and ran it."},
]
state = {"n": 0}
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        msg = TURNS[min(state["n"], len(TURNS)-1)]; state["n"] += 1
        body = json.dumps({"choices":[{"message":msg,"finish_reason":"stop"}]}).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 8931), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print("ready", flush=True)
import time; time.sleep(60)
