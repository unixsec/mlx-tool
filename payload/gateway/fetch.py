"""T2 · 媒体代取（spec §4.4、D23）。**网关对外发请求只在这里**。

远端来源发来的 `http(s)://` 图片 URL 若原样交给后端，mlx_vlm 会用 `requests.get` 去取 ——
于是任何持令牌的 Wi-Fi 设备都能让本机去访问 VPN 网段、云元数据地址或本机管理端口（SSRF）。
网关先取回来、改写成 data URI，后端就不再对外连接。

规则：每一跳**只解析一次**，全部地址合格才以首个地址建连（挡 DNS 重绑定）；重定向手动处理并
逐跳重新校验（urllib 默认 opener 会自动跟随而不校验）；非规范 IP 写法直接拒绝（`getaddrinfo`
会把 `2130706433`、`0x7f000001`、`127.1` 解析成 127.0.0.1）。

「地址是否落在放行网段里」复用 `gateway.peers.net_matches`——`peers.classify_peer`
判来源、这里的 `target_allowed` 判代取目标，是同一个仓库里两处独立的地址判定；两处
各写一套包含逻辑，日后网段条目形状一变或某个特例只改了一处，两处就会对同一个地址
判出不同结果，而这正是 SSRF 检查与来源放行检查互相拖后腿的那种缺陷，参见
`gateway.peers.net_matches` 的文档。
"""
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

from gateway import peers

REASONS = ("address_denied", "dns_failed", "connect_failed", "redirect_limit", "too_large",
           "bad_content_type", "timeout", "encoding_rejected", "http_error")
WALL_SEC = 30.0
MAX_REDIRECTS = 3
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NUMERICISH = re.compile(r"^(?:0x[0-9a-fx.]*|[0-9.]+)$", re.IGNORECASE)


class FetchError(Exception):
    """代取失败。`reason ∈ REASONS`；`detail` 只进日志，不回给客户端。"""

    def __init__(self, reason, detail=""):
        # type: (str, str) -> None
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def unwrap(ip):
    # type: (object) -> object
    """内嵌 IPv4 的 IPv6 地址还原成 IPv4 再校验（IPv4-mapped、NAT64、6to4）。"""
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.sixtofour is not None:
            return ip.sixtofour
    return ip


def target_allowed(ip, peer_class, allowed_nets):
    # type: (object, str, list) -> bool
    """D23：本机来源不限；远端来源只放行**公网**与**当前放行网段**。

    网段包含判断复用 `peers.net_matches`（见模块文档）。这里比 `classify_peer`
    多两条代取专属的过滤，是「参与判断的网段有哪些」上的分叉，不是判断本身分叉：

    - 整段排掉带 `scope` 的条目（链路本地网段）：`classify_peer` 靠内核给的真实
      socket `scope_id`去比 `fe80::/64%en0`，代取目标地址来自任意来源的 DNS 解析，
      没有一个可信的 scope 能比——传 `scope=None` 让 `net_matches` 走到 scope 比较
      也必然不等，这里索性直接跳过这类条目，语义更直白；
    - `ip.is_global` 直接放行公网地址：媒体代取本来就是要连公网站点，`classify_peer`
      没有这条，因为「来源」判定从不把未配置的公网地址当已知网络。
    """
    if peer_class == "LOOPBACK":
        return True
    ip = unwrap(ip)
    if (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
            or ip.is_reserved):
        return False
    if ip.version == 4 and int(ip) == 0xFFFFFFFF:
        return False
    if ip.version == 6 and ip.teredo is not None:
        return False
    for n in allowed_nets or ():                  # 本机自身地址（含公网形态的）不作代取目标
        try:
            if ip == unwrap(ipaddress.ip_address(str(n.get("addr")).split("%")[0])):
                return False
        except ValueError:
            continue
    if ip.is_global:
        return True
    for n in allowed_nets or ():
        if n.get("kind") == "loopback" or n.get("scope"):
            continue                              # 回环与链路本地网段不作为代取目标
        if peers.net_matches(n, ip):
            return True
    return False


