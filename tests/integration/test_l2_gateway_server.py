"""spec §4.1 / §8.2「传输」「TLS」：真 socket、真 HTTP、真 TLS 握手。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

TOK = "t" * 43

PRE = r"""
import json, os, re, socket, ssl, subprocess, sys, tempfile, threading, time
import urllib.error, urllib.request
from gateway.app import Gateway
from gateway.server import GatewayServer, make_handler, tls_context
from mlx_server.result import Ok
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R("qwen", 1)]
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: {"model_arg": s},
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
g = Gateway(None, facade, token="@TOK@"); g.refresh_routes()
AUTH = {"Authorization": "Bearer @TOK@"}
def start(host="127.0.0.1", **kw):
    srv = GatewayServer((host, 0), make_handler(g, **kw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
def call(url, data=None, headers=None, method=None, ctx=None, timeout=5):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
def raw_request(port, head, body=b"", timeout=3):
    # 发一条自定义头/体的真请求，拿第一行状态与耗时——不判定它是否读完了体
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(head + body)
    s.settimeout(timeout); t0 = time.time()
    try:
        first = s.recv(256).decode(errors="replace")
    except OSError:
        first = ""
    elapsed = round(time.time() - t0, 2)
    s.close()
    return [first.split("\r\n")[0], elapsed]
"""

BODY = PRE + r"""
srv, port = start(max_body_bytes=1024)
url = "http://127.0.0.1:%d/v1/chat/completions" % port
J = dict(AUTH, **{"Content-Type": "application/json"})
out = {"big": call(url, b'{"model":"qwen","pad":"' + b"x" * 2048 + b'"}', J, "POST"),
       "small_missing_model": call(url, b'{"pad":"x"}', J, "POST")}
# 未鉴权 + 声称 10 MB 的请求体、只发请求头：必须立刻 401，而不是等着读体
s = socket.create_connection(("127.0.0.1", port))
s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
           "Content-Length: 10485760\r\n\r\n").encode())
s.settimeout(3); t0 = time.time()
first = s.recv(64).decode(errors="replace")
out["unauth_big"] = [first.split("\r\n")[0], round(time.time() - t0, 2)]
print(json.dumps(out))
"""

NEG = PRE + r"""
srv, port = start()
def post_cl(cl, extra=b""):
    head = ("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
            "Authorization: Bearer @TOK@\r\nContent-Type: application/json\r\n"
            "Content-Length: %s\r\n\r\n" % cl).encode()
    return raw_request(port, head, extra)
# 负数 / 非数字 Content-Length：不能落进 `int()` 直接抛异常，也不能落进
# `rfile.read(n)` 无界读到 EOF——两种畸形声明都该立刻 400，即便声明之后还跟了字节
out = {"neg": post_cl("-1", b"x" * 2048), "nonint": post_cl("abc", b"x" * 2048)}
print(json.dumps(out))
"""

HEALTHZ = PRE + r"""
srv, port = start()
def post_healthz(cl, extra=b""):
    head = ("POST /healthz HTTP/1.1\r\nHost: x\r\n"
            "Content-Type: application/json\r\nContent-Length: %s\r\n\r\n" % cl).encode()
    return raw_request(port, head, extra)
# `/healthz` 免鉴权但只挂 GET：未鉴权的 POST 不该因为「鉴权豁免」就被放去读最多 256 MB 的体，
# 白名单判定必须先于读体生效——不管声明的体积是正的还是负的
out = {"partial": post_healthz("4096", b"x" * 10), "neg": post_healthz("-1", b"x" * 10)}
print(json.dumps(out))
"""

EXPECT100 = PRE + r"""
srv, port = start()
s = socket.create_connection(("127.0.0.1", port), timeout=3)
s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
           "Content-Type: application/json\r\nContent-Length: 2\r\n"
           "Expect: 100-continue\r\n\r\n").encode())
s.settimeout(3)
buf = b""
try:
    while True:
        chunk = s.recv(256)
        if not chunk:
            break
        buf += chunk
except socket.timeout:
    pass
s.close()
# 未鉴权的客户端不该先收到 100 Continue 才被拒——那等于邀请它把体发过来
print(json.dumps({"status_lines": re.findall(r"HTTP/1\.[01] \d{3}", buf.decode("latin-1"))}))
"""

ERR = PRE + r"""
import io
buf = io.StringIO()
sys.stderr = buf
class Boom(Gateway):
    # 故意在 classify 里炸，制造一个 handler 里的未捕获异常，检验 handle_error 记没记异常类型
    def classify(self, peer):
        raise RuntimeError("induced_boom")
g = Boom(None, facade, token="@TOK@"); g.refresh_routes()
srv, port = start()
try:
    call("http://127.0.0.1:%d/v1/models" % port, headers=AUTH)
except Exception:
    pass
time.sleep(0.2)
print(json.dumps({"log": buf.getvalue()}))
"""

HANDSHAKE = PRE + r"""
import gateway.server as server_mod
server_mod._HANDSHAKE_SEC = 0.5          # 缩短握手超时，不用真等 10 s
d = tempfile.mkdtemp()
cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=mlx-gateway",
                "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
