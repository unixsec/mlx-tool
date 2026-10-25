"""网关层：REMOTE / LOOPBACK 两种来源的 4xx 回程；401 记日志；流式请求下后端 400 回给客户端 400 而不是 504。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import AS_STREAM, run_floor  # noqa: E402

NET = Path(__file__).resolve().parents[1] / "fixtures" / "net"
TOK = "t" * 43

PROG = AS_STREAM + r"""
import io, json, sys, threading, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway import peers
from gateway.app import Gateway
from gateway.server import make_handler
from mlx_server.result import Ok
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
LEAK = {"error": {"message": "open /Users/alex/lab/models/x failed", "type": "invalid_request_error"}}

class Up(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        raw = json.dumps(LEAK).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
up = ThreadingHTTPServer(("127.0.0.1", 0), Up); up.daemon_threads = True
threading.Thread(target=up.serve_forever, daemon=True).start()

class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R("qwen", up.server_address[1])]
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: {"model_arg": s, "endpoints": ["chat", "responses"]},
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
nets = peers.NetView(lambda: (HW, IF), clock=lambda: 0.0); nets.refresh()
g = Gateway(None, facade, token="@TOK@", nets=nets); g.refresh_routes()
H = {"Content-Type": "application/json", "Authorization": "Bearer @TOK@"}
fwd400 = lambda p, pa, b, **kw: (400, LEAK)
fwd401 = lambda p, pa, b, **kw: (401, {"detail": "Unauthorized"})
err = io.StringIO(); real_err, sys.stderr = sys.stderr, err
out = {"remote": g.handle("POST", "/v1/chat/completions", H, {"model": "qwen"}, stream=as_stream(fwd400), peer="172.20.10.5"),
       "local": g.handle("POST", "/v1/chat/completions", H, {"model": "qwen"}, stream=as_stream(fwd400)),
       "auth": g.handle("POST", "/v1/chat/completions", H, {"model": "qwen"}, stream=as_stream(fwd401))[0],
       # 扇出分支（GET /v1/responses/<id>）单独走一条 peer_class，不与按体路由那条共用变量——
       # 光测按体路由那条不能证明扇出分支也把真实来源接对了（Important 3）。
       "fanout_remote": g.handle("GET", "/v1/responses/anything", H, None, fwd400, peer="172.20.10.5"),
       "fanout_local": g.handle("GET", "/v1/responses/anything", H, None, fwd400)}
sys.stderr = real_err
out["auth_log"] = "backend_auth_failed" in err.getvalue() and "@TOK@" not in err.getvalue()

srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(g)); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1],
    data=json.dumps({"model": "qwen", "stream": True, "messages": []}).encode(), method="POST",
    headers=H)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        out["stream"] = [r.status, r.read().decode()]
except urllib.error.HTTPError as e:
    out["stream"] = [e.code, e.read().decode()]

# 流式路径的 peer_class 同样单独有一条变量（server.py `_stream`），不能只靠上面
# 走真实 127.0.0.1（回环）的那次请求去证明它接对了——用一个把 classify() 恒定判成
# REMOTE 的网关子类，驱动同一条真实流式请求路径，断言响应体确实被净化（Important 3）。
class RemoteGateway(Gateway):
    def classify(self, peer):
        return "REMOTE"
g2 = RemoteGateway(None, facade, token="@TOK@", nets=nets); g2.refresh_routes()
srv2 = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(g2)); srv2.daemon_threads = True
threading.Thread(target=srv2.serve_forever, daemon=True).start()
req2 = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % srv2.server_address[1],
    data=json.dumps({"model": "qwen", "stream": True, "messages": []}).encode(), method="POST",
    headers=H)
try:
    with urllib.request.urlopen(req2, timeout=10) as r:
        out["stream_remote"] = [r.status, r.read().decode()]
except urllib.error.HTTPError as e:
    out["stream_remote"] = [e.code, e.read().decode()]

print(json.dumps(out))
"""


def test_error_body_by_peer_and_streaming_4xx(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=30, NET=NET, TOK=TOK)
    st, body = got["remote"]
    assert st == 400 and "/Users" not in str(body) and body["error"]["type"] == "invalid_request_error"
    assert got["local"][0] == 400 and "/Users/alex/lab/models/x" in str(got["local"][1])
    assert got["auth"] == 502 and got["auth_log"] is True
    assert got["stream"][0] == 400, f"流式请求下后端 400 被变成了 {got['stream'][0]}"
    assert "invalid_request_error" in got["stream"][1]

    st, body = got["fanout_remote"]
    assert st == 400 and "/Users" not in str(body) and body["error"]["type"] == "invalid_request_error"
    assert got["fanout_local"][0] == 400 and "/Users/alex/lab/models/x" in str(got["fanout_local"][1])

    assert got["stream_remote"][0] == 400, f"流式 REMOTE 请求下 400 被变成了 {got['stream_remote'][0]}"
    assert "invalid_request_error" in got["stream_remote"][1]
    assert "/Users" not in got["stream_remote"][1], \
        f"流式响应体对 REMOTE 来源应净化，实得：{got['stream_remote'][1]}"
