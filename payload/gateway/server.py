"""T2 · HTTP 服务器。**用标准库**，不引 fastapi/uvicorn。

理由：本地单用户场景下 `ThreadingHTTPServer` 完全够用，而零依赖让网关
在 venv 坏掉时**仍能起来** —— 与 T1 「venv 坏了 mlx check 也能跑」同一条哲学。
流式转发用 `urllib` 的分块读，不需要 httpx。
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mlx_server.wire import SseDecoder

from . import translate
from .app import log_event

_MAX_BODY = 8 * 1024 * 1024          # 8 MB：拒绝超大体，防内存打爆


def forward(port, path, payload, host="127.0.0.1", timeout_sec=600, headers=None):
    """把改写后的请求转给本机后端。返回 `(status, body)`；传输失败返回 `None`。"""
    req = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=json.dumps(payload).encode(), method="POST",
        headers=dict({"Content-Type": "application/json"}, **(headers or {})),
    )
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
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def make_handler(gw):
    """把 `Gateway` 包成 handler。**所有判定都在 `Gateway.handle` 里**，

    本层只做 HTTP 的搬运：读体、写头、写体。
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mlx_server-gateway"

        def log_message(self, fmt, *args):
            """默认实现写 stderr 的非结构化行。换成结构化事件（BC-D-02）。"""
            log_event("info", "http", line=fmt % args)

        def _reply(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > _MAX_BODY:
                return None, (413, {"error": {"message": "body too large",
                                              "type": "invalid_request"}})
            if n == 0:
                return None, None
            try:
                return json.loads(self.rfile.read(n)), None
            except ValueError:
                return None, (400, {"error": {"message": "body is not valid JSON",
                                              "type": "invalid_request"}})

        def do_GET(self):        # noqa: N802
            st, body = gw.handle("GET", self.path.split("?")[0], dict(self.headers))
            self._reply(st, body)

        def do_POST(self):       # noqa: N802
            body, err = self._read_body()
            if err:
                return self._reply(*err)
            path = self.path.split("?")[0]
            # **流式要走另一条转发路径。** 此前不分流：`stream: true` 的请求
            # 被原样转给后端，后端回 SSE，而 `forward` 用 json.loads 解析它 ⇒
            # ValueError ⇒ None ⇒ 客户端收到 504 upstream_timeout。
            # Cherry Studio 这类客户端**默认开流式**，等于接进去第一条消息就失败。
            if body and body.get("stream"):
                return self._stream(path, body)
            st, out = gw.handle("POST", path, dict(self.headers), body,
                                forward=forward)
            self._reply(st, out)

        def _stream(self, path, body):
            """流式转发：逐帧改写后立刻写给客户端。

            分帧交给 `wire.SseDecoder`（INV-12：SSE 分帧**只有一份实现**，
            不在这里另写一个），改写交给 `translate.rewrite_frame`
            （只在完整 Frame 上改，不对 chunk 做字节级替换）。
            鉴权与路由解析走 `gw.prepare` —— 与非流式同一处判定。
            """
            kind, *rest = gw.prepare(path, dict(self.headers), body)
            if kind == "reply":
                return self._reply(*rest[0])
            port, model_arg, short_id = rest

            dec = SseDecoder()
            state = {"started": False}

            def on_chunk(chunk):
                if not state["started"]:
                    self.send_response(200)
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
            ok = forward_stream(port, path, translate.inbound(body, model_arg), on_chunk,
                                **({"headers": hdrs} if hdrs else {}))
            if not state["started"]:
                # 一个字节都没来 ⇒ 还能正常回一个错误响应
                return self._reply(*translate.build_error(
                    504, "gateway.upstream_timeout"))
            if dec.pending():
                # 未终结的帧**不得当作正常结束**（SseDecoder.pending 的契约）
                log_event("error", "sse_truncated", model=short_id,
                          pending_bytes=len(dec.pending()))
            elif not ok:
                log_event("error", "sse_stream_aborted", model=short_id)
            return None

    return Handler


def serve(gw, host="127.0.0.1", port=8080, refresh_sec=5.0):
    """起服务器 + 后台路由刷新。**阻塞** —— 调用方是 `gw.app` 的 `__main__`。"""
    httpd = ThreadingHTTPServer((host, port), make_handler(gw))
    httpd.daemon_threads = True

    stop = threading.Event()

    def refresher():
        while not stop.wait(refresh_sec):
            try:
                gw.refresh_routes()
                if gw.nets is not None:
                    gw.nets.tick()
            except Exception as e:                      # noqa: BLE001
                # 刷新失败**不能让网关死** —— 路由表转 stale，请求仍按旧表转发
                log_event("error", "route_refresh_failed", detail=str(e)[:200])

    if gw.nets is not None:
        gw.nets.refresh()
    gw.refresh_routes()
    t = threading.Thread(target=refresher, daemon=True)
    t.start()
    log_event("info", "listening", host=host, port=port,
              routes=gw.view.route_keys())
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        httpd.server_close()