os.chmod(key, 0o600)
srv, port = start(tls_context=tls_context(cert, key))
stall = socket.create_connection(("127.0.0.1", port))
stall.sendall(b"\x16\x03\x01\x00\x01")   # 不完整的 TLS record：握手永远收不到剩下的字节
stall.settimeout(3); t0 = time.time()
try:
    data = stall.recv(64)
    result = "data" if data else "closed"
except OSError as e:
    result = type(e).__name__
elapsed = round(time.time() - t0, 2)
stall.close()
print(json.dumps({"result": result, "elapsed": elapsed}))
"""

DUAL = PRE + r"""
seen = []
class Probe(Gateway):
    # Gateway 有 __slots__，实例上的方法不能替换；子类覆盖，记录 handler 传给来源分类的对端地址
    def classify(self, peer):
        seen.append(peer)
        return super().classify(peer)
g = Probe(None, facade, token="@TOK@"); g.refresh_routes()
srv, port = start(host="::")
c4 = socket.create_connection(("127.0.0.1", port))
c4.sendall(("GET /v1/models HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
            "Connection: close\r\n\r\n").encode())
c4.settimeout(3)
status4 = c4.recv(64).decode().split("\r\n")[0]
v6 = call("http://[::1]:%d/v1/models" % port, headers=AUTH)
print(json.dumps({"v4": status4, "v6": v6, "seen": seen, "family": srv.address_family == socket.AF_INET6}))
"""

TLS = PRE + r"""
d = tempfile.mkdtemp()
cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=mlx-gateway",
                "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
os.chmod(key, 0o600)
srv, port = start(tls_context=tls_context(cert, key))
idle = socket.create_connection(("127.0.0.1", port))       # 挂一个空闲明文连接
time.sleep(0.2)
cctx = ssl.create_default_context(cafile=cert)
t0 = time.time()
https = call("https://127.0.0.1:%d/healthz" % port, ctx=cctx)
elapsed = round(time.time() - t0, 2)
p = socket.create_connection(("127.0.0.1", port))
p.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
p.settimeout(3); t1 = time.time()
try:
    plain = "data" if p.recv(64) else "closed"
except OSError as e:
    plain = type(e).__name__
plain_elapsed = round(time.time() - t1, 2)
bad = {}
os.chmod(key, 0o644)
# 内容损坏但文件可读、权限也对——只有 `ctx.load_cert_chain` 才能发现的失败形态
corrupt_cert, corrupt_key = os.path.join(d, "bad_cert.pem"), os.path.join(d, "bad_key.pem")
with open(corrupt_cert, "w") as fh:
    fh.write("not a cert")
with open(corrupt_key, "w") as fh:
    fh.write("not a key")
os.chmod(corrupt_key, 0o600)
for name, args in (("permissive", (cert, key)), ("missing", (cert, key + ".nope")),
                   ("corrupt", (corrupt_cert, corrupt_key))):
    try:
        tls_context(*args); bad[name] = "accepted"
    except ValueError as e:
        bad[name] = str(e)
print(json.dumps({"https": https, "elapsed": elapsed, "plain": plain,
                  "plain_elapsed": plain_elapsed, "bad": bad}))
