"""spec §8.2：CORS 响应头逐字一致、永不发 `Access-Control-Allow-Credentials`；带体的 OPTIONS 不破坏请求分帧。

两条都跑在**真 HTTP** 上（`make_handler` + 真 socket）：头是 server 层写的，
只断言 `Gateway.handle` 的状态码看不见它们；分帧问题只在 keep-alive 连接上出现。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

TOK = "t" * 43

PROG = r'''
import http.client, json, re, socket, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway.app import Gateway
from gateway.server import make_handler
from mlx_server.result import Ok


class R:
    def __init__(s, i, p):
        s.id, s.machine, s.user = i, {"port": p}, {}


class Res:
    records = [R("qwen", 8100)]


class Up(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"model": "qwen"}\n\ndata: [DONE]\n\n')
        self.wfile.flush()


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: {"model_arg": s},
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
g = Gateway(None, facade, token="@TOK@")
g.refresh_routes()
g.view._routes["qwen"] = {"port": serve(Up)}
gw_port = serve(make_handler(g))


def call(method, path, headers, body=None):
    c = http.client.HTTPConnection("127.0.0.1", gw_port, timeout=10)
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    r.read()
    out = {"status": r.status, "headers": [[k.lower(), v] for k, v in r.getheaders()]}
    c.close()
    return out


def raw(method, target, extra_headers, body):
    # 真 socket：发一条带体的请求，读到服务端关连接（或 3 s 无数据）为止
    s = socket.create_connection(("127.0.0.1", gw_port), timeout=3)
    s.sendall(("%s %s HTTP/1.1\r\nHost: x\r\n" % (method, target)).encode() + extra_headers
              + b"\r\n" + body)
    buf, closed = b"", False
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                closed = True
                break
            buf += chunk
    except socket.timeout:
        pass
    s.close()
    return {"status_lines": re.findall(r"HTTP/1\.[01] \d{3}", buf.decode("latin-1")),
            "closed": closed}


out = {
    "preflight": call("OPTIONS", "/v1/chat/completions",
                      {"Origin": "http://x", "Access-Control-Request-Method": "POST"}),
    "unauth": call("GET", "/v1/models", {}),
    "sse": call("POST", "/v1/chat/completions",
                {"Content-Type": "application/json", "Authorization": "Bearer @TOK@"},
                body=json.dumps({"model": "qwen", "stream": True})),
}
inner = b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n"
out["smuggle_cl"] = raw("OPTIONS", "/v1/chat/completions",
                        b"Content-Length: %d\r\n" % len(inner), inner)
out["smuggle_cl_preflight"] = raw(
    "OPTIONS", "/v1/chat/completions",
    b"Origin: http://x\r\nAccess-Control-Request-Method: POST\r\nContent-Length: %d\r\n"
    % len(inner), inner)
out["smuggle_te"] = raw("OPTIONS", "/v1/chat/completions", b"Transfer-Encoding: chunked\r\n",
                        b"%x\r\n" % len(inner) + inner + b"\r\n0\r\n\r\n")
# **裁决/复检 Critical 1**：`do_DELETE`／`do_GET` 此前没有 `do_OPTIONS` 那道
# 「带体回完即关连接」的判定 —— keep-alive 连接上体里的字节被当成下一条请求解析，
# 是请求走私。两者都用真实的扇出目标路径 + 有效令牌，走完整链路。
out["smuggle_delete_cl"] = raw(
    "DELETE", "/v1/responses/resp_1",
    b"Authorization: Bearer @TOK@\r\nContent-Length: %d\r\n" % len(inner), inner)
out["smuggle_get_cl"] = raw(
    "GET", "/v1/models",
    b"Authorization: Bearer @TOK@\r\nContent-Length: %d\r\n" % len(inner), inner)
print(json.dumps(out))
'''

# 逐字取自 spec（不从 guard.CORS_* 反读 —— 否则改了常量测试跟着变，守不住）
PREFLIGHT_CORS = sorted([
    ["access-control-allow-origin", "*"],
    ["access-control-allow-methods", "GET, POST, DELETE, OPTIONS"],
    ["access-control-allow-headers",
     "Authorization, x-api-key, Content-Type, anthropic-version, anthropic-beta, openai-beta"],
    ["access-control-max-age", "600"],
])


def _cors(resp):
    return sorted(h for h in resp["headers"] if h[0].startswith("access-control-"))


def test_cors_response_headers_exact_and_never_credentials(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60, TOK=TOK)
    pre, unauth, sse = got["preflight"], got["unauth"], got["sse"]
    assert pre["status"] == 204, pre
    assert _cors(pre) == PREFLIGHT_CORS, f"预检的 Access-Control-* 头集合与 spec 不一致：{_cors(pre)}"
    assert unauth["status"] == 401, unauth
    assert _cors(unauth) == [["access-control-allow-origin", "*"]], _cors(unauth)
    assert sse["status"] == 200 and ["content-type", "text/event-stream"] in sse["headers"], sse
    assert _cors(sse) == [["access-control-allow-origin", "*"]], _cors(sse)
    for name, resp in (("preflight", pre), ("401", unauth), ("sse", sse)):
        names = [h[0] for h in resp["headers"]]
        assert "access-control-allow-credentials" not in names, \
            f"{name} 响应发了 Allow-Credentials（令牌走头不走 cookie，永不该发）"


def test_options_with_body_does_not_smuggle_a_second_request(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60, TOK=TOK)
    for case in ("smuggle_cl", "smuggle_cl_preflight", "smuggle_te"):
        r = got[case]
        assert len(r["status_lines"]) == 1, \
            f"{case}：带体的 OPTIONS 回了 {r['status_lines']} —— 体被当成了下一条请求"
        assert r["closed"], f"{case}：带体的 OPTIONS 回完没有关连接"


def test_delete_and_get_with_body_do_not_smuggle_a_second_request(payload, sys_py):
    """Critical 1 复检：`do_DELETE`/`do_GET` 与 `do_OPTIONS` 同一处坑，同一个修法。"""
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60, TOK=TOK)
    for case in ("smuggle_delete_cl", "smuggle_get_cl"):
        r = got[case]
        assert len(r["status_lines"]) == 1, \
            f"{case}：带体的请求回了 {r['status_lines']} —— 体被当成了下一条请求"
        assert r["closed"], f"{case}：带体的请求回完没有关连接"
