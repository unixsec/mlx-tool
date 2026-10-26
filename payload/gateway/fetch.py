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
import contextlib
import errno
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import quote, urljoin, urlsplit

from gateway import peers

REASONS = ("address_denied", "dns_failed", "connect_failed", "redirect_limit", "too_large",
           "bad_content_type", "timeout", "encoding_rejected", "http_error")
WALL_SEC = 30.0
MAX_REDIRECTS = 3
DNS_MAX_THREADS = 8        # 同时挂着的解析线程上限：超时放弃的线程仍占位，直到 getaddrinfo 自己返回
_DNS_SLOTS = threading.BoundedSemaphore(DNS_MAX_THREADS)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NUMERICISH = re.compile(r"^(?:0x[0-9a-fx.]*|[0-9.]+)$", re.IGNORECASE)
_URL_SAFE = "/?#[]@!$&'()*+,;=:%-._~"       # RFC 3986 保留字符 + 非保留标点 + `%`：只编码非 ASCII 与空白等


class FetchError(Exception):
    """代取失败。`reason ∈ REASONS`；`detail` 不进日志与客户端，只供测试/调试断言。"""

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


def _bare(ip):
    # type: (object) -> object
    """去掉 IPv6 的 zone（`%en0`）。3.9 起 `IPv6Address` 的相等与哈希带 `scope_id`，
    `fd00:1::5%en0 != fd00:1::5` —— 不剥掉，任何「等于某个本机地址」的判断都能被加一个后缀绕过。"""
    if ip.version == 6 and getattr(ip, "scope_id", None):
        return ipaddress.IPv6Address(str(ip).split("%")[0])
    return ip


def own_set(allowed_nets, own_addrs):
    # type: (object, object) -> set
    """本机自身地址集合：`own_addrs`（`NetView` 快照里全部接口的全部地址，含被排除接口）
    并上放行网段条目的 `addr`（快照缺 `own_addrs` 时至少不比此前更松）。比较一律剥 zone、还原内嵌 IPv4。"""
    out = set()
    raw = list(own_addrs or ()) + [n.get("addr") for n in (allowed_nets or ())]
    for a in raw:
        if not a:
            continue
        try:
            out.add(unwrap(_bare(ipaddress.ip_address(str(a).split("%")[0]))))
        except ValueError:
            continue
    return out


def bindable(ip):
    # type: (object) -> bool
    """内核认不认 `ip` 是本机地址：`bind((ip, 0))` 成功即是。不依赖网段视图 ——
    视图取不到（回退模式）或地址刚变化、还没到刷新周期时，快照里的本机地址不全，这里补上。
    只有 `EADDRNOTAVAIL` 算「不是本机」；其余错误（fd 耗尽等）按本机处理，宁拒勿放。"""
    family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.bind((str(ip), 0))
    except OSError as exc:
        return exc.errno != errno.EADDRNOTAVAIL
    return True


def target_allowed(ip, peer_class, allowed_nets, own_addrs=None):
    # type: (object, str, list, object) -> bool
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
    ip = unwrap(_bare(ip))
    if (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
            or ip.is_reserved):
        return False
    if ip.version == 4 and int(ip) == 0xFFFFFFFF:
        return False
    if ip.version == 6 and (ip.teredo is not None or ip.is_site_local):
        return False                              # fec0::/10 已弃用，各版本 Python 却都算 is_global
    if ip in own_set(allowed_nets, own_addrs):   # 本机自身地址（含公网形态、含被排除接口上的）不作代取目标
        return False
    if ip.is_global:
        return not bindable(ip)
    for n in allowed_nets or ():
        if n.get("kind") == "loopback" or n.get("scope"):
            continue                              # 回环与链路本地网段不作为代取目标
        if peers.net_matches(n, ip):
            return not bindable(ip)
    return False


def _resolve(host, port):
    # type: (str, int) -> list
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _resolve_by_deadline(resolver, host, port, remaining):
    # type: (object, str, int, float) -> list
    """解析放守护线程，按剩余墙钟 join；超时放弃线程（它只会自己结束），不阻塞调用方。"""
    box = {}
    if not _DNS_SLOTS.acquire(blocking=False):
        raise FetchError("timeout", "DNS 并发已满")   # 慢解析堆满了名额：当作超时，不再新开线程
    slots = _DNS_SLOTS

    def work():
        try:
            box["ok"] = resolver(host, port)
        except BaseException as exc:  # noqa: BLE001 —— 原样交回调用线程处理
            box["err"] = exc
        finally:
            slots.release()

    t = threading.Thread(target=work, daemon=True)
    try:
        t.start()
    except BaseException:
        slots.release()
        raise
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


def _kill(sock):
    # type: (object) -> None
    """看门狗动作：从另一线程切断连接，让阻塞中的 recv/握手立刻返回或抛错。"""
    with contextlib.suppress(OSError, ValueError):
        sock.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError, ValueError):
        sock.close()


