"""L3 · 真安装产物上由 `mlx up api` 起的真网关（spec §8.2「来源·分类」「传输」「TLS」、§8.3「密钥·泄漏」）。

REMOTE 来源**真实触发**：客户端 socket 绑定本机 Wi-Fi / 有线 / 虚拟机网桥上的 IPv4 再连网关，
网关看到的对端地址就是它 —— 产品代码里没有「把回环当远端」的测试钩子。

2 MB 请求体不真的发：见 controller ruling —— 只发**超限 Content-Length 的请求头**、
不写体、只读状态行断 413，跟 Task 7（`tests/integration/test_l2_gateway_server.py`）的
`raw_request` 用同一形状。真发 2 MB 体在这台机器上会在 server 判 413 不读体、
client 自己的 write 卡住再 ECONNRESET（预检 10/10 复现），测的是传输巧合不是行为。
"""
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402
from floor import run_floor  # noqa: E402

pytestmark = pytest.mark.e2e


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def product(tmp_path, repo):
    roots = {"MLX_ROOT_CODE": tmp_path / "code", "MLX_ROOT_DATA": tmp_path / "data",
             "MLX_ROOT_WEIGHTS": tmp_path / "weights"}
    port = _free_port()
    env = dict(os.environ, PATH="/usr/bin:/bin", MLX_GATEWAY_PORT=str(port),
               **{k: str(v) for k, v in roots.items()})
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                       capture_output=True, text=True, timeout=120, check=False, env=env)
    assert r.returncode == 0, r.stderr
    code = roots["MLX_ROOT_CODE"]

    def mlx(*argv, extra_env=None):
        return subprocess.run([str(code / "bin" / "mlx"), *argv], capture_output=True, text=True,
                              timeout=90, check=False,
                              env=dict(env, MLX_SHARE_ROOT=str(code / "share"), **(extra_env or {})))

    data = roots["MLX_ROOT_DATA"]
    settings = json.loads((data / "settings.json").read_text())
    try:
        yield {"mlx": mlx, "port": port, "data": data, "code": code, "root": tmp_path,
               "token": settings["api.token"], "backend_key": settings["backend.api_key"],
               "roots": list(roots.values())}
    finally:
        mlx("down", "api")


def _lan_ipv4(payload, sys_py):
    """真实 `networksetup`/`ifconfig` 抓取上跑一遍放行网段计算。

    除了给出一个可用于 REMOTE 触发的 LAN IPv4，**同一次抓取**还核对排除规则
    命中了这台机器真实的 VPN 与雷雳接口——夹具（`tests/fixtures/net/ifconfig.txt`）
    能证明规则对着冻结文本正确，证不了规则对着*这台机器现在的真实输出*也对；
    删掉 `compute_nets` 里的 `utun` 排除，这条走真实抓取的 L3 是唯一能抓到的地方。
    """
    got = run_floor(payload, sys_py, """
        import json
        from gateway import peers
        from mlx_server.ctx import DEFAULT_BINS
        hw, ifc = peers.command_loader(DEFAULT_BINS)()
        n = peers.compute_nets(peers.parse_hardware_ports(hw), peers.parse_ifconfig(ifc))
        print(json.dumps({
            "allowed_v4": [a["addr"] for a in n["allowed"]
                          if a["kind"] in ("wifi", "ethernet", "vm") and a["addr"]
                          and ":" not in a["addr"]],
            "allowed_addrs": [a["addr"] for a in n["allowed"] if a["addr"]],
            "excluded_ifaces": [e["iface"] for e in n["excluded"]],
        }))
    """, with_gateway=True)
    allowed_v4 = got["allowed_v4"]
    assert allowed_v4, ("本机没有 Wi-Fi / 有线 / 虚拟机网桥上的 IPv4 地址 —— REMOTE 来源无法真实触发。"
                       "spec §8.2 要求此时失败而不是跳过：连上 Wi-Fi 或启动 Parallels 后重跑")
    # VPN（utun4，10.14.0.2）绝不能落进放行网段——按默认路由判定会误放行 VPN 对端，
    # 这正是 `peers.py` 排除表要挡的那一种。host 状态变了（VPN 掉线）要失败，不是跳过。
    assert "10.14.0.2" not in got["allowed_addrs"], (
        f"VPN 地址 10.14.0.2 出现在放行网段：utun 排除规则没有命中真实 ifconfig 输出——"
        f"{got['allowed_addrs']}")
    assert any(iface.startswith("utun") for iface in got["excluded_ifaces"]), (
        f"真实抓取的排除列表里没有任何 utun* 接口（VPN 应在线才能触发这条）："
        f"{got['excluded_ifaces']}")
    return allowed_v4[0]


