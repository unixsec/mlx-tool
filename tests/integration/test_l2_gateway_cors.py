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
# 小体上限的独立实例：413 测试用不着真发 256 MB —— 声明一个超过这个小上限的
# Content-Length 就够复现「体没读、没关连接 ⇒ 体里的字节被当下一条请求解析」
gw_port_413 = serve(make_handler(g, max_body_bytes=64))


def call(method, path, headers, body=None):
    c = http.client.HTTPConnection("127.0.0.1", gw_port, timeout=10)
    c.request(method, path, body=body, headers=headers)
    r = c.getresponse()
    r.read()
    out = {"status": r.status, "headers": [[k.lower(), v] for k, v in r.getheaders()]}
    c.close()
    return out


def raw(method, target, extra_headers, body, port=None):
    # 真 socket：发一条带体的请求，读到服务端关连接（或 3 s 无数据）为止
    s = socket.create_connection(("127.0.0.1", port or gw_port), timeout=3)
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
    text = buf.decode("latin-1")
    return {"status_lines": re.findall(r"HTTP/1\.[01] \d{3}", text),
            "closed": closed, "raw": text}


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
# 裁决 1：413（体超过 `max_body_bytes`）也要关连接。声明的 Content-Length（1000）
# 超过 gw_port_413 的 64 字节上限，实际只发一个 inner 请求的字节数 —— 服务端不读体，
# 直接 413；若它没关连接，这些字节会被当成同一条 keep-alive 连接上的下一条请求解析。
out["smuggle_413"] = raw(
    "POST", "/v1/chat/completions",
    b"Authorization: Bearer @TOK@\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n",
    inner, port=gw_port_413)
# 裁决 Critical 3：`do_POST` 只看 `Content-Length`，`Transfer-Encoding: chunked` 声明
# 的体此前当空体放行（读 0 字节）——分块编码的字节留在 socket 里，被当成同一条
# keep-alive 连接上的下一条请求解析。这个仓库没有分块解码器，唯一安全的做法是
# 直接拒绝，不是当空体处理。
out["smuggle_post_chunked"] = raw(
    "POST", "/v1/chat/completions",
    b"Authorization: Bearer @TOK@\r\nContent-Type: application/json\r\n"
    b"Transfer-Encoding: chunked\r\n",
    b"%x\r\n" % len(inner) + inner + b"\r\n0\r\n\r\n")
# 复检 Important（同一类缺陷第六、第七次复发）：`Content-Length` 解析要接住整整
# 一类畸形拼法，且**每一个读它的调用点**都要接住——不是一个值一个值、一个方法
# 一个方法地补丁。CTRL_BODY 是一个真实、格式合法但缺 `model` 字段的小体（给
# 「应该被正常处理」的两个 control case 用，只对 POST 有意义——GET/DELETE/OPTIONS
# 本来就不读体，声明了非零长度就该关连接，不管那个长度写得对不对）；其余 case
# 声明的长度本身就非法，实际发送的字节固定用 `inner`（一条完整的 `GET /healthz`）
# ——如果没被拒绝/没被关连接，又没被整条读走，剩下的字节会被当成下一条 keep-alive
# 请求解析。矩阵按「值 × 方法」铺开，同一份 case 定义，不为方法另开一份表。
CTRL_BODY = b'{"pad":"x"}'
cl_len = str(len(CTRL_BODY)).encode().decode()
CL_VALUES = {
    "empty": [""], "negative": ["-1"], "nonnumeric": ["abc"],
    "plus_prefixed": ["+5"], "whitespace_padded": [" 5 "],
    # 首值特意选 "0"：旧版 `_close_if_body_unread` 只看第一个 Content-Length 值，
    # `"0".lstrip("0")` 是空串（假），会误判成"没有体"而不关连接——这正是复检点名
    # 的那个洞；新的严格解析看的是"重复且不一致"，不管哪个值排第一都要拒
    "dup_differing": ["0", "1000"],
    "dup_identical": [cl_len, cl_len],
    "valid_control": [cl_len],
}
CL_ACCEPT_CASES = ("dup_identical", "valid_control")
# 每个方法自带能给出干净、确定的单条响应的目标路径/固定头（与既有 smuggle_* 用例
# 同一套目标）；`Content-Length` 头由下面的循环按 case 拼接在后面
METHOD_TARGETS = {
    "GET": ("/v1/models", b"Authorization: Bearer @TOK@\r\n"),
    "OPTIONS": ("/v1/chat/completions", b""),
    "POST": ("/v1/chat/completions",
             b"Authorization: Bearer @TOK@\r\nContent-Type: application/json\r\n"),
}
cl_matrix = {}
for _method, (_target, _base_headers) in METHOD_TARGETS.items():
    for _case, _values in CL_VALUES.items():
        _hdrs = _base_headers + b"".join(b"Content-Length: %s\r\n" % v.encode() for v in _values)
        _body = CTRL_BODY if _case in CL_ACCEPT_CASES else inner
        cl_matrix["%s:%s" % (_method, _case)] = raw(_method, _target, _hdrs, _body)
