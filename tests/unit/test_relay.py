"""spec §4.6 保活与断连 / §8.2「保活与断连」。真 socket：本地 HTTP 上游 + socketpair 当客户端。间隔注入为亚秒级。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway import relay as R

EVENTS = {}
class Up(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        mode = body.get("mode")
        if mode == "bad":
            raw = b'{"error": {"message": "bad request", "type": "invalid_request_error"}}'
            self.send_response(400); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if mode == "slow_head":
            time.sleep(1.5)
        if mode == "abort":
            # 分块传输：声明 0x20 字节的块，只发 15 字节就断开 ⇒ 客户端 read1 抛 IncompleteRead
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            self.wfile.write(b"20\r\n" + b'data: {"half": '); self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers(); self.wfile.flush()
        try:
            if mode == "quiet":
                time.sleep(1.3)
                self.wfile.write(b'data: {"x": 1}\n\n'); self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            elif mode == "endless":
                for i in range(200):
                    self.wfile.write(b'data: {"i": %d}\n\n' % i); self.wfile.flush(); time.sleep(0.05)
            elif mode == "abort":
                pass                              # 见下面的分块分支
            else:
                self.wfile.write(b'data: {"ok": 1}\n\ndata: [DONE]\n\n'); self.wfile.flush()
        except OSError:                           # macOS 写已关闭的 socket 也可能是 errno 41
            EVENTS[mode] = time.monotonic()
up = ThreadingHTTPServer(("127.0.0.1", 0), Up); up.daemon_threads = True
threading.Thread(target=up.serve_forever, daemon=True).start()
PORT = up.server_address[1]

def run(mode, keepalive_sec=0.5, probe_sec=0.1, disconnect_after=None, write_fails_after=None):
    client, peer = socket.socketpair()
    got = {"heads": [], "chunks": b"", "keepalives": 0, "ka_before_head": 0}
    t0 = time.monotonic()
    def on_head(head):
        got["heads"].append(head[0] if head[0] != "error" else ["error", head[1], head[2]])
    def on_chunk(chunk):
        if write_fails_after is not None and time.monotonic() - t0 > write_fails_after:
            raise BrokenPipeError(32, "Broken pipe")
        got["chunks"] += chunk
    def keepalive():
        got["keepalives"] += 1
        if not got["heads"]:
            got["ka_before_head"] += 1
    if disconnect_after is not None:
        threading.Timer(disconnect_after, peer.close).start()
    res = R.relay(lambda reg: R.open_upstream(PORT, "/v1/chat/completions", {"mode": mode}, register=reg),
                  on_head, on_chunk, lambda: R.client_alive(client), keepalive,
                  keepalive_sec=keepalive_sec, probe_sec=probe_sec)
    elapsed = time.monotonic() - t0
    client.close()
    try:
        peer.close()
    except OSError:
        pass
    return {"outcome": res["outcome"], "heads": got["heads"], "chunks": got["chunks"].decode(),
            "keepalives": got["keepalives"], "ka_before_head": got["ka_before_head"],
            "elapsed": round(elapsed, 2), "bytes": res["bytes"]}
r = {}
r["ok"] = run("ok")
r["quiet"] = run("quiet", keepalive_sec=0.4)
t_disc = time.monotonic()
r["endless_disconnect"] = run("endless", disconnect_after=0.4)
time.sleep(0.5)
r["upstream_saw_close"] = "endless" in EVENTS and EVENTS["endless"] - t_disc < 2.0
r["write_fail"] = run("endless", write_fails_after=0.3)
r["slow_head_disconnect"] = run("slow_head", disconnect_after=0.3)
r["bad"] = run("bad")
# 上游 1.5 s 才回响应头（mlx_lm 排队），保活间隔 0.3 s：头之前一行保活都不得发 ——
# server 的 keepalive 直接写 wfile，此时还没 send_response，客户端会把 `: keepalive` 当状态行
r["slow_head_ka"] = run("slow_head", keepalive_sec=0.3)
# 出厂保活间隔（不传 keepalive_sec）的行为：时钟放大 100 倍，上游静默 1.3 s 真实时间 ≈ 130 s，
# 按 15 s 一行约 8 行；常量若被改成 30 s 只剩约 4 行
_t0 = time.monotonic()
def fast_clock():
    return (time.monotonic() - _t0) * 100.0
_ka = []
_c, _p = socket.socketpair()
R.relay(lambda reg: R.open_upstream(PORT, "/v1/chat/completions", {"mode": "quiet"}, register=reg),
        lambda h: None, lambda c: None, lambda: True, lambda: _ka.append(1), probe_sec=0.01, clock=fast_clock)
_c.close(); _p.close()
r["default_ka"] = len(_ka)
r["abort"] = run("abort")
r["failed"] = R.relay(lambda reg: R.open_upstream(1, "/x", {}, register=reg), lambda h: None, lambda c: None,
                      lambda: True, None, probe_sec=0.05)
r["failed"] = [r["failed"]["outcome"], r["failed"]["head"][0]]
a, b = socket.socketpair()
r["alive"] = [R.client_alive(a)]
b.close()
r["alive"].append(R.client_alive(a))
print(json.dumps(r))
"""