def _ascii_length(resp):
    # type: (object) -> object
    """响应的 `Content-Length` → int | None。**只认 ASCII 数字**：`"²".isdigit()` 为真而
    `int("²")` 抛 `ValueError`（http.client 按 latin-1 解码头值，字节 0xB2 就是它）。
    其余形态与 http.client 一样当作没给长度（按关连接标界读）。"""
    length = resp.getheader("Content-Length")
    if length and length.isascii() and length.isdigit():
        return int(length)
    return None


def fetch(url, peer_class, allowed_nets, max_bytes, kinds=("image", "audio", "video"),
          resolver=None, connect=None, ssl_context=None, wall_sec=WALL_SEC, clock=time.monotonic,
          own_addrs=None):
    # type: (str, str, list, int, tuple, object, object, object, float, object, object) -> dict
    """→ `{"content_type", "data", "host"}`；失败一律 `FetchError`（`reason ∈ REASONS`）。

    **异常契约按类兜底**：管线里漏网的 `ValueError`（含 `UnicodeError`）映射为
    `http_error`，不让它越过契约穿到 server 的 `handle_error`、给客户端零字节。
    """
    try:
        return _fetch(url, peer_class, allowed_nets, max_bytes, kinds, resolver, connect,
                      ssl_context, wall_sec, clock, own_addrs)
    except ValueError as exc:
        raise FetchError("http_error", f"{type(exc).__name__}: {str(exc)[:80]}") from None


def _fetch(url, peer_class, allowed_nets, max_bytes, kinds, resolver, connect, ssl_context,
           wall_sec, clock, own_addrs):
    # type: (str, str, list, int, tuple, object, object, object, float, object, object) -> dict
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
        if not host.isascii():
            # IDN / 非 ASCII 主机名：Host 头按 latin-1 编码会抛 UnicodeEncodeError；
            # 在解析与建连之前拒绝（不替客户端做 punycode，也不发出这次 DNS 查询）
            raise FetchError("address_denied", "主机名含非 ASCII 字符")
        if explicit_port == 0:
            raise FetchError("address_denied", "端口 0")
        default_port = 443 if scheme == "https" else 80
        port = explicit_port or default_port
        ips = _host_ips(host, port, resolver, deadline - clock())
        if not all(target_allowed(ip, peer_class, allowed_nets, own_addrs) for ip in ips):
            raise FetchError("address_denied", host)
        remaining = deadline - clock()
        if remaining <= 0:
            raise FetchError("timeout", host)
        conn = None
        dog = None
        fired = threading.Event()
        try:
            try:
                conn = connect(ips[0], port, scheme, host, remaining, ssl_context)
                sock = getattr(conn, "sock", None)   # getresponse 遇 will_close 会把 conn.sock 置空，先留住
                if sock is not None:
                    # 看门狗：发请求、读响应头、读体整段都受总墙钟约束（头部逐字节滴灌时
                    # 每次 recv 都在超时内返回，单靠 socket 超时切不断）
                    def _fire(s=sock, ev=fired):
                        ev.set()
                        _kill(s)
                    dog = threading.Timer(max(0.01, deadline - clock()), _fire)
                    dog.daemon = True
                    dog.start()
                # 请求行按 ASCII 编码：路径/查询串里的非 ASCII 字符按 UTF-8 百分号编码（与浏览器一致），
                # 已有的 ASCII 字符（含 `%XX`）原样保留
                target = quote((parts.path or "/") + ("?" + parts.query if parts.query else ""),
                               safe=_URL_SAFE)
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
                if fired.is_set() or clock() >= deadline:
                    raise FetchError("timeout", host) from exc
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
            length = _ascii_length(resp)
            if length is not None and length > max_bytes:
                raise FetchError("too_large", str(length))
            chunks, total = [], 0
            while True:
                left = deadline - clock()
                if left <= 0:
                    raise FetchError("timeout", host)
                if resp.isclosed():
                    # 正文已读完（Content-Length 读满或关连接标界读到 EOF）。3.14 的 read1 读满后
                    # 会关掉 fp，以关连接标界的响应（HTTP/1.0、`Connection: close`）此时 fd 已真正
                    # 关闭 —— 再 settimeout 抛 EBADF，被误判成 connect_failed（终审 X-1）
                    break
                try:
                    if sock is not None and sock.fileno() != -1:
                        sock.settimeout(max(0.01, left))   # 每次 recv 都受剩余墙钟约束
                    chunk = resp.read1(65536)               # 至多一次 recv，不攒满再返回
                except socket.timeout as exc:
                    raise FetchError("timeout", host) from exc
                except (OSError, http.client.HTTPException) as exc:
                    if fired.is_set() or clock() >= deadline:
                        raise FetchError("timeout", host) from exc
                    raise FetchError("connect_failed", str(exc)[:80]) from exc
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("too_large", str(total))
                chunks.append(chunk)
            if fired.is_set():                      # 被看门狗切断后读到的 EOF 不是完整正文
                raise FetchError("timeout", host)
            if length is not None and total != length:
                raise FetchError("http_error", f"正文被截断 {total}/{length}")
            return {"content_type": ctype, "data": b"".join(chunks), "host": host}
        finally:
            if dog is not None:
                dog.cancel()
            if conn is not None:
                conn.close()
    raise FetchError("redirect_limit", str(MAX_REDIRECTS))
