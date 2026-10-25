"""spec §4.4 代取 / D23 / §8.2「媒体 · SSRF」。本地 HTTP(S) 服务 + 注入的解析器与连接探针，全程不联网。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import ipaddress, json, os, socket, struct, ssl, subprocess, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway import fetch as F
from gateway import peers as P

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, ctype=None, body=b"", headers=None):
        self.send_response(code)
        if ctype: self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        p = self.path
        SEEN.append([self.headers.get("Host"), self.headers.get("Accept-Encoding")])
        if p == "/img.png": return self._send(200, "image/png; charset=binary", PNG)
        if p == "/gz": return self._send(200, "image/png", PNG, {"Content-Encoding": "gzip"})
        if p == "/html": return self._send(200, "text/html", b"<html>")
        if p == "/big": return self._send(200, "image/png", b"x" * (3 * 1024 * 1024))
        if p == "/nolen":
            self.send_response(200); self.send_header("Content-Type", "audio/wav"); self.end_headers()
            self.wfile.write(b"x" * (3 * 1024 * 1024)); return
        if p == "/meta": return self._send(302, headers={"Location": "http://169.254.169.254/latest"})
        if p.startswith("/chain"):
            n = int(p[len("/chain"):] or 0)
            return self._send(301, headers={"Location": "/chain%d" % (n + 1) if n < 4 else "/img.png"})
        if p == "/drip":                      # 声明 1000 字节，每 0.3 s 滴 1 字节
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "1000"); self.end_headers()
            try:
                for _ in range(1000):
                    self.wfile.write(b"x"); self.wfile.flush(); time.sleep(0.3)
            except OSError:
                pass
            return
        if p == "/stall":                     # 0.8 s 后发头和 10 字节，随后停住
            time.sleep(0.8)
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "1000"); self.end_headers()
            self.wfile.write(b"x" * 10); self.wfile.flush(); time.sleep(5); return
        if p == "/trunc":                     # 声明 1000 字节只发 10 字节就关
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "1000"); self.end_headers()
            self.wfile.write(b"x" * 10); self.wfile.flush(); return
        if p == "/reset":                     # 发一半后 RST
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "1000"); self.end_headers()
            self.wfile.write(b"x" * 10); self.wfile.flush(); time.sleep(0.2)
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            self.connection.close(); return
        if p.startswith("/rbad"):
            loc = ["http://8.8.8.8:99999/x", "http://8.8.8.8:abc/x", "http://[::1/x", "http://8.8.8.8:0/x"][int(p[5:])]
            return self._send(302, headers={"Location": loc})
        if p == "/slow":
            time.sleep(3); return self._send(200, "image/png", PNG)
        return self._send(404, "text/plain", b"no")
SEEN = []
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]

d = tempfile.mkdtemp()
cert, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                "-out", cert, "-days", "1", "-subj", "/CN=public.test",
                "-addext", "subjectAltName=DNS:public.test"], check=True, capture_output=True)
sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain(cert, key)
tsrv = ThreadingHTTPServer(("127.0.0.1", 0), H); tsrv.daemon_threads = True
tsrv.socket = sctx.wrap_socket(tsrv.socket, server_side=True)
threading.Thread(target=tsrv.serve_forever, daemon=True).start()
TPORT = tsrv.server_address[1]

NETS = [{"net": "127.0.0.0/8", "iface": "lo0", "kind": "loopback", "scope": None, "addr": "127.0.0.1"},
        {"net": "172.20.10.0/28", "iface": "en0", "kind": "wifi", "scope": None, "addr": "172.20.10.3"},
        {"net": "10.211.55.0/24", "iface": "bridge100", "kind": "vm", "scope": None, "addr": "10.211.55.2"},
        {"net": "fe80::/64", "iface": "en0", "kind": "wifi", "scope": "en0", "addr": "fe80::103"}]
CONNECTS = []
def to_local(ip, port, scheme, host, timeout, ssl_context=None):
    CONNECTS.append(str(ip))
    return F.default_connect("127.0.0.1", TPORT if scheme == "https" else PORT, scheme, host, timeout, ssl_context)
def resolver_of(mapping):
    calls = []
    def resolve(host, port):
        calls.append(host)
        seq = mapping[host]
        return seq[min(len(calls), len(seq)) - 1] if isinstance(seq[0], list) else seq
    return resolve
def run(url, peer="REMOTE", resolver=None, connect=to_local, max_bytes=1024 * 1024, nets=None, **kw):
    CONNECTS.clear()
    try:
        got = F.fetch(url, peer, NETS if nets is None else nets, max_bytes, resolver=resolver, connect=connect, **kw)
        return ["ok", got["content_type"], len(got["data"]), list(CONNECTS)]
    except F.FetchError as e:
        return [e.reason, list(CONNECTS)]

r = {}
r["public_ok"] = run("http://public.test/img.png", resolver=resolver_of({"public.test": ["8.8.8.8"]}))
r["any_bad_addr"] = run("http://mixed.test/img.png", resolver=resolver_of({"mixed.test": ["8.8.8.8", "127.0.0.1"]}))
r["rebinding"] = run("http://rebind.test/img.png",
                     resolver=resolver_of({"rebind.test": [["8.8.8.8"], ["127.0.0.1"]]}))
r["redirect_metadata"] = run("http://public.test/meta", resolver=resolver_of({"public.test": ["8.8.8.8"]}))
for name, host in (("vpn", "10.14.0.7"), ("cgnat", "100.64.1.1"), ("vm", "10.211.55.3"),
                   ("wifi", "172.20.10.5"), ("decimal", "2130706433"), ("hex", "0x7f000001"),
                   ("short", "127.1"), ("mapped", "[::ffff:127.0.0.1]"), ("nat64", "[64:ff9b::7f00:1]"),
                   ("linklocal", "169.254.169.254"), ("loopback", "127.0.0.1"), ("multicast", "224.0.0.1"),
                   ("unspecified", "0.0.0.0"), ("v6_linklocal", "[fe80::1]")):
    r["remote_" + name] = run("http://%s/img.png" % host)[0]
r["loopback_peer_to_loopback"] = run("http://127.0.0.1/img.png", peer="LOOPBACK")[0]
r["gzip"] = run("http://8.8.8.8/gz")[0]
r["html"] = run("http://8.8.8.8/html")[0]
r["big"] = run("http://8.8.8.8/big")[0]
r["nolen"] = run("http://8.8.8.8/nolen")[0]
r["kinds_audio_only"] = run("http://8.8.8.8/img.png", kinds=("audio",))[0]
r["chain3_ok"] = run("http://8.8.8.8/chain2")[0]          # chain2→3→4→img：恰好 3 跳
r["chain4_limit"] = run("http://8.8.8.8/chain0")[0]        # chain0→…→img：5 跳，超过上限 3
r["404"] = run("http://8.8.8.8/missing")[0]
t0 = time.monotonic(); r["slow"] = [run("http://8.8.8.8/slow", wall_sec=1.0)[0], round(time.monotonic() - t0, 1)]
def gai_fail(host, port): raise socket.gaierror(8, "nodename nor servname provided")
r["dns"] = run("http://nope.test/img.png", resolver=gai_fail)[0]
def refused(*a, **k): raise ConnectionRefusedError(61, "refused")
r["connect"] = run("http://8.8.8.8/img.png", connect=refused)[0]
r["scheme"] = run("file:///etc/passwd")[0]
r["https_ok"] = run("https://public.test/img.png", resolver=resolver_of({"public.test": ["8.8.8.8"]}),
                    ssl_context=ssl.create_default_context(cafile=cert))[0]
r["https_wrong_name"] = run("https://other.test/img.png", resolver=resolver_of({"other.test": ["8.8.8.8"]}),
                            ssl_context=ssl.create_default_context(cafile=cert))[0]
SEEN.clear(); run("http://public.test:8080/img.png", resolver=resolver_of({"public.test": ["8.8.8.8"]}))
r["headers"] = SEEN[0]
r["unwrap"] = [str(F.unwrap(ipaddress.ip_address(a))) for a in
               ("::ffff:10.0.0.1", "64:ff9b::a00:1", "2002:a00:1::1", "8.8.8.8")]

t0 = time.monotonic(); d = run("http://8.8.8.8/drip", wall_sec=1.0)[0]
r["drip"] = [d, time.monotonic() - t0 < 2.0]       # 滴灌也要在墙钟内被切断
def slow_dns(host, port): time.sleep(3); return ["8.8.8.8"]
t0 = time.monotonic(); d = run("http://slowdns.test/img.png", resolver=slow_dns, wall_sec=1.0)[0]
r["slow_dns"] = [d, time.monotonic() - t0 < 2.0]
t0 = time.monotonic(); d = run("http://8.8.8.8/stall", wall_sec=1.0)[0]
r["stall"] = [d, time.monotonic() - t0 < 1.5]      # 读中途停住：socket 超时要按剩余墙钟收紧
r["trunc"] = run("http://8.8.8.8/trunc")[0]
r["reset"] = run("http://8.8.8.8/reset")[0]
BADURLS = ["http://8.8.8.8:99999/x", "http://8.8.8.8:abc/x", "http://[::1/x", "http://8.8.8.8:0/x"]
r["badurl"] = [run(u)[0] for u in BADURLS]
r["badredirect"] = [run("http://8.8.8.8/rbad%d" % i)[0] for i in range(4)]
NETS2 = NETS + [{"net": "8.8.4.0/24", "iface": "en1", "kind": "ethernet", "scope": None, "addr": "8.8.4.4"}]
r["self_addr"] = [run("http://8.8.4.4/img.png", nets=NETS2)[0], run("http://8.8.8.8/img.png", nets=NETS2)[0],
                  run("http://8.8.4.4/img.png", peer="LOOPBACK", nets=NETS2)[0]]

# 共享网段包含判断（gateway.peers.net_matches）在同一份放行网段上必须给出同一个答案——
# classify_peer 判来源、target_allowed 判代取目标，各自只多筛/少筛哪些条目参与判断，
# 但一条网段是否覆盖某地址这件事不能有两个答案。用不落公网、不带 scope 的地址对拍：
# target_allowed 的「is_global 直接放行」与「scope 网段整段不作代取目标」两条专属分叉
# 都不介入这些地址，因此这里必须完全一致（一旦不一致就是两处判断走漂了）。
def agree(addr):
    ip = ipaddress.ip_address(addr)
    peer_ok = P.classify_peer(addr, {"allowed": NETS}) == "REMOTE"
    target_ok = F.target_allowed(ip, "REMOTE", NETS)
    return [peer_ok, target_ok]
r["agree_vm_allowed"] = agree("10.211.55.3")
r["agree_wifi_allowed"] = agree("172.20.10.5")
r["agree_vpn_denied"] = agree("10.14.0.7")
r["agree_cgnat_denied"] = agree("100.64.1.1")

print(json.dumps(r))
"""