def test_relay_keepalive_disconnect_and_errors(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60)
    assert got["ok"]["outcome"] == "ok" and got["ok"]["heads"] == ["stream"]
    assert got["ok"]["chunks"] == 'data: {"ok": 1}\n\ndata: [DONE]\n\n' and got["ok"]["bytes"] == 31
    q = got["quiet"]
    assert q["outcome"] == "ok" and q["keepalives"] >= 2, f"上游静默期间应按间隔保活：{q}"
    e = got["endless_disconnect"]
    assert e["outcome"] == "client_disconnected" and e["elapsed"] < 1.2, \
        f"上游持续出数据时也要按间隔探测客户端（非流式聚合不写客户端，只能靠探测）：{e['outcome']} {e['elapsed']}"
    assert got["upstream_saw_close"] is True, "客户端断开后上游连接必须被关闭（后端据此取消生成）"
    assert got["write_fail"]["outcome"] == "client_disconnected" and got["write_fail"]["elapsed"] < 1.2
    s = got["slow_head_disconnect"]
    assert s["outcome"] == "client_disconnected" and s["heads"] == [] and s["elapsed"] < 1.2, \
        f"等上游响应头期间也要探测断连：{s}"
    assert got["bad"]["outcome"] == "upstream_error"
    assert got["bad"]["heads"] == [["error", 400, {"error": {"message": "bad request",
                                                             "type": "invalid_request_error"}}]]
    assert got["abort"]["outcome"] == "upstream_aborted"
    assert got["failed"] == ["upstream_failed", "failed"]
    assert got["alive"] == [True, False]
    sk = got["slow_head_ka"]
    assert sk["outcome"] == "ok" and sk["ka_before_head"] == 0 and sk["heads"] == ["stream"], \
        f"响应头到达之前绝不能发保活：{sk}"
    assert 6 <= got["default_ka"] <= 10, f"出厂保活间隔应为 15 s（≈130 s 静默约 8 行）：{got['default_ka']}"


