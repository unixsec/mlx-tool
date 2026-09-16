"""T2 · HTTP 服务器。**用标准库**，不引 fastapi/uvicorn。

理由：本地单用户场景下 `ThreadingHTTPServer` 完全够用，而零依赖让网关
在 venv 坏掉时**仍能起来** —— 与 T1 「venv 坏了 mlx check 也能跑」同一条哲学。
流式转发用 `urllib` 的分块读，不需要 httpx。
"""
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
    except (urllib.error.URLError, TimeoutError, ValueError):
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
    except (urllib.error.URLError, TimeoutError, OSError):
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

        def log_message(self, fmt, *args):
            """默认实现写 stderr 的非结构化行。换成结构化事件（BC-D-02）。"""
            log_event("info", "http", line=fmt % args)

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
            # type: () -> object
            """严格解析 `Content-Length`：**只接受一段裸数字**（`str.isdigit`），
            拒绝空值、负号、`+` 前缀、内部空白、任何非数字字符；重复出现时**必须
            全部一致**才放行，否则一律 `None`（非法）。

            复检 Important（同一类缺陷第六次复发）：此前逐个补丁具体的坏值——先补
            `int()` 直抛异常、再补负数、再补 `Transfer-Encoding`，每一次都只堵住
            了送到眼前的那一个畸形样本，对下一种拼法保持沉默。`empty` 用「不在
            `(None, "")` 里才 int()」的写法把空值特例成 0（当无体处理，实际字节
            没被丢弃，keep-alive 下被当成下一条请求解析）；重复的 `Content-Length`
            头此前从没处理过，`self.headers.get()` 只取第一个值（`email.message.
            Message.get` 语义），声明值与实际字节不一致时同一形状的走私就成立。
            这里改成一次性接住整个类：不是纯数字（含空、含符号、含空白）→ 非法；
            重复但取值不完全一致 → 非法；没有 `Content-Length` 头 → 0（无体）。
            """
            values = self.headers.get_all("Content-Length") or []
            if not values:
                return 0
            if len(set(values)) > 1:
                return None
            if not values[0].isdigit():
                return None
            return int(values[0])

        def _read_body(self):
            if self.headers.get("Transfer-Encoding") is not None:
                # 本实现没有分块解码器（复检 Critical 3）：放行「零字节体」等于把没读的
                # 分块数据留在 socket 里，keep-alive 下会被当成下一条请求解析（请求走私，
                # 与 413 那条同一形状）。没法安全读完就直接拒绝，不是「当空体处理」。
                self.close_connection = True
                return None, (400, {"error": {"message": "chunked transfer encoding is not supported",
                                              "type": "invalid_request"}})
            n = self._parse_content_length()
            if n is None:
                self.close_connection = True
                return None, (400, {"error": {"message": "invalid Content-Length",
                                              "type": "invalid_request"}})
            if n > max_body_bytes:
                # 声明的体没读、没丢弃 —— keep-alive 下体里的字节会被当成下一条请求解析
                # （与 `_close_if_body_unread` 同一种请求走私；这条路径没有共享它的判定，
                # 因为 `Content-Length` 本身就超限，不是「看头就知道有体没读」的情形）
                self.close_connection = True
                return None, (413, {"error": {"message": "body too large",
                                              "type": "invalid_request"}})
            if n == 0:
                return None, None
            try:
                return json.loads(self.rfile.read(n)), None
            except ValueError:
                return None, (400, {"error": {"message": "body is not valid JSON",
                                              "type": "invalid_request"}})

        def _close_if_body_unread(self):
            """GET / DELETE / OPTIONS 都不读请求体。带体（Content-Length 非 0 —— 含无法解析
            的值 —— 或出现 Transfer-Encoding）时回完就关连接：否则 keep-alive 下体里的字节
            会被当成同一连接上的下一条请求解析，请求分帧就被打乱了（请求走私）。"""
            clen = (self.headers.get("Content-Length") or "").strip()
            if self.headers.get("Transfer-Encoding") is not None or clen.lstrip("0"):
                self.close_connection = True

        def do_GET(self):        # noqa: N802
            self._close_if_body_unread()
            st, body = gw.handle("GET", self.path, dict(self.headers),
                                 peer=self.client_address[0])
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
