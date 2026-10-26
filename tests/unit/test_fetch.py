"""spec §4.4 代取 / D23 / §8.2「媒体 · SSRF」。本地 HTTP(S) 服务 + 注入的解析器与连接探针，全程不联网。"""
import sys
from pathlib import Path

import pytest

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
        if p == "/%C3%A9.png": return self._send(200, "image/png", PNG)   # 非 ASCII 路径按 UTF-8 百分号编码后到达
        if p == "/sup2":                      # Content-Length 是 Unicode 数字「²」（字节 0xB2）
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "\u00b2"); self.end_headers()
            self.wfile.write(PNG); return
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
        if p == "/hdrdrip":                   # 响应头逐字节滴灌（0.2 s/字节）
            try:
                for b in b"HTTP/1.0 200 OK\r\nContent-Type: image/png\r\nX-Pad: " + b"a" * 500:
                    self.wfile.write(bytes([b])); self.wfile.flush(); time.sleep(0.2)
            except OSError:
                pass
            return
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
        {"net": "fe80::/64", "iface": "en0", "kind": "wifi", "scope": "en0", "addr": "fe80::103"},
        {"net": "fda8:69e7:f901:187b::/64", "iface": "bridge100", "kind": "vm", "scope": None,
         "addr": "fda8:69e7:f901:187b::10c"}]
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
    except Exception as e:                    # 越过 FetchError 契约的裸异常：记成值，让断言点名是哪一条
        return ["ESCAPED " + type(e).__name__, list(CONNECTS)]

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
r["chain4_exact"] = run("http://8.8.8.8/chain1")[0]        # chain1→2→3→4→img：4 跳，恰好超过上限 3
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
t0 = time.monotonic(); d = run("http://8.8.8.8/hdrdrip", wall_sec=1.0)[0]
r["hdrdrip"] = [d, time.monotonic() - t0 < 1.5]    # 头部滴灌由看门狗切断
SLOTS0 = F._DNS_SLOTS; F._DNS_SLOTS = threading.BoundedSemaphore(2)
def hang_dns(host, port): time.sleep(2.0); return ["8.8.8.8"]
fast_calls = []
def fast_dns(host, port): fast_calls.append(host); return ["8.8.8.8"]
two = [run("http://h%d.test/img.png" % i, resolver=hang_dns, wall_sec=0.3)[0] for i in range(2)]
t0 = time.monotonic(); third = run("http://h3.test/img.png", resolver=fast_dns)[0]
r["dns_slots"] = [two, third, list(fast_calls), time.monotonic() - t0 < 0.5]
time.sleep(2.2); r["dns_slots_freed"] = run("http://h4.test/img.png", resolver=fast_dns)[0]
F._DNS_SLOTS = SLOTS0
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
r["agree_v6_vm_allowed"] = agree("fda8:69e7:f901:187b::5")
r["agree_v6_ula_denied"] = agree("fd00:9::1")
r["agree_v6_doc_denied"] = agree("2001:db8:0:9::1")
# 非 ASCII：路径按 UTF-8 百分号编码；IDN 主机在解析前拒绝（不发 DNS），都不得以裸异常越过契约
idn_calls = []
def idn_resolver(host, port): idn_calls.append(host); return ["8.8.8.8"]
r["nonascii_path"] = run("http://8.8.8.8/\u00e9.png")[0]
r["idn_host"] = [run("http://\u4f8b\u5b50.test/img.png", resolver=idn_resolver)[0], list(idn_calls)]
r["sup2_length"] = run("http://8.8.8.8/sup2")[:3]
r["site_local"] = run("http://[fec0::1]/img.png")[0]
# 非规范 IP 写法的拒绝**由这条规则本身**给出：解析器桩把它们解析到公网地址（不经回环拒绝兜底），
# 规则若被删掉，结果会变成 ok
num_calls = []
def num_resolver(host, port): num_calls.append(host); return ["8.8.8.8"]
r["numericish_public"] = [[run("http://%s/img.png" % h, resolver=num_resolver)[0]
                           for h in ("134744072", "0x08080808", "8.8.2056", "8.8.8.8.")], list(num_calls)]