PROG2 = r"""
import json, socket, ssl, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway import relay as R

SAW_CLOSE = {}          # mode -> 上游观测到连接关闭的时刻
class Up(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _watch(self, mode, limit=6.0):
        # 阻塞等客户端关连接；只做 recv，不写（写在 macOS 上对已关连接也可能成功）
        self.connection.settimeout(0.1)
        end = time.monotonic() + limit
        while time.monotonic() < end:
            try:
                if self.connection.recv(1) == b"":
                    SAW_CLOSE[mode] = time.monotonic(); return
            except socket.timeout:
                continue
            except OSError:
                SAW_CLOSE[mode] = time.monotonic(); return
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        mode = json.loads(self.rfile.read(n) or b"{}").get("mode")
        if mode == "silent_head":                 # 排队：连响应头都不发
            self._watch(mode); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers(); self.wfile.flush()
        if mode == "silent_stream":               # 头发了，之后一个字节都不发
            self._watch(mode)
        elif mode == "one_then_silent":
            self.wfile.write(b'data: {"i": 0}\n\n'); self.wfile.flush()
            self._watch(mode)
        elif mode == "stall":                     # 静默 3 s，用来验证单次读超时真的传到了 socket
            time.sleep(3)
        elif mode == "steady":
            for i in range(14):                   # 间隔 0.15 s > probe 0.1 s：队列会空，但不到保活间隔
                self.wfile.write(b'data: {"i": %d}\n\n' % i); self.wfile.flush(); time.sleep(0.15)
            self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
up = ThreadingHTTPServer(("127.0.0.1", 0), Up); up.daemon_threads = True
threading.Thread(target=up.serve_forever, daemon=True).start()
PORT = up.server_address[1]

def run(mode, on_head=None, on_chunk=None, keepalive=None, alive=None, open_kw=None,
        keepalive_sec=0.4, probe_sec=0.1, disconnect_after=None, open_fn=None):
    client, peer = socket.socketpair()
    if disconnect_after is not None:
        threading.Timer(disconnect_after, peer.close).start()
    cnt = {"ka": 0}
    def ka():
        cnt["ka"] += 1
        if keepalive:
            keepalive()
    t0 = time.monotonic()
    err = None
    res = {"outcome": None, "bytes": 0}
    try:
        res = R.relay(open_fn or (lambda reg: R.open_upstream(PORT, "/x", {"mode": mode}, register=reg,
                                                              **(open_kw or {}))),
                      on_head or (lambda h: None), on_chunk or (lambda c: None),
                      alive or (lambda: R.client_alive(client)), ka,
                      keepalive_sec=keepalive_sec, probe_sec=probe_sec)
    except Exception as exc:
        err = type(exc).__name__
    elapsed = time.monotonic() - t0
    client.close()
    try: peer.close()
    except OSError: pass
    time.sleep(0.4)
    return {"outcome": res["outcome"], "err": err, "elapsed": round(elapsed, 2), "ka": cnt["ka"],
            "upstream_closed": mode in SAW_CLOSE and SAW_CLOSE[mode] - t0 < elapsed + 2.0}

def boom(exc):
    def f(*a):
        raise exc
    return f

r = {}
r["consts"] = [R.KEEPALIVE_SEC, R.PROBE_SEC, R.READ_TIMEOUT_SEC]
# I5a 单次读超时真的传到 socket：上游静默 3 s，timeout_sec=0.4 ⇒ read1 超时 ⇒ aborted，不等 3 s
r["read_timeout"] = run("stall", open_kw={"timeout_sec": 0.4}, keepalive_sec=60)
# I1 / I5b 等响应头期间断开，上游（排队中）必须看到连接被关
SAW_CLOSE.clear(); r["head_wait"] = run("silent_head", disconnect_after=0.3)
# I5b 静默流 + 断开：读线程正阻塞在 read1
SAW_CLOSE.clear(); r["quiet_disc"] = run("silent_stream", disconnect_after=0.3, keepalive_sec=60)
# I2 on_head 写失败
SAW_CLOSE.clear(); r["head_write_fail"] = run("silent_stream", on_head=boom(BrokenPipeError(32, "x")), keepalive_sec=60)
# I5d 保活写失败
SAW_CLOSE.clear(); r["ka_write_fail"] = run("silent_stream", keepalive=boom(BrokenPipeError(32, "x")), keepalive_sec=0.3)
# I4 回调抛非 OSError：异常外抛，但上游必须被关
SAW_CLOSE.clear(); r["alive_raises"] = run("silent_stream", alive=boom(ValueError("closed")), probe_sec=0.1, keepalive_sec=60)
SAW_CLOSE.clear(); r["chunk_raises"] = run("one_then_silent", on_chunk=boom(ValueError("closed")), keepalive_sec=60)
SAW_CLOSE.clear(); r["ka_raises"] = run("silent_stream", keepalive=boom(ValueError("closed")), keepalive_sec=0.3)
# I3 open_fn 抛任意异常 / 头值含换行 / 载荷不可序列化
r["open_raises"] = run("x", open_fn=boom(RuntimeError("boom")))
r["bad_header"] = run("x", open_fn=lambda reg: R.open_upstream(PORT, "/x", {}, headers={"X-A": "a\nb"}, register=reg))
r["bad_payload"] = run("x", open_fn=lambda reg: R.open_upstream(PORT, "/x", {"m": object()}, register=reg))
# I5e 保活间隔：静默 ~1.3 s（用断开收尾）次数有上界；持续出数据时不保活
r["quiet_ka"] = run("silent_stream", disconnect_after=1.3, keepalive_sec=0.4)
r["steady_ka"] = run("steady", keepalive_sec=0.5)

# I5c client_alive
a, b = socket.socketpair()
b.send(b"x")
alive_pending = R.client_alive(a)
kept = a.recv(1)
r["peek"] = [alive_pending, kept.decode()]
lst = socket.socket(); lst.bind(("127.0.0.1", 0)); lst.listen(1)
c = socket.create_connection(lst.getsockname()); s_, _ = lst.accept()
s_.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
s_.close(); time.sleep(0.2)
r["reset"] = R.client_alive(c)
# TLS：真握手的 SSLSocket 对 MSG_PEEK 抛 ValueError（前提），client_alive 须判在线且不吞数据
sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain("@CERT@", "@KEY@")
cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); cctx.check_hostname = False; cctx.verify_mode = ssl.CERT_NONE
ta, tb = socket.socketpair()
box = {}
def srv():
    box["s"] = sctx.wrap_socket(tb, server_side=True)
th = threading.Thread(target=srv); th.start()
tc = cctx.wrap_socket(ta, server_hostname="localhost"); th.join()
box["s"].send(b"y"); time.sleep(0.2)
try:
    tc.recv(1, socket.MSG_PEEK); premise = "no-raise"
except ValueError:
    premise = "ValueError"
r["tls"] = [premise, R.client_alive(tc), tc.recv(1).decode()]
print(json.dumps(r))
"""