"""


def test_body_limit_and_auth_before_body(payload, sys_py):
    got = run_floor(payload, sys_py, BODY, with_gateway=True, TOK=TOK)
    assert got["big"] == 413
    assert got["small_missing_model"] == 400
    line, elapsed = got["unauth_big"]
    assert "401" in line and elapsed < 2, f"未鉴权请求应在读请求体之前被拒：{got['unauth_big']}"


def test_dual_stack_and_peer_probe(payload, sys_py):
    got = run_floor(payload, sys_py, DUAL, with_gateway=True, TOK=TOK)
    assert got["family"] is True
    assert "200" in got["v4"] and got["v6"] == 200
    assert "::ffff:127.0.0.1" in got["seen"] and "::1" in got["seen"], \
        f"handler 传给来源分类的应是 socket 对端地址：{got['seen']}"


def test_tls_handshake_in_worker_thread(payload, sys_py):
    got = run_floor(payload, sys_py, TLS, with_gateway=True, timeout=60, TOK=TOK)
    assert got["https"] == 200 and got["elapsed"] < 2, "空闲明文连接不得挡住 HTTPS 请求"
    assert got["plain"] in ("closed", "ConnectionResetError") and got["plain_elapsed"] < 2
    assert "0600" in got["bad"]["permissive"]
    assert "不可读" in got["bad"]["missing"]
    assert "无法加载" in got["bad"]["corrupt"], \
        f"内容损坏的证书/私钥（文件可读、权限也对）应在 load_cert_chain 失败：{got['bad']}"


def test_negative_and_non_integer_content_length_rejected_fast(payload, sys_py):
    """Critical 1 复检：`Content-Length: -1` 不能落进 `rfile.read(-1)` 无界读到 EOF，
    `Content-Length: abc` 不能让 `int()` 直接抛异常把客户端晾在那。两者都该立刻 400。"""
    got = run_floor(payload, sys_py, NEG, with_gateway=True, TOK=TOK)
    for case in ("neg", "nonint"):
        line, elapsed = got[case]
        assert "400" in line and elapsed < 2, f"{case}：畸形 Content-Length 应立刻 400：{got[case]}"


def test_unauth_post_healthz_rejected_before_body_read(payload, sys_py):
    """Critical 2 复检：`/healthz` 鉴权豁免，但只挂 GET——未鉴权的 POST 不该因为鉴权豁免
    就被放去读体，白名单判定必须先于读体生效，不管声明的体积是正的还是负的。"""
    got = run_floor(payload, sys_py, HEALTHZ, with_gateway=True, TOK=TOK)
    for case in ("partial", "neg"):
        line, elapsed = got[case]
        assert "404" in line and elapsed < 2, \
            f"{case}：POST /healthz 不在白名单里，应在读体之前就 404：{got[case]}"


def test_expect_100_continue_does_not_precede_precheck(payload, sys_py):
    """Minor 6 复检：默认实现在鉴权之前就回 100 Continue，等于邀请被拒客户端发体。"""
    got = run_floor(payload, sys_py, EXPECT100, with_gateway=True, TOK=TOK)
    assert got["status_lines"] == ["HTTP/1.1 401"], \
        f"未鉴权请求不该先收到 100 Continue：{got['status_lines']}"


def test_handler_exception_logs_exception_type(payload, sys_py):
    """Important 4 复检：`handle_error` 必须带异常类型与消息，不能只留一行光秃秃的
    `connection_error`——那样和默认实现相比反而丢了信息。"""
    got = run_floor(payload, sys_py, ERR, with_gateway=True, TOK=TOK)
    assert "RuntimeError" in got["log"] and "connection_error" in got["log"], \
        f"handler 异常应该在日志里点名异常类型：{got['log']!r}"


def test_stalled_tls_handshake_times_out(payload, sys_py):
    """Minor 8(a) 复检：`setup()` 里两行 `settimeout` 若被删掉，整套件仍然全绿——
    唯一能测出它们有没有生效的办法是真的让一次握手卡住，看工作线程会不会放弃。"""
    got = run_floor(payload, sys_py, HANDSHAKE, with_gateway=True, timeout=60, TOK=TOK)
    assert got["result"] in ("closed", "ConnectionResetError", "BrokenPipeError"), \
        f"卡在握手中途的连接应该在超时后被服务端放弃：{got}"
    assert got["elapsed"] < 3, f"握手超时应该接近缩短后的 0.5 s，而不是卡到客户端自己的 3 s 超时：{got}"


FANOUT = PRE + r"""
import http.server
class Backend(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _ok(self):
        body = json.dumps({"id": "resp_1", "model": "qwen"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = _ok
    do_DELETE = _ok
    def log_message(self, *a):
        pass
bsrv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
threading.Thread(target=bsrv.serve_forever, daemon=True).start()
BPORT = bsrv.server_address[1]
class R2:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res2:
    records = [R2("qwen", BPORT)]
facade2 = dict(facade, **{
    "store.load": lambda c: Res2(),
    "supervisor.snapshot": lambda c, s: {"model_arg": s,
                                         "endpoints": ["chat", "responses", "messages"]}})
g2 = Gateway(None, facade2, token="@TOK@"); g2.refresh_routes()
srv2 = GatewayServer(("127.0.0.1", 0), make_handler(g2))
threading.Thread(target=srv2.serve_forever, daemon=True).start()
port2 = srv2.server_address[1]
def line(method, path):
    head = ("%s %s HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
            "Connection: close\r\n\r\n" % (method, path)).encode()
    return raw_request(port2, head)[0]
print(json.dumps({"get_by_id": line("GET", "/v1/responses/resp_1"),
                  "get_input_items": line("GET", "/v1/responses/resp_1/input_items"),
                  "delete": line("DELETE", "/v1/responses/resp_1")}))
"""


def test_fanout_get_reaches_the_backend_over_real_http(payload, sys_py):
    """收尾评审 Critical 1：`do_GET` 不传 `forward` ⇒ 两个扇出端点**恒 503**。

    `GET /v1/responses/{id}` 与 `GET /v1/responses/{id}/input_items` 都是 fanout，
    而 `gw.handle` 在 `forward is None` 时直接回 503 `no_upstream`。
    `do_DELETE` 与流式的扇出分支一直传着它，只有 `do_GET` 漏了。

    **为什么活到现在**：既有的扇出用例（`test_l2_gateway_endpoints.py`）全都直调
    `gw.handle(...)` 并**自己注入 forward**，于是它们对「server 层到底传没传」
    这件事完全不敏感 —— 缺陷正好活在那条缝里。这条用例因此走真 socket、
    真 `make_handler`、真后端 HTTP 服务器，并把同路径的 DELETE 作为对照
    （修复前它是通的，GET 不通）。
    """
    got = run_floor(payload, sys_py, FANOUT, with_gateway=True, timeout=60, TOK=TOK)
    assert "200" in got["get_by_id"], f"扇出 GET 没到后端：{got}"
    assert "200" in got["get_input_items"], f"扇出 GET input_items 没到后端：{got}"
    assert "200" in got["delete"], f"同路径 DELETE 对照也该通：{got}"


FRAMING = PRE + r"""
srv, port = start()
SMUG = b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n"
AUTHL = "Authorization: Bearer @TOK@\r\n"
def responses(head, body):
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(head + body)
    s.settimeout(1.5)
    buf = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            buf += c
    except OSError:
        pass
    s.close()
    return re.findall(r"HTTP/1\.[01] (\d{3})", buf.decode("latin-1"))
START = "POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n" + AUTHL + \
        "Content-Type: application/json\r\n"
SHAPES = {
    "bad_line_no_colon": START + "X-Bad\r\nContent-Length: %d\r\n\r\n" % len(SMUG),
    "space_before_colon": START + "Content-Length : %d\r\n\r\n" % len(SMUG),
    "obs_fold_swallow": START + "X-Fold: a\r\n Content-Length: %d\r\n\r\n" % len(SMUG),
    # handle_expect_100 在 parse_request 内部、早于分帧不变式运行：不可信的头上不得先发 100（终审 B m2）
    "obs_fold_expect": START + "Expect: 100-continue\r\nX-Fold: a\r\n Content-Length: %d\r\n\r\n" % len(SMUG),
}
out = {k: responses(v.encode(), SMUG) for k, v in SHAPES.items()}
out["wellformed"] = responses((START + "Content-Length: 17\r\n\r\n").encode(),
                              b'{"model":"qwen"}\n')
print(json.dumps(out))
"""


def test_malformed_header_block_cannot_smuggle_a_second_request(payload, sys_py):
    """收尾评审 Critical 2 —— 请求走私的**第九个成员**，按分帧层收口。

    三种拼法都让 `Content-Length` 从 `self.headers` 里消失，而 `http.client`
    已经把那些字节读走了 ⇒ `_parse_content_length` 判「无体」⇒ 声明的体留在
    socket 里被当成下一条请求解析：**一条请求进、两条响应出**。

    前两种（`X-Bad` 裸行、冒号前带空格）解析器会记
    `MissingHeaderBodySeparatorDefect`；**第三种 obs-fold 续行不记任何缺陷**
    （续行是 RFC 7230 的合法语法），两个解释器上都实测复现 —— 所以只查
    `headers.defects` 拦不住它，必须同时禁掉「头值跨行」。

    良性对照必须一起断言：这道新判据不能把格式正确的请求也打成 400。
    """
    got = run_floor(payload, sys_py, FRAMING, with_gateway=True, timeout=60, TOK=TOK)
    for shape in ("bad_line_no_colon", "space_before_colon", "obs_fold_swallow", "obs_fold_expect"):
        assert got[shape] == ["400"], (
            f"{shape}：一条请求进、{len(got[shape])} 条响应出 ⇒ 响应错位：{got[shape]}")
    assert len(got["wellformed"]) == 1 and got["wellformed"][0] != "400", (
        f"格式正确的请求被新判据误伤：{got['wellformed']}")


UPSTREAM = PRE + r"""
from gateway.server import forward, forward_stream
bad = socket.socket(); bad.bind(("127.0.0.1", 0)); bad.listen(8)
BADPORT = bad.getsockname()[1]
def garbage():
    while True:
        try:
            c, _ = bad.accept()
        except OSError:
            return
        try:
            c.recv(65536); c.sendall(b"GARBAGE NOT HTTP\r\n\r\n")
        except OSError:
            pass
        finally:
            c.close()
threading.Thread(target=garbage, daemon=True).start()
stall = socket.socket(); stall.bind(("127.0.0.1", 0)); stall.listen(8)
STALLPORT = stall.getsockname()[1]
def stalling():
    while True:
        try:
            c, _ = stall.accept()
        except OSError:
            return
        try:
            c.recv(65536)
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
            time.sleep(20)
        except OSError:
            pass
threading.Thread(target=stalling, daemon=True).start()
out = {"forward_bad_status": forward(BADPORT, "/v1/chat/completions", {"model": "qwen"}),
       "forward_read_timeout": forward(STALLPORT, "/v1/chat/completions",
                                       {"model": "qwen"}, timeout_sec=1),
       "stream_bad_status": forward_stream(BADPORT, "/v1/chat/completions",
                                           {"model": "qwen"}, lambda ch: None)}
class R3:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res3:
    records = [R3("qwen", BADPORT)]
g3 = Gateway(None, dict(facade, **{"store.load": lambda c: Res3()}), token="@TOK@")
g3.refresh_routes()
srv3 = GatewayServer(("127.0.0.1", 0), make_handler(g3))
threading.Thread(target=srv3.serve_forever, daemon=True).start()
head = ("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
        "Content-Type: application/json\r\nContent-Length: 17\r\n"
        "Connection: close\r\n\r\n").encode()
out["end_to_end"] = raw_request(srv3.server_address[1], head, b'{"model":"qwen"}\n')[0]
# 流式客户端同一情形（终审 D I-3）：上游在回头之前就失败 ⇒ 同样 502，不是 0 字节
sbody = b'{"model":"qwen","stream":true}\n'
shead = head.replace(b"Content-Length: 17", b"Content-Length: %d" % len(sbody))
out["end_to_end_stream"] = raw_request(srv3.server_address[1], shead, sbody)[0]
print(json.dumps(out))
"""


def test_upstream_transport_failure_becomes_502_not_zero_bytes(payload, sys_py):
    """收尾评审 Critical 3：`forward` 的契约是「传输失败 → `None`」，
    而它的 except 面按**名字**列举，漏了两族：

    · `http.client.BadStatusLine` / `IncompleteRead` 是 `HTTPException`，
      **不是 `OSError`** ⇒ 后端回一行垃圾状态行时异常穿到工作线程，
      客户端拿到零字节（实测 `bytes_received: 0`）而不是 504。两个解释器都中。
    · **3.9.6 上 `socket.timeout` 不是 `TimeoutError`**（那个别名 3.10 才有），
      于是 600 s 读超时在下限解释器上逃逸、在 venv 的 3.14 上被接住 ——
      同一份代码两种行为。本用例用 1 s 超时把这一半也钉住。

    `forward_stream` 同样需要 `HTTPException`（读超时与连接重置本已被 `OSError`
    接住，只有 `BadStatusLine` 会逃）。

    端到端（裁决 M4）：上游一律流式后，连不上上游 / 上游回垃圾是 **502**
    `upstream_error`，不再是 504 —— 那不是超时。
    """
    got = run_floor(payload, sys_py, UPSTREAM, with_gateway=True, timeout=60, TOK=TOK)
    assert got["forward_bad_status"] is None, (
        f"垃圾状态行没被接住（BadStatusLine 不是 OSError）：{got['forward_bad_status']}")
    assert got["forward_read_timeout"] is None, (
        f"读超时没被接住（3.9.6 上 socket.timeout 不是 TimeoutError）："
        f"{got['forward_read_timeout']}")
    assert got["stream_bad_status"] is False, (
        f"forward_stream 的 BadStatusLine 逃逸了：{got['stream_bad_status']}")
    assert "502" in got["end_to_end"], f"端到端应收到 502 而不是零字节：{got['end_to_end']}"
    assert "502" in got["end_to_end_stream"], f"流式端到端同样应收到 502：{got['end_to_end_stream']}"


EXPECT100_OVERSIZE = PRE + r"""
srv, port = start(max_body_bytes=64)
s = socket.create_connection(("127.0.0.1", port), timeout=3)
s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
           "Authorization: Bearer @TOK@\r\nContent-Type: application/json\r\n"
           "Content-Length: 4096\r\nExpect: 100-continue\r\n\r\n").encode())
s.settimeout(3)
buf = b""
try:
    while True:
        chunk = s.recv(256)
        if not chunk:
            break
        buf += chunk
except socket.timeout:
    pass
s.close()
print(json.dumps({"status_lines": re.findall(r"HTTP/1\.[01] \d{3}", buf.decode("latin-1"))}))
"""


def test_expect_100_not_granted_for_a_body_already_over_the_cap(payload, sys_py):
    """收尾评审 Important 4：`handle_expect_100` 跑了 `precheck` 却从不看
    `Content-Length`，尽管此刻它已经知道了 —— 于是对一个**必然回 413** 的体
    先发 `100 Continue`（实测 64 字节上限下：100 然后 413），等于邀请客户端
    把注定被拒的几 MB 传上来。RFC 9110 §10.1.1 要的是直接给最终状态。
    这也正好架空了本计划新加的 `api.max_body_mb` 闸门。
    """
    got = run_floor(payload, sys_py, EXPECT100_OVERSIZE, with_gateway=True, TOK=TOK)
    assert got["status_lines"] == ["HTTP/1.1 413"], (
        f"超限的体不该先拿到 100 Continue：{got['status_lines']}")


STALL_BODY = PRE + r"""
import io
import gateway.server as server_mod
server_mod.BODY_IDLE_SEC = 0.5           # 缩短读体静默上限，不用真等 60 s
logs = io.StringIO(); sys.stderr = logs
g = Gateway(None, facade, token="@TOK@", limits={"inflight_max_mb": 64}); g.refresh_routes()
srv, port = start()
def open_stalled(mb):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
               "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (mb * 1024 * 1024)).encode())
    return s
a, b = open_stalled(32), open_stalled(32)
time.sleep(0.2)
during = g.budget.used
def first_line(s):
    try:
        return s.recv(256).decode("latin-1").split("\r\n")[0]
    except OSError as e:
        return type(e).__name__
lines = [first_line(a), first_line(b)]
time.sleep(0.2)
after = g.budget.used
small = raw_request(port, ("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
                           "Content-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n").encode(),
                    b"{}")[0]
print(json.dumps({"during": during, "after": after, "lines": lines, "small": small,
                  "logged": "body_read_timeout" in logs.getvalue()}))
"""


def test_stalled_body_upload_releases_budget(payload, sys_py):
    """终审 B I1：先计费、后读体，而读体没有超时 —— 声明大体后静止的连接永久占住在途预算，
    8 条就能让整个网关一直 503。读体静默超时 ⇒ 408、关连接、预算退回。"""
    got = run_floor(payload, sys_py, STALL_BODY, with_gateway=True, timeout=60, TOK=TOK)
    assert got["during"] == 64 * 1024 * 1024, f"两条连接应各计入 32 MB：{got}"
    assert got["lines"] == ["HTTP/1.1 408 Request Timeout"] * 2, f"静止的上传应收到 408：{got}"
    assert got["after"] == 0, f"超时后在途预算必须退回 0：{got}"
    assert "503" not in got["small"], f"预算退回后的正常请求不该再 503：{got}"
    assert got["logged"], "超时要记 body_read_timeout"


LINGER = PRE + r"""
import http.client
g = Gateway(None, facade, token="@TOK@", limits={"inflight_max_mb": 1}); g.refresh_routes()
srv, port = start()
BIG = b'{"model":"qwen","pad":"' + b"x" * (20 * 1024 * 1024) + b'"}'
def post(token, body, expect=False):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    try:
        c.request("POST", "/v1/chat/completions", body=body, headers=h)
        r = c.getresponse()
        return [r.status, r.getheader("Retry-After")]
    except OSError as e:
        return [type(e).__name__, None]
    finally:
        c.close()
out = {"busy_big": post("@TOK@", BIG), "unauth_big": post("wrong", BIG)}
# Expect 路径：预算满时直接 503，不先发 100 Continue
s = socket.create_connection(("127.0.0.1", port), timeout=3)
s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
           "Content-Type: application/json\r\nContent-Length: %d\r\n"
           "Expect: 100-continue\r\n\r\n" % (2 * 1024 * 1024)).encode())
