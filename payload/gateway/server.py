"""T2 · HTTP 服务器。**用标准库**，不引 fastapi/uvicorn。

理由：本地单用户场景下 `ThreadingHTTPServer` 完全够用，而零依赖让网关
在 venv 坏掉时**仍能起来** —— 与 T1 「venv 坏了 mlx check 也能跑」同一条哲学。
所有判定在 `Gateway`（`precheck` / `prepare` / `handle`）；本层只做 HTTP 搬运、
流式直出（经 `relay`：`read1`、保活、断连即取消上游）与连接生命周期。
"""
import contextlib
import errno
import http.client
import io
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mlx_server.wire import SseDecoder

from . import aggregate, frames, guard, media, peers, relay, translate
from .app import log_event

_ABORTED = "upstream backend aborted the stream"   # 网关自己的固定文案，不带上游原文
DEFAULT_MAX_BODY_MB = 256          # `api.max_body_mb` 出厂值（spec D22：多图与长上下文请求体）
_HANDSHAKE_SEC = 10.0
_MAX_CL_DIGITS = 20                # `Content-Length` 最长位数，见 `_parse_content_length`
# 读请求体的时限（终审 B I1 + 安全复核）。只有「每次 recv 的静默上限」不够：每 59 s 发 1 字节
# 就能无限期占住已计费的在途预算。所以另按**到货字节记时间额度**：已收 `got` 字节时，读体
# 最多可以进行到 `min(BODY_MAX_SEC, BODY_MIN_SEC + got / BODY_MIN_RATE)` 秒；超出 ⇒ 408、
# 退预算、关连接。读完整个体的总时限因此 ≤ min(BODY_MAX_SEC, BODY_MIN_SEC + n / BODY_MIN_RATE)，
# 而滴灌者在 `BODY_MIN_SEC` 之后很快就被切断（它挣不到额度）。取值：
#   · BODY_MIN_RATE = 256 KiB/s（约 2 Mbps）：远低于任何 Wi-Fi / 以太网的真实上传速率；
#   · BODY_MIN_SEC = 30 s：小体与起步阶段的宽限（TCP 慢启动、客户端先序列化再发）；
#   · BODY_MAX_SEC = 1200 s：绝对上限。256 MB（出厂 max_body_mb）按最低速率需 1024 s + 30 s，仍在上限内；
#   · BODY_IDLE_SEC = 60 s：单次 recv 的静默上限（与上面取较紧者）。
BODY_IDLE_SEC = 60.0
RETRY_AFTER_SEC = 5                # 503 gateway_busy 的重试提示（spec §4.4）；手册的值由文档守卫绑定到这里
BODY_MIN_SEC = 30.0
BODY_MIN_RATE = 256 * 1024
BODY_MAX_SEC = 1200.0
LINGER_SEC = 2.0                   # 体未读即拒：回复后最多排空这么久，再关（见 `_lingering_close`）
LINGER_MAX_BYTES = 64 * 1024 * 1024
# 请求头阶段的总时限：从一条请求开始等（含 keep-alive 两条请求之间的空闲）到头块读完。
# 只设单次 recv 的超时挡不住逐字节滴灌；这里按截止时刻算，每次 recv 前取剩余时间。
# 30 s 对连接池安全：urllib3 复用前探活，httpx / undici 的空闲回收都在 5 s 以内。
HEADER_SEC = 30.0
# 连接数上限。超出的连接在 accept 之后立刻关掉，不起线程（每连接一线程，原先没有上限）。
# 每来源按 IPv4 地址 / IPv6 /64 计数（隐私地址会轮换）。`MAX_CONN` 只计**远端**连接：
# 远端（含一堆轮换的 IPv6 地址）占满它也挤不掉本机客户端，本机另由 `PEER_CONN_LOCAL` 限住。
MAX_CONN = 256
PEER_CONN_REMOTE = 32
PEER_CONN_LOCAL = 128