print(json.dumps(r))
"""


# **两个解释器都跑**（终审 A X-1）：网关优先用部署 venv 的 3.14（spec D20），回落 3.9.6。
# 3.14 的 `read1` 读满后关 fp，以关连接标界的响应（本文件的桩服务器正是 HTTP/1.0）此前
# 每次代取都 connect_failed —— 只在 3.9.6 上跑的这组用例对它完全不敏感。
INTERPRETERS = ["/usr/bin/python3", sys.executable]


@pytest.mark.parametrize("interp", INTERPRETERS, ids=["py39", "venv"])
def test_fetch_rules(payload, interp):
    got = run_floor(payload, interp, PROG, with_gateway=True, timeout=90)
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
    assert got["hdrdrip"] == ["timeout", True], "响应头滴灌要被总墙钟看门狗切断"
    assert got["dns_slots"] == [["timeout", "timeout"], "timeout", [], True], "名额满：立刻 timeout 且不再调用解析器"
    assert got["dns_slots_freed"] == "ok", "解析线程结束后名额释放"
    assert got["chain4_exact"] == "redirect_limit", "4 跳必须超限：上限是 3，不是「足够大」"
    assert got["agree_v6_vm_allowed"] == [True, True], "IPv6 放行网段两处判定必须一致"
    assert got["agree_v6_ula_denied"] == [False, False], "未放行的 IPv6 ULA 两处都拒"
    assert got["agree_v6_doc_denied"] == [False, False], "未放行的非公网 IPv6 两处都拒"
    assert got["nonascii_path"] == "ok", "非 ASCII 路径按 UTF-8 百分号编码，不得抛 UnicodeEncodeError"
    assert got["idn_host"] == ["address_denied", []], "IDN 主机解析前拒绝，映射成原因码"
    assert got["sup2_length"] == ["ok", "image/png", 108], "Unicode 数字的 Content-Length 不得让 int() 抛出"
    assert got["site_local"] == "address_denied", "fec0::/10 站点本地地址拒绝"
    assert got["numericish_public"] == [["address_denied"] * 4, []], \
        "非规范 IP 写法由规则本身拒绝，且不交给解析器"


NET = Path(__file__).resolve().parents[1] / "fixtures" / "net"

# 「本机自身地址一律拒」按**真实 ifconfig 形状**对拍（终审 A I-1）。冻结抓取本身已有
# en0 同一 /64 上的 secured + temporary 两个地址；再注入真实机器上常见、夹具里缺的三种：
# en0 的第二个 IPv4（别名）、en0 的第二个 ULA、被排除接口 utun4 上 VPN 分配的公网 IPv6。
# 每个解析出的本机地址，连同它的 `%接口` 变体，都必须 address_denied。
OWN = r"""
import ipaddress, json
from gateway import fetch as F
from gateway import peers as P
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
def inject(ifc, iface, line):
    out = []
    for ln in ifc.splitlines(True):
        out.append(ln)
        if ln.startswith(iface + ": flags="):
            out.append("\t" + line + "\n")
    return "".join(out)
IF = inject(IF, "en0", "inet 172.20.10.4 netmask 0xfffffff0 broadcast 172.20.10.15")
IF = inject(IF, "en0", "inet6 fda8:1111:2222:3333::7 prefixlen 64 autoconf secured")
IF = inject(IF, "en0", "inet6 fda8:1111:2222:3333::8 prefixlen 64 autoconf temporary")
IF = inject(IF, "utun4", "inet6 2606:4700:110:8a2b::1 prefixlen 64")
v = P.NetView(lambda: (HW, IF), clock=lambda: 0.0)
v.refresh()
snap = v.snapshot()
ifs = P.parse_ifconfig(IF)
pairs = []
for name, info in ifs.items():
    for a in [x[0] for x in info["inet"]] + [x[0] for x in info["inet6"]]:
        pairs.append((name, a))
def literal(a, zone=None):
    return ("[%s%s]" % (a, "%" + zone if zone else "")) if ":" in a else a
leaks = []
for name, a in pairs:
    variants = [a] + (["%s%%%s" % (a, name), "%s%%en0" % a] if ":" in a else [])
    for var in variants:
        ip = ipaddress.ip_address(var)
        if F.target_allowed(ip, "REMOTE", snap["allowed_nets"], snap["own_addrs"]):
            leaks.append(["target_allowed", name, var])
        url = "http://%s:8000/x" % literal(*(var.split("%") if "%" in var else [var]))
        def no_connect(*a, **k):
            raise AssertionError("不该建连")
        try:
            F.fetch(url, "REMOTE", snap["allowed_nets"], 1024, connect=no_connect,
                    own_addrs=snap["own_addrs"])
            leaks.append(["fetch_ok", name, var])
        except AssertionError:
            leaks.append(["fetch_connected", name, var])
        except F.FetchError as e:
            if e.reason != "address_denied":
                leaks.append(["fetch_" + e.reason, name, var])
# 对照：同网段的**非本机**地址照常放行（拒绝不是靠把整段拉黑实现的）
ctrl = {a: F.target_allowed(ipaddress.ip_address(a), "REMOTE", snap["allowed_nets"], snap["own_addrs"])
        for a in ("172.20.10.5", "fda8:69e7:f901:187b::5", "2606:4700:110:8a2b::2")}
print(json.dumps({"n": len(pairs), "own_n": len(snap["own_addrs"]), "leaks": leaks, "ctrl": ctrl,
                  "has_utun": "2606:4700:110:8a2b::1" in snap["own_addrs"]}))