buf = b""
try:
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
except OSError:
    pass
s.close()
out["expect_lines"] = re.findall(r"HTTP/1\.[01] \d{3}", buf.decode("latin-1"))
out["expect_retry_after"] = "Retry-After: 5" in buf.decode("latin-1")
# 预算够时 Expect 路径照常 100 → 体 → 回复，而且只计一次费（回复后预算回 0）
g2 = Gateway(None, facade, token="@TOK@", limits={"inflight_max_mb": 64}); g2.refresh_routes()
srv2 = GatewayServer(("127.0.0.1", 0), make_handler(g2))
threading.Thread(target=srv2.serve_forever, daemon=True).start()
s = socket.create_connection(("127.0.0.1", srv2.server_address[1]), timeout=3)
body = b'{"pad":"x"}'
s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
           "Content-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n"
           "Expect: 100-continue\r\n\r\n" % len(body)).encode())
first = s.recv(4096)
s.sendall(body)
rest = b""
try:
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        rest += chunk
except OSError:
    pass
s.close()
time.sleep(0.1)
out["expect_ok"] = re.findall(r"HTTP/1\.[01] \d{3}", (first + rest).decode("latin-1"))
out["expect_ok_budget"] = g2.budget.used
print(json.dumps(out))
"""


def test_early_rejection_reaches_a_client_still_sending_its_body(payload, sys_py):
    """终审 B I2：体没读就回 503/401 后直接 close，接收缓冲里的残余让内核回 RST，
    还在发体的客户端读不到状态码与 `Retry-After`（实测 20 MB 体 → BrokenPipeError）。
    Expect 路径此前不看预算：先发 100，客户端开传，再 503 + RST。"""
    got = run_floor(payload, sys_py, LINGER, with_gateway=True, timeout=60, TOK=TOK)
    assert got["busy_big"] == [503, "5"], f"预算满时大体请求应拿到 503 + Retry-After：{got}"
    assert got["unauth_big"][0] == 401, f"错误令牌的大体请求应拿到 401：{got}"
    assert got["expect_lines"] == ["HTTP/1.1 503"] and got["expect_retry_after"], \
        f"Expect 路径预算满时应直接 503（不发 100）：{got}"
    assert got["expect_ok"][0] == "HTTP/1.1 100" and len(got["expect_ok"]) == 2, \
        f"预算够时 Expect 路径照常：{got}"
    assert got["expect_ok_budget"] == 0, f"Expect 预留只计一次费且回复后退还：{got}"


SWEEP_FAIL = PRE + r"""
import io
import gateway.server as server_mod
from gateway import media as M
logs = io.StringIO(); sys.stderr = logs
out_buf = io.StringIO(); real_stdout = sys.stdout; sys.stdout = out_buf
def boom(self, *a, **k):
    raise PermissionError(13, "denied")