class _ConnSlots:
    """连接名额。一个网关的所有监听（通配 + 回环）共用一份，上限才是整个网关的上限。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.total = 0
        self.by_peer = {}

    def take(self, key, local):
        # type: (str, bool) -> bool
        limit = PEER_CONN_LOCAL if local else PEER_CONN_REMOTE
        with self.lock:
            if (not local and self.total >= MAX_CONN) or self.by_peer.get(key, 0) >= limit:
                return False
            if not local:
                self.total += 1
            self.by_peer[key] = self.by_peer.get(key, 0) + 1
            return True

    def give(self, key, local):
        with self.lock:
            if not local:
                self.total -= 1
            n = self.by_peer.get(key, 0) - 1
            if n > 0:
                self.by_peer[key] = n
            else:
                self.by_peer.pop(key, None)


class _DeadlineIO(io.RawIOBase):
    """`rfile` 的底层：`deadline` 不为 None 时，每次 recv 前把超时设成剩余时间。

    `BufferedReader.readline` 每次补缓冲只调一次 `readinto`，所以限住的是**总时长**。
    `deadline` 为 None 时一概不碰超时 —— 读体的时限逻辑（`_read_body`）自己设超时，照旧生效。
    """

    def __init__(self, sock):
        super().__init__()
        self._sock = sock
        self.deadline = None

    def readable(self):
        return True

    def readinto(self, b):
        if self.deadline is not None:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise socket.timeout("header read deadline")
            self._sock.settimeout(left)
        return self._sock.recv_into(b)


class GatewayServer(ThreadingHTTPServer):
    """双栈、每连接一线程。

    `allow_reuse_address` **保持 True**（2026-09-15 实测）：关掉它，处理过请求的网关关闭后
    立刻在同端口重绑会 `errno 48`，`mlx restart api` 必然失败。它带来的「与别人的
    `127.0.0.1:P` 共存」由 `mlx up api` 执行段的外来监听者检查拦截（spec D24）。
    """

    daemon_threads = True
    allow_reuse_address = True
    # listen 队列。`socketserver` 默认 5：accept 线程来不及取的连接排在内核队列里，满了内核
    # 直接 RST，网关一行日志都没有（2026-10-29 真机：12 路并发 4 路 ConnectionReset，而
    # max_concurrency 默认就是 8）。128 = macOS 的 kern.ipc.somaxconn，再大也会被内核截到它。
    request_queue_size = 128

    def __init__(self, server_address, handler):
        if ":" in server_address[0]:
            self.address_family = socket.AF_INET6
        self.slots = _ConnSlots()
        super().__init__(server_address, handler)

    def process_request(self, request, client_address):
        """起线程之前先占连接名额：全局与每来源都满了就直接关，不起线程、不读一个字节。"""
        key, local = peers.conn_key(client_address[0] if client_address else "")
        if not self.slots.take(key, local):
            log_event("warn", "conn_limit", peer=str(key))
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.give(key, local)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.give(*peers.conn_key(client_address[0] if client_address else ""))

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            # `::` 同时接 IPv4（对端地址显示为 ::ffff:a.b.c.d，来源分类会还原）
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def handle_error(self, request, client_address):
        """握手失败、客户端中途断开、handler 里任何未捕获异常都会到这里
        （`ThreadingMixIn.process_request_thread` 是唯一的兜底）。

        **必须带异常类型与消息**（复检 Important 4）：此前只记了对端地址，
        一条裸的 `connection_error` 分不清是对端正常掉线还是 handler 自己的 bug——
        默认实现好歹会把整段栈打到 stderr，这里换掉默认实现却把信息也换没了。
        `OSError`（含 `ConnectionResetError`/`BrokenPipeError`/`socket.timeout`）与
        `ssl.SSLError` 是对端行为造成的预期噪音，记 `warn`；其余一律 `error`。
        """
        exc_type, exc, _tb = sys.exc_info()
        name = exc_type.__name__ if exc_type else ""
        noisy = exc_type is not None and issubclass(exc_type, (OSError, ssl.SSLError))
        log_event("warn" if noisy else "error", "connection_error",
                  peer=str(client_address[0]) if client_address else "",
                  exc_type=name, detail=str(exc)[:200] if exc else "")


def tls_context(cert_path, key_path):
    # type: (str, str) -> object
    """服务端 TLS 上下文。最低 TLS 1.2（spec D15）；系统 python 的 LibreSSL 2.8.3 最高也只到 1.2。"""
    for p in (cert_path, key_path):
        if not p or not os.access(p, os.R_OK):
            raise ValueError(f"TLS 文件不可读：{p}")
    if os.stat(key_path).st_mode & 0o077:
        raise ValueError(f"私钥权限宽于 0600：{key_path}")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.load_cert_chain(cert_path, key_path)
    except (ssl.SSLError, OSError) as exc:
        raise ValueError(f"证书或私钥无法加载：{exc}") from exc
    return ctx


def forward(port, path, payload, host="127.0.0.1", timeout_sec=600, headers=None, method="POST"):
    """**非流式**转发 —— 只服务按 id 扇出的端点（GET / DELETE / cancel）；按 body 路由的端点
    一律经 `forward_stream`。返回 `(status, body)`；传输失败返回 `None`。`payload is None` ⇒ 无请求体。

    2xx 的体不是 JSON ⇒ `(502, {})`：后端回了话、只是回的不是约定的形状，这**不是**超时
    （此前 `json.loads` 的 `ValueError` 落进传输失败分支，客户端看到的是 504 upstream_timeout）。
    """
    data = None if payload is None else json.dumps(payload).encode()
    base = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(f"http://{host}:{port}{path}", data=data, method=method,
                                 headers=dict(base, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:  # noqa: S310
            status, raw = r.status, r.read()
        try:
            return (status, json.loads(raw or b"{}"))
        except ValueError:
            return (502, {})
    except urllib.error.HTTPError as e:
        try:
            return (e.code, json.loads(e.read() or b"{}"))
        except ValueError:
            return (e.code, {})
    except (OSError, http.client.HTTPException, ValueError):
        # **按类捕获，不按名字列举**（收尾评审 Critical 3）。此前写的是
        # `(urllib.error.URLError, TimeoutError, ValueError)`，两处漏：
        # · `http.client.BadStatusLine` / `IncompleteRead` 是 `HTTPException`，
        #   **不是** `OSError` —— 后端回一行垃圾状态行，异常穿到工作线程，
        #   客户端拿到的是零字节而不是本函数契约承诺的 `None` ⇒ 504。
        #   两个解释器上都实测复现。
        # · **3.9.6 上 `socket.timeout` 不是 `TimeoutError`**（那个别名 3.10 才有），
        #   它只是 `OSError` 的子类 ⇒ 600 s 读超时在下限解释器上逃逸，
        #   而在 venv 的 3.14 上被接住 —— 同一份代码两种行为。
        # `OSError` 同时盖住这两族（`URLError`、`socket.timeout`、连接重置都在其下）。
        return None


def forward_stream(port, path, payload, on_chunk, host="127.0.0.1",
                   timeout_sec=relay.READ_TIMEOUT_SEC, headers=None, client_alive=None,
                   keepalive=None, on_head=None, keepalive_sec=relay.KEEPALIVE_SEC,
                   probe_sec=relay.PROBE_SEC, on_prehead_idle=None, prehead_sec=relay.STREAM_COMMIT_SEC):
    """流式转发（经 `relay`：读取线程 `read1`、按 `probe_sec` 探测客户端、静默满 `keepalive_sec` 保活）。

    本函数不碰字节内容（INV-12：改写只发生在 Frame 上，由调用方的 `on_chunk` 分帧改写）。
    → `True` 正常结束 · `False` 上游连不上或中途断开 · `(status, body)` 上游在任何字节之前回了
    HTTP 错误 · `"client_disconnected"`（上游已关闭）。以关连接标界的响应中途断开在 HTTP 层看起来
    就是正常结束 ⇒ 这里给 `True`，调用方须用 `SseDecoder.pending()` 判截断。
    """
    res = relay.relay(
        lambda register: relay.open_upstream(port, path, payload, headers=headers, host=host,
                                             timeout_sec=timeout_sec, register=register),
        on_head or (lambda head: None), on_chunk, client_alive or (lambda: True),
        keepalive=keepalive, keepalive_sec=keepalive_sec, probe_sec=probe_sec,
        on_prehead_idle=on_prehead_idle, prehead_sec=prehead_sec)
    if res["outcome"] == "upstream_error":
        return (res["head"][1], res["head"][2])
    if res["outcome"] == "client_disconnected":
        return "client_disconnected"
    return res["outcome"] == "ok"


def make_handler(gw, max_body_bytes=DEFAULT_MAX_BODY_MB * 1024 * 1024, tls_context=None):
    """把 `Gateway` 包成 handler。**所有判定都在 `Gateway` 里**，

    本层只做 HTTP 的搬运：读体、写头、写体、流式直出与连接生命周期。
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mlx_server-gateway"

        def setup(self):
            self._linger = False          # 本连接以「体未读即拒」收尾 ⇒ 关之前先排空
            self._pre_ticket = None       # `handle_expect_100` 发 100 前预留的在途预算
            # **来源先于一切**：不在放行网段的连接不握手、不读请求头（原先要等头块读完，
            # 被拒来源也能慢慢发头占住线程）。`precheck` 里的同一检查保留作第二道。
            blocked = guard.peer_guard(gw.classify(self.client_address[0]))
            self._denied = blocked is not None
            if self._denied:
                log_event("warn", "peer_denied", addr=str(self.client_address[0]))
                super().setup()
                if tls_context is None:
                    raw = json.dumps(blocked[1]).encode()
                    with contextlib.suppress(OSError):
                        self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: application/json"
                                         b"\r\nConnection: close\r\nContent-Length: "
                                         + str(len(raw)).encode() + b"\r\n\r\n" + raw)
                    self._linger = True
                return
            if tls_context is not None:
                # **握手在本连接的工作线程里做**：放在 accept 线程里，一个只连不发的明文连接
                # 就能让网关停止接受新连接
                self.request.settimeout(_HANDSHAKE_SEC)
                self.request = tls_context.wrap_socket(self.request, server_side=True)
                self.request.settimeout(None)
            super().setup()
            self._hdr = _DeadlineIO(self.connection)
            self.rfile.close()            # 基类那份 makefile 持有 socket 引用，不关它 fd 要等 GC
            self.rfile = io.BufferedReader(self._hdr, self.rbufsize if self.rbufsize > 0 else 8192)

        def handle_one_request(self):
            # 每条请求（含 keep-alive 的空闲等待）开始时布防，头块解析完撤防（`_headers_done`）
            self._hdr.deadline = time.monotonic() + HEADER_SEC
            try:
                super().handle_one_request()
            finally:
                self._headers_done()
                if getattr(self.connection, "_mlx_poisoned", False):
                    self.close_connection = True     # TLS 断连探测读走过客户端的字节，见 relay._tls_alive

        def _headers_done(self):
            if self._hdr.deadline is not None:
                self._hdr.deadline = None
                with contextlib.suppress(OSError, ValueError):
                    self.connection.settimeout(None)

        def handle(self):
            if self._denied:
                with contextlib.suppress(OSError):
                    self.wfile.flush()
                if self._linger:
                    self._lingering_close()
                return
            try:
                super().handle()
            finally:
                if self._pre_ticket is not None:     # 预留了却没走到 do_POST（解析失败、断开）
                    self._pre_ticket.close()
                    self._pre_ticket = None
                if self._linger:
                    self._lingering_close()

        def _lingering_close(self):
            """**体没读就回了最终状态**时的收尾：先 `shutdown(SHUT_WR)` 把回复连同 FIN 发出去，
            再有界地读掉客户端还在发的体，然后才关（终审 B I2）。

            直接 close 时接收缓冲里还有未读字节，内核回 RST —— 还在 `send` 体的客户端
            （Python `BrokenPipeError`、curl `Connection reset`）读不到 503 与 `Retry-After`，
            最该按 `Retry-After` 退避的大体请求偏偏拿不到它。上限 `LINGER_SEC` / `LINGER_MAX_BYTES`。

            不构成新的放大面：每条被拒连接至多占一个工作线程 `LINGER_SEC`（2 s），排空的字节
            按 64 KB 一块读出即丢、不缓冲、不计入在途预算；而未鉴权客户端本来就能用慢速请求头
            无限期占住一个线程（`ThreadingHTTPServer` 线程数无上限，已知遗留），这里的上界更紧。
            """
            sock = self.connection
            try:
                self.wfile.flush()
                sock.shutdown(socket.SHUT_WR)
            except (OSError, ValueError):
                return
            deadline = time.monotonic() + LINGER_SEC
            got = 0
            while got < LINGER_MAX_BYTES:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                try:
                    sock.settimeout(left)
                    chunk = sock.recv(65536)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                got += len(chunk)

        def _reject_unread(self, status, body):
            """体还在 socket 里时回最终状态：连接不可复用，且以 lingering close 收尾。"""
            self.close_connection = True
            self._linger = True
            return self._reply(status, body)

        def _framing_defect(self):
            # type: () -> object
            """头块的**分帧不变式**：解析出来的头必须与线上的字节一致。→ 理由 | `None`。

            请求走私的第九个成员（收尾评审 Critical 2）。前八次修复各自堵住
            `Content-Length` 的**一种拼法**；这一条不再看那个头长什么样，
            改判「这次解析本身可不可信」——判不可信就整条拒掉。

            两条判据，都在分帧层，都不枚举拼法：

            ① `self.headers.defects` 非空。头行不匹配 `email.feedparser.headerRE`
               时（`X-Bad`、`Content-Length : 55` 这类），解析器抛
               `MissingHeaderBodySeparatorDefect` 并**丢掉其后的全部头**，
               而 `http.client` 已经把那些字节从 socket 里读走了。
               `Content-Length` 随之消失 ⇒ `_parse_content_length` 判「无体」⇒
               声明的体留在 socket 里被当成下一条请求解析。实测：一条请求进、
               两条响应出。

            ② 任何头值里含换行（obs-fold 续行）。**实测订正裁决的前提**：
               `X-Fold: a\\r\\n Content-Length: 35` 这一种 `defects` 是**空的**
               （续行是 RFC 7230 的合法语法，解析器不记缺陷），
               `Content-Length` 照样被并进上一个头的值里而消失，
               两个解释器上同样一条请求进、两条响应出 —— 只查 ① 拦不住它。
               RFC 9110 §5.2 已弃用 obs-fold 并要求非代理接收方拒绝，
               所以这里按「头值不得跨行」判，仍是分帧层的性质，不是某个头的拼法。
            """
            if self.headers.defects:
                return "malformed header block"
            for value in self.headers.values():
                if "\n" in value or "\r" in value:
                    return "obsolete line folding in header block"
            return None

        def parse_request(self):
            """解析请求行与头块，**随即校验分帧不变式**（见 `_framing_defect`）。

            落点选在这里而不是各 `do_*`：它是 `handle_one_request` 上游唯一
            「头已解析、体尚未被任何人碰过」的点，四个方法共用，加一个方法
            不会漏掉它。返回 `False` ⇒ `handle_one_request` 直接收工。
            """
            if not super().parse_request():
                return False
            self._headers_done()
            bad = self._framing_defect()
            if bad is None:
                return True
            # 头块不可信 ⇒ 体的边界也就不可信：这条连接不能再复用
            self._reject_unread(400, {"error": {"message": bad, "type": "invalid_request"}})
            return False

        def log_message(self, fmt, *args):
            """默认实现写 stderr 的非结构化行。换成结构化事件（BC-D-02）。

            带 `peer`（spec §9.2 acceptance #9 要求 `http` 事件里能看到对端地址）——
            基类的 `address_string()` 前缀被这个覆盖丢掉了，之前这条事件里没有对端。
            """
            log_event("info", "http", line=fmt % args, peer=self.client_address[0])

        def _reply(self, status, body, preflight=False):
            raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            # CORS：普通响应只带 Allow-Origin；**不发 Allow-Credentials**（令牌走头，不走 cookie）
            self.send_header("Access-Control-Allow-Origin", "*")
            if preflight:
                self.send_header("Access-Control-Allow-Methods", guard.CORS_ALLOW_METHODS)
                self.send_header("Access-Control-Allow-Headers", guard.CORS_ALLOW_HEADERS)
                self.send_header("Access-Control-Max-Age", guard.CORS_MAX_AGE)
            if status == 503 and isinstance(body, dict) and isinstance(body.get("error"), dict) \
                    and body["error"].get("type") == "gateway_busy":
                # `MediaError.reply()` 不带响应头：在途预算满时由这里补上重试提示（spec §4.4）
                self.send_header("Retry-After", str(RETRY_AFTER_SEC))
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw:
                self.wfile.write(raw)

        def _parse_content_length(self):
            # type: () -> tuple
            """**整个 handler 里关于「`Content-Length` 是什么」的唯一判据**——
            `_read_body`（POST 读体）与 `_close_if_body_unread`（GET/DELETE/OPTIONS
            判断要不要关连接）都调它，不再各自维护一份对这个头的理解。

            → `(n, 错误 | None)`。`错误` 非 None 时 `n` 无意义，调用方必须让连接
            不可复用——声明了但没被读走/丢弃的字节，留给下一条 keep-alive 请求解析
            就是请求走私。

            复检 Important（同一类缺陷第六次复发，round 2 只收窄到 `_read_body`
            一个调用点，第七次复发就发生在 `_close_if_body_unread` 那边）：此前
            逐个补丁具体的坏值——先补 `int()` 直抛异常、再补负数、再补
            `Transfer-Encoding`——每一次都只堵住了送到眼前的那一个畸形样本，对
            下一种拼法、对另一个调用点都保持沉默。这里改成一次性接住整个类，且
            只有这一个函数认得这个头：不是「≤ 20 位的纯 ASCII 数字」（`[0-9]{1,20}`
            ——拒绝空值、负号、`+` 前缀、内部空白、任何非数字字符、`int()` 吃不下的
            Unicode 数字字符，以及长到 `int()` 拒绝解析 / 解析起来是二次方开销的
            数字串）→ 非法；重复出现但取值不完全一致 → 非法；没有该头 → `(0, None)`
            （无体）。

            `int()` 另外还兜一层 `try/except ValueError`（round 5）：**故意与上面的门
            重复**。「门放行的值 `int()` 一定吃得下」这个说法已经错过两次（`'²'`、
            超长数字串），所以这段代码不再依赖它恰好为真——真值判断归门，兜底归
            `except`，两者都指向同一个 `(None, 错误)` 返回。
            """
            values = self.headers.get_all("Content-Length") or []
            if not values:
                return 0, None
            if len(set(values)) > 1:
                return None, "Content-Length values disagree"
            value = values[0]
            # **≤ 20 位的 ASCII 数字**，等价于 `[0-9]{1,20}`。两条都不能少：
            # · 只查 `str.isdigit()` 不够 —— 它对 U+00B2（上标 2）这类 Unicode 数字字符
            #   也为真，而 `int()` 解析不了（复检 Important，round 4）。
            # · 只查「是 ASCII 数字」还不够 —— 长度无界的数字串照样是 `int()` 吃不下的：
            #   CPython 自 3.11（及 3.9.14+/3.10.7+ 的 CVE-2020-10735 回移）对 int(str)
            #   设了 4300 位上限，`int("9"*5000)` 抛同一个 `ValueError`；而在**没有**该
            #   上限的解释器（本仓库的下限 3.9.6 正是）上，它变成这条上限本来要防的
            #   二次方 CPU 开销——单条 64 KB 头行约 65000 位，实测一次 int() 就 14 ms
            #   （复检 Important，round 5）。两族解释器各有一种坏结局，长度上限同时关掉。
            # 这两种坏值都在 `do_GET`/`do_OPTIONS` 上**先于鉴权**可达（`_close_if_body_unread`
            # 在 `gw.handle` 之前），未鉴权的客户端一条超长头就能触发。
            # 20 位覆盖到 10^20 字节，远超任何真实请求体（uint64 上限也才 20 位）。
            if len(value) > _MAX_CL_DIGITS or not (value.isascii() and value.isdigit()):
                return None, "Content-Length is not a bare ASCII digit string within 20 digits"
            try:
                return int(value), None
            except ValueError:
                # 上面的门已经排除了这条路径——**仍然兜住**：见 docstring，这个
                # 「门与 int() 之间不留缝」的说法已经错过两次，不再拿它当前提。
                return None, "Content-Length is not parseable as an integer"

        def _declared_body_error(self):
            # type: () -> object
            """**光看请求头就能定下的拒绝理由** → `(状态, 体)` | `None`。

            `_read_body`（读体前）与 `handle_expect_100`（发 100 Continue 前）
            共用这一份判据 —— 后者此前只跑 `precheck`，对体积一无所知，
            于是给一个**已经判定要拒**的体发了 100 Continue，客户端把 2 MB
            传上来再收 413（RFC 9110 §10.1.1 要求直接给最终状态，
            而这也正好架空了本计划新加的 `api.max_body_mb` 闸门）。
            """
            if self.headers.get("Transfer-Encoding") is not None:
                # 本实现没有分块解码器（复检 Critical 3）：放行「零字节体」等于把没读的
                # 分块数据留在 socket 里，keep-alive 下会被当成下一条请求解析（请求走私，
                # 与 413 那条同一形状）。没法安全读完就直接拒绝，不是「当空体处理」。
                self.close_connection = True
                return (400, {"error": {"message": "chunked transfer encoding is not supported",
                                        "type": "invalid_request"}})
            n, err = self._parse_content_length()
            if err:
                self.close_connection = True
                return (400, {"error": {"message": "invalid Content-Length",
                                        "type": "invalid_request"}})
            if n > max_body_bytes:
                # 声明的体没读、没丢弃 —— keep-alive 下体里的字节会被当成下一条请求解析
                # （与 `_close_if_body_unread` 同一种请求走私；这条路径没有共享它的判定，
                # 因为 `Content-Length` 本身就超限，不是「看头就知道有体没读」的情形）
                self.close_connection = True
                return (413, {"error": {"message": "body too large",
                                        "type": "invalid_request"}})
            return None

        def _read_body(self):
            err = self._declared_body_error()
            if err:
                return None, err
            n, _err = self._parse_content_length()      # 上面已判过，这里必然合法
            if n == 0:
                return None, None
            # **读体有时限**（终审 B I1）：声明大体后不发或滴灌（合盖、断 Wi-Fi 不发 FIN，或恶意）
            # 的连接此前永远阻塞在这里，已计入的在途预算随之永久占住 —— 8 条就能让整个网关
            # 一直 503。按到货字节挣时间额度（见模块顶部 BODY_* 常量），分块读、每块前按剩余
            # 时间 settimeout；超时 ⇒ 408 并关连接，预算由 do_POST 的 finally 退还。
            chunks, got, start = [], 0, time.monotonic()
            try:
                while got < n:
                    budget_sec = min(BODY_MAX_SEC, BODY_MIN_SEC + got / BODY_MIN_RATE)
                    left = start + budget_sec - time.monotonic()
                    if left <= 0:
                        raise socket.timeout("body read deadline")
                    self.connection.settimeout(min(BODY_IDLE_SEC, left))
                    chunk = self.rfile.read1(min(65536, n - got))
                    if not chunk:
                        break                       # 客户端提前关闭：体不完整，下面按非法 JSON 处理
                    chunks.append(chunk)
                    got += len(chunk)
            except socket.timeout:
                log_event("warn", "body_read_timeout", peer=str(self.client_address[0]),
                          declared=n, received=got)
                k, local = peers.conn_key(self.client_address[0])
                if not local:
                    gw.budget.strike(k)          # 声明大体不发的来源：份额暂时减半
                self.close_connection = True
                return None, (408, {"error": {"message": "request body not received in time",
                                              "type": "invalid_request"}})
            finally:
                with contextlib.suppress(OSError, ValueError):
                    self.connection.settimeout(None)
            raw = b"".join(chunks)
            try:
                return json.loads(raw), None
            except ValueError:
                return None, (400, {"error": {"message": "body is not valid JSON",
                                              "type": "invalid_request"}})

        def _close_if_body_unread(self):
            """GET / DELETE / OPTIONS 都不读请求体。带体（`Content-Length` 非零、
            解析失败/冲突，或出现 `Transfer-Encoding`）时回完就关连接：否则
            keep-alive 下体里的字节会被当成同一连接上的下一条请求解析，请求
            分帧就被打乱了（请求走私）。

            复检 Important 第七次复发：这里此前自己用 `.strip()`/`.lstrip("0")`
            土办法猜"有没有体"——`self.headers.get("Content-Length")` 只取重复头的
            第一个值，空值被 `.lstrip("0")` 判成假（当无体）。改成只关连接、不回
            400（GET/DELETE/OPTIONS 本来就要往下走各自的正常响应，插一个新的错误
            分支要动三处调用点；这个方法唯一的职责就是"关不关连接"，加一条判据
            分支比改调用点更干净——见 fix round 3 报告）。
            """
            n, err = self._parse_content_length()
            if err or n or self.headers.get("Transfer-Encoding") is not None:
                self.close_connection = True

        def do_GET(self):        # noqa: N802
            self._close_if_body_unread()
            # **`forward` 必须传**（收尾评审 Critical 1）：`/v1/responses/{id}` 与
            # `/v1/responses/{id}/input_items` 是扇出端点，`gw.handle` 在
            # `forward is None` 时直接回 503 `no_upstream` ⇒ 这两个白名单端点
            # 恒 503。`do_DELETE` 与流式的扇出分支一直传着它，只有这里漏了。
            st, body = gw.handle("GET", self.path, dict(self.headers), None,
                                 forward=forward, peer=self.client_address[0])
            self._reply(st, body)

        def do_OPTIONS(self):    # noqa: N802
            self._close_if_body_unread()
            st, body = gw.handle("OPTIONS", self.path, dict(self.headers),
                                 peer=self.client_address[0])
            self._reply(st, body, preflight=(st == 204))

        def do_DELETE(self):     # noqa: N802
            self._close_if_body_unread()
            st, body = gw.handle("DELETE", self.path, dict(self.headers), None,
                                 forward=forward, peer=self.client_address[0])
            self._reply(st, body)

        def handle_expect_100(self):
            """默认实现在这里直接回 100 Continue，等于邀请未鉴权 / 被拒来源的客户端把体发过来
            （复检 Minor 6）。先跑 `precheck`：不通过就直接回绝，不发 100 Continue。"""
            # 本方法在 `super().parse_request()` **内部**被调用，早于 `parse_request` 里的
            # 分帧不变式：先在这里验，免得在不可信的头上跑 precheck、发出 100（终审 B m2）
            self._headers_done()
            bad = self._framing_defect()
            if bad is not None:
                self._reject_unread(400, {"error": {"message": bad, "type": "invalid_request"}})
                return False
            _peer_class, _path, _query, _hit, early = gw.precheck(
                self.command, self.path, dict(self.headers), self.client_address[0])
            if early:
                self._reject_unread(*early)
                return False
            # **体积也要在这里判**（收尾评审 Important 4）：`Content-Length` 此刻
            # 已经知道了，而 `100 Continue` 的意思是「把体发过来吧」——
            # 对一个必然回 413 的体发出这句邀请，等于让客户端白传一遍。
            err = self._declared_body_error()
            if err:
                self._reject_unread(*err)
                return False
            if self.command == "POST":
                # **先预留在途预算、再发 100**（终审 B I2）：预算满时直接给 503 + Retry-After，
                # 不邀请客户端把体传上来再被拒。预留交给 do_POST，不重复计费
                n, _err = self._parse_content_length()
                ticket = gw.new_ticket(self.client_address[0])
                try:
                    ticket.charge(n)
                except media.MediaError as exc:
                    ticket.close()
                    self._reject_unread(*exc.reply())
                    return False
                self._pre_ticket = ticket
            return super().handle_expect_100()

        def do_POST(self):       # noqa: N802
            peer = self.client_address[0]
            _peer_class, _path, _query, _hit, early = gw.precheck(
                "POST", self.path, dict(self.headers), peer)
            if early:
                return self._reject_unread(*early)  # 请求体没读，这条连接不能再复用
            # 裁决 M3：先按头判 Transfer-Encoding / Content-Length / 413，再把声明的体积计入在途预算
            err = self._declared_body_error()
            if err:
                return self._reject_unread(*err)
            n, _err = self._parse_content_length()
            # Ticket 覆盖请求的**全部**退出路径：入站管线已落盘/计费后才抛错、中继、聚合、客户端断开
            ticket, self._pre_ticket = self._pre_ticket, None
            try:
                if ticket is None:
                    ticket = gw.new_ticket(self.client_address[0])
                    try:
                        ticket.charge(n)
                    except media.MediaError as exc:
                        return self._reject_unread(*exc.reply())   # 体没读
                body, err = self._read_body()
                if err:
                    return self._reply(*err)
                if body is not None and not isinstance(body, dict):
                    # 下游一律按对象取 `.model` / `.stream`：数组、字符串体在这里拒，不让它打穿工作线程
                    return self._reply(400, {"error": {"message": "body must be a JSON object",
                                                       "type": "invalid_request"}})
                # **流式要走另一条直出路径**（Cherry Studio 这类客户端默认开流式）
                if body and body.get("stream"):
                    return self._stream(body, ticket)
                out = gw.handle("POST", self.path, dict(self.headers), body, forward=forward,
                                peer=peer, stream=self._upstream, client_alive=self._alive,
                                ticket=ticket)
                # 上游已结束 ⇒ 后端不再读临时文件：**先删再回**，客户端拿到响应时目录已不在
                ticket.close()
                if out is None:                    # 客户端已断开，没有人可回
                    self.close_connection = True
                    return None
                return self._reply(*out)
            finally:
                ticket.close()

        def _alive(self):
            return relay.client_alive(self.connection)

        def _upstream(self, port, path, payload, on_chunk, headers=None, client_alive=None):
            return forward_stream(port, path, payload, on_chunk, headers=headers,
                                  client_alive=client_alive, keepalive_sec=gw.keepalive_sec,
                                  probe_sec=gw.probe_sec)

        def _stream_headers(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            # 不发 Content-Length（长度未知）⇒ 用连接关闭标界
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def _stream(self, body, ticket):
            """流式客户端：逐帧改写后立刻写出，上游静默时保活，客户端断开即关上游。

            分帧交给 `wire.SseDecoder`（INV-12：SSE 分帧**只有一份实现**），改写交给
            `frames.rewrite`（只在完整 Frame 上改，**显式传 peer_class**：远端来源的帧内错误净化）。
            鉴权与路由解析走 `gw.prepare` —— 与非流式同一处判定。**收到上游 200 头之后**才下发
            响应头；此前上游的任何失败都还能回一个正常的错误响应。
            """
            peer = self.client_address[0]
            kind, *rest = gw.prepare(self.path, dict(self.headers), body, method="POST", peer=peer)
            if kind == "reply":
                return self._reply(*rest[0])
            if kind == "fanout":                 # 按 id 的端点没有流式形态，按非流式处理
                return self._reply(*gw.handle("POST", self.path, dict(self.headers), body,
                                              forward=forward, peer=peer))
            port, model_arg, short_id, peer_class, upstream, family, snap = rest
            try:
                up_body, meta = translate.build_upstream(body, family, snap, peer_class, model_arg,
                                                         gw.media_context(ticket))
            except (media.MediaError, translate.InboundError) as exc:
                return self._reply(*exc.reply())
            # 网关为聚合强制开了 usage；客户端自己没要 ⇒ 丢掉 usage 帧
            drop_usage = family in ("chat", "completions") and not meta["client_usage"]
            dec = SseDecoder()
            state = {"started": False, "terminal": False, "errored": False, "seq": -1}

            def commit():
                if not state["started"]:
                    self._stream_headers()
                    state["started"] = True

            def on_head(head):
                if head[0] == "stream":
                    commit()

            def on_chunk(chunk):
                for frame in dec.feed(chunk):
                    if not state["terminal"] and aggregate.is_terminal(family, frame):
                        state["terminal"] = True
                    out = frames.rewrite(frame, short_id, peer_class, drop_usage)
                    if out:
                        frames.track(out, state)
                        self.wfile.write(out)
                self.wfile.flush()

            def fail(message, err_type="upstream_error", code="upstream_aborted"):
                """响应头已发之后出错：补一个协议内的错误事件，不发 `[DONE]`。上游已发过错误帧就不重复。"""
                if state["errored"] or state["terminal"]:
                    return
                with contextlib.suppress(OSError):
                    self.wfile.write(frames.error_event(family, message, err_type, code, state["seq"] + 1))
                    self.wfile.flush()

            def keepalive():
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()

            hdrs = gw.upstream_headers(short_id, snap)
            res = forward_stream(port, upstream, up_body, on_chunk, headers=hdrs or None,
                                 client_alive=self._alive, keepalive=keepalive, on_head=on_head,
                                 keepalive_sec=gw.keepalive_sec, probe_sec=gw.probe_sec,
                                 on_prehead_idle=commit, prehead_sec=gw.stream_commit_sec)
            ticket.close()                       # 可重复调用；do_POST 的 finally 兜底
            if isinstance(res, tuple):
                st, err_body = gw.upstream_reply(res[0], res[1], peer_class, short_id)
                if not state["started"]:
                    return self._reply(st, err_body)
                # 排队超过阈值后才回的错：状态码已经是 200，只能以错误事件告知
                e = (err_body or {}).get("error") or {}
                fail(e.get("message") or "backend returned an error", e.get("type") or "upstream_error",
                     e.get("code") or "upstream_error")
                return None
            if res == "client_disconnected":
                log_event("info", "client_disconnected", model=short_id)
                self.close_connection = True
                return None
            if not state["started"]:
                # 一个字节都没来 ⇒ 还能正常回一个错误响应（M4：连不上上游是 502，不是 504）
                log_event("error", "upstream_aborted", model=short_id)
                return self._reply(*translate.build_error(
                    502, "upstream_error", "backend connection failed"))
            if dec.pending().strip():
                # 未终结的帧**不得当作正常结束**（SseDecoder.pending 的契约）：以关连接标界的
                # 响应中途断开，relay 在 HTTP 层判不出来，只能在这里发现
                log_event("error", "sse_truncated", model=short_id,
                          pending_bytes=len(dec.pending()))
                fail(_ABORTED)
            elif res is False:
                log_event("error", "upstream_aborted", model=short_id)
                fail(_ABORTED)
            elif not state["terminal"]:
                # 以关连接标界的上游（mlx_lm 是 HTTP/1.0）断在**帧边界**：relay 判 ok、没有半帧，
                # 只有「没见过终止帧」能说明后端没把流写完（崩溃 / OOM 最常见的形态）
                log_event("error", "sse_truncated", model=short_id, pending_bytes=0)
                fail(_ABORTED)
            return None

    return Handler


def bind_servers(host, port, handler):
    # type: (str, int, object) -> list
    """绑定监听地址。**绑通配地址时先占回环**：网关只绑 `0.0.0.0` / `::` 时，同用户的进程能再绑
    `127.0.0.1:P`（BSD 允许更具体的地址与通配共存），此后本机客户端的连接连同令牌全落到它手里
    （2026-10-31 实测，与网关自己的 `allow_reuse_address` 无关）。先绑回环、再绑通配，
    后来者绑回环一律 EADDRINUSE。返回的第一个是通配（或唯一）那个。"""
    if host not in ("0.0.0.0", "::"):
        return [GatewayServer((host, port), handler)]
    loops = [GatewayServer(("127.0.0.1", port), handler)]
    port = loops[0].server_address[1]
    if socket.has_ipv6:
        try:
            loops.append(GatewayServer(("::1", port), handler))
        except OSError as e:
            # 只容忍「本机没有 IPv6 回环」；`::1:P` 已被别人占着（EADDRINUSE）正是要防的情形，
            # 照常起来等于把 `localhost` 的流量留给那个占用者
            if e.errno not in (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT):
                for s in loops:
                    s.server_close()
                raise
            log_event("warn", "loopback6_unavailable", detail=str(e)[:200])
    try:
        main = GatewayServer((host, port), handler)
    except OSError:
        for s in loops:
            s.server_close()
        raise
    for s in loops:
        s.slots = main.slots
    return [main] + loops


def serve(gw, host="127.0.0.1", port=8080, refresh_sec=5.0,
          max_body_bytes=DEFAULT_MAX_BODY_MB * 1024 * 1024, tls_context=None):
    """起服务器 + 后台路由、网段与临时媒体刷新。**阻塞** —— 调用方是 `gateway.__main__`。"""
    servers = bind_servers(host, port, make_handler(gw, max_body_bytes, tls_context))
    httpd = servers[0]

    stop = threading.Event()

    def refresher():
        while not stop.wait(refresh_sec):
            # 两次调用**分开 try**：`store.load` 持续失败不该饿死网段重算，
            # `tick()` 失败也不该被记成 `route_refresh_failed`（两者的失败原因、
            # 影响面都不一样，合在一个 except 里，日志会分不清是哪一半坏了）
            try:
                gw.refresh_routes()
            except Exception as e:                      # noqa: BLE001
                # 刷新失败**不能让网关死** —— 路由表转 stale，请求仍按旧表转发
                log_event("error", "route_refresh_failed", detail=str(e)[:200])
            try:
                if gw.nets is not None:
                    gw.nets.tick()
            except Exception as e:                      # noqa: BLE001
                log_event("error", "nets_refresh_failed", detail=str(e)[:200])
            try:
                gw.media_store.sweep()                  # 超过 1 h 的遗留临时媒体目录
            except Exception as e:                      # noqa: BLE001
                log_event("error", "media_sweep_failed", detail=str(e)[:200])

    if gw.nets is not None:
        gw.nets.refresh()
    try:
        gw.media_store.sweep()                          # 上次崩溃留下的临时媒体
    except Exception as e:                              # noqa: BLE001
        # 与刷新线程里同一调用同等对待：媒体根目录不可读不该让网关起不来
        log_event("error", "media_sweep_failed", detail=str(e)[:200])
    gw.refresh_routes()
    t = threading.Thread(target=refresher, daemon=True)
    t.start()
    log_event("info", "listening", host=host, port=port, tls=tls_context is not None,
              routes=gw.view.route_keys())
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        for extra in servers[1:]:
            extra.shutdown()
        for s in servers:
            s.server_close()
