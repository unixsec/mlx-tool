"""T2 · 上游流式中继（spec §4.6、D22）。

读上游放在**读取线程**里（建连、等响应头、`read1` 都在那里阻塞），主循环按 `probe_sec` 从队列取：
取不到数据时探测客户端 socket，断开就**关闭上游连接** —— 后端的流式生成器在 `finally` 里关闭 token
迭代器，生成随即取消（mlx_vlm 日志 `Generation cancelled`）；上游静默满 `keepalive_sec` 就发一行
SSE 注释保活（mlx_vlm 排队与 prefill 期间不发任何字节，客户端会按自己的读超时断开）。
等上游响应头期间同样探测断连：mlx_lm 排队时连响应头都还没发。

**不在读取线程里 socket 超时轮询**：`http.client` 在分块读到一半时超时，内部状态会乱。

两种断流由不同的地方发现：分块传输（mlx_vlm 的 StreamingResponse）中途断开 ⇒ `read1` 抛
`IncompleteRead` ⇒ 这里判 `upstream_aborted`；以关连接标记结束的响应（无长度、不分块）中途断开
在 HTTP 层看起来就是正常结束 ⇒ 这里判 `ok`，由调用方的 `SseDecoder.pending()` 发现半截帧、记 `sse_truncated`。
"""
import contextlib
import http.client
import json
import queue
import select
import socket
import ssl
import threading
import time

# 上游排队超过它还没回响应头 ⇒ 先给客户端发响应头再保活（调用方提供 `on_prehead_idle` 时）。
# 之内出的错仍能回真实状态码；之后只能以协议内错误事件告知
STREAM_COMMIT_SEC = 15.0
KEEPALIVE_SEC = 15.0
PROBE_SEC = 1.0
READ_TIMEOUT_SEC = 600.0


def open_upstream(port, path, payload, headers=None, host="127.0.0.1", timeout_sec=READ_TIMEOUT_SEC,
                  register=None):
    # type: (int, str, dict, object, str, float, object) -> tuple
    """→ `("stream", conn, resp)` / `("error", status, body)`（发出任何字节前的 HTTP 错误）/ `("failed", detail)`。

    `timeout_sec` 是**单次读等待**（spec：600 s，覆盖排队、prefill 与 token 间隔），没有总时长上限。
    `register(conn)` 在连接建立后、发请求前调用：让调用方在响应头到达前就能关掉这条连接
    （mlx_lm 排队时可能几分钟不回响应头，客户端此时走了也要能取消）。
    """
    conn = http.client.HTTPConnection(host, port, timeout=timeout_sec)
    try:
        conn.connect()
        if register is not None:
            register(conn)
        conn.request("POST", path, body=json.dumps(payload).encode(),
                     headers=dict({"Content-Type": "application/json",
                                   "Accept": "text/event-stream"}, **(headers or {})))
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException, ValueError, TypeError) as exc:
        conn.close()                     # ValueError：头值含换行；TypeError：载荷不可序列化
        return ("failed", str(exc)[:200])
    if resp.status >= 400:
        try:
            raw = resp.read()
        except (OSError, http.client.HTTPException):
            raw = b""
        conn.close()
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"error": {"message": raw[:500].decode("utf-8", "replace")}}
        return ("error", resp.status, body)
    return ("stream", conn, resp)


def client_alive(sock):
    # type: (object) -> bool
    """客户端还连着吗。明文：**不读走数据**（`MSG_PEEK`）；可读且读到 0 字节 ⇒ 对端已关闭。"""
    if isinstance(sock, ssl.SSLSocket):
        return _tls_alive(sock)
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return True
        return bool(sock.recv(1, socket.MSG_PEEK))
    except OSError:
        return False


def _tls_alive(sock):
    # type: (object) -> bool
    """TLS 连接的断连探测。`SSLSocket.recv` 不接受 `MSG_PEEK`，分三步：

    1. TLS 层已有解密好的数据，或底层不可读 ⇒ 在线；
    2. 绕过 TLS 直接 peek 底层 TCP：读到 0 字节（FIN）或出错（RST）⇒ 离线；
    3. 底层有字节：可能是 close_notify（TLS 1.3 下它也伪装成应用数据记录），也可能是
       客户端流水线发来的下一条请求 —— 只能非阻塞地真读一次。读到 EOF / 关闭告警 ⇒ 离线；
       读到数据 ⇒ 在线，但那个字节已被读走，**这条连接不能再复用**（`_mlx_poisoned`，
       handler 据此关连接）。数据还不够一条记录 ⇒ 在线。
    """
    try:
        if sock.pending():
            return True
        readable, _, _ = select.select([sock], [], [], 0)
        if not readable:
            return True
        if not socket.socket.recv(sock, 1, socket.MSG_PEEK):
            return False
    except OSError:
        return False
    timeout = sock.gettimeout()
    try:
        sock.setblocking(False)
        try:
            data = sock.recv(1)
        except ssl.SSLWantReadError:
            return True
        except (ssl.SSLError, OSError):
            return False
        if not data:
            return False
        sock._mlx_poisoned = True
        return True
    finally:
        with contextlib.suppress(OSError):
            sock.settimeout(timeout)