M.Store.sweep = boom
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); free = sock.getsockname()[1]; sock.close()
t = threading.Thread(target=server_mod.serve, args=(g,), kwargs={"port": free, "refresh_sec": 60}, daemon=True)
t.start()
deadline = time.time() + 5
status = None
while time.time() < deadline and status is None:
    try:
        status = call("http://127.0.0.1:%d/healthz" % free, timeout=1)
    except Exception:
        time.sleep(0.1)
sys.stdout = real_stdout
print(json.dumps({"status": status, "logged": "media_sweep_failed" in logs.getvalue()}))
"""


def test_startup_sweep_failure_does_not_block_serving(payload, sys_py):
    """终审 B m7：启动时的 `media_store.sweep()` 没包 try —— 媒体根目录不可读就起不来，
    而刷新线程里的同一调用是包了的。"""
    got = run_floor(payload, sys_py, SWEEP_FAIL, with_gateway=True, timeout=60, TOK=TOK)
    assert got == {"status": 200, "logged": True}, f"清扫失败应只记事件，不阻止启动：{got}"


DRIP_BODY = PRE + r"""
import io
import gateway.server as server_mod
server_mod.BODY_IDLE_SEC = 1.0           # 单次 recv 静默上限：滴灌间隔 0.3 s 永远碰不到它
server_mod.BODY_MIN_SEC = 0.5            # 起步宽限
server_mod.BODY_MIN_RATE = 10            # 每秒至少 10 字节才挣得到时间额度
server_mod.BODY_MAX_SEC = 60.0
logs = io.StringIO(); sys.stderr = logs
g = Gateway(None, facade, token="@TOK@", limits={"inflight_max_mb": 64}); g.refresh_routes()
srv, port = start()
s = socket.create_connection(("127.0.0.1", port), timeout=5)
s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
           "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (32 * 1024 * 1024)).encode())