def test_fetch_rules(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=90)
    assert got["public_ok"] == ["ok", "image/png", 108, ["8.8.8.8"]]
    assert got["any_bad_addr"] == ["address_denied", []], "任一解析结果不合格 ⇒ 拒绝且零连接"
    assert got["rebinding"][0] == "ok" and got["rebinding"][-1] == ["8.8.8.8"], "每跳只解析一次，建连用首次合格地址"
    assert got["redirect_metadata"][0] == "address_denied", "重定向目标要重新校验（不是 connect_failed）"
    denied = ("vpn", "cgnat", "decimal", "hex", "short", "mapped", "nat64", "linklocal", "loopback",
              "multicast", "unspecified", "v6_linklocal")
    assert {n: got["remote_" + n] for n in denied} == {n: "address_denied" for n in denied}
    assert (got["remote_vm"], got["remote_wifi"]) == ("ok", "ok"), "D23：放行网段内的地址对远端来源放行"
    assert got["loopback_peer_to_loopback"] == "ok", "本机来源不限目标"
    assert (got["gzip"], got["html"], got["big"], got["nolen"]) == \
        ("encoding_rejected", "bad_content_type", "too_large", "too_large")
    assert got["kinds_audio_only"] == "bad_content_type"
    assert (got["chain3_ok"], got["chain4_limit"], got["404"]) == ("ok", "redirect_limit", "http_error")
    assert got["slow"][0] == "timeout" and got["slow"][1] < 2.5
    assert (got["dns"], got["connect"], got["scheme"]) == ("dns_failed", "connect_failed", "address_denied")
    assert got["https_ok"] == "ok" and got["https_wrong_name"] == "connect_failed", "https 按原主机名校验证书"
    assert got["headers"] == ["public.test:8080", "identity"]
    assert got["unwrap"] == ["10.0.0.1", "10.0.0.1", "10.0.0.1", "8.8.8.8"]
    assert got["agree_vm_allowed"] == [True, True], "peers.classify_peer 与 fetch.target_allowed 对 vm 网段必须一致"
    assert got["agree_wifi_allowed"] == [True, True], "两处对 wifi 网段必须一致"
    assert got["agree_vpn_denied"] == [False, False], "两处对未放行的私网地址必须一致"
    assert got["agree_cgnat_denied"] == [False, False], "两处对 CGNAT 私网地址必须一致"
    assert got["drip"] == ["timeout", True], "滴灌不得拖过 wall_sec"
    assert got["slow_dns"] == ["timeout", True], "DNS 解析受墙钟约束"
    assert got["trunc"] == "http_error", "Content-Length 与实际字节不符不得算成功"
    assert got["reset"] == "connect_failed", "读中途 RST 要映射成原因码而不是裸异常"
    assert got["badurl"] == ["address_denied"] * 4, "越界/非数字端口、方括号不配对、端口 0"
    assert got["badredirect"] == ["address_denied"] * 4, "重定向 Location 同样"
    assert got["self_addr"] == ["address_denied", "ok", "ok"], "本机自身地址（即便公网形态）远端来源不可达；本机来源不限"
    assert got["stall"] == ["timeout", True], "每次 recv 的超时按剩余墙钟收紧，不是沿用建连时的整段"