def _close(state):
    # type: (dict) -> None
    """**真正关掉**上游连接，响应头到达前后都行（`state["conn"]` 由 `open_upstream` 提前登记）。
    只 `conn.close()` 不够：响应对象经 `makefile` 仍持有 socket 引用，fd 不会关闭；macOS 上发往已
    `SHUT_RD` 的数据被静默丢弃、不回 RST —— 后端的写一直成功，永远走不到取消生成的 `finally`。
    先 shutdown 唤醒阻塞中的读，再关响应对象释放引用，最后关连接。"""
    head = state["head"]
    conn = state["conn"]
    resp = None
    if head and head[0] == "stream":
        conn, resp = head[1], head[2]
    if conn is None:
        return
    # 取登记时的 socket：以关连接标记结束的响应在 getresponse() 里就把 `conn.sock` 置 None，
    # 此时只有响应对象还握着 socket，不 shutdown 的话 `resp.close()` 会等阻塞中的读线程
    sock = state["sock"] or conn.sock
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_RDWR)
    if resp is not None:
        with contextlib.suppress(OSError, http.client.HTTPException):
            resp.close()
    with contextlib.suppress(OSError, http.client.HTTPException):
        conn.close()


def relay(open_fn, on_head, on_chunk, client_alive_fn, keepalive=None,
          keepalive_sec=KEEPALIVE_SEC, probe_sec=PROBE_SEC, clock=time.monotonic,
          on_prehead_idle=None, prehead_sec=STREAM_COMMIT_SEC):
    # type: (object, object, object, object, object, float, float, object, object, float) -> dict
    """`open_fn(register)` 在读取线程里调用，把 `register` 交给 `open_upstream`。
    任何退出路径（含回调抛出任意异常）都会关闭上游，读取线程随之结束。

    `on_prehead_idle`：上游响应头 `prehead_sec` 内没到时调一次（调用方借此先发响应头），
    此后即使头还没到也照常保活。"""
    items = queue.Queue()
    state = {"head": None, "conn": None, "sock": None, "cancelled": False}

    def register(conn):
        state["conn"] = conn
        state["sock"] = conn.sock

    def reader():
        try:
            head = open_fn(register)
        except BaseException as exc:     # noqa: BLE001  读线程静默死亡会让主循环永远等
            head = ("failed", f"{type(exc).__name__}: {str(exc)[:200]}")
        state["head"] = head
        if state["cancelled"]:
            _close(state)                # 客户端在等响应头期间就走了
        items.put(("head", head))
        if head[0] != "stream":
            return
        resp = head[2]
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    items.put(("end", None))
                    return
                items.put(("chunk", chunk))
        except Exception as exc:         # noqa: BLE001
            items.put(("abort", exc))

    threading.Thread(target=reader, daemon=True).start()
    result = {"outcome": "ok", "head": None, "bytes": 0}
    last = last_probe = started = clock()
    committed = False
    try:
        while True:
            # **按时间间隔探测，不依附于「取不到数据」**：上游持续出 token 时队列永远不空，
            # 若只在空闲时探测，非流式客户端（帧只进聚合器、不写客户端）断开后后端会一直跑到生成结束
            if clock() - last_probe >= probe_sec:
                last_probe = clock()
                if not client_alive_fn():
                    result["outcome"] = "client_disconnected"
                    return result
            try:
                kind, value = items.get(timeout=probe_sec)
            except queue.Empty:
                if result["head"] is None and on_prehead_idle is not None and not committed \
                        and clock() - started >= prehead_sec:
                    try:
                        on_prehead_idle()
                    except OSError:
                        result["outcome"] = "client_disconnected"
                        return result
                    committed = True
                    last = clock()
                if keepalive is not None and (result["head"] is not None or committed) \
                        and clock() - last >= keepalive_sec:
                    try:
                        keepalive()
                    except OSError:
                        result["outcome"] = "client_disconnected"
                        return result
                    last = clock()
                continue
            if kind == "head":
                result["head"] = value
                try:
                    on_head(value)
                except OSError:
                    result["outcome"] = "client_disconnected"
                    return result
                if value[0] == "error":
                    result["outcome"] = "upstream_error"
                    return result
                if value[0] == "failed":
                    result["outcome"] = "upstream_failed"
                    return result
                last = clock()
            elif kind == "chunk":
                try:
                    on_chunk(value)
                except OSError:
                    result["outcome"] = "client_disconnected"
                    return result
                result["bytes"] += len(value)
                last = clock()
            elif kind == "end":
                return result
            else:
                result["outcome"] = "upstream_aborted"
                return result
    finally:
        state["cancelled"] = True
        _close(state)
