"""spec §4.1 / §8.2「传输」「TLS」：真 socket、真 HTTP、真 TLS 握手。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

TOK = "t" * 43

PRE = r"""
import json, os, socket, ssl, subprocess, tempfile, threading, time, urllib.error, urllib.request
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
for name, args in (("permissive", (cert, key)), ("missing", (cert, key + ".nope"))):
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