"""

# 真机：本机 ifconfig 的全部地址（含 `%接口` 变体）都必须被拒。只回计数与泄漏条数，不打印地址。
REAL = r"""
import ipaddress, json, subprocess
from gateway import fetch as F
from gateway import peers as P
load = P.command_loader({"networksetup": "/usr/sbin/networksetup", "ifconfig": "/sbin/ifconfig"})
try:
    HW, IF = load()
except Exception:
    print(json.dumps({"skip": True})); raise SystemExit
v = P.NetView(lambda: (HW, IF), clock=lambda: 0.0)
v.refresh()
snap = v.snapshot()
n, leaks = 0, 0
for name, info in P.parse_ifconfig(IF).items():
    for a in [x[0] for x in info["inet"]] + [x[0] for x in info["inet6"]]:
        for var in [a] + (["%s%%%s" % (a, name)] if ":" in a else []):
            n += 1
            if F.target_allowed(ipaddress.ip_address(var), "REMOTE", snap["allowed_nets"], snap["own_addrs"]):
                leaks += 1
print(json.dumps({"skip": n == 0, "n": n, "leaks": leaks}))
"""


@pytest.mark.parametrize("interp", INTERPRETERS, ids=["py39", "venv"])
def test_own_addresses_denied_in_every_form(payload, interp):
    got = run_floor(payload, interp, OWN, with_gateway=True, timeout=60, NET=NET)
    assert got["n"] >= 20 and got["has_utun"], f"夹具形状不对（地址太少或被排除接口没进 own_addrs）：{got}"
    assert got["leaks"] == [], f"本机自身地址经别名/zone/被排除接口漏过：{got['leaks']}"
    assert got["ctrl"] == {"172.20.10.5": True, "fda8:69e7:f901:187b::5": True,
                           "2606:4700:110:8a2b::2": True}, f"非本机地址被误拒：{got['ctrl']}"


def test_own_addresses_denied_on_this_machine(payload, sys_py):
    got = run_floor(payload, sys_py, REAL, with_gateway=True, timeout=60)
    if got["skip"]:
        pytest.skip("本机取不到网络接口")
    assert got["leaks"] == 0, f"本机 {got['n']} 个地址形态里有 {got['leaks']} 个没被拒"


# 网段视图取不到（回退模式 / 地址刚变化、还没到刷新周期）时快照里没有本机地址：
# 放行网段照旧覆盖本机，`own_addrs` 为空。本机地址仍须拒 —— 判据是内核能否 bind 上它，
# 不是快照。对照：同网段里绑不上的地址照常放行；bind 出意外错误时按本机处理（拒）。
FALLBACK = r"""
import errno, ipaddress, json, socket
from gateway import fetch as F
from gateway import peers as P
load = P.command_loader({"networksetup": "/usr/sbin/networksetup", "ifconfig": "/sbin/ifconfig"})
try:
    _, IF = load()
except Exception:
    print(json.dumps({"skip": True})); raise SystemExit
own = set()
for info in P.parse_ifconfig(IF).values():
    for a in [x[0] for x in info["inet"]] + [x[0] for x in info["inet6"]]:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not (ip.is_loopback or ip.is_link_local):
            own.add(ip)
n, leaks, ctrl = 0, [], []
for ip in sorted(own, key=str):
    net = ipaddress.ip_network("%s/%d" % (ip, 24 if ip.version == 4 else 64), strict=False)
    nets = [{"net": str(net), "iface": "en9", "kind": "ethernet", "scope": None, "addr": None}]
    n += 1
    if F.target_allowed(ip, "REMOTE", nets, []):
        leaks.append(str(ip))
    other = next(h for h in (net.network_address + k for k in range(2, 50)) if h not in own)
    ctrl.append(F.target_allowed(other, "REMOTE", nets, []))
class Boom:
    def __init__(self, *a): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def bind(self, addr): raise OSError(errno.EMFILE, "too many open files")
real, F.socket.socket = F.socket.socket, Boom
boom = F.target_allowed(ipaddress.ip_address("10.211.55.3"), "REMOTE",
                        [{"net": "10.211.55.0/24", "iface": "en9", "kind": "ethernet", "scope": None, "addr": None}], [])
F.socket.socket = real
print(json.dumps({"skip": n == 0, "n": n, "leaks": leaks, "ctrl": ctrl, "boom": boom}))
"""


@pytest.mark.parametrize("interp", INTERPRETERS, ids=["py39", "venv"])
def test_own_addresses_denied_without_net_view(payload, interp):
    got = run_floor(payload, interp, FALLBACK, with_gateway=True, timeout=60)
    if got["skip"]:
        pytest.skip("本机没有回环与链路本地之外的地址")
    assert got["leaks"] == [], f"快照缺本机地址时，这些本机地址被当作可代取目标：{got['leaks']}"
    assert got["ctrl"] == [True] * got["n"], "同网段的非本机地址被误拒：判据退化成了一律拒"
    assert got["boom"] is False, "bind 出意外错误时必须按本机地址拒（fail closed）"