stop = threading.Event()
def drip():
    while not stop.wait(0.3):
        try:
            s.sendall(b" ")
        except OSError:
            return
threading.Thread(target=drip, daemon=True).start()
t0 = time.time()
s.settimeout(4)
try:
    line = s.recv(256).decode("latin-1").split("\r\n")[0]
except OSError as e:
    line = type(e).__name__
elapsed = round(time.time() - t0, 2)
stop.set()
time.sleep(0.2)
print(json.dumps({"line": line, "elapsed": elapsed, "after": g.budget.used,
                  "logged": "body_read_timeout" in logs.getvalue()}))
"""


def test_dripping_body_upload_hits_total_deadline(payload, sys_py):
    """安全复核：只有单次 recv 的静默上限时，每次间隔小于它的滴灌能无限期
    占住已计费的在途预算。读体按到货字节挣时间额度：滴灌者超过总时限后 408、预算退还。"""
    got = run_floor(payload, sys_py, DRIP_BODY, with_gateway=True, timeout=60, TOK=TOK)
    assert got["line"] == "HTTP/1.1 408 Request Timeout", f"滴灌超过总时限应 408：{got}"
    assert got["elapsed"] < 3, f"应在时限附近被切断，而不是等到客户端自己超时：{got}"
    assert got["after"] == 0 and got["logged"], f"切断后预算退还并记事件：{got}"


REUSE = PRE + r"""
srv, port = start()
st = raw_request(port, ("GET /healthz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").encode())[0]
time.sleep(0.2)                          # 服务端先关连接 ⇒ 本端口留下 TIME_WAIT
srv.shutdown(); srv.server_close()
try:
    srv2 = GatewayServer(("127.0.0.1", port), make_handler(g))
    rebound = True
    srv2.server_close()
