"""流式的两种「响应头已发、之后出事」：

1. 上游流中途断开：客户端此前只看到流突然结束（缺 `[DONE]` 常被当成正常结束）。
   现在按协议补一个原生错误事件，且不发 `[DONE]`。
2. 上游排队超过 `stream_commit_sec` 还没回响应头：先发响应头与保活注释，免得客户端按自己的超时断开；
   之后上游再报错，以协议内错误事件告知。阈值之内的错误仍回真实 HTTP 状态码。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

TOK = "t" * 43

PROG = r"""
import http.client, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway.app import Gateway
from gateway.server import GatewayServer, make_handler
from mlx_server.result import Ok

CUT = {
    "chat": b'data: {"id":"x","object":"chat.completion.chunk","model":"M","choices":[{"index":0,"delta":{"content":"a"}}]}\n\n',
    "resp": b'event: response.created\ndata: {"type":"response.created","sequence_number":0,"response":{"id":"r1","object":"response","status":"in_progress","model":"M"}}\n\n',
    "msgs": b'event: message_start\ndata: {"type":"message_start","message":{"id":"m1","type":"message","role":"assistant","model":"M","content":[]}}\n\n',
}
ERR = b'{"error":{"message":"max_tokens must be at least 1","type":"invalid_request_error"}}'

class Up(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        mode = self.server.mode
        if mode in ("stall400", "fast400"):
            if mode == "stall400":
                time.sleep(1.2)
            self.send_response(400); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(ERR))); self.end_headers(); self.wfile.write(ERR)
            return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers()
        self.wfile.write(CUT[mode]); self.wfile.flush()     # 一帧之后直接断：没有终止帧

def upstream(mode):
    s = ThreadingHTTPServer(("127.0.0.1", 0), Up); s.daemon_threads = True; s.mode = mode
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]

LM = {"model_arg": "M", "backend": "mlx_lm", "endpoints": ["chat", "completions"],
      "effort_domain": [], "reads_completion_tokens": True, "native_template_kwargs": True}
VLM = dict(LM, backend="mlx_vlm", endpoints=["chat", "responses", "messages"],
           reads_completion_tokens=False, native_template_kwargs=False)
MODELS = {"chat": (LM, "chat"), "resp": (VLM, "resp"), "msgs": (VLM, "msgs"),
          "stall": (LM, "stall400"), "fast": (LM, "fast400")}
PORTS = {k: upstream(m) for k, (_, m) in MODELS.items()}
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R(k, p) for k, p in PORTS.items()]
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: MODELS[s][0] if s in MODELS else None,
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
g = Gateway(None, facade, token="@TOK@", keepalive_sec=0.3, probe_sec=0.1, stream_commit_sec=0.5)
g.refresh_routes()
srv = GatewayServer(("127.0.0.1", 0), make_handler(g))
threading.Thread(target=srv.serve_forever, daemon=True).start()
GW = srv.server_address[1]

def call(path, body):
    c = http.client.HTTPConnection("127.0.0.1", GW, timeout=20)
    c.request("POST", path, body=json.dumps(body).encode(),
              headers={"Content-Type": "application/json", "Authorization": "Bearer @TOK@"})
    r = c.getresponse(); raw = r.read().decode("utf-8", "replace"); c.close()
    return r.status, raw

def last_event(raw):
    frames = [f for f in raw.strip().split("\n\n") if f.strip() and not f.startswith(":")]
    ev, data = None, None
    for line in frames[-1].split("\n"):
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
    return ev, json.loads(data)

MSG = [{"role": "user", "content": "hi"}]
out = {}
st, raw = call("/v1/chat/completions", {"model": "chat", "stream": True, "messages": MSG})
out["chat"] = [st, last_event(raw), "[DONE]" in raw]
st, raw = call("/v1/responses", {"model": "resp", "stream": True, "input": "hi"})
out["resp"] = [st, last_event(raw)]
st, raw = call("/v1/messages", {"model": "msgs", "stream": True, "max_tokens": 16, "messages": MSG})
out["msgs"] = [st, last_event(raw)]
st, raw = call("/v1/chat/completions", {"model": "stall", "stream": True, "messages": MSG})
out["stall"] = [st, raw.count(": keepalive"), last_event(raw)]
st, raw = call("/v1/chat/completions", {"model": "fast", "stream": True, "messages": MSG})
out["fast"] = [st, raw[:200]]
print(json.dumps(out))
"""


def test_mid_stream_abort_and_late_errors_reach_the_client(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=90, TOK=TOK)
    st, (ev, obj), done = got["chat"]
    assert st == 200 and ev is None and obj["error"]["code"] == "upstream_aborted" and not done, got["chat"]
    st, (ev, obj) = got["resp"]
    assert st == 200 and ev == "error" and obj["type"] == "error" and obj["sequence_number"] == 1, got["resp"]
    st, (ev, obj) = got["msgs"]
    assert st == 200 and ev == "error" and obj == {"type": "error", "error": {
        "type": "api_error", "message": "upstream backend aborted the stream"}}, got["msgs"]
    st, ka, (ev, obj) = got["stall"]
    assert st == 200 and ka >= 1, f"排队超过阈值应先发响应头与保活：{got['stall']}"
    assert obj["error"]["type"] == "invalid_request_error" and "max_tokens" in obj["error"]["message"], got["stall"]
    assert got["fast"][0] == 400, f"阈值之内的上游错误仍回真实状态码：{got['fast']}"
