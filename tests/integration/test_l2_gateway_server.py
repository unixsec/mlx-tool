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