def _http(host, port, method, path, token=None, body=None, source=None, tls_ctx=None):
    kw = {"timeout": 30, "source_address": (source, 0) if source else None}
    conn = (http.client.HTTPSConnection(host, port, context=tls_ctx, **kw) if tls_ctx
            else http.client.HTTPConnection(host, port, **kw))
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=headers)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, raw


def _headers_only_status(host, port, token, content_length, path="/v1/chat/completions",
                         timeout=5):
    """发只带请求头的 POST（声明 `content_length` 字节的体，一个字节体都不写），只读状态行。

    controller ruling 1 的替代形状：不真的发一个超大体 —— server 看 `Content-Length`
    立刻判超限就回 413 且不读体，client 若真写体会在这条连接上卡住再被 RST。
    这里连体都不构造、不 sendall，规避同一条竞态。
    """
    s = socket.create_connection((host, port), timeout=timeout)
    head = (f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\n"
           f"Content-Type: application/json\r\nContent-Length: {content_length}\r\n\r\n").encode()
    s.sendall(head)
    s.settimeout(timeout)
    try:
        first = s.recv(256).decode(errors="replace")
    except OSError:
        first = ""
    s.close()
    m = re.match(r"HTTP/1\.[01] (\d+)", first)
    assert m, f"没收到状态行：{first!r}"
    return int(m.group(1))


def _wait_record(data, key, want=None, timeout=15):
    path = data / "run" / "api.json"
    end = time.time() + timeout
    while time.time() < end:
        if path.exists():
            rec = json.loads(path.read_text())
            if key in rec and (want is None or rec[key] == want):
                return rec
        time.sleep(0.2)
    raise AssertionError(f"{timeout}s 内运行记录没有出现 {key}={want!r}")