except OSError as e:
    rebound = "errno %s" % e.errno
print(json.dumps({"first": st, "rebound": rebound, "flag": GatewayServer.allow_reuse_address}))
"""


def test_restart_rebinds_the_same_port_immediately(payload, sys_py):
    """终审 D I-4（承接 A12）：`allow_reuse_address` 关掉后，处理过请求的网关关闭后立刻在同端口
    重绑会 errno 48 —— `mlx restart api` 必然失败（2026-09-15 实测）。"""
    got = run_floor(payload, sys_py, REUSE, with_gateway=True, TOK=TOK)
    assert "200" in got["first"], got
    assert got["rebound"] is True, f"同端口立刻重绑失败：{got}"


CL_DIGITS = PRE + r"""
srv, port = start()
def post_cl(cl, body):
    head = ("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer @TOK@\r\n"
            "Content-Type: application/json\r\nConnection: close\r\nContent-Length: %s\r\n\r\n" % cl).encode()
    return raw_request(port, head, body)[0]
B = b'{"model":"qwen"}\n'
out = {"d20": post_cl("0" * 18 + "17", B), "d21": post_cl("0" * 19 + "17", B),
       "d5000": post_cl("9" * 5000, B)}
out["no_dict"] = not hasattr(g, "__dict__")
print(json.dumps(out))
"""


def test_content_length_digit_cap_boundary_and_gateway_slots(payload, sys_py):
    """终审 D（承接 A3）：`Content-Length` 的 20 位长度门 —— 20 位的合法值放行，21 位与 5000 位拒；
    整个门删掉、或位数改大，21 位样本都会走到 413/正常处理而不是 400。
    另承接 A19：`Gateway` 有 `__slots__`（请求处理路径不能给它挂新属性）。"""
    got = run_floor(payload, sys_py, CL_DIGITS, with_gateway=True, TOK=TOK)
    assert "400" not in got["d20"], f"20 位（含前导零）的合法值应放行：{got}"
    assert "400" in got["d21"] and "400" in got["d5000"], f"超过 20 位应 400：{got}"
    assert got["no_dict"] is True, "Gateway 必须保留 __slots__"


PRE_HEADERS = PRE + r"""
import gateway.server as server_mod
def wait_close(s, limit):
    # 等服务端动作：收到首行 / 被关闭 / 到 limit 仍无动静（"timeout"）
    s.settimeout(limit); t0 = time.time()
    try:
        d = s.recv(256)
        r = d.decode(errors="replace").split("\r\n")[0] if d else "closed"
    except socket.timeout:
        r = "timeout"
    except OSError as e:
        r = type(e).__name__
    return [r, round(time.time() - t0, 2)]