out["cl_matrix"] = cl_matrix
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


def test_413_closes_connection_and_does_not_smuggle_a_second_request(payload, sys_py):
    """裁决 1：413（超过 `max_body_bytes`）没读体，也不能让 keep-alive 复用这条连接。"""
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60, TOK=TOK)
    r = got["smuggle_413"]
    assert r["status_lines"] == ["HTTP/1.1 413"], \
        f"超限请求体应恰好回一条 413，收到 {r['status_lines']}"
    assert r["closed"], "413 之后连接应被关闭，否则内嵌的第二条请求会被当作下一条请求解析"


def test_post_chunked_is_rejected_and_does_not_smuggle_a_second_request(payload, sys_py):
    """裁决 Critical 3：分块编码的 POST 体没有解码器可读，必须整条拒绝并关连接，
    而不是当空体放行——当空体放行会把分块字节留给下一条 keep-alive 请求解析。

    **`status_lines`/`closed` 不够**：分块数据没被读走时，留下的字节以十六进制长度行
    开头，stdlib 把它当 HTTP/0.9 请求处理——那条路径的错误响应**不带状态行**（只有
    body），单看 `status_lines == 1` 和 `closed` 两条断言即使裸词汇上不作为(bug 没修)
    也会碰巧全部成立。改看 `raw`：没被解析成第二条请求的唯一证据是响应里完全不出现
    stdlib 那句「Bad request syntax」。
    """
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60, TOK=TOK)
    r = got["smuggle_post_chunked"]
    assert r["status_lines"] == ["HTTP/1.1 400"], \
        f"分块编码的 POST 应恰好回一条 400，收到 {r['status_lines']}"
    assert r["closed"], "分块编码的 POST 被拒后连接应被关闭"
    assert "Bad request syntax" not in r["raw"], \
        f"分块编码没读走的字节被当成了下一条请求解析：{r['raw']!r}"


REJECT_CL_CASES = ("empty", "negative", "nonnumeric", "plus_prefixed",
                   "whitespace_padded", "dup_differing")
ACCEPT_CL_CASES = ("dup_identical", "valid_control")
CL_METHODS = ("GET", "OPTIONS", "POST")


def test_content_length_parsed_strictly_across_the_whole_class(payload, sys_py):
    """复检 Important（同一类缺陷第六、第七次复发）：严格解析 `Content-Length`
    是这个 handler 里**唯一**的判据，`_read_body`（POST 读体）与
    `_close_if_body_unread`（GET/DELETE/OPTIONS 判断要不要关连接）必须共用它——
    矩阵按「值 × 方法」铺开，不是给某一个方法单开一张表。

    三条不变式对矩阵里的**每一格**都成立：
    1. 恰好一条状态行（没有把没读走的字节当成了下一条 keep-alive 请求解析）；
    2. 响应原文里不出现 stdlib 的「Bad request syntax」（同一份证据，防 Critical 3
       那种"看 status_lines/closed 两条表面断言也会碰巧全绿"的假阴性）；
    3. 关不关连接：畸形值（`REJECT_CL_CASES`）在任何方法上都必须关；"重复但一致"
       与"单值合法"（`ACCEPT_CL_CASES`）只在 POST 上不关（体被正常读走处理）——
       GET/OPTIONS/DELETE 本来就不读体，声明了非零长度（哪怕值写得完全合法）
       就该关，这不是误杀，是这两个方法本来就有的行为。
    """
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60, TOK=TOK)
    matrix = got["cl_matrix"]
    for method in CL_METHODS:
        for case in REJECT_CL_CASES + ACCEPT_CL_CASES:
            r = matrix[f"{method}:{case}"]
            assert len(r["status_lines"]) == 1, \
                f"{method}:{case}：应当恰好一条状态行，收到 {r['status_lines']}"
            assert "Bad request syntax" not in r["raw"], \
                f"{method}:{case}：没读走的字节被当成了下一条请求解析：{r['raw']!r}"
            should_close = case in REJECT_CL_CASES or method != "POST"
            assert r["closed"] is should_close, \
                f"{method}:{case}：期望 closed={should_close}，收到 {r['closed']}"