def _resolve(host, port):
    # type: (str, int) -> list
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _resolve_by_deadline(resolver, host, port, remaining):
    # type: (object, str, int, float) -> list
    """解析放守护线程，按剩余墙钟 join；超时放弃线程（它只会自己结束），不阻塞调用方。"""
    box = {}

    def work():
        try:
            box["ok"] = resolver(host, port)
        except BaseException as exc:  # noqa: BLE001 —— 原样交回调用线程处理
            box["err"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(max(0.01, remaining))
    if t.is_alive():
        raise FetchError("timeout", "DNS")
    if "err" in box:
        raise box["err"]
    return box["ok"]


def _host_ips(host, port, resolver, remaining=WALL_SEC):
    # type: (str, int, object, float) -> list
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    if _NUMERICISH.match(host):
        raise FetchError("address_denied", "非规范的 IP 写法")
    try:
        answers = _resolve_by_deadline(resolver or _resolve, host, port, remaining)
    except (OSError, UnicodeError) as exc:
        raise FetchError("dns_failed", str(exc)[:80]) from exc
    ips = []
    for a in answers:
        ip = ipaddress.ip_address(str(a).split("%")[0])
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise FetchError("dns_failed", "没有地址")
    return ips


def default_connect(ip, port, scheme, host, timeout, ssl_context=None):
    # type: (object, int, str, str, float, object) -> object
    """**连向已校验的 IP**，https 用原主机名做 SNI 与证书校验。返回 socket 已就绪的连接。"""
    sock = socket.create_connection((str(ip), port), timeout=timeout)
    if scheme == "https":
        ctx = ssl_context or ssl.create_default_context()
        sock = ctx.wrap_socket(sock, server_hostname=host)
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = sock
    return conn


def fetch(url, peer_class, allowed_nets, max_bytes, kinds=("image", "audio", "video"),
          resolver=None, connect=None, ssl_context=None, wall_sec=WALL_SEC, clock=time.monotonic):
    # type: (str, str, list, int, tuple, object, object, object, float, object) -> dict
    deadline = clock() + wall_sec
    connect = connect or default_connect
    for _hop in range(MAX_REDIRECTS + 1):
        try:
            parts = urlsplit(url)
            scheme = (parts.scheme or "").lower()
            host = parts.hostname
            explicit_port = parts.port
        except ValueError as exc:                 # 端口越界/非数字、方括号不配对
            raise FetchError("address_denied", "URL 无法解析") from exc
        if scheme not in ("http", "https") or not host:
            raise FetchError("address_denied", "只允许 http / https")
        if explicit_port == 0:
            raise FetchError("address_denied", "端口 0")
        default_port = 443 if scheme == "https" else 80
        port = explicit_port or default_port
        ips = _host_ips(host, port, resolver, deadline - clock())
        if not all(target_allowed(ip, peer_class, allowed_nets) for ip in ips):
            raise FetchError("address_denied", host)
        remaining = deadline - clock()
        if remaining <= 0:
            raise FetchError("timeout", host)
        conn = None
        try:
            try:
                conn = connect(ips[0], port, scheme, host, remaining, ssl_context)
                sock = getattr(conn, "sock", None)   # getresponse 遇 will_close 会把 conn.sock 置空，先留住
                target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
                host_header = (f"[{host}]" if ":" in host else host) + (
                    "" if port == default_port else f":{port}")
                conn.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
                conn.putheader("Host", host_header)
                conn.putheader("Accept-Encoding", "identity")
                conn.putheader("User-Agent", "mlx-toolkit-gateway")
                conn.endheaders()
                resp = conn.getresponse()
            except socket.timeout as exc:
                raise FetchError("timeout", host) from exc
            except (OSError, http.client.HTTPException) as exc:
                raise FetchError("connect_failed", str(exc)[:80]) from exc
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.getheader("Location")
                if not location:
                    raise FetchError("http_error", "重定向缺 Location")
                try:
                    url = urljoin(url, location)
                except ValueError as exc:
                    raise FetchError("address_denied", "重定向目标无法解析") from exc
                continue
            if resp.status != 200:
                raise FetchError("http_error", str(resp.status))
            encoding = (resp.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding not in ("identity", ""):
                raise FetchError("encoding_rejected", encoding)
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if ctype.split("/")[0] not in kinds:
                raise FetchError("bad_content_type", ctype or "（缺失）")
            length = resp.getheader("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("too_large", length)
            chunks, total = [], 0
            while True:
                left = deadline - clock()
                if left <= 0:
                    raise FetchError("timeout", host)
                try:
                    if sock is not None:
                        sock.settimeout(max(0.01, left))   # 每次 recv 都受剩余墙钟约束
                    chunk = resp.read1(65536)               # 至多一次 recv，不攒满再返回
                except socket.timeout as exc:
                    raise FetchError("timeout", host) from exc
                except (OSError, http.client.HTTPException) as exc:
                    raise FetchError("connect_failed", str(exc)[:80]) from exc
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("too_large", str(total))
                chunks.append(chunk)
            if length and length.isdigit() and total != int(length):
                raise FetchError("http_error", f"正文被截断 {total}/{length}")
            return {"content_type": ctype, "data": b"".join(chunks), "host": host}
        finally:
            if conn is not None:
                conn.close()
    raise FetchError("redirect_limit", str(MAX_REDIRECTS))
