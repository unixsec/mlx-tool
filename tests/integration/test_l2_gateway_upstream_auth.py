"""L2 · 流式路径的后端鉴权头有真 HTTP 测试（I-5）。

坏样本（评审）：`server.py` 流式转发附加 `upstream_headers` 的那行参数删掉后
全套测试仍绿——没有任何用例真的用真 HTTP 发一次**流式**请求去看上游收到的
`Authorization` 头。计划 3 会让所有上游调用都走流式，这条缝必须补上。

起一个真上游（`ThreadingHTTPServer`）、一个真网关（`make_handler`），
经真 HTTP 分别发流式与非流式请求，断言：
  · `backend_auth: True` 的模型（vis）两条路径上游都收到 `Bearer <backend_key>`；
  · `backend_auth: False` 的模型（txt）两条路径上游都**没有** Authorization 头；
  · 客户端自带的 Authorization 值从未出现在上游收到的头里。
"""
import random
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

KEY = "K" + "".join(random.choices(string.ascii_uppercase + string.digits, k=31))
CLIENT_TOKEN = "C" + "".join(random.choices(string.ascii_lowercase + string.digits, k=31))

PROG = r'''
import json, threading, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway.app import Gateway
from gateway.server import make_handler
from mlx_server.result import Ok


class R:
    def __init__(s, i, p):
        s.id, s.machine, s.user = i, {"port": p}, {}


class Res:
    records = [R("vis", 8100), R("txt", 8101)]


seen = []


# ── 真上游：记录每个请求的 Authorization 头；stream=true 回 SSE，否则回 JSON ──
class Up(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        seen.append([body.get("model"), self.headers.get("Authorization")])
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in ("hi", "!"):
                frame = ("data: " + json.dumps(
                    {"model": body.get("model"),
                     "choices": [{"delta": {"content": c}}]}) + "\n\n").encode()
                self.wfile.write(frame)
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            out = json.dumps({"model": body.get("model"),
                              "choices": [{"message": {"content": "ok"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)


up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
up.daemon_threads = True
threading.Thread(target=up.serve_forever, daemon=True).start()
up_port = up.server_address[1]

snaps = {"vis": {"model_arg": "vis", "backend_auth": True},
         "txt": {"model_arg": "txt", "backend_auth": False}}
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: snaps.get(s)}

g = Gateway(None, facade, backend_key="@KEY@")
g.refresh_routes()
# 两个模型都路由到桩上游端口
g.view._routes["vis"] = {"port": up_port}
g.view._routes["txt"] = {"port": up_port}

gwsrv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(g))
gwsrv.daemon_threads = True
threading.Thread(target=gwsrv.serve_forever, daemon=True).start()
gw_port = gwsrv.server_address[1]


def req(model, stream):
    body = json.dumps({"model": model, "stream": stream,
                       "messages": [{"role": "user", "content": "x"}]}).encode()
    r = urllib.request.Request(
        "http://127.0.0.1:%d/v1/chat/completions" % gw_port,
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer @CLIENT_TOKEN@"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


results = {}
for model in ("vis", "txt"):
    for stream in (True, False):
        status, raw = req(model, stream)
        results["%s_%s" % (model, stream)] = {"status": status, "raw": raw}

print(json.dumps({"seen": seen, "results": results}))
'''


def test_gateway_attaches_backend_auth_upstream_only_streaming_and_nonstreaming(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=30,
                    KEY=KEY, CLIENT_TOKEN=CLIENT_TOKEN)
    seen = got["seen"]
    assert len(seen) == 4, f"预期四次上游请求（vis/txt × 流式/非流式），实得 {len(seen)}：{seen}"

    vis_auths = [auth for model, auth in seen if model == "vis"]
    txt_auths = [auth for model, auth in seen if model == "txt"]
    assert len(vis_auths) == 2 and len(txt_auths) == 2, f"模型分布不对：{seen}"

    for auth in vis_auths:
        assert auth == "Bearer " + KEY, f"vis（backend_auth=True）上游未收到期望的鉴权头：{auth!r}"
    for auth in txt_auths:
        assert auth is None, f"txt（backend_auth=False）不该带鉴权头，实得 {auth!r}"

    assert all(auth != "Bearer " + CLIENT_TOKEN for _, auth in seen), (
        "客户端自带的 Authorization 值泄漏到了上游"
    )

    for key, r in got["results"].items():
        assert r["status"] == 200, f"{key} 请求失败：{r}"
