"""T2 · HTTP 服务器。**用标准库**，不引 fastapi/uvicorn。

理由：本地单用户场景下 `ThreadingHTTPServer` 完全够用，而零依赖让网关
在 venv 坏掉时**仍能起来** —— 与 T1 「venv 坏了 mlx check 也能跑」同一条哲学。
流式转发用 `urllib` 的分块读，不需要 httpx。
"""
import http.client
import json
import os
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mlx_server.wire import SseDecoder

from . import guard, translate
from .app import log_event

DEFAULT_MAX_BODY_MB = 256          # `api.max_body_mb` 出厂值（spec D22：多图与长上下文请求体）
_HANDSHAKE_SEC = 10.0
_MAX_CL_DIGITS = 20                # `Content-Length` 最长位数，见 `_parse_content_length`


class GatewayServer(ThreadingHTTPServer):
    """双栈、每连接一线程。

    `allow_reuse_address` **保持 True**（2026-09-15 实测）：关掉它，处理过请求的网关关闭后
    立刻在同端口重绑会 `errno 48`，`mlx restart api` 必然失败。它带来的「与别人的
    `127.0.0.1:P` 共存」由 `mlx up api` 执行段的外来监听者检查拦截（spec D24）。
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler):
        if ":" in server_address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(server_address, handler)

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
    """把请求转给本机后端。返回 `(status, body)`；传输失败返回 `None`。`payload is None` ⇒ 无请求体。"""
    data = None if payload is None else json.dumps(payload).encode()
    base = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(f"http://{host}:{port}{path}", data=data, method=method,
                                 headers=dict(base, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:  # noqa: S310
            return (r.status, json.loads(r.read() or b"{}"))
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


def forward_stream(port, path, payload, on_chunk, host="127.0.0.1", timeout_sec=600,
                    headers=None):
    """流式转发。**逐块喂给 `on_chunk`，由调用方分帧改写** ——

    本函数不碰字节内容（INV-12：改写只发生在 Frame 上，不对 chunk 做替换）。
    返回 `True` 正常结束 · `False` 传输失败 · `(status, body)` 上游在任何字节之前回了 HTTP 错误。
    """
    req = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=json.dumps(dict(payload, stream=True)).encode(), method="POST",
        headers=dict({"Content-Type": "application/json", "Accept": "text/event-stream"},
                     **(headers or {})),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:  # noqa: S310
            while True:
                chunk = r.read(4096)
                if not chunk:
                    return True
                on_chunk(chunk)
    except urllib.error.HTTPError as e:
        # **必须先于 URLError 捕获**（它是 URLError 的子类）。此前被当成传输失败，
        # 后端对流式请求回的 400 到了客户端变成 504 upstream_timeout（spec §0.7-8）
        try:
            return (e.code, json.loads(e.read() or b"{}"))
        except ValueError:
            return (e.code, {})
    except (OSError, http.client.HTTPException):
        # `HTTPException` 与 `forward` 同一条理由：`BadStatusLine` 不是 `OSError`，
        # 实测在两个解释器上都从这里逃逸（读超时与连接重置本已被 `OSError` 接住）。
        return False


def make_handler(gw, max_body_bytes=DEFAULT_MAX_BODY_MB * 1024 * 1024, tls_context=None):
    """把 `Gateway` 包成 handler。**所有判定都在 `Gateway.handle` 里**，

    本层只做 HTTP 的搬运：读体、写头、写体。
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mlx_server-gateway"

        def setup(self):
            if tls_context is not None:
                # **握手在本连接的工作线程里做**：放在 accept 线程里，一个只连不发的明文连接
                # 就能让网关停止接受新连接
                self.request.settimeout(_HANDSHAKE_SEC)
                self.request = tls_context.wrap_socket(self.request, server_side=True)
                self.request.settimeout(None)
            super().setup()

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
            bad = self._framing_defect()
            if bad is None:
                return True
            # 头块不可信 ⇒ 体的边界也就不可信：这条连接不能再复用
            self.close_connection = True
            self._reply(400, {"error": {"message": bad, "type": "invalid_request"}})
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
            try:
                return json.loads(self.rfile.read(n)), None
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
            _peer_class, _path, _query, _hit, early = gw.precheck(
                self.command, self.path, dict(self.headers), self.client_address[0])
            if early:
                self.close_connection = True
                self._reply(*early)
                return False
            # **体积也要在这里判**（收尾评审 Important 4）：`Content-Length` 此刻
            # 已经知道了，而 `100 Continue` 的意思是「把体发过来吧」——
            # 对一个必然回 413 的体发出这句邀请，等于让客户端白传一遍。
            err = self._declared_body_error()
            if err:
                self._reply(*err)        # close_connection 已由 _declared_body_error 置位
                return False
            return super().handle_expect_100()

        def do_POST(self):       # noqa: N802
            _peer_class, _path, _query, _hit, early = gw.precheck(
                "POST", self.path, dict(self.headers), self.client_address[0])
            if early:
                self.close_connection = True       # 请求体没读，这条连接不能再复用
                return self._reply(*early)
            body, err = self._read_body()
            if err:
                return self._reply(*err)
            path = self.path
            # **流式要走另一条转发路径。** 此前不分流：`stream: true` 的请求
            # 被原样转给后端，后端回 SSE，而 `forward` 用 json.loads 解析它 ⇒
            # ValueError ⇒ None ⇒ 客户端收到 504 upstream_timeout。
            # Cherry Studio 这类客户端**默认开流式**，等于接进去第一条消息就失败。
            if body and body.get("stream"):
                return self._stream(path, body)
            st, out = gw.handle("POST", path, dict(self.headers), body,
                                forward=forward, peer=self.client_address[0])
            self._reply(st, out)

        def _stream(self, path, body):
            """流式转发：逐帧改写后立刻写给客户端。

            分帧交给 `wire.SseDecoder`（INV-12：SSE 分帧**只有一份实现**，
            不在这里另写一个），改写交给 `translate.rewrite_frame`
            （只在完整 Frame 上改，不对 chunk 做字节级替换）。
            鉴权与路由解析走 `gw.prepare` —— 与非流式同一处判定。
            """
            kind, *rest = gw.prepare(path, dict(self.headers), body, method="POST",
                                     peer=self.client_address[0])
            if kind == "reply":
                return self._reply(*rest[0])
            if kind == "fanout":                 # 按 id 的端点没有流式形态，按非流式处理
                st, out = gw.handle("POST", path, dict(self.headers), body, forward=forward,
                                    peer=self.client_address[0])
                return self._reply(st, out)
            port, model_arg, short_id, peer_class, upstream = rest

            dec = SseDecoder()
            state = {"started": False}

            def on_chunk(chunk):
                if not state["started"]:
                    self.send_response(200)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    # 不发 Content-Length（长度未知）⇒ 用连接关闭标界
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    state["started"] = True
                for frame in dec.feed(chunk):
                    self.wfile.write(translate.rewrite_frame(frame, short_id))
                    self.wfile.flush()

            hdrs = gw.upstream_headers(short_id)
            ok = forward_stream(port, upstream, translate.inbound(body, model_arg), on_chunk,
                                **({"headers": hdrs} if hdrs else {}))
            if isinstance(ok, tuple):
                return self._reply(*gw.upstream_reply(ok[0], ok[1], peer_class, short_id))
            if not state["started"]:
                # 一个字节都没来 ⇒ 还能正常回一个错误响应
                return self._reply(*translate.build_error(
                    504, "upstream_timeout", "backend did not answer in time"))
            if dec.pending():
                # 未终结的帧**不得当作正常结束**（SseDecoder.pending 的契约）
                log_event("error", "sse_truncated", model=short_id,
                          pending_bytes=len(dec.pending()))
            elif not ok:
                log_event("error", "sse_stream_aborted", model=short_id)
            return None

    return Handler


def serve(gw, host="127.0.0.1", port=8080, refresh_sec=5.0,
          max_body_bytes=DEFAULT_MAX_BODY_MB * 1024 * 1024, tls_context=None):
    """起服务器 + 后台路由与网段刷新。**阻塞** —— 调用方是 `gateway.__main__`。"""
    httpd = GatewayServer((host, port), make_handler(gw, max_body_bytes, tls_context))

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

    if gw.nets is not None:
        gw.nets.refresh()
    gw.refresh_routes()
    t = threading.Thread(target=refresher, daemon=True)
    t.start()
    log_event("info", "listening", host=host, port=port, tls=tls_context is not None,
              routes=gw.view.route_keys())
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        httpd.server_close()