def test_remote_peer_record_checks_and_no_key_leak(product, payload, sys_py):
    lan = _lan_ipv4(payload, sys_py)
    mlx, port, tok = product["mlx"], product["port"], product["token"]
    r = mlx("up", "api")
    assert r.returncode == 0, r.stdout + r.stderr

    rec = _wait_record(product["data"], "nets_source", "parsed")
    assert rec["bind"] == "0.0.0.0" and rec["interpreter"], rec
    assert any(n.get("addr") == lan for n in rec["allowed_nets"]), f"{lan} 不在放行网段：{rec}"

    assert _http(lan, port, "GET", "/v1/models", token=tok, source=lan)[0] == 200
    assert _http(lan, port, "GET", "/v1/models", source=lan)[0] == 401
    assert _http("127.0.0.1", port, "GET", "/v1/models")[0] == 401, "本机也要令牌"
    assert _http("127.0.0.1", port, "GET", "/v1/models", token="wrong" * 8)[0] == 401
    assert _http("127.0.0.1", port, "POST", "/v1/unload", token=tok, body=b"{}")[0] == 404

    info = json.loads(mlx("api", "info", "--json").stdout)
    assert f"http://{lan}:{port}/v1" in [u["openai"] for u in info["urls"]]
    assert info["token"]["state"] == "ok"

    check = mlx("check")
    text = check.stdout + check.stderr
    for item in ("gateway_nets", "gateway_port", "settings_mode"):
        assert re.search(item + r"\s+ok", text), f"check 的 {item} 未通过：\n{text}"
    mlx("status")

    needles = [product["token"], product["backend_key"]]
    needles += [n[:8] for n in needles]
    scanned, leaked = [], []
    for base in product["roots"]:
        for p in base.rglob("*"):
            if p.is_symlink() or not p.is_file() or p.name == "settings.json" or ".venv" in p.parts:
                continue
            if p.stat().st_size > 50 * 1024 * 1024:
                continue
            scanned.append(p.name)
            if any(n.encode() in p.read_bytes() for n in needles):
                leaked.append(str(p))
    Cardinality.require_nonempty("被扫描的文件", scanned, "一个文件都没扫到 ⇒ 空真")
    assert "api.log" in scanned and "api.json" in scanned, "泄漏扫描没覆盖网关日志与运行记录"

    # 上面这一遍是网关**还活着**时扫的（`api.json` 之所以只能在这时扫：
    # `mlx down api` 会删掉运行记录，晚一步它就不在盘上了）。但 `api.log` 是子进程的
    # stdout/stderr，stdout 重定向到文件后是块缓冲（8 KB）——本轮请求量远不到 8 KB，
    # 进程活着时 `info`（含每条 http 访问行）大概率还压在缓冲区里没落盘，这一遍在
    # `api.log` 上「没扫到泄漏」证明不了任何东西。`mlx down api` 用 `killpg` 等到
    # 进程真的退出才返回；停掉之后重读一遍 `api.log`，才是扫的终态内容。
    down_r = mlx("down", "api")
    assert down_r.returncode == 0, down_r.stdout + down_r.stderr
    api_log_bytes = (product["data"] / "logs" / "api.log").read_bytes()
    assert api_log_bytes, "api.log 是空文件——缓冲区没落盘，泄漏扫描没有实际扫过任何访问日志内容"
    if any(n.encode() in api_log_bytes for n in needles):
        leaked.append(str(product["data"] / "logs" / "api.log") + "（进程退出后重读）")
    events = []
    for line in api_log_bytes.decode(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    assert any(e.get("event") == "http" for e in events), (
        f"api.log 里没有任何一条可解析的 http 事件——落盘内容没有覆盖到访问日志：{events!r}")
    Cardinality.check("含密钥的文件", leaked, rel="empty")


def test_tls_transport_env_token_and_loopback_bind(product, payload, sys_py):
    lan = _lan_ipv4(payload, sys_py)
    mlx, port, tok, root = product["mlx"], product["port"], product["token"], product["root"]

    cert, key = root / "gw.crt", root / "gw.key"
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=mlx-gateway",
                    "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
    key.chmod(0o600)
    for k, v in (("api.tls_cert", cert), ("api.tls_key", key)):
        assert mlx("config", "set", k, f"--value={v}").returncode == 0
    r = mlx("up", "api")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _wait_record(product["data"], "tls")["tls"] is True
    tls_ctx = ssl.create_default_context(cafile=str(cert))
    assert _http("127.0.0.1", port, "GET", "/v1/models", token=tok, tls_ctx=tls_ctx)[0] == 200

    s = socket.create_connection(("127.0.0.1", port), 3)
    s.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
    s.settimeout(3)
    t0 = time.time()
    try:
        data = s.recv(64)
    except OSError:
        data = b""
    assert data == b"" and time.time() - t0 < 2, "明文请求打到 TLS 端口应被立即关闭"

    key.chmod(0o644)
    r = mlx("restart", "api")
    assert r.returncode == 6 and "0600" in (r.stdout + r.stderr), r.stdout + r.stderr
    # **校验拒绝不得先把正在跑的服务毁掉**（收尾评审 Critical 5）—— 在真网关进程上验。
    # `restart` 的校验段整段排在 `supervisor.down()` 之前，所以这条 rc 6 之后网关
    # **仍在跑**，而且跑的还是它启动时加载的那份 TLS 配置。
    # 此前的顺序（先 down 再校验）会让这里的网关已经死了、运行记录也没了，
    # 用户拿到的是一句「拒绝」加一个停掉的服务；本用例当时正是**依赖**那个缺陷
    # ——下面那句原本是 `up api`，靠的就是「前面那次 restart 已经把它停了」。
    assert _http("127.0.0.1", port, "GET", "/v1/models", token=tok, tls_ctx=tls_ctx)[0] == 200, \
        "restart 的校验拒绝把正在跑的网关一起停掉了"
    key.chmod(0o600)
    for k in ("api.tls_cert", "api.tls_key"):
        assert mlx("config", "unset", k).returncode == 0

    assert mlx("config", "set", "api.max_body_mb", "--value=1").returncode == 0
    # 网关还在跑（见上），所以要让新配置生效必须 `restart`：`up` 会命中
    # `already_running` 而 rc 0，屏幕上看着成功，跑的却还是旧配置的老进程。
    assert mlx("restart", "api").returncode == 0
    status = _headers_only_status("127.0.0.1", port, tok, 2 * 1024 * 1024)
    assert status == 413, "1 MB 上限下声明 2 MB 体的请求应立即 413（只发头，不发体）"
    assert mlx("config", "unset", "api.max_body_mb").returncode == 0

    other = "E" * 43
    r = mlx("restart", "api", extra_env={"MLX_GATEWAY_TOKEN": other})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _http("127.0.0.1", port, "GET", "/v1/models", token=tok)[0] == 401
    assert _http("127.0.0.1", port, "GET", "/v1/models", token=other)[0] == 200
    assert _wait_record(product["data"], "token_source")["token_source"] == "env"
    huge = json.dumps({"pad": "x" * (200 * 1024 * 1024)}).encode()
    assert _http("127.0.0.1", port, "POST", "/v1/chat/completions", token=other, body=huge)[0] == 400, \
        "默认上限 256 MB 内的请求体应通过上限检查（缺 model ⇒ 400）"

    assert mlx("config", "set", "api.bind", "--value=127.0.0.1").returncode == 0
    assert mlx("restart", "api").returncode == 0
    assert _http("127.0.0.1", port, "GET", "/v1/models", token=tok)[0] == 200
    # `ConnectionRefusedError`（而不是宽泛的 `OSError`）：`socket.timeout`/`EHOSTUNREACH`
    # 也是 `OSError` 的子类——Wi-Fi 掉线时连接会超时或不可达，那种失败会让这条断言
    # 以错误的理由通过，把「网口坏了」误证成「回环收紧生效了」。
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((lan, port), 2)


def test_connection_burst_is_queued_not_reset(product):
    """N 个客户端同时连进来，每个都要拿到 HTTP 响应（2026-10-29 真机验收 #16）。

    `socketserver` 默认 `request_queue_size = 5`：accept 线程来不及取的连接排在内核的
    listen 队列里，队列满了内核直接 RST —— 网关代码见不到这些连接，日志里什么都没有，
    客户端拿到 `ConnectionResetError` / `BrokenPipeError`。真机上 12 路并发有 4 路这样失败，
    而产品默认的 `max_concurrency` 就是 8。

    40 路用 barrier 同时 connect，逼出 accept 跟不上的那一刻；只认 HTTP 响应行，
    `401`（没带令牌）也算 —— 要证的是连接进得来，不是鉴权。
    """
    mlx, port = product["mlx"], product["port"]
    r = mlx("up", "api")
    assert r.returncode == 0, r.stdout + r.stderr
    n = 40
    go = threading.Barrier(n)
    got = [None] * n

    def one(i):
        s = socket.socket()
        s.settimeout(15)
        go.wait()
        try:
            s.connect(("127.0.0.1", port))
            s.sendall(b"GET /v1/models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            got[i] = s.recv(12)
        except OSError as exc:
            got[i] = type(exc).__name__
        finally:
            s.close()

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    bad = [g for g in got if not (isinstance(g, bytes) and g.startswith(b"HTTP/1."))]
    assert bad == [], f"{len(bad)}/{n} 个连接没拿到 HTTP 响应：{sorted(set(map(str, bad)))}"