"""

HEADER_DEADLINE = PRE_HEADERS + r"""
server_mod.HEADER_SEC = 1.5
srv, port = start()
out = {}
# 1. 请求头逐字节滴灌：每次间隔都小于单次 recv 上限，只有「总时限」能切断它
s = socket.create_connection(("127.0.0.1", port))
stop = threading.Event()
def drip(sock=s):
    for ch in b"GET /healthz HTTP/1.1\r\nHost: x\r\nX-Pad: " + b"a" * 200:
        if stop.wait(0.2):
            return
        try:
            sock.sendall(bytes([ch]))
        except OSError:
            return
threading.Thread(target=drip, daemon=True).start()
out["drip"] = wait_close(s, 5)
stop.set(); s.close()
# 2. keep-alive 空闲：一条完整请求之后什么也不发，连接到时被关
s = socket.create_connection(("127.0.0.1", port))
s.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
s.settimeout(3)
first = s.recv(4096).decode(errors="replace").split("\r\n")[0]
out["idle"] = [first] + wait_close(s, 5)
s.close()
print(json.dumps(out))
"""

DENIED_EARLY = PRE_HEADERS + r"""
class Deny(Gateway):
    def classify(self, peer):
        return None
g = Deny(None, facade, token="@TOK@"); g.refresh_routes()
srv, port = start()
s = socket.create_connection(("127.0.0.1", port))
out = {"denied": wait_close(s, 3)}       # 一个字节都不发
s.close()
print(json.dumps(out))
"""

CONN_CAPS = PRE_HEADERS + r"""
server_mod.MAX_CONN = 3
server_mod.PEER_CONN_LOCAL = 100
srv, port = start()
held = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]
time.sleep(0.3)
extra = socket.create_connection(("127.0.0.1", port))
out = {"global": wait_close(extra, 3)}
extra.close()
for h in held:
    h.close()
time.sleep(0.3)
server_mod.MAX_CONN = 100
server_mod.PEER_CONN_LOCAL = 2
held = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
time.sleep(0.3)
extra = socket.create_connection(("127.0.0.1", port))
out["peer"] = wait_close(extra, 3)
for h in held:
    h.close()
time.sleep(0.3)
out["after"] = raw_request(port, b"GET /healthz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")[0]
print(json.dumps(out))
"""

TLS_DRIP = PRE_HEADERS + r"""
server_mod._HANDSHAKE_SEC = 1.0
d = tempfile.mkdtemp()
cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=mlx-gateway"],
               check=True, capture_output=True)
os.chmod(key, 0o600)
srv, port = start(tls_context=tls_context(cert, key))
s = socket.create_connection(("127.0.0.1", port))
stop = threading.Event()
def drip(sock=s):
    # 合法的记录头声明 16 KB 的 ClientHello，之后逐字节慢慢喂：记录永远收不齐，
    # 每次间隔又都在单次 recv 超时之内
    for ch in b"\x16\x03\x01\x40\x00\x01\x00\x3f\xfc" + b"\x00" * 300:
        if stop.wait(0.3):
            return
        try:
            sock.sendall(bytes([ch]))
        except OSError:
            return
threading.Thread(target=drip, daemon=True).start()
out = {"drip": wait_close(s, 5)}
stop.set(); s.close()
print(json.dumps(out))
"""


def _gone(r):
    return r in ("closed", "ConnectionResetError", "BrokenPipeError")


def test_header_read_has_a_total_deadline_and_idle_keepalive_closes(payload, sys_py):
    """请求头阶段原本没有任何时限：逐字节滴灌、或 keep-alive 之后什么也不发，
    都能无限期占住一个工作线程，而且发生在来源检查与鉴权之前。"""
    got = run_floor(payload, sys_py, HEADER_DEADLINE, with_gateway=True, timeout=60, TOK=TOK)
    r, t = got["drip"]
    assert _gone(r) and t < 3, f"滴灌请求头应在总时限附近被切断：{got}"
    first, r, t = got["idle"]
    assert "200" in first, got
    assert _gone(r) and t < 3, f"空闲的 keep-alive 连接应在时限后被关：{got}"


def test_denied_peer_is_closed_before_any_bytes_are_read(payload, sys_py):
    """不在放行网段的来源：连上就回 403 并关，不等它发请求头。"""
    got = run_floor(payload, sys_py, DENIED_EARLY, with_gateway=True, timeout=30, TOK=TOK)
    r, t = got["denied"]
    assert ("403" in r or _gone(r)) and t < 2, f"被拒来源应立刻被打发：{got}"


def test_connection_caps_global_and_per_peer(payload, sys_py):
    """线程数原本没有上限：全局与每来源都要有上限，超出的连接立刻关掉，不起线程。"""
    got = run_floor(payload, sys_py, CONN_CAPS, with_gateway=True, timeout=60, TOK=TOK)
    for k in ("global", "peer"):
        r, t = got[k]
        assert _gone(r) and t < 2, f"超出{k}上限的连接应立刻被关：{got}"
    assert "200" in got["after"], f"占用释放后应恢复服务：{got}"


def test_dripping_tls_handshake_hits_total_deadline(payload, sys_py):
    """握手原本只有「单次 recv」超时：每次间隔都小于它的滴灌永远不会被切断。"""
    got = run_floor(payload, sys_py, TLS_DRIP, with_gateway=True, timeout=60, TOK=TOK)
    r, t = got["drip"]
    assert _gone(r) and t < 3, f"滴灌握手应在总时限附近被切断：{got}"