def _cert(tmp_path):
    import subprocess
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(crt), "-days", "1", "-subj", "/CN=localhost"], check=True,
                   capture_output=True)
    return crt, key


def test_relay_contract_fixes(payload, sys_py, tmp_path):
    crt, key = _cert(tmp_path)
    g = run_floor(payload, sys_py, PROG2, with_gateway=True, timeout=120, CERT=crt, KEY=key)
    # 常量（spec 设计值）
    assert g["consts"] == [15.0, 1.0, 600.0]
    # 单次读超时真传到上游 socket
    t = g["read_timeout"]
    assert t["outcome"] == "upstream_aborted" and t["elapsed"] < 2.2, t
    # I1：等响应头期间断开 ⇒ 上游连接被关
    h = g["head_wait"]
    assert h["outcome"] == "client_disconnected" and h["elapsed"] < 1.5 and h["upstream_closed"], h
    # I5b：静默流 + 断开，读线程阻塞在 read1 时也要及时返回并关上游
    q = g["quiet_disc"]
    assert q["outcome"] == "client_disconnected" and q["elapsed"] < 1.5 and q["upstream_closed"], q
    # I2：写响应头失败
    w = g["head_write_fail"]
    assert w["outcome"] == "client_disconnected" and w["err"] is None and w["upstream_closed"], w
    # I5d：保活写失败
    k = g["ka_write_fail"]
    assert k["outcome"] == "client_disconnected" and k["elapsed"] < 1.5 and k["upstream_closed"], k
    # I4：回调抛非 OSError ⇒ 异常外抛，上游照关
    for name in ("alive_raises", "chunk_raises", "ka_raises"):
        x = g[name]
        assert x["err"] == "ValueError" and x["upstream_closed"] and x["elapsed"] < 3.0, (name, x)
    # I3：open_fn 异常不让主循环永远等
    for name in ("open_raises", "bad_header", "bad_payload"):
        x = g[name]
        assert x["outcome"] == "upstream_failed" and x["elapsed"] < 3.0, (name, x)
    # I5e：保活间隔上界与「出数据时不保活」
    ka = g["quiet_ka"]
    assert 2 <= ka["ka"] <= ka["elapsed"] / 0.4 + 1, ka
    assert g["steady_ka"]["ka"] == 0 and g["steady_ka"]["outcome"] == "ok", g["steady_ka"]
    # I5c：peek 不吞数据；对端 RST ⇒ 离线；TLS ⇒ 在线且数据仍在
    assert g["peek"] == [True, "x"]
    assert g["reset"] is False
    assert g["tls"] == ["ValueError", True, "y"], g["tls"]
